"""Deprecated module location — use :mod:`agent_safety.distributed.backends.dynamo_backend` instead."""

import warnings as _warnings

_warnings.warn(
    "agent_safety.backends.dynamo_backend has moved to agent_safety.distributed.backends.dynamo_backend; "
    "this alias will be removed in a future release",
    DeprecationWarning,
    stacklevel=2,
)

from ..distributed.backends.dynamo_backend import *  # noqa: E402,F401,F403
