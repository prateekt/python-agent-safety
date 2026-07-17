"""Explainability: rationale gates and the thought trace.

:class:`~agent_safety.gates.ReasoningGate` (re-exported here) makes the agent
say *why* before it acts: for matching capabilities the agent must supply a
``rationale="..."`` argument with the tool call; the rationale is validated,
recorded to the audit trail, then stripped before the underlying tool runs. A
missing or thin rationale raises
:class:`~agent_safety.exceptions.ExplanationRequired`, which is reported back
to the model so it can retry *with* an explanation.

Separately, :func:`thought_trace` / :func:`record_thought` let the agent narrate
its step-by-step reasoning inside a block; each thought is timestamped onto the
same audit trail (and stamped with the active :func:`~agent_safety.tracing.trace_span`),
giving you a replayable record of the agent's stated intent alongside the
decisions it triggered.

``rationale`` is the reserved keyword; it is only intercepted when a gate covers
the capability, so tools that genuinely take a ``rationale`` parameter are
unaffected otherwise.
"""

from __future__ import annotations

import contextvars
import warnings
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator, List, Optional

from .action import Action
from .gates import RATIONALE_KWARG, RationaleValidator, ReasoningGate

__all__ = [
    "RATIONALE_KWARG",
    "RationaleValidator",
    "ReasoningGate",
    "Thought",
    "ThoughtTrace",
    "current_trace",
    "record_thought",
    "thought_trace",
]


def __getattr__(name: str) -> Any:
    if name == "ReasoningRequest":
        warnings.warn(
            "ReasoningRequest is a deprecated alias of Action; "
            "import Action from agent_safety instead",
            DeprecationWarning,
            stacklevel=2,
        )
        return Action
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# -- thought trace --------------------------------------------------------

@dataclass(frozen=True)
class Thought:
    """One recorded reasoning step."""

    text: str
    span: Optional[str] = None


class ThoughtTrace:
    """An ordered, in-memory record of an agent's stated reasoning."""

    def __init__(self) -> None:
        self.thoughts: List[Thought] = []

    def __len__(self) -> int:
        return len(self.thoughts)

    def __iter__(self) -> Iterator[Thought]:
        return iter(self.thoughts)

    def add(self, thought: Thought) -> None:
        self.thoughts.append(thought)

    def text(self) -> str:
        """The whole trace as newline-joined text."""
        return "\n".join(t.text for t in self.thoughts)


_trace: "contextvars.ContextVar[Optional[ThoughtTrace]]" = contextvars.ContextVar(
    "agent_safety_thought_trace", default=None
)


def current_trace() -> Optional[ThoughtTrace]:
    """Return the active :class:`ThoughtTrace`, or ``None`` outside a block."""
    return _trace.get()


@contextmanager
def thought_trace() -> Iterator[ThoughtTrace]:
    """Collect :func:`record_thought` entries for the duration of a block."""
    trace = ThoughtTrace()
    token = _trace.set(trace)
    try:
        yield trace
    finally:
        _trace.reset(token)


def record_thought(text: str) -> None:
    """Record one reasoning step: append it to the active trace and audit it.

    Stamps the current :func:`~agent_safety.tracing.trace_span` so a thought is
    located in the same causal tree as the decisions around it. A no-op-safe call
    anywhere — outside a ``thought_trace`` block it still lands on the audit log.
    """
    # Imported lazily to avoid an import cycle (context -> policy -> reasoning).
    from .audit import AuditEvent
    from .context import current_policy
    from .tracing import current_span

    span = current_span()
    trace = _trace.get()
    if trace is not None:
        trace.add(Thought(text, span))
    current_policy().audit(AuditEvent("thought", "record", detail=text, span=span))
