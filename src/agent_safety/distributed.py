"""Distributed rollout and client helpers."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from enum import Enum
from pathlib import Path
from typing import Dict, Optional


class DistributedMode(str, Enum):
    """Production rollout modes per org."""

    LOCAL = "local"
    SHADOW = "shadow"
    CANARY = "canary"
    ENFORCE = "enforce"


def distributed_mode(org_id: Optional[str] = None) -> DistributedMode:
    """Read ``AGENT_SAFETY_DISTRIBUTED`` env var (default ``local``)."""
    _ = org_id  # reserved for per-org overrides
    raw = os.environ.get("AGENT_SAFETY_DISTRIBUTED", "local").strip().lower()
    try:
        return DistributedMode(raw)
    except ValueError:
        return DistributedMode.LOCAL


def canary_percent() -> int:
    """Fraction of task_ids that require envelopes in ``canary`` mode (0–100)."""
    raw = os.environ.get("AGENT_SAFETY_CANARY_PERCENT", "10").strip()
    try:
        return max(0, min(100, int(raw)))
    except ValueError:
        return 10


def gateway_url() -> Optional[str]:
    """Return ``AGENT_SAFETY_GATEWAY_URL`` if set."""
    url = os.environ.get("AGENT_SAFETY_GATEWAY_URL", "").strip()
    return url or None


def org_id_from_env() -> str:
    """Return ``AGENT_SAFETY_ORG_ID`` (default empty)."""
    return os.environ.get("AGENT_SAFETY_ORG_ID", "").strip()


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


def _task_in_canary(task_id: str, percent: int) -> bool:
    if percent <= 0:
        return False
    if percent >= 100:
        return True
    digest = hashlib.sha256(task_id.encode("utf-8")).digest()
    bucket = int.from_bytes(digest[:2], "big") % 100
    return bucket < percent


def should_enforce_envelope(org_id: Optional[str] = None) -> bool:
    """True when mode is ``enforce`` (global hard requirement)."""
    return distributed_mode(org_id) == DistributedMode.ENFORCE


def should_shadow_envelope(org_id: Optional[str] = None) -> bool:
    """True when envelopes should be verified when present (shadow/canary/enforce)."""
    mode = distributed_mode(org_id)
    return mode in (DistributedMode.SHADOW, DistributedMode.CANARY, DistributedMode.ENFORCE)


def should_require_envelope(
    task_id: Optional[str] = None,
    *,
    org_id: Optional[str] = None,
) -> bool:
    """True when a missing envelope must fail closed.

    * ``enforce`` — always
    * ``canary`` — for a stable hash-fraction of ``task_id``s
    * ``shadow`` / ``local`` — never (shadow verifies only when an envelope is supplied)
    """
    mode = distributed_mode(org_id)
    if mode == DistributedMode.ENFORCE:
        return True
    if mode == DistributedMode.CANARY:
        if not task_id:
            return False
        return _task_in_canary(task_id, canary_percent())
    return False
