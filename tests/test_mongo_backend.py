"""Tests for MongoBudgetBackend / MongoNonceStore (mocked client — no hosting)."""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Any, Dict

import pytest

from agent_safety.core.exceptions import LoopDetected, QuotaExceeded, RateLimitExceeded
from agent_safety.distributed.backends import BudgetCharge, BudgetLimits, MemoryBackend
from agent_safety.distributed.backends.mongo_backend import MongoBudgetBackend, mongo_backend
from agent_safety.distributed.nonces import MongoNonceStore


class _FakeColl:
    def __init__(self) -> None:
        self.docs: Dict[Any, Dict[str, Any]] = {}

    def find_one(self, query: Dict[str, Any]) -> Any:
        return self.docs.get(query.get("_id"))

    def replace_one(self, query: Dict[str, Any], doc: Dict[str, Any], upsert: bool = False) -> None:
        self.docs[query["_id"]] = doc

    def insert_one(self, doc: Dict[str, Any]) -> None:
        _id = doc["_id"]
        if _id in self.docs:
            raise Exception("DuplicateKey")
        self.docs[_id] = doc

    def update_one(self, query: Dict[str, Any], update: Dict[str, Any]) -> None:
        doc = self.docs.get(query["_id"])
        if doc is None:
            return
        unset = update.get("$unset") or {}
        for path in unset:
            # path like leases.<id>
            parts = path.split(".", 1)
            if len(parts) == 2 and parts[0] in doc and isinstance(doc[parts[0]], dict):
                doc[parts[0]].pop(parts[1], None)

    def delete_many(self, query: Dict[str, Any]) -> None:
        expired = [
            k
            for k, v in self.docs.items()
            if float(v.get("expires_at", 0)) <= float(query.get("expires_at", {}).get("$lte", 0))
        ]
        for k in expired:
            self.docs.pop(k, None)

    def create_index(self, *args: Any, **kwargs: Any) -> None:
        return None


class _FakeDB:
    def __init__(self) -> None:
        self._cols: Dict[str, _FakeColl] = {}

    def __getitem__(self, name: str) -> _FakeColl:
        if name not in self._cols:
            self._cols[name] = _FakeColl()
        return self._cols[name]


class _FakeClient:
    def __init__(self) -> None:
        self._dbs: Dict[str, _FakeDB] = {}

    def __getitem__(self, name: str) -> _FakeDB:
        if name not in self._dbs:
            self._dbs[name] = _FakeDB()
        return self._dbs[name]


@pytest.fixture
def mongo() -> MongoBudgetBackend:
    return MongoBudgetBackend(_FakeClient(), db_name="test")


def test_mongo_backend_fallback():
    assert isinstance(mongo_backend(None), MemoryBackend)


def test_mongo_charges_and_quota(mongo: MongoBudgetBackend):
    limits = BudgetLimits(max_calls=2)
    req = BudgetCharge(task_id="t", request_id="r1", signature="s")
    mongo.charge(req, limits)
    mongo.charge(replace(req, request_id="r2"), limits)
    with pytest.raises(QuotaExceeded):
        mongo.charge(replace(req, request_id="r3"), limits)


def test_mongo_idempotent(mongo: MongoBudgetBackend):
    limits = BudgetLimits(max_calls=1)
    req = BudgetCharge(task_id="t", request_id="same", signature="s")
    assert mongo.charge(req, limits).cached is False
    assert mongo.charge(req, limits).cached is True


def test_mongo_loop(mongo: MongoBudgetBackend):
    limits = BudgetLimits(max_identical=1)
    mongo.charge(BudgetCharge(task_id="t", request_id="a", signature="x"), limits)
    with pytest.raises(LoopDetected):
        mongo.charge(BudgetCharge(task_id="t", request_id="b", signature="x"), limits)


def test_mongo_rate(mongo: MongoBudgetBackend):
    limits = BudgetLimits(rate_per_second=1, rate_window=60.0)
    mongo.charge(BudgetCharge(task_id="t", request_id="a", signature="s"), limits)
    with pytest.raises(RateLimitExceeded):
        mongo.charge(BudgetCharge(task_id="t", request_id="b", signature="s"), limits)


def test_mongo_concurrency_release(mongo: MongoBudgetBackend):
    limits = BudgetLimits(max_concurrent=1, lease_ttl=60.0)
    r = mongo.charge(BudgetCharge(task_id="t", request_id="a", signature="s"), limits)
    assert r.lease_id
    with pytest.raises(QuotaExceeded):
        mongo.charge(BudgetCharge(task_id="t", request_id="b", signature="s"), limits)
    mongo.release_concurrency("t", r.lease_id)
    mongo.charge(BudgetCharge(task_id="t", request_id="c", signature="s"), limits)


def test_mongo_nonce_replay():
    store = MongoNonceStore(_FakeClient(), db_name="test")
    exp = time.time() + 60
    assert store.spend("n1", exp) is True
    assert store.spend("n1", exp) is False
