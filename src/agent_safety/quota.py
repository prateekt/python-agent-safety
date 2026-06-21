"""Resource quotas: cap how much an agent may consume within a context.

A :class:`Quota` is a small mutable counter — how many tool calls and/or model
tokens an agent is still allowed to spend. Unlike :class:`~agent_safety.policy.Policy`
(immutable) a quota carries live state, because spending is inherently stateful.

Quotas compose through nesting. When a :func:`~agent_safety.context.safety_context`
adds a quota, charges are applied to *every* quota currently in scope — so an
inner budget can be tighter than an outer one but never looser, and the outer
budget keeps counting down as the inner work runs. This mirrors how the
permission ratchet only narrows.

Quotas are provider-agnostic: "tokens" is just an integer you report from
whatever usage object your model returns (Claude ``usage.output_tokens``,
OpenAI ``usage.total_tokens``, Gemini ``usage_metadata.total_token_count``).
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Optional

from .exceptions import QuotaExceeded


@dataclass
class Quota:
    """A mutable call/token budget. ``None`` means "no limit" for that resource."""

    max_calls: Optional[int] = None
    max_tokens: Optional[int] = None
    calls_used: int = 0
    tokens_used: int = 0

    def __post_init__(self) -> None:
        self._lock = Lock()

    # -- spending ---------------------------------------------------------
    def charge_call(self, n: int = 1) -> None:
        """Account for *n* tool/model calls, raising if it would exceed the cap."""
        with self._lock:
            if self.max_calls is not None and self.calls_used + n > self.max_calls:
                raise QuotaExceeded("calls", self.max_calls, self.calls_used + n)
            self.calls_used += n

    def charge_tokens(self, n: int) -> None:
        """Account for *n* consumed tokens, raising if it would exceed the cap."""
        if n < 0:
            raise ValueError("token charge must be non-negative")
        with self._lock:
            if self.max_tokens is not None and self.tokens_used + n > self.max_tokens:
                raise QuotaExceeded("tokens", self.max_tokens, self.tokens_used + n)
            self.tokens_used += n

    # -- introspection ----------------------------------------------------
    def remaining_calls(self) -> Optional[int]:
        return None if self.max_calls is None else self.max_calls - self.calls_used

    def remaining_tokens(self) -> Optional[int]:
        return None if self.max_tokens is None else self.max_tokens - self.tokens_used

    def __str__(self) -> str:
        return (
            f"Quota(calls={self.calls_used}/{self.max_calls}, "
            f"tokens={self.tokens_used}/{self.max_tokens})"
        )
