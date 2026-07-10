"""Serializable policy configuration for distributed agent runs."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, replace
from threading import Lock
from typing import Any, ContextManager, Dict, Optional, Tuple

from .backends import BudgetBackend, BudgetLimits
from .easy import safely
from .limits import ConcurrencyLimit, Deadline, LoopGuard, RateLimit
from .permissions import PermissionSet
from .policy import Policy
from .quota import CostBudget, Quota, RiskBudget


def _canonical_json(obj: Dict[str, Any]) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class PolicySpec:
    """JSON-serializable policy configuration (limits + permissions)."""

    version: int = 1
    allow: Tuple[str, ...] = ()
    deny: Tuple[str, ...] = ()
    calls: Optional[int] = None
    tokens: Optional[int] = None
    per_second: Optional[int] = None
    per_minute: Optional[int] = None
    seconds: Optional[float] = None
    at_most: Optional[int] = None
    no_repeats: Optional[int] = None
    risk_budget: Optional[int] = None
    budget: Optional[float] = None
    ask: bool = False
    monitor: bool = False

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"version": self.version}
        if self.allow:
            d["allow"] = list(self.allow)
        if self.deny:
            d["deny"] = list(self.deny)
        for key in (
            "calls", "tokens", "per_second", "per_minute", "seconds",
            "at_most", "no_repeats", "risk_budget", "budget",
        ):
            val = getattr(self, key)
            if val is not None:
                d[key] = val
        if self.ask:
            d["ask"] = True
        if self.monitor:
            d["monitor"] = True
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PolicySpec":
        allow = tuple(data.get("allow", ()))
        deny = tuple(data.get("deny", ()))
        return cls(
            version=int(data.get("version", 1)),
            allow=allow,
            deny=deny,
            calls=data.get("calls"),
            tokens=data.get("tokens"),
            per_second=data.get("per_second"),
            per_minute=data.get("per_minute"),
            seconds=data.get("seconds"),
            at_most=data.get("at_most"),
            no_repeats=data.get("no_repeats"),
            risk_budget=data.get("risk_budget"),
            budget=data.get("budget"),
            ask=bool(data.get("ask", False)),
            monitor=bool(data.get("monitor", False)),
        )

    def canonical_json(self) -> str:
        return _canonical_json(self.to_dict())

    def policy_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()

    def permissions(self) -> PermissionSet:
        allow = list(self.allow) or ["*"]
        return PermissionSet.of(*allow, deny=self.deny)

    def budget_limits(self) -> BudgetLimits:
        rate = self.per_second
        window = 1.0
        if self.per_minute is not None:
            rate = self.per_minute
            window = 60.0
        return BudgetLimits(
            max_calls=self.calls,
            max_tokens=self.tokens,
            rate_per_second=rate,
            rate_window=window,
            max_identical=self.no_repeats,
            max_concurrent=self.at_most,
            max_risk=self.risk_budget,
        )

    def narrow(self, other: "PolicySpec") -> "PolicySpec":
        """Return a stricter spec (permission intersection + tighter limits)."""
        perms = self.permissions().intersect(other.permissions())
        allow = tuple(sorted(perms.allow))
        deny = tuple(sorted(perms.deny))

        def _tighter(a: Optional[int], b: Optional[int]) -> Optional[int]:
            vals = [v for v in (a, b) if v is not None]
            return min(vals) if vals else None

        def _tighter_f(a: Optional[float], b: Optional[float]) -> Optional[float]:
            vals = [v for v in (a, b) if v is not None]
            return min(vals) if vals else None

        return replace(
            self,
            allow=allow,
            deny=deny,
            calls=_tighter(self.calls, other.calls),
            tokens=_tighter(self.tokens, other.tokens),
            per_second=_tighter(self.per_second, other.per_second),
            per_minute=_tighter(self.per_minute, other.per_minute),
            seconds=_tighter_f(self.seconds, other.seconds),
            at_most=_tighter(self.at_most, other.at_most),
            no_repeats=_tighter(self.no_repeats, other.no_repeats),
            risk_budget=_tighter(self.risk_budget, other.risk_budget),
            budget=_tighter_f(self.budget, other.budget),
            ask=self.ask or other.ask,
            monitor=self.monitor or other.monitor,
        )

    @classmethod
    def from_safely_kwargs(
        cls,
        allow: Any = None,
        deny: Any = None,
        **kwargs: Any,
    ) -> "PolicySpec":
        """Build from the same keywords as :func:`~agent_safety.easy.safely`."""
        allow_list: Tuple[str, ...] = ()
        if allow is not None:
            if isinstance(allow, str):
                allow_list = (allow,)
            else:
                allow_list = tuple(str(a) for a in allow)
        deny_list: Tuple[str, ...] = ()
        if deny is not None:
            if isinstance(deny, str):
                deny_list = (deny,)
            else:
                deny_list = tuple(str(d) for d in deny)
        budget = kwargs.get("budget")
        if isinstance(budget, str):
            budget = float(budget.strip().lstrip("$").replace(",", ""))
        return cls(
            allow=allow_list,
            deny=deny_list,
            calls=kwargs.get("calls"),
            tokens=kwargs.get("tokens"),
            per_second=kwargs.get("per_second"),
            per_minute=kwargs.get("per_minute"),
            seconds=kwargs.get("seconds"),
            at_most=kwargs.get("at_most"),
            no_repeats=kwargs.get("no_repeats"),
            risk_budget=kwargs.get("risk_budget"),
            budget=budget,
            ask=bool(kwargs.get("ask", False)),
            monitor=bool(kwargs.get("monitor", False)),
        )

    def to_policy(
        self,
        backend: Optional[BudgetBackend] = None,
    ) -> Policy:
        """Materialize a live :class:`Policy` for local guard enforcement."""
        perms = self.permissions()
        quota = Quota(max_calls=self.calls, max_tokens=self.tokens) if (
            self.calls or self.tokens
        ) else None
        rate: Optional[RateLimit] = None
        if self.per_second is not None:
            rate = RateLimit(per_second=self.per_second)
        elif self.per_minute is not None:
            rate = RateLimit(per_minute=self.per_minute)
        policy = Policy(permissions=perms, enforce=not self.monitor)
        narrow_kwargs: Dict[str, Any] = {}
        if quota:
            narrow_kwargs["quotas"] = (quota,)
        if rate:
            narrow_kwargs["rate_limits"] = (rate,)
        if self.seconds:
            narrow_kwargs["deadlines"] = (Deadline(self.seconds),)
        if self.at_most:
            narrow_kwargs["concurrency_limits"] = (ConcurrencyLimit(self.at_most),)
        if self.no_repeats:
            narrow_kwargs["loop_guards"] = (LoopGuard(self.no_repeats),)
        if self.risk_budget:
            narrow_kwargs["risk_budgets"] = (RiskBudget(self.risk_budget),)
        if self.budget is not None:
            narrow_kwargs["cost_budgets"] = (CostBudget(self.budget),)
        if narrow_kwargs:
            policy = policy.narrow(**narrow_kwargs)
        _ = backend  # backend used at charge time via gateway, not Policy object
        return policy


@dataclass(frozen=True)
class PolicyRegistryEntry:
    spec: PolicySpec
    version: int
    published_at: float
    published_by: str = "system"


class PolicyRegistry:
    """Versioned :class:`PolicySpec` store with hot-reload."""

    def __init__(self) -> None:
        self._entries: Dict[str, PolicyRegistryEntry] = {}
        self._by_hash: Dict[str, str] = {}
        self._lock = Lock()

    def publish(
        self,
        spec: PolicySpec,
        *,
        version: Optional[int] = None,
        published_by: str = "system",
    ) -> str:
        """Register *spec* and return its ``policy_hash``."""
        phash = spec.policy_hash()
        ver = version if version is not None else int(time.time())
        entry = PolicyRegistryEntry(
            spec=spec,
            version=ver,
            published_at=time.time(),
            published_by=published_by,
        )
        with self._lock:
            self._entries[phash] = entry
            self._by_hash[phash] = phash
        return phash

    def resolve(self, policy_hash: str) -> Optional[PolicySpec]:
        with self._lock:
            entry = self._entries.get(policy_hash)
            return entry.spec if entry else None

    def rollback(self, policy_hash: str) -> bool:
        """Pin resolves to an older hash (no-op if already present)."""
        return self.resolve(policy_hash) is not None


def safely_from_spec(
    spec: PolicySpec,
    *,
    backend: Optional[BudgetBackend] = None,
    envelope: Any = None,
    run: Any = None,
) -> ContextManager[Policy]:
    """Context manager equivalent to ``safely(...)`` built from a :class:`PolicySpec`."""
    kwargs: Dict[str, Any] = {
        "allow": list(spec.allow) or None,
        "deny": list(spec.deny) or None,
        "calls": spec.calls,
        "tokens": spec.tokens,
        "per_second": spec.per_second,
        "per_minute": spec.per_minute,
        "seconds": spec.seconds,
        "at_most": spec.at_most,
        "no_repeats": spec.no_repeats,
        "risk_budget": spec.risk_budget,
        "budget": spec.budget,
        "ask": spec.ask,
        "monitor": spec.monitor,
    }
    if envelope is not None:
        kwargs["envelope"] = envelope
    if run is not None:
        kwargs["run"] = run
    if backend is not None:
        kwargs["backend"] = backend
    return safely(**kwargs)
