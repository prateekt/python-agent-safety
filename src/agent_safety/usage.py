"""Deprecated module location — use :mod:`agent_safety.core.usage` instead."""

import warnings as _warnings

_warnings.warn(
    "agent_safety.usage has moved to agent_safety.core.usage; "
    "this alias will be removed in a future release",
    DeprecationWarning,
    stacklevel=2,
)

from .core.usage import *  # noqa: E402,F401,F403
