"""Human-in-the-loop approval — see :mod:`agent_safety.gates`.

This module re-exports :class:`~agent_safety.gates.ApprovalGate` from the
unified gates module, where all four gate kinds now live. Every approval hook
receives an :class:`~agent_safety.action.Action`.
"""

from __future__ import annotations

import warnings
from typing import Any

from .core.action import Action
from .core.gates import ApprovalGate, Approver

__all__ = ["ApprovalGate", "Approver"]


def __getattr__(name: str) -> Any:
    if name == "ApprovalRequest":
        warnings.warn(
            "ApprovalRequest is a deprecated alias of Action; "
            "import Action from agent_safety instead",
            DeprecationWarning,
            stacklevel=2,
        )
        return Action
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
