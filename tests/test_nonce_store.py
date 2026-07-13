"""Tests for shared nonce spend stores (envelope replay protection)."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from agent_safety import safely, tool
from agent_safety.envelope import EnvelopeSigner, EnvelopeVerifier
from agent_safety.exceptions import PermissionDenied
from agent_safety.nonces import MemoryNonceStore, RedisNonceStore, default_memory_nonce_store
from agent_safety.run import RunContext


@tool
def search(q: str) -> str:
    return f"results: {q}"


def test_memory_nonce_store_rejects_replay():
    store = MemoryNonceStore()
    assert store.spend("n1", time.time() + 60) is True
    assert store.spend("n1", time.time() + 60) is False


def test_memory_nonce_store_purges_expired():
    store = MemoryNonceStore()
    assert store.spend("old", time.time() - 1) is True
    # expired entry is purged on next spend of any nonce
    assert store.spend("new", time.time() + 60) is True
    assert store.spend("old", time.time() + 60) is True  # can spend again after expiry


def test_shared_nonce_store_across_verifiers():
    secret = b"shared-nonce-signing-secret-32b!"
    store = MemoryNonceStore()
    signer = EnvelopeSigner(secret)
    env = signer.sign(
        task_id="t",
        policy_hash="h",
        allowed_capabilities=("search",),
        capability="search",
    )
    v1 = EnvelopeVerifier({"default": secret}, nonce_store=store)
    v2 = EnvelopeVerifier({"default": secret}, nonce_store=store)
    v1.verify(env)
    with pytest.raises(PermissionDenied, match="nonce"):
        v2.verify(env)


def test_safely_shared_nonce_store_blocks_replay():
    secret = b"safely-nonce-signing-secret-32b!"
    store = MemoryNonceStore()
    signer = EnvelopeSigner(secret)
    env = signer.sign(
        task_id="t",
        policy_hash="h",
        allowed_capabilities=("search",),
        capability="search",
    )
    with safely(
        envelope=env,
        envelope_keys={"default": secret},
        nonce_store=store,
        run=RunContext.new(),
    ):
        assert search("a") == "results: a"

    with pytest.raises(PermissionDenied, match="nonce"):
        with safely(
            envelope=env,
            envelope_keys={"default": secret},
            nonce_store=store,
            run=RunContext.new(),
        ):
            search("b")


def test_redis_nonce_store_set_nx():
    client = MagicMock()
    client.set.return_value = True
    store = RedisNonceStore(client)
    assert store.spend("n1", time.time() + 30) is True
    client.set.assert_called_once()
    kwargs = client.set.call_args.kwargs
    assert kwargs.get("nx") is True

    client.set.return_value = None
    assert store.spend("n1", time.time() + 30) is False


def test_default_memory_nonce_store_singleton():
    a = default_memory_nonce_store()
    b = default_memory_nonce_store()
    assert a is b
