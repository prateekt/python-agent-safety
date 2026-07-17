"""Deprecated module location — use :mod:`agent_safety.core.guards` instead."""

import warnings as _warnings

_warnings.warn(
    "agent_safety.guards has moved to agent_safety.core.guards; "
    "this alias will be removed in a future release",
    DeprecationWarning,
    stacklevel=2,
)

from .core.guards import *  # noqa: E402,F401,F403
