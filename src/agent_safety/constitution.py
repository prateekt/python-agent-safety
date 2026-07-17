"""Constitutional rules — see :mod:`agent_safety.gates`.

This module re-exports :class:`~agent_safety.gates.ConstitutionGate` from the
unified gates module, where all four gate kinds now live.
"""

from __future__ import annotations

from .core.gates import ConstitutionGate, Judge

__all__ = ["ConstitutionGate", "Judge"]
