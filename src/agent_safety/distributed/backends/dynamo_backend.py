"""DynamoDB budget backend — bring your own ``boto3`` / low-level client.

The library does **not** host DynamoDB. Pass a boto3 DynamoDB client (or
resource-compatible wrapper) and the name of a table you already provisioned.
"""

from __future__ import annotations

import json
import time
import uuid
from threading import Lock
from typing import Any, Dict, List, Optional

from ...core.exceptions import LoopDetected, QuotaExceeded, RateLimitExceeded, RiskBudgetExceeded
from . import BudgetBackend, BudgetCharge, BudgetLimits, ChargeResult, MemoryBackend


def _scope(org_id: str, task_id: str) -> str:
    return f"{org_id}:{task_id}" if org_id else task_id


class DynamoBudgetBackend:
    """Budget charging against a user-supplied DynamoDB table.

    Expects a table with partition key ``pk`` (string). Budget rows use
    ``pk=BUDGET#<scope>``; idempotency rows use ``pk=IDEM#<request_id>``.
    Create the table yourself (on-demand or provisioned) — this class only
    reads/writes items.

    Example::

        import boto3
        from agent_safety.backends.dynamo_backend import DynamoBudgetBackend

        client = boto3.client("dynamodb", region_name="us-east-1")
        backend = DynamoBudgetBackend(client, table_name="agent-safety-budgets")
    """

    def __init__(
        self,
        client: Any,
        *,
        table_name: str,
        org_id: str = "",
    ) -> None:
        self._client = client
        self._table = table_name
        self._org_id = org_id
        self._lock = Lock()

    def _get_item(self, pk: str) -> Optional[Dict[str, Any]]:
        resp = self._client.get_item(
            TableName=self._table,
            Key={"pk": {"S": pk}},
            ConsistentRead=True,
        )
        item = resp.get("Item")
        return item if isinstance(item, dict) else None

    def _put_item(self, item: Dict[str, Any], *, condition: Optional[str] = None) -> None:
        kwargs: Dict[str, Any] = {"TableName": self._table, "Item": item}
        if condition:
            kwargs["ConditionExpression"] = condition
        self._client.put_item(**kwargs)

    def charge(self, req: BudgetCharge, limits: BudgetLimits) -> ChargeResult:
        org = req.org_id or self._org_id
        scope = _scope(org, req.task_id)
        budget_pk = f"BUDGET#{scope}"
        idem_pk = f"IDEM#{req.request_id}"
        now = time.time()
        lease_id = uuid.uuid4().hex

        with self._lock:
            cached = self._get_item(idem_pk)
            if cached is not None:
                return ChargeResult(
                    lease_id=(cached.get("lease_id") or {}).get("S") or None,
                    calls_used=int((cached.get("calls_used") or {}).get("N", "0")),
                    cached=True,
                )

            raw = self._get_item(budget_pk)
            if raw and "payload" in raw:
                doc = json.loads(raw["payload"]["S"])
            else:
                doc = {
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
            payload = {
                "calls": new_calls,
                "tokens": new_tokens,
                "risk": new_risk,
                "rate": rate,
                "loop": loop,
                "leases": leases,
            }
            self._put_item(
                {
                    "pk": {"S": budget_pk},
                    "payload": {"S": json.dumps(payload)},
                }
            )
            try:
                self._put_item(
                    {
                        "pk": {"S": idem_pk},
                        "lease_id": {"S": out_lease or ""},
                        "calls_used": {"N": str(new_calls)},
                    },
                    condition="attribute_not_exists(pk)",
                )
            except Exception as exc:
                # ConditionalCheckFailedException → concurrent idem win
                name = type(exc).__name__
                if "ConditionalCheckFailed" in name or "ConditionalCheckFailed" in str(exc):
                    again = self._get_item(idem_pk)
                    if again is not None:
                        return ChargeResult(
                            lease_id=(again.get("lease_id") or {}).get("S") or None,
                            calls_used=int((again.get("calls_used") or {}).get("N", "0")),
                            cached=True,
                        )
                raise

            return ChargeResult(lease_id=out_lease, calls_used=new_calls, cached=False)

    def release_concurrency(self, task_id: str, lease_id: str, *, org_id: str = "") -> None:
        org = org_id or self._org_id
        scope = _scope(org, task_id)
        budget_pk = f"BUDGET#{scope}"
        with self._lock:
            raw = self._get_item(budget_pk)
            if not raw or "payload" not in raw:
                return
            doc = json.loads(raw["payload"]["S"])
            leases = dict(doc.get("leases") or {})
            leases.pop(lease_id, None)
            doc["leases"] = leases
            self._put_item(
                {
                    "pk": {"S": budget_pk},
                    "payload": {"S": json.dumps(doc)},
                }
            )


def dynamo_backend(
    client: Optional[Any] = None,
    *,
    table_name: str = "agent-safety-budgets",
    org_id: str = "",
) -> BudgetBackend:
    """Return a DynamoDB backend if *client* given, else in-memory fallback."""
    if client is None:
        return MemoryBackend()
    return DynamoBudgetBackend(client, table_name=table_name, org_id=org_id)
