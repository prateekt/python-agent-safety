"""Deprecated module location — use :mod:`agent_safety.distributed.backends.sql_backend` instead."""

import warnings as _warnings

_warnings.warn(
    "agent_safety.backends.sql_backend has moved to agent_safety.distributed.backends.sql_backend; "
    "this alias will be removed in a future release",
    DeprecationWarning,
    stacklevel=2,
)

from ..distributed.backends.sql_backend import *  # noqa: E402,F401,F403
