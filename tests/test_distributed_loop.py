"""Integration tests for distributed loops."""

import pytest

from agent_safety.backends import BudgetCharge, BudgetLimits, MemoryBackend
from agent_safety.envelope import EnvelopeVerifier
from agent_safety.exceptions import LoopDetected
from agent_safety.gateway.server import GatewayConfig, PolicyGateway
from agent_safety.policy_spec import PolicySpec
from agent_safety.run import RunContext


def test_gateway_mint_and_worker_verify():
    secret = b"gateway-signing-secret-32bytes!!"
    backend = MemoryBackend()
    gw = PolicyGateway(GatewayConfig(require_auth=False, signing_secret=secret, backend=backend))
    spec = PolicySpec(allow=("weather.read",), calls=5)
    gw.config.registry.publish(spec)

    resp = gw.mint({
        "task_id": "task-abc",
        "request_id": "req-1",
        "capability": "weather.read",
        "tool": "get_weather",
        "policy_spec": spec.to_dict(),
        "signature": "get_weather|weather.read|('Paris',)|()",
    })
    assert resp.ok
    assert resp.envelope is not None

    verifier = EnvelopeVerifier({"default": secret})
    from agent_safety.envelope import CapabilityEnvelope
    env = CapabilityEnvelope.from_dict(resp.envelope)
    verifier.verify(env)


def test_two_workers_share_loop_guard_via_backend():
    backend = MemoryBackend()
    limits = BudgetLimits(max_identical=2)
    sig = "get_weather|weather.read|('Paris',)|()"
    backend.charge(BudgetCharge("task", "r1", sig), limits)
    backend.charge(BudgetCharge("task", "r2", sig), limits)
    with pytest.raises(LoopDetected):
        backend.charge(BudgetCharge("task", "r3", sig), limits)


def test_run_context_fields():
    ctx = RunContext.new(agent_id="worker-1", org_id="acme")
    assert ctx.task_id
    assert ctx.request_id
    assert ctx.org_id == "acme"
