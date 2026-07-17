"""Deprecated module location — use :mod:`agent_safety.distributed.gateway.client` instead."""

import warnings as _warnings

_warnings.warn(
    "agent_safety.gateway.client has moved to agent_safety.distributed.gateway.client; "
    "this alias will be removed in a future release",
    DeprecationWarning,
    stacklevel=2,
)

from ..distributed.gateway.client import *  # noqa: E402,F401,F403
