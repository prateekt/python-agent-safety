"""Tests for capability envelopes."""

import pytest

from agent_safety.envelope import CapabilityEnvelope, EnvelopeSigner, EnvelopeVerifier
from agent_safety.exceptions import PermissionDenied


def test_envelope_sign_and_verify():
    secret = b"test-secret-key-for-hmac!!"
    signer = EnvelopeSigner(secret)
    env = signer.sign(
        task_id="task-1",
        policy_hash="abc123",
        allowed_capabilities=("search",),
        capability="search",
    )
    verifier = EnvelopeVerifier({"default": secret})
    verifier.verify(env, capability="search")


def test_envelope_rejects_wrong_capability():
    secret = b"test-secret-key-for-hmac!!"
    signer = EnvelopeSigner(secret)
    env = signer.sign(
        task_id="task-1",
        policy_hash="abc123",
        allowed_capabilities=("search",),
        capability="search",
    )
    verifier = EnvelopeVerifier({"default": secret})
    with pytest.raises(PermissionDenied):
        verifier.verify(env, capability="delete")


def test_envelope_round_trip_dict():
    secret = b"test-secret-key-for-hmac!!"
    signer = EnvelopeSigner(secret)
    env = signer.sign(
        task_id="task-1",
        policy_hash="abc123",
        allowed_capabilities=("search",),
        capability="search",
    )
    restored = CapabilityEnvelope.from_dict(env.to_dict())
    verifier = EnvelopeVerifier({"default": secret})
    verifier.verify(restored)
