"""Tests for safely() distributed wiring: backend, rollout, keys."""

from __future__ import annotations

import base64
import json

import pytest

from agent_safety import safely, tool
from agent_safety.backends import MemoryBackend
from agent_safety.envelope import EnvelopeSigner
from agent_safety.exceptions import PermissionDenied, QuotaExceeded
from agent_safety.policy_spec import PolicySpec
from agent_safety.run import RunContext


@tool
def search(q: str) -> str:
    return f"results: {q}"


def test_backend_charges_shared_budget_across_runs():
    backend = MemoryBackend()
    task_id = "shared-task"

    with safely(
        allow="search",
        calls=2,
        backend=backend,
        run=RunContext(task_id=task_id, agent_id="a", request_id="r1"),
    ):
        assert search("one") == "results: one"

    with safely(
        allow="search",
        calls=2,
        backend=backend,
        run=RunContext(task_id=task_id, agent_id="b", request_id="r2"),
    ):
        assert search("two") == "results: two"

    with pytest.raises(QuotaExceeded):
        with safely(
            allow="search",
            calls=2,
            backend=backend,
            run=RunContext(task_id=task_id, agent_id="c", request_id="r3"),
        ):
            search("three")


def test_enforce_mode_requires_envelope(monkeypatch):
    monkeypatch.setenv("AGENT_SAFETY_DISTRIBUTED", "enforce")
    with pytest.raises(PermissionDenied, match="envelope required"):
        with safely(allow="search", calls=5):
            search("x")


def test_enforce_mode_accepts_valid_envelope(monkeypatch):
    monkeypatch.setenv("AGENT_SAFETY_DISTRIBUTED", "enforce")
    secret = b"enforce-test-signing-secret-32b!!"
    signer = EnvelopeSigner(secret)
    spec = PolicySpec(allow=("search",), calls=5)
    env = signer.sign(
        task_id="t1",
        policy_hash=spec.policy_hash(),
        allowed_capabilities=("search",),
        capability="search",
    )
    with safely(envelope=env, envelope_keys={"default": secret}, run=RunContext.new()):
        assert search("ok") == "results: ok"


def test_shadow_mode_allows_bad_envelope(monkeypatch, capsys):
    monkeypatch.setenv("AGENT_SAFETY_DISTRIBUTED", "shadow")
    secret = b"shadow-test-signing-secret-32b!!"
    signer = EnvelopeSigner(secret)
    spec = PolicySpec(allow=("search",), calls=5)
    env = signer.sign(
        task_id="t1",
        policy_hash=spec.policy_hash(),
        allowed_capabilities=("search",),
        capability="search",
    )
    # Wrong key -> verify fails; shadow continues with envelope permissions.
    with safely(envelope=env, envelope_keys={"default": b"wrong-key-not-the-real-secret!!"}, allow="search"):
        assert search("shadow") == "results: shadow"
    captured = capsys.readouterr()
    assert "shadow" in captured.out.lower() or "envelope verify failed" in captured.out


def test_canary_requires_for_bucketed_task(monkeypatch):
    monkeypatch.setenv("AGENT_SAFETY_DISTRIBUTED", "canary")
    monkeypatch.setenv("AGENT_SAFETY_CANARY_PERCENT", "100")
    with pytest.raises(PermissionDenied, match="envelope required"):
        with safely(allow="search", run=RunContext.new(task_id="any")):
            pass


def test_load_signing_keys_from_env_json(monkeypatch, tmp_path):
    from agent_safety.distributed import load_signing_keys

    secret = base64.b64encode(b"file-secret-32-bytes-long!!!!!!").decode()
    path = tmp_path / "keys.json"
    path.write_text(json.dumps({"default": secret}))
    monkeypatch.setenv("AGENT_SAFETY_SIGNING_KEYS", str(path))
    keys = load_signing_keys()
    assert keys["default"] == b"file-secret-32-bytes-long!!!!!!"


def test_safely_loads_keys_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_SAFETY_DISTRIBUTED", "enforce")
    secret = b"env-keys-signing-secret-32bytes!"
    path = tmp_path / "keys.json"
    path.write_text(json.dumps({"default": base64.b64encode(secret).decode()}))
    monkeypatch.setenv("AGENT_SAFETY_SIGNING_KEYS", str(path))
    signer = EnvelopeSigner(secret)
    spec = PolicySpec(allow=("search",), calls=5)
    env = signer.sign(
        task_id="t",
        policy_hash=spec.policy_hash(),
        allowed_capabilities=("search",),
        capability="search",
    )
    with safely(envelope=env, run=RunContext.new()):
        assert search("env") == "results: env"
