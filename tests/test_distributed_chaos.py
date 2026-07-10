"""Chaos-style tests for distributed safety."""


import pytest

from agent_safety.backends import BudgetCharge, BudgetLimits, MemoryBackend
from agent_safety.envelope import EnvelopeSigner, EnvelopeVerifier
from agent_safety.exceptions import PermissionDenied
from agent_safety.gateway.server import GatewayConfig, PolicyGateway
from agent_safety.observability import CircuitBreaker
from agent_safety.policy_spec import PolicySpec


def test_duplicate_request_id_idempotent():
    backend = MemoryBackend()
    limits = BudgetLimits(max_calls=1)
    req = BudgetCharge(task_id="t", request_id="dup", signature="s")
    backend.charge(req, limits)
    # retry should not double-charge
    backend.charge(req, limits)


def test_expired_envelope_rejected():
    secret = b"test-secret-key-for-hmac!!"
    signer = EnvelopeSigner(secret)
    env = signer.sign(
        task_id="t",
        policy_hash="h",
        allowed_capabilities=("x",),
        capability="x",
        ttl=1.0,
    )
    verifier = EnvelopeVerifier({"default": secret})
    with pytest.raises(PermissionDenied):
        verifier.verify(env, now=env.expires_at + 60.0)


def test_circuit_breaker_opens():
    cb = CircuitBreaker(failure_threshold=2, open_seconds=1.0)
    cb.record_failure()
    cb.record_failure()
    assert cb.is_open()


def test_gateway_fail_closed_on_circuit_open():
    secret = b"gateway-signing-secret-32bytes!!"
    cb = CircuitBreaker(failure_threshold=1, open_seconds=60.0)
    cb.record_failure()
    gw = PolicyGateway(GatewayConfig(signing_secret=secret, circuit_breaker=cb))
    resp = gw.mint({
        "task_id": "t",
        "capability": "x",
        "policy_spec": PolicySpec(allow=("x",)).to_dict(),
    })
    assert not resp.ok
