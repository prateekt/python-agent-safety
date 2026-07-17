"""Deprecated module location — use :mod:`agent_safety.core.action` instead."""

import warnings as _warnings

_warnings.warn(
    "agent_safety.action has moved to agent_safety.core.action; "
    "this alias will be removed in a future release",
    DeprecationWarning,
    stacklevel=2,
)

from .core.action import *  # noqa: E402,F401,F403
