"""Distributed rollout and client helpers."""

from __future__ import annotations

import os
from enum import Enum
from typing import Optional


class DistributedMode(str, Enum):
    """Production rollout modes per org."""

    LOCAL = "local"
    SHADOW = "shadow"
    CANARY = "canary"
    ENFORCE = "enforce"


def distributed_mode(org_id: Optional[str] = None) -> DistributedMode:
    """Read ``AGENT_SAFETY_DISTRIBUTED`` env var (default ``local``)."""
    raw = os.environ.get("AGENT_SAFETY_DISTRIBUTED", "local").strip().lower()
    try:
        return DistributedMode(raw)
    except ValueError:
        return DistributedMode.LOCAL


def should_enforce_envelope(org_id: Optional[str] = None) -> bool:
    mode = distributed_mode(org_id)
    return mode == DistributedMode.ENFORCE


def should_shadow_envelope(org_id: Optional[str] = None) -> bool:
    mode = distributed_mode(org_id)
    return mode in (DistributedMode.SHADOW, DistributedMode.CANARY, DistributedMode.ENFORCE)
