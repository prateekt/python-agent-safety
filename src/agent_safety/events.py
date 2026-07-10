"""Transport-agnostic events for distributed agent loops."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class ToolRequest:
    """Planner emits this to request a guarded tool invocation."""

    request_id: str
    task_id: str
    capability: str
    tool: str
    arguments: Dict[str, Any]
    policy_spec_hash: str
    trace_span: Optional[str] = None
    org_id: str = ""


@dataclass(frozen=True)
class ToolResult:
    """Tool worker emits this after execution."""

    request_id: str
    task_id: str
    ok: bool
    result: Any = None
    error: Optional[str] = None
    org_id: str = ""


@dataclass(frozen=True)
class MintResponse:
    """Gateway response for envelope minting."""

    ok: bool
    envelope: Optional[Dict[str, Any]] = None
    approval_required: bool = False
    error: Optional[str] = None
    audit_id: Optional[str] = None
