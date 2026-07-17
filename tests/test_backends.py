"""Tests for atomic budget backends."""

from dataclasses import replace

import pytest

from agent_safety.core.exceptions import LoopDetected, QuotaExceeded
from agent_safety.distributed.backends import BudgetCharge, BudgetLimits, MemoryBackend


def test_memory_backend_charges_calls():
    backend = MemoryBackend()
    limits = BudgetLimits(max_calls=3)
    req = BudgetCharge(task_id="t1", request_id="r1", signature="sig1")
    backend.charge(req, limits)
    backend.charge(replace(req, request_id="r2"), limits)
    backend.charge(replace(req, request_id="r3"), limits)
    with pytest.raises(QuotaExceeded):
        backend.charge(replace(req, request_id="r4"), limits)


def test_idempotent_charge():
    backend = MemoryBackend()
    limits = BudgetLimits(max_calls=1)
    req = BudgetCharge(task_id="t1", request_id="same", signature="sig")
    r1 = backend.charge(req, limits)
    r2 = backend.charge(req, limits)
    assert r1.calls_used == 1
    assert r2.cached is True


def test_loop_detection_shared_task():
    backend = MemoryBackend()
    limits = BudgetLimits(max_identical=2)
    sig = "tool|cap|()|()"
    for i in range(2):
        backend.charge(
            BudgetCharge(task_id="t1", request_id=f"r{i}", signature=sig),
            limits,
        )
    with pytest.raises(LoopDetected):
        backend.charge(
            BudgetCharge(task_id="t1", request_id="r3", signature=sig),
            limits,
        )


def test_org_id_isolation():
    backend = MemoryBackend()
    limits = BudgetLimits(max_calls=1)
    backend.charge(
        BudgetCharge(task_id="t1", request_id="r1", signature="s", org_id="a"),
        limits,
    )
    # different org, same task_id — fresh budget
    backend.charge(
        BudgetCharge(task_id="t1", request_id="r2", signature="s", org_id="b"),
        limits,
    )
