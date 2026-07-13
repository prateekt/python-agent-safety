"""Tests for GatewayClient HTTP wrapper."""

from __future__ import annotations

import socket
import uuid

import pytest

from agent_safety.gateway.client import GatewayClient
from agent_safety.gateway.server import GatewayConfig, PolicyGateway, serve_gateway
from agent_safety.observability import CircuitBreaker
from agent_safety.policy_spec import PolicySpec


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def live_gateway():
    secret = b"client-test-signing-secret-32b!"
    backend = __import__("agent_safety.backends", fromlist=["MemoryBackend"]).MemoryBackend()
    gw = PolicyGateway(GatewayConfig(require_auth=False, signing_secret=secret, backend=backend))
    spec = PolicySpec(allow=("search",), calls=5)
    gw.config.registry.publish(spec)
    port = _free_port()
    server = serve_gateway(gw, host="127.0.0.1", port=port)
    base = f"http://127.0.0.1:{port}"
    yield base, secret, spec
    server.shutdown()


def test_client_fetch_envelope(live_gateway):
    base, _, spec = live_gateway
    client = GatewayClient(base)
    env = client.fetch_envelope({
        "task_id": uuid.uuid4().hex,
        "request_id": uuid.uuid4().hex,
        "capability": "search",
        "policy_spec": spec.to_dict(),
    })
    assert env is not None
    assert env.capability == "search"


def test_client_mint_response(live_gateway):
    base, _, spec = live_gateway
    client = GatewayClient(base)
    resp = client.mint({
        "task_id": "t1",
        "request_id": "r1",
        "capability": "search",
        "policy_spec": spec.to_dict(),
    })
    assert resp.ok
    assert resp.envelope is not None


def test_client_charge(live_gateway):
    base, _, spec = live_gateway
    client = GatewayClient(base)
    result = client.charge({
        "task_id": "t1",
        "request_id": "c1",
        "tokens": 50,
        "policy_spec": {**spec.to_dict(), "tokens": 500},
    })
    assert result.get("ok") is True


def test_client_circuit_breaker_blocks():
    cb = CircuitBreaker(failure_threshold=1, open_seconds=60.0)
    cb.record_failure()
    client = GatewayClient("http://127.0.0.1:1", circuit_breaker=cb)
    with pytest.raises(RuntimeError, match="circuit breaker"):
        client.mint({})
