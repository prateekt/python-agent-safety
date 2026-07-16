"""Tests for SqlBudgetBackend / SqlNonceStore (stdlib sqlite3 — no DB hosting)."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import replace
from pathlib import Path

import pytest

from agent_safety.backends import BudgetCharge, BudgetLimits, MemoryBackend
from agent_safety.backends.sql_backend import SqlBudgetBackend, sql_backend
from agent_safety.exceptions import (
    LoopDetected,
    QuotaExceeded,
    RateLimitExceeded,
    RiskBudgetExceeded,
)
from agent_safety.nonces import SqlNonceStore


def _connect_factory(path: Path):
    def connect() -> sqlite3.Connection:
        return sqlite3.connect(str(path), timeout=30)

    return connect


@pytest.fixture
def sql_backend_db(tmp_path: Path) -> SqlBudgetBackend:
    path = tmp_path / "budgets.db"
    backend = SqlBudgetBackend(_connect_factory(path), dialect="sqlite")
    backend.ensure_schema()
    return backend


def test_sql_backend_fallback_without_connect():
    assert isinstance(sql_backend(None), MemoryBackend)


def test_sql_charges_calls(sql_backend_db: SqlBudgetBackend):
    limits = BudgetLimits(max_calls=3)
    req = BudgetCharge(task_id="t1", request_id="r1", signature="sig1")
    sql_backend_db.charge(req, limits)
    sql_backend_db.charge(replace(req, request_id="r2"), limits)
    sql_backend_db.charge(replace(req, request_id="r3"), limits)
    with pytest.raises(QuotaExceeded):
        sql_backend_db.charge(replace(req, request_id="r4"), limits)


def test_sql_idempotent_charge(sql_backend_db: SqlBudgetBackend):
    limits = BudgetLimits(max_calls=1)
    req = BudgetCharge(task_id="t1", request_id="same", signature="sig")
    r1 = sql_backend_db.charge(req, limits)
    r2 = sql_backend_db.charge(req, limits)
    assert r1.calls_used == 1
    assert r2.cached is True
    assert r2.calls_used == 1


def test_sql_loop_detection(sql_backend_db: SqlBudgetBackend):
    limits = BudgetLimits(max_identical=2)
    sig = "tool|cap|()|()"
    for i in range(2):
        sql_backend_db.charge(
            BudgetCharge(task_id="t1", request_id=f"r{i}", signature=sig),
            limits,
        )
    with pytest.raises(LoopDetected):
        sql_backend_db.charge(
            BudgetCharge(task_id="t1", request_id="r3", signature=sig),
            limits,
        )


def test_sql_org_isolation(sql_backend_db: SqlBudgetBackend):
    limits = BudgetLimits(max_calls=1)
    sql_backend_db.charge(
        BudgetCharge(task_id="t1", request_id="r1", signature="s", org_id="a"),
        limits,
    )
    sql_backend_db.charge(
        BudgetCharge(task_id="t1", request_id="r2", signature="s", org_id="b"),
        limits,
    )


def test_sql_rate_limit(sql_backend_db: SqlBudgetBackend):
    limits = BudgetLimits(rate_per_second=2, rate_window=60.0)
    sql_backend_db.charge(
        BudgetCharge(task_id="t1", request_id="r1", signature="s"),
        limits,
    )
    sql_backend_db.charge(
        BudgetCharge(task_id="t1", request_id="r2", signature="s"),
        limits,
    )
    with pytest.raises(RateLimitExceeded):
        sql_backend_db.charge(
            BudgetCharge(task_id="t1", request_id="r3", signature="s"),
            limits,
        )


def test_sql_risk_budget(sql_backend_db: SqlBudgetBackend):
    limits = BudgetLimits(max_risk=5)
    sql_backend_db.charge(
        BudgetCharge(task_id="t1", request_id="r1", signature="s", risk=3),
        limits,
    )
    with pytest.raises(RiskBudgetExceeded):
        sql_backend_db.charge(
            BudgetCharge(task_id="t1", request_id="r2", signature="s", risk=3),
            limits,
        )


def test_sql_token_budget(sql_backend_db: SqlBudgetBackend):
    limits = BudgetLimits(max_tokens=10)
    sql_backend_db.charge(
        BudgetCharge(task_id="t1", request_id="r1", signature="s", tokens=8),
        limits,
    )
    with pytest.raises(QuotaExceeded):
        sql_backend_db.charge(
            BudgetCharge(task_id="t1", request_id="r2", signature="s", tokens=5),
            limits,
        )


def test_sql_concurrency_lease_and_release(sql_backend_db: SqlBudgetBackend):
    limits = BudgetLimits(max_concurrent=1, lease_ttl=60.0)
    r1 = sql_backend_db.charge(
        BudgetCharge(task_id="t1", request_id="r1", signature="s"),
        limits,
    )
    assert r1.lease_id
    with pytest.raises(QuotaExceeded):
        sql_backend_db.charge(
            BudgetCharge(task_id="t1", request_id="r2", signature="s"),
            limits,
        )
    sql_backend_db.release_concurrency("t1", r1.lease_id)
    sql_backend_db.charge(
        BudgetCharge(task_id="t1", request_id="r3", signature="s"),
        limits,
    )


def test_sql_helper_creates_schema(tmp_path: Path):
    path = tmp_path / "via_helper.db"
    backend = sql_backend(_connect_factory(path), dialect="sqlite")
    assert isinstance(backend, SqlBudgetBackend)
    backend.charge(
        BudgetCharge(task_id="t", request_id="r", signature="s"),
        BudgetLimits(max_calls=1),
    )


def test_sql_nonce_store_replay(tmp_path: Path):
    path = tmp_path / "nonces.db"
    store = SqlNonceStore(_connect_factory(path), dialect="sqlite")
    expires = time.time() + 60
    assert store.spend("n1", expires) is True
    assert store.spend("n1", expires) is False
    assert store.spend("n2", expires) is True


def test_sql_nonce_store_expires(tmp_path: Path):
    path = tmp_path / "nonces_exp.db"
    store = SqlNonceStore(_connect_factory(path), dialect="sqlite")
    assert store.spend("old", time.time() - 1) is True
    # expired row cleaned on next spend — same nonce can be reused
    assert store.spend("old", time.time() + 60) is True
