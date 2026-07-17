"""Deprecated module location — use :mod:`agent_safety.core.prices` instead."""

import warnings as _warnings

_warnings.warn(
    "agent_safety.prices has moved to agent_safety.core.prices; "
    "this alias will be removed in a future release",
    DeprecationWarning,
    stacklevel=2,
)

from .core.prices import *  # noqa: E402,F401,F403
