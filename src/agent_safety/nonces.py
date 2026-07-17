"""Deprecated module location — use :mod:`agent_safety.distributed.nonces` instead."""

import warnings as _warnings

_warnings.warn(
    "agent_safety.nonces has moved to agent_safety.distributed.nonces; "
    "this alias will be removed in a future release",
    DeprecationWarning,
    stacklevel=2,
)

from .distributed.nonces import *  # noqa: E402,F401,F403
