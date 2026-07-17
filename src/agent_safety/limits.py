"""Deprecated module location — use :mod:`agent_safety.core.limits` instead."""

import warnings as _warnings

_warnings.warn(
    "agent_safety.limits has moved to agent_safety.core.limits; "
    "this alias will be removed in a future release",
    DeprecationWarning,
    stacklevel=2,
)

from .core.limits import *  # noqa: E402,F401,F403
