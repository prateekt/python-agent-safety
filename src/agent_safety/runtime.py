"""Deprecated module location — use :mod:`agent_safety.core.runtime` instead."""

import warnings as _warnings

_warnings.warn(
    "agent_safety.runtime has moved to agent_safety.core.runtime; "
    "this alias will be removed in a future release",
    DeprecationWarning,
    stacklevel=2,
)

from .core.runtime import *  # noqa: E402,F401,F403
