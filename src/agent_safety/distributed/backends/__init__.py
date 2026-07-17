"""Atomic budget backends for single-process and distributed agent runs."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from threading import Lock
from typing import Dict, List, Optional, Protocol, Tuple

from ...core.exceptions import QuotaExceeded, RiskBudgetExceeded
from ...core.limits import ConcurrencyLimit, LoopGuard, RateLimit
from ...core.quota import Quota, RiskBudget


@dataclass(frozen=True)
class BudgetLimits:
    """Limit configuration applied atomically by a backend."""

    max_calls: Optional[int] = None
    max_tokens: Optional[int] = None
    rate_per_second: Optional[int] = None
    rate_window: float = 1.0
    max_identical: Optional[int] = None
    loop_history: int = 64
    max_concurrent: Optional[int] = None
    max_risk: Optional[int] = None
    lease_ttl: float = 60.0


@dataclass(frozen=True)
class BudgetCharge:
    """One atomic budget operation for a tool invocation."""

    task_id: str
    request_id: str
    signature: str
    org_id: str = ""
    call_n: int = 1
    risk: int = 0
    tokens: int = 0


@dataclass(frozen=True)
class ChargeResult:
    """Outcome of a successful :meth:`BudgetBackend.charge`."""

    lease_id: Optional[str] = None
    calls_used: int = 0
    cached: bool = False


class BudgetBackend(Protocol):
    """Store for atomic budget charging across processes."""

    def charge(self, req: BudgetCharge, limits: BudgetLimits) -> ChargeResult:
        """Apply all limits atomically, or raise a typed safety exception."""
        ...

    def release_concurrency(self, task_id: str, lease_id: str, *, org_id: str = "") -> None:
        """Release a concurrency lease acquired by :meth:`charge`."""
        ...


def _key(org_id: str, task_id: str, suffix: str) -> str:
    if org_id:
        return f"{org_id}:{task_id}:{suffix}"
    return f"{task_id}:{suffix}"


@dataclass
class _TaskState:
    quota: Quota = field(default_factory=Quota)
    rate: Optional[RateLimit] = None
    loop: Optional[LoopGuard] = None
    concurrency: Optional[ConcurrencyLimit] = None
    risk: Optional[RiskBudget] = None
    loop_ring: List[str] = field(default_factory=list)
    leases: Dict[str, float] = field(default_factory=dict)
    sem_count: int = 0


class MemoryBackend:
    """In-process budget store — default when no distributed backend is configured."""

    def __init__(self) -> None:
        self._tasks: Dict[str, _TaskState] = {}
        self._idem: Dict[str, Tuple[ChargeResult, float]] = {}
        self._lock = Lock()

    def _task(self, org_id: str, task_id: str) -> _TaskState:
        key = _key(org_id, task_id, "state")
        state = self._tasks.get(key)
        if state is None:
            state = _TaskState()
            self._tasks[key] = state
        return state

    def charge(self, req: BudgetCharge, limits: BudgetLimits) -> ChargeResult:
        with self._lock:
            cached = self._idem.get(req.request_id)
            if cached is not None:
                result, _ = cached
                return ChargeResult(
                    lease_id=result.lease_id,
                    calls_used=result.calls_used,
                    cached=True,
                )

            state = self._task(req.org_id, req.task_id)
            now = time.monotonic()

            if limits.max_calls is not None:
                state.quota.max_calls = limits.max_calls
                state.quota.charge_call(req.call_n)

            if limits.rate_per_second is not None:
                if state.rate is None or state.rate.limit != limits.rate_per_second:
                    state.rate = RateLimit(
                        max_calls=limits.rate_per_second,
                        per_seconds=limits.rate_window,
                    )
                for _ in range(req.call_n):
                    state.rate.charge(now)

            if limits.max_identical is not None:
                if state.loop is None or state.loop.max_identical != limits.max_identical:
                    state.loop = LoopGuard(
                        limits.max_identical,
                        history=limits.loop_history,
                    )
                state.loop.record("tool", req.signature)

            lease_id: Optional[str] = None
            if limits.max_concurrent is not None:
                expired = [lid for lid, exp in state.leases.items() if exp <= now]
                for lid in expired:
                    state.leases.pop(lid, None)
                    state.sem_count = max(0, state.sem_count - 1)
                if state.sem_count >= limits.max_concurrent:
                    raise QuotaExceeded("concurrency", limits.max_concurrent, state.sem_count + 1)
                lease_id = uuid.uuid4().hex
                state.leases[lease_id] = now + limits.lease_ttl
                state.sem_count += 1

            if limits.max_risk is not None and req.risk > 0:
                if state.risk is None or state.risk.max_risk != limits.max_risk:
                    state.risk = RiskBudget(limits.max_risk)
                try:
                    state.risk.charge(req.risk)
                except RiskBudgetExceeded:
                    if lease_id is not None:
                        state.leases.pop(lease_id, None)
                        state.sem_count = max(0, state.sem_count - 1)
                    raise

            if limits.max_tokens is not None and req.tokens > 0:
                state.quota.max_tokens = limits.max_tokens
                try:
                    state.quota.charge_tokens(req.tokens)
                except QuotaExceeded:
                    if lease_id is not None:
                        state.leases.pop(lease_id, None)
                        state.sem_count = max(0, state.sem_count - 1)
                    raise

            result = ChargeResult(lease_id=lease_id, calls_used=state.quota.calls_used)
            self._idem[req.request_id] = (result, now)
            return result

    def release_concurrency(self, task_id: str, lease_id: str, *, org_id: str = "") -> None:
        with self._lock:
            state = self._task(org_id, task_id)
            if lease_id in state.leases:
                state.leases.pop(lease_id, None)
                state.sem_count = max(0, state.sem_count - 1)


_default_backend: Optional[MemoryBackend] = None


def default_memory_backend() -> MemoryBackend:
    global _default_backend
    if _default_backend is None:
        _default_backend = MemoryBackend()
    return _default_backend


class BackendQuota:
    """Quota adapter that charges a shared :class:`BudgetBackend` on each spend.

    Duck-types :class:`~agent_safety.quota.Quota` so it plugs into
    :class:`~agent_safety.policy.Policy` without a gateway hop per call when the
    backend is local Redis/memory. Each charge uses a unique idempotency key
    derived from the active :class:`~agent_safety.run.RunContext`.
    """

    def __init__(
        self,
        backend: BudgetBackend,
        *,
        task_id: str,
        request_id: str,
        org_id: str = "",
        agent_id: str = "worker",
        limits: Optional[BudgetLimits] = None,
        charge_calls: bool = True,
    ) -> None:
        self._backend = backend
        self._task_id = task_id
        self._request_id = request_id
        self._org_id = org_id
        self._agent_id = agent_id
        self._limits = limits or BudgetLimits()
        self._charge_calls = charge_calls
        self._seq = 0
        self._lock = Lock()
        self.max_calls = self._limits.max_calls
        self.max_tokens = self._limits.max_tokens
        self.calls_used = 0
        self.tokens_used = 0

    def charge_call(self, n: int = 1) -> None:
        if not self._charge_calls:
            return
        with self._lock:
            self._seq += 1
            result = self._backend.charge(
                BudgetCharge(
                    task_id=self._task_id,
                    request_id=f"{self._request_id}:call:{self._seq}",
                    signature=f"call|{self._agent_id}",
                    org_id=self._org_id,
                    call_n=n,
                ),
                self._limits,
            )
            self.calls_used = result.calls_used

    def charge_tokens(self, n: int) -> None:
        if n < 0:
            raise ValueError("token charge must be non-negative")
        if n == 0:
            return
        with self._lock:
            self._seq += 1
            self._backend.charge(
                BudgetCharge(
                    task_id=self._task_id,
                    request_id=f"{self._request_id}:tok:{self._seq}",
                    signature=f"tokens|{self._agent_id}",
                    org_id=self._org_id,
                    call_n=0,
                    tokens=n,
                ),
                self._limits,
            )
            self.tokens_used += n

    def remaining_calls(self) -> Optional[int]:
        return None if self.max_calls is None else max(0, self.max_calls - self.calls_used)

    def remaining_tokens(self) -> Optional[int]:
        return None if self.max_tokens is None else max(0, self.max_tokens - self.tokens_used)
