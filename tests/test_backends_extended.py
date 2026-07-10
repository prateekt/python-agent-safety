"""Extended backend tests for rate, risk, tokens, concurrency."""

from __future__ import annotations

import pytest

from agent_safety.backends import BudgetCharge, BudgetLimits, MemoryBackend
from agent_safety.exceptions import QuotaExceeded, RateLimitExceeded, RiskBudgetExceeded


def test_rate_limit_enforced():
    backend = MemoryBackend()
    limits = BudgetLimits(rate_per_second=2, rate_window=1.0)
    for i in range(2):
        backend.charge(
            BudgetCharge(task_id="t", request_id=f"r{i}", signature=f"s{i}"),
            limits,
        )
    with pytest.raises(RateLimitExceeded):
        backend.charge(
            BudgetCharge(task_id="t", request_id="r3", signature="s3"),
            limits,
        )


def test_risk_budget_enforced():
    backend = MemoryBackend()
    limits = BudgetLimits(max_risk=5)
    backend.charge(
        BudgetCharge(task_id="t", request_id="r1", signature="s", risk=3),
        limits,
    )
    with pytest.raises(RiskBudgetExceeded):
        backend.charge(
            BudgetCharge(task_id="t", request_id="r2", signature="s2", risk=3),
            limits,
        )


def test_token_charge():
    backend = MemoryBackend()
    limits = BudgetLimits(max_tokens=100)
    backend.charge(
        BudgetCharge(task_id="t", request_id="r1", signature="s", tokens=40, call_n=0),
        limits,
    )
    backend.charge(
        BudgetCharge(task_id="t", request_id="r2", signature="s2", tokens=50, call_n=0),
        limits,
    )
    with pytest.raises(QuotaExceeded):
        backend.charge(
            BudgetCharge(task_id="t", request_id="r3", signature="s3", tokens=20, call_n=0),
            limits,
        )


def test_concurrency_release():
    backend = MemoryBackend()
    limits = BudgetLimits(max_concurrent=1, lease_ttl=60.0)
    r1 = backend.charge(
        BudgetCharge(task_id="t", request_id="r1", signature="s1"),
        limits,
    )
    assert r1.lease_id
    with pytest.raises(QuotaExceeded):
        backend.charge(
            BudgetCharge(task_id="t", request_id="r2", signature="s2"),
            limits,
        )
    backend.release_concurrency("t", r1.lease_id)
    backend.charge(
        BudgetCharge(task_id="t", request_id="r3", signature="s3"),
        limits,
    )
