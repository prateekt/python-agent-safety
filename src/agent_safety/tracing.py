"""Deprecated module location — use :mod:`agent_safety.core.tracing` instead."""

import warnings as _warnings

_warnings.warn(
    "agent_safety.tracing has moved to agent_safety.core.tracing; "
    "this alias will be removed in a future release",
    DeprecationWarning,
    stacklevel=2,
)

from .core.tracing import *  # noqa: E402,F401,F403
