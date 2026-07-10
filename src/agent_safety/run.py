"""Run context for distributed agent loops.

:class:`RunContext` carries ``task_id``, worker identity, and idempotency keys
across process boundaries. It lives in a :class:`contextvars.ContextVar` so it
is correct under threads and ``asyncio`` tasks.
"""

from __future__ import annotations

import contextvars
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, Optional


@dataclass(frozen=True)
class RunContext:
    """Identity and correlation for one logical agent run."""

    task_id: str
    agent_id: str
    request_id: str
    parent_span: Optional[str] = None
    org_id: Optional[str] = None

    @classmethod
    def new(
        cls,
        *,
        task_id: Optional[str] = None,
        agent_id: str = "default",
        request_id: Optional[str] = None,
        parent_span: Optional[str] = None,
        org_id: Optional[str] = None,
    ) -> "RunContext":
        return cls(
            task_id=task_id or uuid.uuid4().hex,
            agent_id=agent_id,
            request_id=request_id or uuid.uuid4().hex,
            parent_span=parent_span,
            org_id=org_id,
        )


_current: contextvars.ContextVar[Optional[RunContext]] = contextvars.ContextVar(
    "agent_safety_run", default=None
)


def current_run() -> Optional[RunContext]:
    """Return the active run context, if any."""
    return _current.get()


@contextmanager
def run_context(ctx: RunContext) -> Iterator[RunContext]:
    """Install *ctx* for the duration of the block."""
    token = _current.set(ctx)
    try:
        yield ctx
    finally:
        _current.reset(token)
