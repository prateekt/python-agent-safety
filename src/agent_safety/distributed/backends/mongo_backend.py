"""MongoDB budget backend — bring your own ``pymongo`` client.

The library does **not** host MongoDB. Pass an existing
:class:`~pymongo.mongo_client.MongoClient` (or compatible) pointed at your
cluster. Documents live under a configurable database name.
"""

from __future__ import annotations

import time
import uuid
from threading import Lock
from typing import Any, Dict, List, Optional

from ...core.exceptions import LoopDetected, QuotaExceeded, RateLimitExceeded, RiskBudgetExceeded
from . import BudgetBackend, BudgetCharge, BudgetLimits, ChargeResult, MemoryBackend


def _scope(org_id: str, task_id: str) -> str:
    return f"{org_id}:{task_id}" if org_id else task_id


class MongoBudgetBackend:
    """Budget charging against a user-supplied MongoDB client.

    State for each task is one document (calls / tokens / risk / rate / loop /
    leases). Idempotency keys live in a separate collection. A process lock
    serializes in-process charges; for multi-process atomicity prefer a replica
    set (transactions) or Redis/SQL.

    Example::

        from pymongo import MongoClient
        from agent_safety.backends.mongo_backend import MongoBudgetBackend

        client = MongoClient(MONGODB_URI)  # your existing cluster
        backend = MongoBudgetBackend(client, db_name="agent_safety")
    """

    def __init__(
        self,
        client: Any,
        *,
        db_name: str = "agent_safety",
        org_id: str = "",
        budget_collection: str = "budget",
        idem_collection: str = "idem",
    ) -> None:
        self._client = client
        self._db_name = db_name
        self._org_id = org_id
        self._budget_name = budget_collection
        self._idem_name = idem_collection
        self._lock = Lock()
        self._indexes_ready = False

    def _db(self) -> Any:
        return self._client[self._db_name]

    def ensure_indexes(self) -> None:
        """Create helpful indexes (idempotent). Safe to call at startup."""
        with self._lock:
            db = self._db()
            db[self._idem_name].create_index("created_at", expireAfterSeconds=86400)
            self._indexes_ready = True

    def charge(self, req: BudgetCharge, limits: BudgetLimits) -> ChargeResult:
        if not self._indexes_ready:
            try:
                self.ensure_indexes()
            except Exception:
                self._indexes_ready = True  # don't block charges if TTL index fails

        org = req.org_id or self._org_id
        scope = _scope(org, req.task_id)
        now = time.time()
        lease_id = uuid.uuid4().hex

        with self._lock:
            db = self._db()
            budget = db[self._budget_name]
            idem = db[self._idem_name]

            cached = idem.find_one({"_id": req.request_id})
            if cached is not None:
                return ChargeResult(
                    lease_id=cached.get("lease_id") or None,
                    calls_used=int(cached.get("calls_used", 0)),
                    cached=True,
                )

            doc = budget.find_one({"_id": scope}) or {
                "_id": scope,
                "calls": 0,
                "tokens": 0,
                "risk": 0,
                "rate": [],
                "loop": [],
                "leases": {},
            }
            calls = int(doc.get("calls", 0))
            tokens = int(doc.get("tokens", 0))
            risk_used = int(doc.get("risk", 0))
            rate: List[Dict[str, Any]] = list(doc.get("rate") or [])
            loop: List[str] = list(doc.get("loop") or [])
            leases: Dict[str, float] = {
                str(k): float(v) for k, v in (doc.get("leases") or {}).items()
            }

            if limits.max_calls is not None and calls + req.call_n > limits.max_calls:
                raise QuotaExceeded("calls", limits.max_calls, calls + req.call_n)

            if (
                limits.max_tokens is not None
                and req.tokens > 0
                and tokens + req.tokens > limits.max_tokens
            ):
                raise QuotaExceeded("tokens", limits.max_tokens, tokens + req.tokens)

            if limits.max_risk is not None and req.risk > 0 and risk_used + req.risk > limits.max_risk:
                raise RiskBudgetExceeded(limits.max_risk, risk_used + req.risk)

            if limits.rate_per_second is not None:
                cutoff = now - limits.rate_window
                rate = [e for e in rate if float(e.get("at", 0)) >= cutoff]
                if len(rate) + req.call_n > limits.rate_per_second:
                    raise RateLimitExceeded(limits.rate_per_second, limits.rate_window, 1.0)
                for i in range(req.call_n):
                    rate.append({"id": f"{req.request_id}:{i}", "at": now})

            if limits.max_identical is not None:
                loop = ([req.signature] + loop)[: limits.loop_history]
                identical = sum(1 for s in loop if s == req.signature)
                if identical > limits.max_identical:
                    raise LoopDetected("tool", limits.max_identical, identical)

            out_lease: Optional[str] = None
            if limits.max_concurrent is not None:
                leases = {lid: exp for lid, exp in leases.items() if exp > now}
                if len(leases) >= limits.max_concurrent:
                    raise QuotaExceeded("concurrency", limits.max_concurrent, len(leases) + 1)
                leases[lease_id] = now + limits.lease_ttl
                out_lease = lease_id

            new_calls = calls + req.call_n
            new_tokens = tokens + (req.tokens if req.tokens > 0 else 0)
            new_risk = risk_used + (req.risk if req.risk > 0 else 0)
            budget.replace_one(
                {"_id": scope},
                {
                    "_id": scope,
                    "calls": new_calls,
                    "tokens": new_tokens,
                    "risk": new_risk,
                    "rate": rate,
                    "loop": loop,
                    "leases": leases,
                },
                upsert=True,
            )
            try:
                idem.insert_one(
                    {
                        "_id": req.request_id,
                        "lease_id": out_lease,
                        "calls_used": new_calls,
                        "created_at": now,
                    }
                )
            except Exception:
                # Race: another worker won the idem insert — return their result.
                again = idem.find_one({"_id": req.request_id})
                if again is not None:
                    return ChargeResult(
                        lease_id=again.get("lease_id") or None,
                        calls_used=int(again.get("calls_used", 0)),
                        cached=True,
                    )
                raise

            return ChargeResult(lease_id=out_lease, calls_used=new_calls, cached=False)

    def release_concurrency(self, task_id: str, lease_id: str, *, org_id: str = "") -> None:
        org = org_id or self._org_id
        scope = _scope(org, task_id)
        with self._lock:
            self._db()[self._budget_name].update_one(
                {"_id": scope},
                {"$unset": {f"leases.{lease_id}": ""}},
            )


def mongo_backend(
    client: Optional[Any] = None,
    *,
    db_name: str = "agent_safety",
    org_id: str = "",
) -> BudgetBackend:
    """Return a Mongo backend if *client* given, else in-memory fallback."""
    if client is None:
        return MemoryBackend()
    return MongoBudgetBackend(client, db_name=db_name, org_id=org_id)
