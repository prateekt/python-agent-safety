"""Deprecated module location — use :mod:`agent_safety.distributed.events` instead."""

import warnings as _warnings

_warnings.warn(
    "agent_safety.events has moved to agent_safety.distributed.events; "
    "this alias will be removed in a future release",
    DeprecationWarning,
    stacklevel=2,
)

from .distributed.events import *  # noqa: E402,F401,F403
