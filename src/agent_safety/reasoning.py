"""Deprecated module location — use :mod:`agent_safety.core.reasoning` instead."""

import warnings as _warnings

_warnings.warn(
    "agent_safety.reasoning has moved to agent_safety.core.reasoning; "
    "this alias will be removed in a future release",
    DeprecationWarning,
    stacklevel=2,
)

from .core.reasoning import *  # noqa: E402,F401,F403
