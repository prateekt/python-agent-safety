"""Causal tracing: group audit events under a named span.

A flat audit log tells you *what* the agent did; a trace tells you *where* in its
reasoning each decision happened. :func:`trace_span` pushes a name onto a
:mod:`contextvars` stack for the duration of a ``with`` block; while it is active,
every :class:`~agent_safety.audit.AuditEvent` the policy emits is stamped with the
current dotted span path (``"plan.search.fetch"``). Spans nest and are correct
under threads and ``asyncio`` tasks.

This module deliberately imports nothing from the rest of the package, so
:mod:`agent_safety.policy` can read :func:`current_span` when auditing without an
import cycle.
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from typing import Iterator, Optional, Tuple

_spans: "contextvars.ContextVar[Tuple[str, ...]]" = contextvars.ContextVar(
    "agent_safety_spans", default=()
)


def current_span() -> Optional[str]:
    """Return the active dotted span path, or ``None`` outside any span."""
    parts = _spans.get()
    return ".".join(parts) if parts else None


@contextmanager
def trace_span(name: str) -> Iterator[str]:
    """Push *name* onto the span stack for the duration of the block.

        with trace_span("plan"):
            with trace_span("search"):
                run_search(...)   # audited events carry span="plan.search"

    Yields the full dotted path of the entered span.
    """
    parts = _spans.get() + (name,)
    token = _spans.set(parts)
    try:
        yield ".".join(parts)
    finally:
        _spans.reset(token)
