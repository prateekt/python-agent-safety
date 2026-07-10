"""Tests for RedisBudgetBackend (mocked Redis client)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from agent_safety.backends import BudgetCharge, BudgetLimits
from agent_safety.backends.redis_backend import RedisBudgetBackend, redis_backend
from agent_safety.exceptions import QuotaExceeded


def test_redis_backend_fallback_without_client():
    backend = redis_backend(None)
    from agent_safety.backends import MemoryBackend
    assert isinstance(backend, MemoryBackend)


def test_redis_backend_charge_success():
    client = MagicMock()
    script = MagicMock(return_value=json.dumps({
        "lease_id": "lease-1",
        "calls_used": 1,
        "cached": False,
    }))
    client.register_script.return_value = script

    backend = RedisBudgetBackend(client, org_id="acme")
    result = backend.charge(
        BudgetCharge(task_id="task-1", request_id="req-1", signature="sig", org_id="acme"),
        BudgetLimits(max_calls=10),
    )
    assert result.calls_used == 1
    assert result.lease_id == "lease-1"
    keys = script.call_args.kwargs["keys"]
    assert keys[0].startswith("acme:task-1:")


def test_redis_backend_quota_error():
    client = MagicMock()
    script = MagicMock(side_effect=Exception("QUOTA_EXCEEDED"))
    client.register_script.return_value = script
    backend = RedisBudgetBackend(client)
    with pytest.raises(QuotaExceeded):
        backend.charge(
            BudgetCharge(task_id="t", request_id="r", signature="s"),
            BudgetLimits(max_calls=1),
        )


def test_redis_release_concurrency():
    client = MagicMock()
    client.register_script.return_value = MagicMock()
    backend = RedisBudgetBackend(client, org_id="org1")
    backend.release_concurrency("task-1", "lease-abc", org_id="org1")
    client.zrem.assert_called_once_with("org1:task-1:leases", "lease-abc")
