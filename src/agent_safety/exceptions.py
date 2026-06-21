"""Exception hierarchy for agent-safety violations.

Every safety failure is an :class:`AgentSafetyError`, so a host application can
catch the whole family with one ``except`` clause and still discriminate between
a *permission* failure and a *content* failure when it wants to.
"""

from __future__ import annotations

from typing import Optional


class AgentSafetyError(Exception):
    """Base class for everything this library raises."""


class PermissionDenied(AgentSafetyError):
    """Raised when an agent attempts a capability the active policy forbids.

    Attributes:
        capability: The capability that was requested (e.g. ``"shell.exec"``).
        reason: Human-readable explanation of why it was denied.
    """

    def __init__(self, capability: str, reason: str = "not permitted in this context"):
        self.capability = capability
        self.reason = reason
        super().__init__(f"capability {capability!r} denied: {reason}")


class GuardViolation(AgentSafetyError):
    """Raised when a prompt, input, or output fails a guard that cannot sanitize it.

    Attributes:
        guard: Name of the guard that tripped.
        stage: Where it tripped (``"prompt"``, ``"input"`` or ``"output"``).
        reason: Human-readable explanation.
    """

    def __init__(self, guard: str, stage: str, reason: str, *, value: Optional[object] = None):
        self.guard = guard
        self.stage = stage
        self.reason = reason
        self.value = value
        super().__init__(f"[{stage}] guard {guard!r} blocked value: {reason}")
