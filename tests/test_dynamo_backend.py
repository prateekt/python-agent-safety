"""Tests for DynamoBudgetBackend / DynamoNonceStore (mocked client — no hosting)."""

from __future__ import annotations

import json
import time
from dataclasses import replace
from typing import Any, Dict, Optional

import pytest

from agent_safety.core.exceptions import LoopDetected, QuotaExceeded
from agent_safety.distributed.backends import BudgetCharge, BudgetLimits, MemoryBackend
from agent_safety.distributed.backends.dynamo_backend import DynamoBudgetBackend, dynamo_backend
from agent_safety.distributed.nonces import DynamoNonceStore


class ConditionalCheckFailedException(Exception):
    pass


class _FakeDynamo:
    def __init__(self) -> None:
        self.items: Dict[str, Dict[str, Any]] = {}

    def get_item(
        self, *, TableName: str, Key: Dict[str, Any], ConsistentRead: bool = False
    ) -> Dict[str, Any]:
        pk = Key["pk"]["S"]
        item = self.items.get(pk)
        return {"Item": item} if item is not None else {}

    def put_item(
        self,
        *,
        TableName: str,
        Item: Dict[str, Any],
        ConditionExpression: Optional[str] = None,
    ) -> Dict[str, Any]:
        pk = Item["pk"]["S"]
        if ConditionExpression and "attribute_not_exists" in ConditionExpression:
            if pk in self.items:
                raise ConditionalCheckFailedException("ConditionalCheckFailedException")
        self.items[pk] = Item
        return {}


@pytest.fixture
def dynamo() -> DynamoBudgetBackend:
    return DynamoBudgetBackend(_FakeDynamo(), table_name="t")


def test_dynamo_backend_fallback():
    assert isinstance(dynamo_backend(None), MemoryBackend)


def test_dynamo_charges_and_quota(dynamo: DynamoBudgetBackend):
    limits = BudgetLimits(max_calls=2)
    req = BudgetCharge(task_id="t", request_id="r1", signature="s")
    dynamo.charge(req, limits)
    dynamo.charge(replace(req, request_id="r2"), limits)
    with pytest.raises(QuotaExceeded):
        dynamo.charge(replace(req, request_id="r3"), limits)


def test_dynamo_idempotent(dynamo: DynamoBudgetBackend):
    limits = BudgetLimits(max_calls=1)
    req = BudgetCharge(task_id="t", request_id="same", signature="s")
    assert dynamo.charge(req, limits).cached is False
    assert dynamo.charge(req, limits).cached is True


def test_dynamo_loop(dynamo: DynamoBudgetBackend):
    limits = BudgetLimits(max_identical=1)
    dynamo.charge(BudgetCharge(task_id="t", request_id="a", signature="x"), limits)
    with pytest.raises(LoopDetected):
        dynamo.charge(BudgetCharge(task_id="t", request_id="b", signature="x"), limits)


def test_dynamo_concurrency_release(dynamo: DynamoBudgetBackend):
    limits = BudgetLimits(max_concurrent=1, lease_ttl=60.0)
    r = dynamo.charge(BudgetCharge(task_id="t", request_id="a", signature="s"), limits)
    assert r.lease_id
    with pytest.raises(QuotaExceeded):
        dynamo.charge(BudgetCharge(task_id="t", request_id="b", signature="s"), limits)
    dynamo.release_concurrency("t", r.lease_id)
    dynamo.charge(BudgetCharge(task_id="t", request_id="c", signature="s"), limits)


def test_dynamo_nonce_replay():
    client = _FakeDynamo()
    store = DynamoNonceStore(client, table_name="t")
    exp = time.time() + 60
    assert store.spend("n1", exp) is True
    assert store.spend("n1", exp) is False


def test_dynamo_payload_roundtrip(dynamo: DynamoBudgetBackend):
    dynamo.charge(
        BudgetCharge(task_id="t", request_id="r1", signature="s", tokens=3),
        BudgetLimits(max_tokens=10),
    )
    raw = dynamo._client.items["BUDGET#t"]  # type: ignore[attr-defined]
    payload = json.loads(raw["payload"]["S"])
    assert payload["tokens"] == 3
