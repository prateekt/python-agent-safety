"""Deprecated module location — use :mod:`agent_safety.core.schema` instead."""

import warnings as _warnings

_warnings.warn(
    "agent_safety.schema has moved to agent_safety.core.schema; "
    "this alias will be removed in a future release",
    DeprecationWarning,
    stacklevel=2,
)

from .core.schema import *  # noqa: E402,F401,F403
