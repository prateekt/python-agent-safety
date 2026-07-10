"""Idempotency tests for distributed charging."""

from agent_safety.backends import BudgetCharge, BudgetLimits, MemoryBackend
from agent_safety.gateway.server import GatewayConfig, PolicyGateway
from agent_safety.policy_spec import PolicySpec


def test_gateway_mint_idempotent_request_id():
    secret = b"idempotent-test-signing-secret!!"
    backend = MemoryBackend()
    gw = PolicyGateway(GatewayConfig(signing_secret=secret, backend=backend))
    spec = PolicySpec(allow=("tool",), calls=2)
    body = {
        "task_id": "task-1",
        "request_id": "fixed-req",
        "capability": "tool",
        "policy_spec": spec.to_dict(),
    }
    r1 = gw.mint(body)
    r2 = gw.mint(body)
    assert r1.ok and r2.ok
    # second mint should not exhaust 1-call budget if idempotent
    assert backend.charge(
        BudgetCharge("task-1", "other", "sig"),
        BudgetLimits(max_calls=2),
    )
