"""Deprecated module location — use :mod:`agent_safety.distributed.gateway.server` instead."""

import warnings as _warnings

_warnings.warn(
    "agent_safety.gateway.server has moved to agent_safety.distributed.gateway.server; "
    "this alias will be removed in a future release",
    DeprecationWarning,
    stacklevel=2,
)

from ..distributed.gateway.server import *  # noqa: E402,F401,F403
