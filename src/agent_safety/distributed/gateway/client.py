"""HTTP client for the policy gateway."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, cast

from ...core.observability import CircuitBreaker
from ..envelope import CapabilityEnvelope
from ..events import MintResponse


class GatewayClient:
    """Cold-path client: mint envelopes and charge tokens via gateway."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        *,
        auth_token: Optional[str] = None,
        circuit_breaker: Optional[CircuitBreaker] = None,
    ) -> None:
        from ..config import gateway_url

        resolved = (base_url or gateway_url() or "").rstrip("/")
        if not resolved:
            raise ValueError(
                "gateway base_url required (or set AGENT_SAFETY_GATEWAY_URL)"
            )
        self.base_url = resolved
        self.auth_token = auth_token
        self.circuit_breaker = circuit_breaker or CircuitBreaker()

    def _post(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        if self.circuit_breaker.is_open():
            raise RuntimeError("gateway circuit breaker open")
        data = json.dumps(body).encode()
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        if self.auth_token:
            req.add_header("Authorization", f"Bearer {self.auth_token}")
        try:
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                payload = cast(Dict[str, Any], json.loads(resp.read().decode()))
            self.circuit_breaker.record_success()
            return payload
        except urllib.error.HTTPError as exc:
            self.circuit_breaker.record_failure()
            detail = exc.read().decode()
            try:
                return cast(Dict[str, Any], json.loads(detail))
            except json.JSONDecodeError:
                return {"ok": False, "error": detail}
        except Exception:
            self.circuit_breaker.record_failure()
            raise

    def mint(self, body: Dict[str, Any]) -> MintResponse:
        payload = self._post("/v1/mint", body)
        return MintResponse(
            ok=bool(payload.get("ok")),
            envelope=payload.get("envelope"),
            approval_required=bool(payload.get("approval_required")),
            error=payload.get("error"),
            audit_id=payload.get("audit_id"),
        )

    def charge(self, body: Dict[str, Any]) -> Dict[str, Any]:
        return self._post("/v1/charge", body)

    def audit(self, events: List[Dict[str, Any]]) -> Dict[str, Any]:
        return self._post("/v1/audit", {"events": events})

    def fetch_envelope(self, body: Dict[str, Any]) -> Optional[CapabilityEnvelope]:
        resp = self.mint(body)
        if resp.envelope:
            return CapabilityEnvelope.from_dict(resp.envelope)
        return None
