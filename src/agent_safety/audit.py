"""Audit hooks: a tamper-evident record of every safety decision.

A safety layer is only trustworthy if you can see what it did. Every permission
check, guard action, and quota charge can emit an :class:`AuditEvent` to the
sinks registered on the active policy. Sinks are plain callables, so a sink can
append to a list (tests), print (dev), ship to your SIEM, or write structured
JSON (production) — the library doesn't care.

Auditing is provider-agnostic: the events describe *what the agent tried to do*,
not which model produced the request.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import IO, Any, Callable, Dict, List, Optional

# A sink is anything callable with an AuditEvent.
AuditSink = Callable[["AuditEvent"], None]


@dataclass(frozen=True)
class AuditEvent:
    """One recorded safety decision."""

    action: str          # e.g. "permission", "guard", "quota", "tool_call"
    decision: str        # e.g. "allow", "deny", "block", "sanitize", "charge", "ok"
    detail: str = ""     # human-readable specifics
    capability: Optional[str] = None
    stage: Optional[str] = None
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "ts": self.timestamp,
            "action": self.action,
            "decision": self.decision,
            "detail": self.detail,
        }
        if self.capability is not None:
            d["capability"] = self.capability
        if self.stage is not None:
            d["stage"] = self.stage
        return d


class ListSink:
    """Collect events in memory. Handy for tests and post-hoc inspection."""

    def __init__(self) -> None:
        self.events: List[AuditEvent] = []

    def __call__(self, event: AuditEvent) -> None:
        self.events.append(event)


class JsonlSink:
    """Append events as JSON lines to an open text stream (file, stderr, ...)."""

    def __init__(self, stream: IO[str]) -> None:
        self.stream = stream

    def __call__(self, event: AuditEvent) -> None:
        self.stream.write(json.dumps(event.to_dict()) + "\n")
        self.stream.flush()
