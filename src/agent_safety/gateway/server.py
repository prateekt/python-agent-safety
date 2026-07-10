"""Policy Decision Point gateway (stdlib HTTP server)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Type, cast

from ..audit import AuditEvent, HashChainSink, ListSink
from ..backends import BudgetCharge, MemoryBackend
from ..envelope import EnvelopeSigner
from ..events import MintResponse
from ..observability import CircuitBreaker, StructuredLog, gateway_metrics
from ..policy_spec import PolicyRegistry, PolicySpec


def _b64url_decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


@dataclass
class GatewayConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    signing_secret: bytes = field(default_factory=lambda: secrets.token_bytes(32))
    jwt_secret: bytes = field(default_factory=lambda: secrets.token_bytes(32))
    kid: str = "default"
    org_id: str = ""
    rate_limit_per_minute: int = 1000
    backend: Any = field(default_factory=MemoryBackend)
    registry: PolicyRegistry = field(default_factory=PolicyRegistry)
    circuit_breaker: CircuitBreaker = field(default_factory=CircuitBreaker)


class PolicyGateway:
    """Stateless policy gateway coordinating mint/charge/audit."""

    def __init__(self, config: Optional[GatewayConfig] = None) -> None:
        self.config = config or GatewayConfig()
        self._signer = EnvelopeSigner(self.config.signing_secret, kid=self.config.kid)
        self._audit = HashChainSink(ListSink())
        self._spent_nonces: Dict[str, float] = {}
        self._rate_counts: Dict[str, List[float]] = {}
        self._lock = threading.Lock()

    def verify_jwt(self, token: str, *, required_sub: str = "planner") -> Dict[str, Any]:
        """Minimal HS256 JWT verification (stdlib only)."""
        parts = token.split(".")
        if len(parts) != 3:
            raise ValueError("invalid jwt")
        header_b, payload_b, sig_b = parts
        signing_input = f"{header_b}.{payload_b}".encode()
        expected = hmac.new(
            self.config.jwt_secret,
            signing_input,
            hashlib.sha256,
        ).digest()
        sig = _b64url_decode(sig_b)
        if not hmac.compare_digest(expected, sig):
            raise ValueError("invalid jwt signature")
        payload = json.loads(_b64url_decode(payload_b))
        if payload.get("sub") != required_sub:
            raise ValueError("invalid jwt subject")
        if payload.get("aud") != "gateway":
            raise ValueError("invalid jwt audience")
        exp = payload.get("exp")
        if exp is not None and time.time() > float(exp):
            raise ValueError("jwt expired")
        return cast(Dict[str, Any], payload)

    def _check_service_rate(self, service_id: str) -> None:
        now = time.time()
        with self._lock:
            times = self._rate_counts.setdefault(service_id, [])
            times[:] = [t for t in times if now - t < 60.0]
            if len(times) >= self.config.rate_limit_per_minute:
                raise ValueError("gateway rate limit exceeded")
            times.append(now)

    def mint(
        self,
        body: Dict[str, Any],
        *,
        auth_token: Optional[str] = None,
    ) -> MintResponse:
        if self.config.circuit_breaker.is_open():
            return MintResponse(ok=False, error="gateway circuit breaker open")

        try:
            if auth_token:
                claims = self.verify_jwt(auth_token)
                self._check_service_rate(str(claims.get("sub", "unknown")))

            spec_data = body.get("policy_spec")
            if spec_data is None and body.get("policy_hash"):
                spec = self.config.registry.resolve(str(body["policy_hash"]))
                if spec is None:
                    return MintResponse(ok=False, error="unknown policy_hash")
            else:
                spec = PolicySpec.from_dict(spec_data or {})
                self.config.registry.publish(spec)

            if spec.ask and not body.get("approval_id"):
                audit_id = uuid.uuid4().hex
                self._audit(AuditEvent("approval", "required", capability=body.get("capability", "")))
                gateway_metrics.inc("agent_safety_mint_total", {"decision": "approval_required"})
                return MintResponse(ok=True, approval_required=True, audit_id=audit_id)

            task_id = str(body["task_id"])
            request_id = str(body.get("request_id", uuid.uuid4().hex))
            capability = str(body["capability"])
            org_id = str(body.get("org_id", self.config.org_id))
            signature = str(body.get("signature", f"{body.get('tool', capability)}|{capability}|()|()"))

            if not spec.permissions().allows(capability):
                gateway_metrics.inc("agent_safety_mint_total", {"decision": "deny"})
                return MintResponse(ok=False, error=f"capability {capability!r} not permitted")

            charge = BudgetCharge(
                task_id=task_id,
                request_id=request_id,
                signature=signature,
                org_id=org_id,
            )
            start = time.monotonic()
            self.config.backend.charge(charge, spec.budget_limits())
            latency = (time.monotonic() - start) * 1000
            gateway_metrics.observe("agent_safety_charge_latency_ms", latency)
            gateway_metrics.inc("agent_safety_mint_total", {"decision": "allow"})

            allowed = tuple(spec.allow) or (capability,)
            envelope = self._signer.sign(
                task_id=task_id,
                policy_hash=spec.policy_hash(),
                allowed_capabilities=allowed,
                capability=capability,
                org_id=org_id,
                approval_id=body.get("approval_id"),
            )
            with self._lock:
                self._spent_nonces[envelope.nonce] = envelope.expires_at

            audit_id = uuid.uuid4().hex
            self._audit(AuditEvent(
                "mint", "allow",
                capability=capability,
                detail=f"task={task_id}",
                task_id=task_id,
                request_id=request_id,
                org_id=org_id or None,
            ))
            StructuredLog(
                "info", "mint ok",
                task_id=task_id,
                request_id=request_id,
                org_id=org_id or None,
                capability=capability,
                decision="allow",
                latency_ms=latency,
            ).emit()
            self.config.circuit_breaker.record_success()
            return MintResponse(ok=True, envelope=envelope.to_dict(), audit_id=audit_id)
        except Exception as exc:
            self.config.circuit_breaker.record_failure()
            gateway_metrics.inc("agent_safety_mint_total", {"decision": "error"})
            StructuredLog("error", f"mint failed: {exc}").emit()
            return MintResponse(ok=False, error=str(exc))

    def charge_tokens(self, body: Dict[str, Any]) -> Dict[str, Any]:
        try:
            spec = PolicySpec.from_dict(body.get("policy_spec", {}))
            charge = BudgetCharge(
                task_id=str(body["task_id"]),
                request_id=str(body.get("request_id", uuid.uuid4().hex)),
                signature="tokens",
                org_id=str(body.get("org_id", self.config.org_id)),
                tokens=int(body.get("tokens", 0)),
                call_n=0,
            )
            limits = spec.budget_limits()
            result = self.config.backend.charge(charge, limits)
            return {"ok": True, "calls_used": result.calls_used}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def ingest_audit(self, body: Dict[str, Any]) -> Dict[str, Any]:
        events = body.get("events", [])
        for raw in events:
            self._audit(AuditEvent(
                str(raw.get("action", "audit")),
                str(raw.get("decision", "ok")),
                detail=str(raw.get("detail", "")),
                capability=raw.get("capability"),
                task_id=raw.get("task_id"),
                request_id=raw.get("request_id"),
                org_id=raw.get("org_id"),
            ))
        return {"ok": True, "count": len(events)}

    def healthz(self) -> Dict[str, str]:
        return {"status": "ok"}

    def readyz(self) -> Dict[str, Any]:
        ready = not self.config.circuit_breaker.is_open()
        return {"ready": ready, "circuit_open": self.config.circuit_breaker.is_open()}

    def metrics_text(self) -> str:
        return gateway_metrics.exposition()

    def public_key_material(self) -> Dict[str, str]:
        return {self.config.kid: base64.b64encode(self.config.signing_secret).decode()}


def create_handler(gw_instance: PolicyGateway) -> Type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        gateway = gw_instance

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _read_json(self) -> Dict[str, Any]:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            return cast(Dict[str, Any], json.loads(raw.decode() or "{}"))

        def _send_json(self, code: int, payload: Dict[str, Any]) -> None:
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _auth_token(self) -> Optional[str]:
            auth = self.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                return auth[7:]
            return None

        def do_GET(self) -> None:
            if self.path == "/healthz":
                self._send_json(200, self.gateway.healthz())
            elif self.path == "/readyz":
                payload = self.gateway.readyz()
                self._send_json(200 if payload["ready"] else 503, payload)
            elif self.path == "/metrics":
                body = self.gateway.metrics_text().encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/v1/keys":
                self._send_json(200, self.gateway.public_key_material())
            else:
                self._send_json(404, {"error": "not found"})

        def do_POST(self) -> None:
            body = self._read_json()
            token = self._auth_token()
            if self.path == "/v1/mint":
                resp = self.gateway.mint(body, auth_token=token)
                code = 200 if resp.ok or resp.approval_required else 403
                self._send_json(code, {
                    "ok": resp.ok,
                    "envelope": resp.envelope,
                    "approval_required": resp.approval_required,
                    "error": resp.error,
                    "audit_id": resp.audit_id,
                })
            elif self.path == "/v1/charge":
                self._send_json(200, self.gateway.charge_tokens(body))
            elif self.path == "/v1/audit":
                self._send_json(202, self.gateway.ingest_audit(body))
            else:
                self._send_json(404, {"error": "not found"})

    return Handler


def serve_gateway(
    gateway: Optional[PolicyGateway] = None,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> ThreadingHTTPServer:
    gw = gateway or PolicyGateway(GatewayConfig(host=host, port=port))
    handler = create_handler(gw)
    server = ThreadingHTTPServer((host, port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def make_service_jwt(
    secret: bytes,
    *,
    sub: str = "planner",
    org_id: str = "",
    ttl: float = 3600.0,
) -> str:
    """Build a minimal HS256 service JWT for gateway auth."""
    header = base64.urlsafe_b64encode(
        json.dumps({"alg": "HS256", "typ": "JWT"}).encode()
    ).decode().rstrip("=")
    payload_obj = {
        "sub": sub,
        "aud": "gateway",
        "org_id": org_id,
        "iat": int(time.time()),
        "exp": int(time.time() + ttl),
    }
    payload = base64.urlsafe_b64encode(
        json.dumps(payload_obj).encode()
    ).decode().rstrip("=")
    signing_input = f"{header}.{payload}".encode()
    sig = hmac.new(secret, signing_input, hashlib.sha256).digest()
    sig_b = base64.urlsafe_b64encode(sig).decode().rstrip("=")
    return f"{header}.{payload}.{sig_b}"
