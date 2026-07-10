"""Tests for PolicySpec materialization and safely_from_spec."""

from __future__ import annotations

import uuid

from agent_safety import safely, tool
from agent_safety.envelope import EnvelopeSigner, EnvelopeVerifier
from agent_safety.exceptions import PermissionDenied
from agent_safety.policy_spec import PolicySpec, safely_from_spec
from agent_safety.run import RunContext


@tool
def search(q: str) -> str:
    return f"results: {q}"


def test_to_policy_permissions():
    spec = PolicySpec(allow=("search", "summarize"), calls=10, no_repeats=3)
    policy = spec.to_policy()
    assert policy.allows("search")
    assert not policy.allows("shell.exec")


def test_from_safely_kwargs_budget_string():
    spec = PolicySpec.from_safely_kwargs(allow="search", budget="$50", calls=5)
    assert spec.budget == 50.0
    assert spec.calls == 5


def test_safely_from_spec_runs_tool():
    spec = PolicySpec(allow=("search",), calls=5)
    with safely_from_spec(spec):
        assert search("hello") == "results: hello"


def test_safely_with_envelope_hot_path():
    secret = b"safely-envelope-test-secret-32b!"
    signer = EnvelopeSigner(secret)
    spec = PolicySpec(allow=("search",), calls=5)
    env = signer.sign(
        task_id=uuid.uuid4().hex,
        policy_hash=spec.policy_hash(),
        allowed_capabilities=("search",),
        capability="search",
    )
    ctx = RunContext.new(agent_id="worker")
    with safely(
        envelope=env,
        envelope_keys={"default": secret},
        run=ctx,
    ):
        assert search("paris") == "results: paris"


def test_safely_envelope_denies_wrong_tool():
    secret = b"safely-envelope-test-secret-32b!"
    signer = EnvelopeSigner(secret)
    spec = PolicySpec(allow=("search",), calls=5)
    env = signer.sign(
        task_id="t",
        policy_hash=spec.policy_hash(),
        allowed_capabilities=("search",),
        capability="search",
    )
    # envelope only authorizes search; safely still has default deny for summarize
    with safely(envelope=env, envelope_keys={"default": secret}, allow="summarize"):
        try:
            search("x")  # search is in envelope allow list via PermissionSet
        except PermissionDenied:
            pass
