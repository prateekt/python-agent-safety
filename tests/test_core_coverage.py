"""Air-tight coverage for core local + distributed features.

Targets previously under-covered paths: tracing, validation edge cases,
BackendQuota tokens, envelope edge cases, distributed env helpers, gateway
client, Redis error mapping, quotas/costs remaining, runtime thread timeout,
circuit-breaker recovery, and audit sinks.
"""

from __future__ import annotations

import asyncio
import io
import json
import socket
import time
from unittest.mock import MagicMock, patch

import pytest

from agent_safety import safely, tool
from agent_safety.core.audit import AuditEvent, HashChainSink, JsonlSink, ListSink
from agent_safety.core.exceptions import (
    CostBudgetExceeded,
    GuardViolation,
    LoopDetected,
    PermissionDenied,
    QuotaExceeded,
    RateLimitExceeded,
    RiskBudgetExceeded,
    TimeoutExceeded,
)
from agent_safety.core.observability import CircuitBreaker, PrometheusMetrics
from agent_safety.core.quota import CostBudget, Quota, RiskBudget
from agent_safety.core.runtime import _with_thread, call_with_timeout
from agent_safety.core.tracing import current_span, trace_span, traceparent_header
from agent_safety.core.validation import validate_args
from agent_safety.distributed.backends import (
    BackendQuota,
    BudgetCharge,
    BudgetLimits,
    MemoryBackend,
)
from agent_safety.distributed.backends.redis_backend import RedisBudgetBackend, redis_backend
from agent_safety.distributed.config import (
    canary_percent,
    gateway_url,
    load_signing_keys,
    org_id_from_env,
    should_require_envelope,
)
from agent_safety.distributed.envelope import EnvelopeSigner, EnvelopeVerifier
from agent_safety.distributed.gateway.client import GatewayClient
from agent_safety.distributed.gateway.server import GatewayConfig, PolicyGateway, serve_gateway
from agent_safety.distributed.policy_spec import PolicySpec, safely_from_spec
from agent_safety.distributed.run import RunContext


@tool
def ping(msg: str = "hi") -> str:
    return msg


# ---------------------------------------------------------------------------
# Tracing
# ---------------------------------------------------------------------------

def test_traceparent_outside_span_is_none():
    assert current_span() is None
    assert traceparent_header() is None


def test_traceparent_inside_nested_spans():
    with trace_span("plan") as path:
        assert path == "plan"
        assert current_span() == "plan"
        header = traceparent_header()
        assert header is not None
        assert header.startswith("00-")
        parts = header.split("-")
        assert len(parts) == 4
        with trace_span("search"):
            assert current_span() == "plan.search"
            assert traceparent_header() is not None


# ---------------------------------------------------------------------------
# Validation edge cases
# ---------------------------------------------------------------------------

def test_validate_boolean_null_number_anyof_additional():
    validate_args({"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}, {"ok": True})
    with pytest.raises(GuardViolation):
        validate_args({"type": "object", "properties": {"ok": {"type": "boolean"}}}, {"ok": 1})

    validate_args({"type": "object", "properties": {"x": {"type": "null"}}}, {"x": None})
    with pytest.raises(GuardViolation):
        validate_args({"type": "object", "properties": {"x": {"type": "null"}}}, {"x": "no"})

    validate_args({"type": "object", "properties": {"n": {"type": "number", "minimum": 0}}}, {"n": 1.5})
    with pytest.raises(GuardViolation):
        validate_args({"type": "object", "properties": {"n": {"type": "number", "minimum": 10}}}, {"n": 1})

    schema = {
        "type": "object",
        "properties": {
            "v": {"anyOf": [{"type": "string"}, {"type": "integer"}]},
        },
    }
    validate_args(schema, {"v": "a"})
    validate_args(schema, {"v": 3})
    with pytest.raises(GuardViolation):
        validate_args(schema, {"v": True})

    # unknown type keyword is permissive
    validate_args({"type": "object", "properties": {"z": {"type": "custom"}}}, {"z": object()})

    # additionalProperties as a schema
    schema2 = {
        "type": "object",
        "properties": {},
        "additionalProperties": {"type": "integer"},
    }
    validate_args(schema2, {"extra": 7})
    with pytest.raises(GuardViolation):
        validate_args(schema2, {"extra": "no"})


# ---------------------------------------------------------------------------
# Quota / risk / cost remaining + str
# ---------------------------------------------------------------------------

def test_quota_risk_cost_remaining_and_str():
    q = Quota(max_calls=5, max_tokens=100)
    q.charge_call(2)
    q.charge_tokens(10)
    assert q.remaining_calls() == 3
    assert q.remaining_tokens() == 90
    assert "calls=" in str(q)

    with pytest.raises(ValueError):
        q.charge_tokens(-1)

    r = RiskBudget(10)
    r.charge(0)  # no-op
    r.charge(3)
    assert r.remaining() == 7
    assert "RiskBudget" in str(r)
    with pytest.raises(ValueError):
        r.charge(-1)
    with pytest.raises(RiskBudgetExceeded):
        r.charge(100)

    c = CostBudget(1.0)
    c.charge(0)
    c.charge(0.25)
    assert c.remaining() == pytest.approx(0.75)
    assert "CostBudget" in str(c)
    with pytest.raises(ValueError):
        c.charge(-0.1)
    with pytest.raises(CostBudgetExceeded):
        c.charge(5.0)


# ---------------------------------------------------------------------------
# BackendQuota tokens + remaining
# ---------------------------------------------------------------------------

def test_backend_quota_tokens_and_remaining():
    backend = MemoryBackend()
    bq = BackendQuota(
        backend,
        task_id="t",
        request_id="r",
        limits=BudgetLimits(max_calls=5, max_tokens=50),
    )
    assert bq.remaining_calls() == 5
    assert bq.remaining_tokens() == 50
    bq.charge_call(1)
    bq.charge_tokens(0)  # no-op
    bq.charge_tokens(20)
    assert bq.tokens_used == 20
    assert bq.remaining_tokens() == 30
    with pytest.raises(ValueError):
        bq.charge_tokens(-1)
    with pytest.raises(QuotaExceeded):
        bq.charge_tokens(40)

    # charge_calls=False skips call metering
    bq2 = BackendQuota(
        backend,
        task_id="t2",
        request_id="r2",
        limits=BudgetLimits(max_calls=1),
        charge_calls=False,
    )
    bq2.charge_call(1)
    bq2.charge_call(1)  # still no-op
    assert bq2.calls_used == 0

    unlimited = BackendQuota(backend, task_id="t3", request_id="r3")
    assert unlimited.remaining_calls() is None
    assert unlimited.remaining_tokens() is None


def test_memory_backend_risk_and_token_rollback_on_failure():
    backend = MemoryBackend()
    limits = BudgetLimits(max_concurrent=1, max_risk=5, max_tokens=10, lease_ttl=60.0)
    # Acquire concurrency lease then fail risk — lease should release
    with pytest.raises(RiskBudgetExceeded):
        backend.charge(
            BudgetCharge(task_id="t", request_id="r1", signature="s", risk=9),
            limits,
        )
    # Concurrency slot free again
    backend.charge(
        BudgetCharge(task_id="t", request_id="r2", signature="s2", risk=1),
        limits,
    )
    # Token overflow after lease
    with pytest.raises(QuotaExceeded):
        backend.charge(
            BudgetCharge(task_id="t", request_id="r3", signature="s3", tokens=100, call_n=0),
            BudgetLimits(max_concurrent=2, max_tokens=10, lease_ttl=60.0),
        )


def test_default_memory_backend_singleton():
    from agent_safety.distributed.backends import default_memory_backend

    a = default_memory_backend()
    b = default_memory_backend()
    assert a is b


# ---------------------------------------------------------------------------
# Envelope edge cases
# ---------------------------------------------------------------------------

def test_envelope_unknown_kid_not_yet_valid_glob_and_nonce():
    secret = b"core-envelope-signing-secret-32b!"
    signer = EnvelopeSigner(secret, kid="k1")
    env = signer.sign(
        task_id="t",
        policy_hash="h",
        allowed_capabilities=("fs.*",),
        capability="fs.read",
    )
    # Glob allow-list match
    EnvelopeVerifier({"k1": secret}).verify(env, capability="fs.read")

    with pytest.raises(PermissionDenied, match="unknown signing key"):
        EnvelopeVerifier({"other": secret}).verify(env)

    with pytest.raises(PermissionDenied, match="not yet valid"):
        EnvelopeVerifier({"k1": secret}).verify(env, now=env.issued_at - 120)

    with pytest.raises(PermissionDenied, match="capability mismatch"):
        EnvelopeVerifier({"k1": secret}).verify(env, capability="other.cap")

    # Nonce replay
    env2 = signer.sign(
        task_id="t2",
        policy_hash="h",
        allowed_capabilities=("x",),
        capability="x",
    )
    v = EnvelopeVerifier({"k1": secret})
    v.verify(env2)
    with pytest.raises(PermissionDenied, match="nonce"):
        v.verify(env2)

    v.mark_spent("manual-nonce", time.time() + 60)
    # mark_spent goes through the nonce store
    from agent_safety.distributed.nonces import MemoryNonceStore
    assert isinstance(v._nonce_store, MemoryNonceStore)

    # from_dict with bytes signature
    raw = env.to_dict()
    raw["signature"] = env.signature
    from agent_safety.distributed.envelope import CapabilityEnvelope
    restored = CapabilityEnvelope.from_dict(raw)
    assert restored.capability == env.capability


def test_envelope_allow_list_reject_without_glob():
    secret = b"core-envelope-signing-secret-32b!"
    import hashlib
    import hmac

    from agent_safety.distributed.envelope import CapabilityEnvelope

    bad = CapabilityEnvelope(
        task_id="t",
        policy_hash="h",
        allowed_capabilities=("other",),
        capability="search",
        nonce="abc",
        issued_at=time.time(),
        expires_at=time.time() + 60,
        signature=b"",
    )
    sig = hmac.new(secret, bad.payload_bytes(), hashlib.sha256).digest()
    bad = CapabilityEnvelope(
        task_id=bad.task_id,
        policy_hash=bad.policy_hash,
        allowed_capabilities=bad.allowed_capabilities,
        capability=bad.capability,
        nonce=bad.nonce,
        issued_at=bad.issued_at,
        expires_at=bad.expires_at,
        signature=sig,
    )
    with pytest.raises(PermissionDenied, match="allow-list"):
        EnvelopeVerifier({"default": secret}).verify(bad)


# ---------------------------------------------------------------------------
# Distributed env helpers
# ---------------------------------------------------------------------------

def test_distributed_env_helpers(monkeypatch, tmp_path):
    monkeypatch.delenv("AGENT_SAFETY_GATEWAY_URL", raising=False)
    monkeypatch.delenv("AGENT_SAFETY_ORG_ID", raising=False)
    monkeypatch.delenv("AGENT_SAFETY_CANARY_PERCENT", raising=False)
    monkeypatch.delenv("AGENT_SAFETY_SIGNING_KEYS", raising=False)

    assert gateway_url() is None
    assert org_id_from_env() == ""
    assert canary_percent() == 10
    assert load_signing_keys() == {}

    monkeypatch.setenv("AGENT_SAFETY_GATEWAY_URL", "http://gw:8765/")
    assert gateway_url() == "http://gw:8765/"

    monkeypatch.setenv("AGENT_SAFETY_ORG_ID", "acme")
    assert org_id_from_env() == "acme"

    monkeypatch.setenv("AGENT_SAFETY_CANARY_PERCENT", "not-int")
    assert canary_percent() == 10
    monkeypatch.setenv("AGENT_SAFETY_CANARY_PERCENT", "150")
    assert canary_percent() == 100
    monkeypatch.setenv("AGENT_SAFETY_CANARY_PERCENT", "-5")
    assert canary_percent() == 0

    # Inline JSON keys
    import base64
    secret = base64.b64encode(b"inline-secret-32-bytes-long!!!!").decode()
    monkeypatch.setenv("AGENT_SAFETY_SIGNING_KEYS", json.dumps({"k": secret}))
    assert load_signing_keys()["k"] == b"inline-secret-32-bytes-long!!!!"

    # Invalid JSON
    monkeypatch.setenv("AGENT_SAFETY_SIGNING_KEYS", "{not-json")
    assert load_signing_keys() == {}

    # Non-dict JSON
    monkeypatch.setenv("AGENT_SAFETY_SIGNING_KEYS", '["x"]')
    assert load_signing_keys() == {}

    # Raw string secret (not base64) falls back to encode
    monkeypatch.setenv("AGENT_SAFETY_SIGNING_KEYS", json.dumps({"k": "plain-text-secret"}))
    assert load_signing_keys()["k"] == b"plain-text-secret"

    monkeypatch.setenv("AGENT_SAFETY_DISTRIBUTED", "canary")
    monkeypatch.setenv("AGENT_SAFETY_CANARY_PERCENT", "0")
    assert not should_require_envelope("task")
    assert not should_require_envelope(None)


# ---------------------------------------------------------------------------
# Gateway client: env URL, auth, audit, HTTP error body
# ---------------------------------------------------------------------------

def test_gateway_client_requires_url(monkeypatch):
    monkeypatch.delenv("AGENT_SAFETY_GATEWAY_URL", raising=False)
    with pytest.raises(ValueError, match="base_url"):
        GatewayClient()


def test_gateway_client_uses_env_url_and_audit(monkeypatch):
    secret = b"client-core-signing-secret-32b!!"
    backend = MemoryBackend()
    gw = PolicyGateway(GatewayConfig(require_auth=False, signing_secret=secret, backend=backend))
    spec = PolicySpec(allow=("ping",), calls=5)
    gw.config.registry.publish(spec)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    server = serve_gateway(gw, host="127.0.0.1", port=port)
    try:
        monkeypatch.setenv("AGENT_SAFETY_GATEWAY_URL", f"http://127.0.0.1:{port}")
        client = GatewayClient()
        assert client.base_url.endswith(str(port))
        env = client.fetch_envelope({
            "task_id": "t",
            "request_id": "r",
            "capability": "ping",
            "policy_spec": spec.to_dict(),
        })
        assert env is not None
        # Auth header path: token is sent; gateway ignores invalid when optional —
        # use a client without auth for audit.
        audit = client.audit([{"action": "tool_call", "decision": "ok"}])
        assert audit.get("count") == 1 or audit.get("ok") is True
        authed = GatewayClient(f"http://127.0.0.1:{port}", auth_token="Bearer-ish")
        # Still reaches server; mint may fail JWT verify but _post returns body
        payload = authed._post("/v1/audit", {"events": []})
        assert "ok" in payload or "error" in payload or "count" in payload
    finally:
        server.shutdown()


def test_gateway_client_http_error_non_json(monkeypatch):
    import urllib.error

    client = GatewayClient("http://127.0.0.1:1")
    err = urllib.error.HTTPError(
        "http://x", 500, "err", hdrs=None, fp=io.BytesIO(b"not-json")
    )
    with patch("urllib.request.urlopen", side_effect=err):
        payload = client._post("/v1/mint", {})
        assert payload["ok"] is False
        assert "not-json" in payload["error"]


# ---------------------------------------------------------------------------
# Redis error mapping + bytes response
# ---------------------------------------------------------------------------

def test_redis_backend_error_mapping_and_bytes():
    client = MagicMock()
    # RATE
    script = MagicMock(side_effect=Exception("RATE_EXCEEDED"))
    client.register_script.return_value = script
    backend = RedisBudgetBackend(client)
    with pytest.raises(RateLimitExceeded):
        backend.charge(BudgetCharge("t", "r", "s"), BudgetLimits(rate_per_second=1))

    script.side_effect = Exception("LOOP_DETECTED")
    with pytest.raises(LoopDetected):
        backend.charge(BudgetCharge("t", "r2", "s"), BudgetLimits(max_identical=1))

    script.side_effect = Exception("RISK_EXCEEDED")
    with pytest.raises(RiskBudgetExceeded):
        backend.charge(BudgetCharge("t", "r3", "s", risk=1), BudgetLimits(max_risk=1))

    script.side_effect = Exception("CONCURRENCY_EXCEEDED")
    with pytest.raises(QuotaExceeded):
        backend.charge(BudgetCharge("t", "r4", "s"), BudgetLimits(max_concurrent=1))

    script.side_effect = Exception("weird failure")
    with pytest.raises(Exception, match="weird"):
        backend.charge(BudgetCharge("t", "r5", "s"), BudgetLimits())

    # bytes payload
    script.side_effect = None
    script.return_value = json.dumps({"lease_id": "", "calls_used": 2, "cached": True}).encode()
    result = backend.charge(BudgetCharge("t", "r6", "s"), BudgetLimits(max_calls=10))
    assert result.calls_used == 2
    assert result.cached is True
    assert result.lease_id is None

    assert isinstance(redis_backend(MagicMock()), RedisBudgetBackend)


# ---------------------------------------------------------------------------
# Runtime thread timeout path
# ---------------------------------------------------------------------------

def test_runtime_thread_timeout_path():
    def slow() -> str:
        time.sleep(0.5)
        return "done"

    with pytest.raises(TimeoutExceeded):
        _with_thread(slow, (), {}, 0.05)

    assert _with_thread(lambda: "ok", (), {}, 1.0) == "ok"

    with patch("agent_safety.core.runtime._can_use_signal", return_value=False):
        with pytest.raises(TimeoutExceeded):
            call_with_timeout(slow, (), {}, 0.05)


# ---------------------------------------------------------------------------
# Circuit breaker recovery + metrics exposition
# ---------------------------------------------------------------------------

def test_circuit_breaker_recovers_after_open_seconds():
    cb = CircuitBreaker(failure_threshold=1, open_seconds=0.05)
    cb.record_failure()
    assert cb.is_open()
    time.sleep(0.08)
    assert not cb.is_open()


def test_prometheus_metrics_exposition():
    m = PrometheusMetrics()
    m.inc("agent_safety_test_total", {"decision": "ok"})
    m.observe("agent_safety_test_latency_ms", 1.5)
    text = m.exposition()
    assert "agent_safety_test_total" in text


# ---------------------------------------------------------------------------
# Audit sinks
# ---------------------------------------------------------------------------

def test_audit_jsonl_and_hash_chain_fields():
    buf = io.StringIO()
    sink = JsonlSink(buf)
    ev = AuditEvent(
        "tool_call",
        "ok",
        capability="search",
        stage="input",
        span="plan.search",
        task_id="t",
        agent_id="a",
        request_id="r",
        org_id="o",
    )
    sink(ev)
    line = json.loads(buf.getvalue().strip())
    assert line["capability"] == "search"
    assert line["span"] == "plan.search"
    assert line["org_id"] == "o"

    chain = HashChainSink(ListSink())
    chain(AuditEvent("a", "ok"))
    chain(AuditEvent("b", "ok"))
    assert len(chain.events) == 2
    # HashChainSink wraps a sink — inspect via calling ListSink directly
    inner = ListSink()
    chained = HashChainSink(inner)
    chained(AuditEvent("x", "ok", detail="1"))
    chained(AuditEvent("y", "ok", detail="2"))
    assert len(inner.events) == 2
    assert inner.events[0].event_hash
    assert inner.events[1].prev_hash == inner.events[0].event_hash
    d = inner.events[1].to_dict()
    assert "prev_hash" in d and "event_hash" in d


# ---------------------------------------------------------------------------
# PolicySpec / safely_from_spec with backend + deny list forms
# ---------------------------------------------------------------------------

def test_policy_spec_per_minute_and_safely_from_spec_backend():
    spec = PolicySpec(allow=("ping",), deny=("nope",), per_minute=30, calls=5, tokens=100)
    limits = spec.budget_limits()
    assert limits.rate_per_second == 30
    assert limits.rate_window == 60.0

    perms = spec.permissions()
    assert perms.allows("ping")
    assert not perms.allows("nope")

    backend = MemoryBackend()
    with safely_from_spec(
        PolicySpec(allow=("ping",), calls=2),
        backend=backend,
        run=RunContext.new(),
    ):
        assert ping("a") == "a"

    # from_safely_kwargs deny as list / allow as list
    s2 = PolicySpec.from_safely_kwargs(allow=["ping", "pong"], deny=["nope"], calls=1)
    assert "ping" in s2.allow
    assert "nope" in s2.deny


# ---------------------------------------------------------------------------
# Local safely edge helpers
# ---------------------------------------------------------------------------

def test_safely_budget_and_memory_parsing_edges():
    with safely(allow="ping", budget=10.0, memory=1024):
        assert ping() == "hi"
    with pytest.raises(TypeError):
        with safely(allow="ping", budget=True):  # type: ignore[arg-type]
            pass
    with pytest.raises(ValueError):
        with safely(allow="ping", budget="$not-a-number"):
            pass
    with pytest.raises(TypeError):
        with safely(allow="ping", memory=True):  # type: ignore[arg-type]
            pass
    with pytest.raises(ValueError):
        with safely(allow="ping", memory="lots"):
            pass


def test_enforce_requires_keys_when_envelope_present(monkeypatch):
    monkeypatch.setenv("AGENT_SAFETY_DISTRIBUTED", "enforce")
    monkeypatch.delenv("AGENT_SAFETY_SIGNING_KEYS", raising=False)
    secret = b"need-keys-signing-secret-32bytes!!"
    env = EnvelopeSigner(secret).sign(
        task_id="t",
        policy_hash="h",
        allowed_capabilities=("ping",),
        capability="ping",
    )
    with pytest.raises(PermissionDenied, match="envelope_keys"):
        with safely(envelope=env):
            pass


# ---------------------------------------------------------------------------
# Permissions / policy / constitution / gateway JWT / client errors
# ---------------------------------------------------------------------------

def test_permission_set_empty_cap_serialize_and_str():
    from agent_safety.core.permissions import PermissionSet

    ps = PermissionSet.of("fs.*", deny=["fs.delete"])
    assert not ps.allows("")
    assert not ps.allows("fs.delete")
    assert ps.allows("fs.read")
    d = ps.to_dict()
    assert PermissionSet.from_dict(d).allows("fs.read")
    assert "allow=" in str(ps)
    assert "deny=" in str(PermissionSet.deny_all())


def test_policy_explain_and_deadline_rate_audit():
    from agent_safety.core.exceptions import DeadlineExceeded, RateLimitExceeded
    from agent_safety.core.limits import Deadline, RateLimit
    from agent_safety.core.permissions import PermissionSet
    from agent_safety.core.policy import Policy
    from agent_safety.core.quota import Quota

    policy = Policy(
        permissions=PermissionSet.of("ping"),
        quotas=(Quota(max_calls=10),),
        rate_limits=(RateLimit(per_second=1),),
    )
    exp = policy.explain("ping")
    assert exp.allowed
    assert "allowed" in str(exp)
    denied = policy.explain("nope")
    assert not denied.allowed

    policy.charge_call(1)
    with pytest.raises(RateLimitExceeded):
        policy.charge_call(1)

    timed = Policy(
        permissions=PermissionSet.of("ping"),
        deadlines=(Deadline(seconds=0.01),),
    )
    timed.charge_call(1)
    time.sleep(0.02)
    with pytest.raises(DeadlineExceeded):
        timed.charge_call(1)


def test_constitution_gate_rejects_empty_rules():
    from agent_safety.core.gates import ConstitutionGate

    with pytest.raises(ValueError, match="at least one rule"):
        ConstitutionGate(rules=["", "  "], judge=lambda *_: True)


def test_gateway_jwt_validation_paths():
    from agent_safety.distributed.gateway.server import make_service_jwt

    secret = b"jwt-core-secret-32-bytes-long!!!"
    gw = PolicyGateway(GatewayConfig(require_auth=False, signing_secret=secret, jwt_secret=secret))
    token = make_service_jwt(secret, org_id="acme")
    claims = gw.verify_jwt(token)
    assert claims["sub"] == "planner"

    with pytest.raises(ValueError, match="invalid jwt"):
        gw.verify_jwt("not.a.jwt")
    with pytest.raises(ValueError, match="signature"):
        parts = token.split(".")
        gw.verify_jwt(f"{parts[0]}.{parts[1]}.AAAA")
    bad_sub = make_service_jwt(secret, sub="attacker")
    with pytest.raises(ValueError, match="subject"):
        gw.verify_jwt(bad_sub)
    expired = make_service_jwt(secret, ttl=-10)
    with pytest.raises(ValueError, match="expired"):
        gw.verify_jwt(expired)

    # mint with policy_hash lookup
    spec = PolicySpec(allow=("ping",), calls=3)
    ph = gw.config.registry.publish(spec)
    resp = gw.mint({
        "task_id": "t",
        "request_id": "r",
        "capability": "ping",
        "policy_hash": ph,
    })
    assert resp.ok
    missing = gw.mint({
        "task_id": "t2",
        "capability": "ping",
        "policy_hash": "deadbeef",
    })
    assert not missing.ok


def test_gateway_client_connection_failure_records_circuit():
    import urllib.error

    client = GatewayClient("http://127.0.0.1:1")
    with pytest.raises((urllib.error.URLError, OSError, ConnectionError, TimeoutError)):
        client.mint({"task_id": "t", "capability": "x"})
    # failures recorded
    assert client.circuit_breaker._failures >= 1


def test_gateway_charge_tokens_error_path():
    gw = PolicyGateway(GatewayConfig(require_auth=False,
        signing_secret=b"charge-err-signing-secret-32b!!",
        backend=MemoryBackend(),
    ))
    result = gw.charge_tokens({
        "task_id": "t",
        "tokens": 1000,
        "policy_spec": PolicySpec(tokens=1).to_dict(),
    })
    assert result.get("ok") is False


def test_transaction_commit_and_compensation_error():
    from agent_safety.core.transaction import rollback

    steps: list[str] = []
    with rollback() as tx:
        steps.append("a")
        tx.on_undo(lambda: steps.append("undo-a"))
        tx.commit()
        steps.append("b")
    assert "undo-a" not in steps

    with pytest.raises(RuntimeError, match="boom"):
        with rollback() as tx:
            tx.on_undo(lambda: (_ for _ in ()).throw(RuntimeError("comp-fail")))
            raise RuntimeError("boom")
    assert tx.compensation_errors


def test_mcp_capability_remap_and_deny():
    from agent_safety import guard_mcp

    class FakeSession:
        async def call_tool(self, name, arguments):
            return {"ok": True, "name": name}

    safe = guard_mcp(FakeSession(), capability=lambda name: f"mcp.{name}")

    async def _run():
        with safely(allow="mcp.search"):
            return await safe.call_tool("search", {"q": "x"})

    assert asyncio.run(_run())["ok"] is True

    async def _deny():
        with safely(allow="other"):
            await safe.call_tool("search", {})

    with pytest.raises(PermissionDenied):
        asyncio.run(_deny())
