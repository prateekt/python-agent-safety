"""Action previews — see :mod:`agent_safety.gates`.

This module re-exports :class:`~agent_safety.gates.PreviewGate` from the
unified gates module, where all four gate kinds now live.
"""

from __future__ import annotations

from .core.gates import PreviewApprover, PreviewGate

__all__ = ["PreviewGate", "PreviewApprover"]
