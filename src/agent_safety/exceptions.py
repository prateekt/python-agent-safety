"""Deprecated module location — use :mod:`agent_safety.core.exceptions` instead."""

import warnings as _warnings

_warnings.warn(
    "agent_safety.exceptions has moved to agent_safety.core.exceptions; "
    "this alias will be removed in a future release",
    DeprecationWarning,
    stacklevel=2,
)

from .core.exceptions import *  # noqa: E402,F401,F403
