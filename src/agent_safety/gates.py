"""Deprecated module location — use :mod:`agent_safety.core.gates` instead."""

import warnings as _warnings

_warnings.warn(
    "agent_safety.gates has moved to agent_safety.core.gates; "
    "this alias will be removed in a future release",
    DeprecationWarning,
    stacklevel=2,
)

from .core.gates import *  # noqa: E402,F401,F403
