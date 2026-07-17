"""Deprecated module location — use :mod:`agent_safety.core.permissions` instead."""

import warnings as _warnings

_warnings.warn(
    "agent_safety.permissions has moved to agent_safety.core.permissions; "
    "this alias will be removed in a future release",
    DeprecationWarning,
    stacklevel=2,
)

from .core.permissions import *  # noqa: E402,F401,F403
