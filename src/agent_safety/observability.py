"""Deprecated module location — use :mod:`agent_safety.core.observability` instead."""

import warnings as _warnings

_warnings.warn(
    "agent_safety.observability has moved to agent_safety.core.observability; "
    "this alias will be removed in a future release",
    DeprecationWarning,
    stacklevel=2,
)

from .core.observability import *  # noqa: E402,F401,F403
