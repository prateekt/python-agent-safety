"""Deprecated module location — use :mod:`agent_safety.distributed.backends` instead."""

import warnings as _warnings

_warnings.warn(
    "agent_safety.backends has moved to agent_safety.distributed.backends; "
    "this alias will be removed in a future release",
    DeprecationWarning,
    stacklevel=2,
)

from ..distributed.backends import *  # noqa: E402,F401,F403
