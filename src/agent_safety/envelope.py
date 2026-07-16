"""Signed capability envelopes for distributed tool authorization."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from .exceptions import PermissionDenied
from .nonces import MemoryNonceStore, NonceStore

CLOCK_SKEW_TOLERANCE = 30.0
DEFAULT_TTL = 120.0


@dataclass(frozen=True)
class CapabilityEnvelope:
    """Short-lived, signed authorization for one tool capability."""

    task_id: str
    policy_hash: str
    allowed_capabilities: Tuple[str, ...]
    capability: str
    nonce: str
    issued_at: float
    expires_at: float
    signature: bytes
    kid: str = "default"
    org_id: str = ""
    approval_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "policy_hash": self.policy_hash,
            "allowed_capabilities": list(self.allowed_capabilities),
            "capability": self.capability,
            "nonce": self.nonce,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "signature": base64.b64encode(self.signature).decode("ascii"),
            "kid": self.kid,
            "org_id": self.org_id,
            "approval_id": self.approval_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CapabilityEnvelope":
        sig = data.get("signature", "")
        if isinstance(sig, str):
            sig_bytes = base64.b64decode(sig)
        else:
            sig_bytes = bytes(sig)
        return cls(
            task_id=str(data["task_id"]),
            policy_hash=str(data["policy_hash"]),
            allowed_capabilities=tuple(data.get("allowed_capabilities", ())),
            capability=str(data["capability"]),
            nonce=str(data["nonce"]),
            issued_at=float(data["issued_at"]),
            expires_at=float(data["expires_at"]),
            signature=sig_bytes,
            kid=str(data.get("kid", "default")),
            org_id=str(data.get("org_id", "")),
            approval_id=data.get("approval_id"),
        )

    def payload_bytes(self) -> bytes:
        body = {
            "task_id": self.task_id,
            "policy_hash": self.policy_hash,
            "allowed_capabilities": list(self.allowed_capabilities),
            "capability": self.capability,
            "nonce": self.nonce,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "kid": self.kid,
            "org_id": self.org_id,
            "approval_id": self.approval_id,
        }
        return json.dumps(body, sort_keys=True, separators=(",", ":")).encode()


class EnvelopeSigner:
    """Sign capability envelopes (gateway / PDP only)."""

    def __init__(self, secret: bytes, *, kid: str = "default") -> None:
        self._secret = secret
        self.kid = kid

    def sign(
        self,
        *,
        task_id: str,
        policy_hash: str,
        allowed_capabilities: Tuple[str, ...],
        capability: str,
        org_id: str = "",
        ttl: float = DEFAULT_TTL,
        approval_id: Optional[str] = None,
    ) -> CapabilityEnvelope:
        now = time.time()
        nonce = secrets.token_hex(16)
        envelope = CapabilityEnvelope(
            task_id=task_id,
            policy_hash=policy_hash,
            allowed_capabilities=allowed_capabilities,
            capability=capability,
            nonce=nonce,
            issued_at=now,
            expires_at=now + ttl,
            signature=b"",
            kid=self.kid,
            org_id=org_id,
            approval_id=approval_id,
        )
        sig = hmac.new(self._secret, envelope.payload_bytes(), hashlib.sha256).digest()
        return CapabilityEnvelope(
            task_id=envelope.task_id,
            policy_hash=envelope.policy_hash,
            allowed_capabilities=envelope.allowed_capabilities,
            capability=envelope.capability,
            nonce=envelope.nonce,
            issued_at=envelope.issued_at,
            expires_at=envelope.expires_at,
            signature=sig,
            kid=envelope.kid,
            org_id=envelope.org_id,
            approval_id=envelope.approval_id,
        )


class EnvelopeVerifier:
    """Verify capability envelopes (workers / PEP).

    Pass a shared :class:`~agent_safety.nonces.NonceStore` (Memory, Redis, or SQL) so
    replay protection works across workers, not just within one process.
    """

    def __init__(
        self,
        keys: Dict[str, bytes],
        *,
        clock_skew: float = CLOCK_SKEW_TOLERANCE,
        nonce_store: Optional[NonceStore] = None,
    ) -> None:
        self._keys = keys
        self._clock_skew = clock_skew
        self._nonce_store: NonceStore = nonce_store or MemoryNonceStore()

    def verify(
        self,
        envelope: CapabilityEnvelope,
        *,
        capability: Optional[str] = None,
        now: Optional[float] = None,
    ) -> None:
        """Raise :class:`PermissionDenied` if the envelope is invalid or expired."""
        ts = time.time() if now is None else now

        key = self._keys.get(envelope.kid)
        if key is None:
            raise PermissionDenied(
                envelope.capability,
                f"unknown signing key {envelope.kid!r}",
            )

        expected = hmac.new(key, envelope.payload_bytes(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, envelope.signature):
            raise PermissionDenied(envelope.capability, "invalid envelope signature")

        if ts > envelope.expires_at + self._clock_skew:
            retry = max(0.0, envelope.expires_at - ts)
            raise PermissionDenied(
                envelope.capability,
                f"envelope expired (retry_after={retry:.1f}s)",
            )
        if ts < envelope.issued_at - self._clock_skew:
            raise PermissionDenied(envelope.capability, "envelope not yet valid")

        cap = capability or envelope.capability
        if cap != envelope.capability:
            raise PermissionDenied(cap, "capability mismatch with envelope")
        if cap not in envelope.allowed_capabilities and "*" not in envelope.allowed_capabilities:
            if not any(_matches(cap, p) for p in envelope.allowed_capabilities):
                raise PermissionDenied(cap, "capability not in envelope allow-list")

        expires = envelope.expires_at + self._clock_skew
        if not self._nonce_store.spend(envelope.nonce, expires):
            raise PermissionDenied(envelope.capability, "envelope nonce already spent")

    def mark_spent(self, nonce: str, expires_at: float) -> None:
        self._nonce_store.spend(nonce, expires_at)


def _matches(capability: str, pattern: str) -> bool:
    from fnmatch import fnmatchcase
    return fnmatchcase(capability, pattern)
