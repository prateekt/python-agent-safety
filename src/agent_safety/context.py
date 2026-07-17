"""Deprecated module location — use :mod:`agent_safety.core.context` instead."""

import warnings as _warnings

_warnings.warn(
    "agent_safety.context has moved to agent_safety.core.context; "
    "this alias will be removed in a future release",
    DeprecationWarning,
    stacklevel=2,
)

from .core.context import *  # noqa: E402,F401,F403
