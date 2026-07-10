"""HTTP integration tests for the policy gateway."""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
import uuid

import pytest

from agent_safety.gateway.server import (
    GatewayConfig,
    PolicyGateway,
    make_service_jwt,
    serve_gateway,
)
from agent_safety.policy_spec import PolicySpec


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _get(url: str) -> tuple[int, bytes]:
    with urllib.request.urlopen(url, timeout=5) as resp:
        return resp.status, resp.read()


def _post(url: str, body: dict, *, token: str | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()
        try:
            return exc.code, json.loads(detail)
        except json.JSONDecodeError:
            return exc.code, {"ok": False, "error": detail}


@pytest.fixture
def gateway_server():
    secret = b"http-test-signing-secret-32bytes!"
    jwt_secret = b"jwt-secret-for-gateway-tests!!"
    spec = PolicySpec(allow=("tool.read",), calls=10)
    gw = PolicyGateway(GatewayConfig(
        signing_secret=secret,
        jwt_secret=jwt_secret,
        backend=__import__("agent_safety.backends", fromlist=["MemoryBackend"]).MemoryBackend(),
    ))
    gw.config.registry.publish(spec)
    port = _free_port()
    server = serve_gateway(gw, host="127.0.0.1", port=port)
    base = f"http://127.0.0.1:{port}"
    token = make_service_jwt(jwt_secret, org_id="acme")
    yield base, secret, token, spec
    server.shutdown()


def test_healthz_and_readyz(gateway_server):
    base, _, _, _ = gateway_server
    status, body = _get(f"{base}/healthz")
    assert status == 200
    assert json.loads(body)["status"] == "ok"
    status, body = _get(f"{base}/readyz")
    assert status == 200
    assert json.loads(body)["ready"] is True


def test_metrics_and_keys(gateway_server):
    base, secret, _, _ = gateway_server
    status, body = _get(f"{base}/metrics")
    assert status == 200
    assert b"agent_safety" in body or body == b""
    status, keys = _get(f"{base}/v1/keys")
    assert status == 200
    data = json.loads(keys)
    assert "default" in data


def test_mint_via_http(gateway_server):
    base, _, token, spec = gateway_server
    status, payload = _post(f"{base}/v1/mint", {
        "task_id": uuid.uuid4().hex,
        "request_id": uuid.uuid4().hex,
        "capability": "tool.read",
        "policy_spec": spec.to_dict(),
    }, token=token)
    assert status == 200
    assert payload["ok"] is True
    assert payload["envelope"]["capability"] == "tool.read"


def test_mint_denied_capability(gateway_server):
    base, _, token, spec = gateway_server
    status, payload = _post(f"{base}/v1/mint", {
        "task_id": "t1",
        "request_id": "r1",
        "capability": "shell.exec",
        "policy_spec": spec.to_dict(),
    }, token=token)
    assert status == 403
    assert payload["ok"] is False


def test_charge_and_audit_endpoints(gateway_server):
    base, _, token, spec = gateway_server
    task_id = uuid.uuid4().hex
    _, charge = _post(f"{base}/v1/charge", {
        "task_id": task_id,
        "request_id": "charge-1",
        "tokens": 100,
        "policy_spec": {**spec.to_dict(), "tokens": 1000},
    }, token=token)
    assert charge.get("ok") is True
    status, audit = _post(f"{base}/v1/audit", {
        "events": [{"action": "tool_call", "decision": "ok", "task_id": task_id}],
    }, token=token)
    assert status == 202
    assert audit["count"] == 1


def test_approval_required(gateway_server):
    base, _, token, _ = gateway_server
    spec = PolicySpec(allow=("tool.read",), calls=5, ask=True)
    status, payload = _post(f"{base}/v1/mint", {
        "task_id": "t-approval",
        "request_id": "r-approval",
        "capability": "tool.read",
        "policy_spec": spec.to_dict(),
    }, token=token)
    assert status == 200
    assert payload["approval_required"] is True
    assert payload["envelope"] is None
