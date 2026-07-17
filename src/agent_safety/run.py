"""Deprecated module location — use :mod:`agent_safety.distributed.run` instead."""

import warnings as _warnings

_warnings.warn(
    "agent_safety.run has moved to agent_safety.distributed.run; "
    "this alias will be removed in a future release",
    DeprecationWarning,
    stacklevel=2,
)

from .distributed.run import *  # noqa: E402,F401,F403
