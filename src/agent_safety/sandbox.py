"""Deprecated module location — use :mod:`agent_safety.core.sandbox` instead."""

import warnings as _warnings

_warnings.warn(
    "agent_safety.sandbox has moved to agent_safety.core.sandbox; "
    "this alias will be removed in a future release",
    DeprecationWarning,
    stacklevel=2,
)

from .core.sandbox import *  # noqa: E402,F401,F403
