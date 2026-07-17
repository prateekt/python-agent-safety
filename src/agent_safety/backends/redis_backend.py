"""Deprecated module location — use :mod:`agent_safety.distributed.backends.redis_backend` instead."""

import warnings as _warnings

_warnings.warn(
    "agent_safety.backends.redis_backend has moved to agent_safety.distributed.backends.redis_backend; "
    "this alias will be removed in a future release",
    DeprecationWarning,
    stacklevel=2,
)

from ..distributed.backends.redis_backend import *  # noqa: E402,F401,F403
