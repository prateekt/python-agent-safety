"""Deprecated module location — use :mod:`agent_safety.core.policy` instead."""

import warnings as _warnings

_warnings.warn(
    "agent_safety.policy has moved to agent_safety.core.policy; "
    "this alias will be removed in a future release",
    DeprecationWarning,
    stacklevel=2,
)

from .core.policy import *  # noqa: E402,F401,F403
