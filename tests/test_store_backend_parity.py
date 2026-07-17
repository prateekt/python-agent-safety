"""Parity: same multi-worker budget behavior across Memory / SQL / Mongo / Dynamo.

Mongo and Dynamo use in-process fakes (no hosted DB). SQL uses a tempfile
SQLite file via stdlib ``sqlite3``.
"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

from agent_safety import safely, tool
from agent_safety.core.exceptions import QuotaExceeded
from agent_safety.distributed.backends import MemoryBackend
from agent_safety.distributed.backends.dynamo_backend import DynamoBudgetBackend
from agent_safety.distributed.backends.mongo_backend import MongoBudgetBackend
from agent_safety.distributed.backends.sql_backend import SqlBudgetBackend
from agent_safety.distributed.run import RunContext


@tool
def search(q: str) -> str:
    return f"results: {q}"


# --- store fakes (same shape as unit tests; no external hosts) ---------------


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
        for path in update.get("$unset") or {}:
            parts = path.split(".", 1)
            if len(parts) == 2 and parts[0] in doc and isinstance(doc[parts[0]], dict):
                doc[parts[0]].pop(parts[1], None)

    def create_index(self, *args: Any, **kwargs: Any) -> None:
        return None


class _FakeDB:
    def __init__(self) -> None:
        self._cols: Dict[str, _FakeColl] = {}

    def __getitem__(self, name: str) -> _FakeColl:
        if name not in self._cols:
            self._cols[name] = _FakeColl()
        return self._cols[name]


class _FakeMongoClient:
    def __init__(self) -> None:
        self._dbs: Dict[str, _FakeDB] = {}

    def __getitem__(self, name: str) -> _FakeDB:
        if name not in self._dbs:
            self._dbs[name] = _FakeDB()
        return self._dbs[name]


class _ConditionalCheckFailed(Exception):
    pass


class _FakeDynamo:
    def __init__(self) -> None:
        self.items: Dict[str, Dict[str, Any]] = {}

    def get_item(
        self, *, TableName: str, Key: Dict[str, Any], ConsistentRead: bool = False
    ) -> Dict[str, Any]:
        item = self.items.get(Key["pk"]["S"])
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
                raise _ConditionalCheckFailed("ConditionalCheckFailedException")
        self.items[pk] = Item
        return {}


@pytest.fixture
def memory_backend() -> MemoryBackend:
    return MemoryBackend()


@pytest.fixture
def sql_backend(tmp_path: Path) -> SqlBudgetBackend:
    path = tmp_path / "parity.db"

    def connect() -> sqlite3.Connection:
        return sqlite3.connect(str(path), timeout=30)

    backend = SqlBudgetBackend(connect, dialect="sqlite")
    backend.ensure_schema()
    return backend


@pytest.fixture
def mongo_backend_fixture() -> MongoBudgetBackend:
    return MongoBudgetBackend(_FakeMongoClient(), db_name="parity")


@pytest.fixture
def dynamo_backend_fixture() -> DynamoBudgetBackend:
    return DynamoBudgetBackend(_FakeDynamo(), table_name="parity")


@pytest.fixture(
    params=["memory", "sql", "mongo", "dynamo"],
    ids=["memory", "sql-sqlite", "mongo-fake", "dynamo-fake"],
)
def shared_backend(
    request: pytest.FixtureRequest,
    memory_backend: MemoryBackend,
    sql_backend: SqlBudgetBackend,
    mongo_backend_fixture: MongoBudgetBackend,
    dynamo_backend_fixture: DynamoBudgetBackend,
) -> Any:
    return {
        "memory": memory_backend,
        "sql": sql_backend,
        "mongo": mongo_backend_fixture,
        "dynamo": dynamo_backend_fixture,
    }[request.param]


def _run_workers(backend: Any, *, workers: int, calls: int) -> tuple[int, list[str]]:
    task_id = uuid.uuid4().hex
    errors: list[str] = []
    ok = 0
    lock = threading.Lock()

    def worker(idx: int) -> None:
        nonlocal ok
        try:
            with safely(
                allow="search",
                calls=calls,
                backend=backend,
                run=RunContext(
                    task_id=task_id,
                    agent_id=f"w{idx}",
                    request_id=f"req-{idx}-{uuid.uuid4().hex[:8]}",
                ),
            ):
                search(f"q{idx}")
            with lock:
                ok += 1
        except QuotaExceeded:
            with lock:
                errors.append(f"quota-{idx}")
        except Exception as exc:
            with lock:
                errors.append(f"other-{idx}:{exc}")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return ok, errors


def test_store_parity_multithread_shared_call_budget(shared_backend: Any) -> None:
    """Six workers, calls=3 → exactly three succeed on every store plugin."""
    ok, errors = _run_workers(shared_backend, workers=6, calls=3)
    assert ok == 3, errors
    assert len(errors) == 3
    assert all(e.startswith("quota-") for e in errors), errors


def test_store_parity_sequential_shared_budget(shared_backend: Any) -> None:
    """Same task_id across sequential safely() blocks shares the budget."""
    task_id = uuid.uuid4().hex
    for i in range(2):
        with safely(
            allow="search",
            calls=2,
            backend=shared_backend,
            run=RunContext(task_id=task_id, agent_id=f"a{i}", request_id=f"r{i}"),
        ):
            assert search(f"q{i}").startswith("results:")

    with pytest.raises(QuotaExceeded):
        with safely(
            allow="search",
            calls=2,
            backend=shared_backend,
            run=RunContext(task_id=task_id, agent_id="overflow", request_id="r-over"),
        ):
            search("nope")
