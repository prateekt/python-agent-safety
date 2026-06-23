"""Explainability as a safety construct: make the agent justify what it does.

Least privilege limits *what* an agent may do; a :class:`ReasoningGate` makes it
say *why* before it does it. For matching capabilities the agent must supply a
``rationale="..."`` argument with the tool call; the rationale is validated,
recorded to the audit trail, and (optionally) handed to a human approver — then
stripped before the underlying tool runs. A missing or thin rationale raises
:class:`~agent_safety.exceptions.ExplanationRequired`, which is reported back to
the model so it can retry *with* an explanation.

    with safety_context(
        PermissionSet.of("shell.exec"),
        reasoning=ReasoningGate(require=["shell.exec"], min_length=20),
    ):
        run_shell("rm build/*", rationale="Clearing stale build artifacts before rebuild")
        # run_shell("rm build/*")  ->  ExplanationRequired

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
from contextlib import contextmanager
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Callable, Iterable, Iterator, List, Optional

from .action import Action

RATIONALE_KWARG = "rationale"

# A validator decides whether a rationale is adequate for a given action.
RationaleValidator = Callable[[str, Action], bool]

# Back-compat: a validator's second argument used to be called ReasoningRequest.
ReasoningRequest = Action


class ReasoningGate:
    """Require an explanation before any tool whose capability it covers runs.

    Args:
        require: Capability patterns (glob ``*``) that must be justified.
        min_length: Minimum rationale length, in stripped characters.
        validator: Optional ``(rationale, request) -> bool`` for richer checks
            (e.g. an LLM-as-judge or a keyword requirement).
    """

    def __init__(
        self,
        require: Iterable[str],
        *,
        min_length: int = 1,
        validator: Optional[RationaleValidator] = None,
    ):
        self.patterns = tuple(p.strip() for p in require if p and p.strip())
        if not self.patterns:
            raise ValueError("a ReasoningGate must require at least one capability")
        if min_length < 1:
            raise ValueError("min_length must be >= 1")
        self.min_length = min_length
        self.validator = validator
        self.name = "reasoning_gate(" + ", ".join(self.patterns) + ")"

    def covers(self, capability: str) -> bool:
        """Whether this gate requires a rationale for *capability*."""
        return any(fnmatchcase(capability, p) for p in self.patterns)

    def evaluate(self, rationale: Optional[str], request: ReasoningRequest) -> Optional[str]:
        """Return ``None`` if the rationale is adequate, else why it is not."""
        text = (rationale or "").strip()
        if not text:
            return "a rationale is required for this action"
        if len(text) < self.min_length:
            return f"rationale is too short (< {self.min_length} characters)"
        if self.validator is not None and not self.validator(text, request):
            return "rationale was rejected by the validator"
        return None


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
