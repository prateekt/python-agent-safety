"""Distributed rollout configuration.

All distributed behavior is driven by one explicit, typed object —
:class:`DistributedConfig`. Build it yourself and pass it to
``safely(distributed=...)``, or let :meth:`DistributedConfig.from_env` read the
conventional ``AGENT_SAFETY_*`` environment variables (the default when you
don't pass one):

* ``AGENT_SAFETY_DISTRIBUTED`` — ``local`` / ``shadow`` / ``canary`` / ``enforce``
* ``AGENT_SAFETY_CANARY_PERCENT`` — 0–100 (default 10)
* ``AGENT_SAFETY_GATEWAY_URL`` — policy-gateway base URL
* ``AGENT_SAFETY_ORG_ID`` — tenant namespace
* ``AGENT_SAFETY_SIGNING_KEYS`` — JSON ``{"kid": "<base64-secret>"}`` or a path to it
* ``AGENT_SAFETY_REDIS_URL`` — shared budget/nonce Redis

The module-level helpers (``distributed_mode()``, ``should_require_envelope()``,
…) are thin wrappers over ``DistributedConfig.from_env()`` kept for
convenience and backward compatibility.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional


class DistributedMode(str, Enum):
    """Production rollout modes per org."""

    LOCAL = "local"
    SHADOW = "shadow"
    CANARY = "canary"
    ENFORCE = "enforce"


def _task_in_canary(task_id: str, percent: int) -> bool:
    if percent <= 0:
        return False
    if percent >= 100:
        return True
    digest = hashlib.sha256(task_id.encode("utf-8")).digest()
    bucket = int.from_bytes(digest[:2], "big") % 100
    return bucket < percent


def load_signing_keys(path: Optional[str] = None) -> Dict[str, bytes]:
    """Load ``kid -> HMAC secret`` from JSON file or env.

    ``AGENT_SAFETY_SIGNING_KEYS`` may be a filesystem path to a JSON object
    ``{"kid": "<base64-secret>", ...}``, or the JSON object itself.
    """
    raw = path or os.environ.get("AGENT_SAFETY_SIGNING_KEYS", "").strip()
    if not raw:
        return {}
    text = raw
    candidate = Path(raw)
    if candidate.is_file():
        text = candidate.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    out: Dict[str, bytes] = {}
    for kid, secret in data.items():
        if isinstance(secret, str):
            try:
                out[str(kid)] = base64.b64decode(secret)
            except Exception:
                out[str(kid)] = secret.encode("utf-8")
        elif isinstance(secret, bytes):
            out[str(kid)] = secret
    return out


@dataclass(frozen=True)
class DistributedConfig:
    """Everything the distributed layer needs, in one explicit object.

    Pass one to ``safely(distributed=...)`` (or a gateway/worker constructor)
    instead of relying on process environment. :meth:`from_env` builds one from
    the conventional ``AGENT_SAFETY_*`` variables for deployments that prefer
    env-based rollout.
    """

    mode: DistributedMode = DistributedMode.LOCAL
    canary_percent: int = 10
    gateway_url: Optional[str] = None
    org_id: str = ""
    signing_keys: Dict[str, bytes] = field(default_factory=dict)
    redis_url: Optional[str] = None

    @classmethod
    def from_env(cls) -> "DistributedConfig":
        """Read the ``AGENT_SAFETY_*`` environment variables (all optional)."""
        raw_mode = os.environ.get("AGENT_SAFETY_DISTRIBUTED", "local").strip().lower()
        try:
            mode = DistributedMode(raw_mode)
        except ValueError:
            mode = DistributedMode.LOCAL
        raw_pct = os.environ.get("AGENT_SAFETY_CANARY_PERCENT", "10").strip()
        try:
            pct = max(0, min(100, int(raw_pct)))
        except ValueError:
            pct = 10
        return cls(
            mode=mode,
            canary_percent=pct,
            gateway_url=os.environ.get("AGENT_SAFETY_GATEWAY_URL", "").strip() or None,
            org_id=os.environ.get("AGENT_SAFETY_ORG_ID", "").strip(),
            signing_keys=load_signing_keys(),
            redis_url=os.environ.get("AGENT_SAFETY_REDIS_URL", "").strip() or None,
        )

    # -- decisions ---------------------------------------------------------
    def should_enforce_envelope(self) -> bool:
        """True when mode is ``enforce`` (global hard requirement)."""
        return self.mode == DistributedMode.ENFORCE

    def should_shadow_envelope(self) -> bool:
        """True when envelopes should be verified when present (shadow/canary/enforce)."""
        return self.mode in (
            DistributedMode.SHADOW, DistributedMode.CANARY, DistributedMode.ENFORCE,
        )

    def should_require_envelope(self, task_id: Optional[str] = None) -> bool:
        """True when a missing envelope must fail closed.

        * ``enforce`` — always
        * ``canary`` — for a stable hash-fraction of ``task_id``s
        * ``shadow`` / ``local`` — never (shadow verifies only when supplied)
        """
        if self.mode == DistributedMode.ENFORCE:
            return True
        if self.mode == DistributedMode.CANARY:
            if not task_id:
                return False
            return _task_in_canary(task_id, self.canary_percent)
        return False

    # -- resources -----------------------------------------------------------
    def nonce_store(self) -> Any:
        """A shared Redis nonce store when ``redis_url`` is set, else process-local."""
        from .nonces import RedisNonceStore, default_memory_nonce_store

        if self.redis_url:
            try:
                import redis  # type: ignore[import-not-found]

                return RedisNonceStore(redis.Redis.from_url(self.redis_url))
            except Exception:
                pass
        return default_memory_nonce_store()


# -- env-reading convenience wrappers (kept for back-compat) ----------------

def distributed_mode(org_id: Optional[str] = None) -> DistributedMode:
    """Read ``AGENT_SAFETY_DISTRIBUTED`` env var (default ``local``)."""
    _ = org_id  # reserved for per-org overrides
    return DistributedConfig.from_env().mode


def canary_percent() -> int:
    """Fraction of task_ids that require envelopes in ``canary`` mode (0–100)."""
    return DistributedConfig.from_env().canary_percent


def gateway_url() -> Optional[str]:
    """Return ``AGENT_SAFETY_GATEWAY_URL`` if set."""
    return DistributedConfig.from_env().gateway_url


def org_id_from_env() -> str:
    """Return ``AGENT_SAFETY_ORG_ID`` (default empty)."""
    return DistributedConfig.from_env().org_id


def should_enforce_envelope(org_id: Optional[str] = None) -> bool:
    """True when mode is ``enforce`` (global hard requirement)."""
    _ = org_id
    return DistributedConfig.from_env().should_enforce_envelope()


def should_shadow_envelope(org_id: Optional[str] = None) -> bool:
    """True when envelopes should be verified when present (shadow/canary/enforce)."""
    _ = org_id
    return DistributedConfig.from_env().should_shadow_envelope()


def should_require_envelope(
    task_id: Optional[str] = None,
    *,
    org_id: Optional[str] = None,
) -> bool:
    """True when a missing envelope must fail closed (see
    :meth:`DistributedConfig.should_require_envelope`)."""
    _ = org_id
    return DistributedConfig.from_env().should_require_envelope(task_id)
