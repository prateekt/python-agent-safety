"""Deprecated module location — use :mod:`agent_safety.distributed.policy_spec` instead."""

import warnings as _warnings

_warnings.warn(
    "agent_safety.policy_spec has moved to agent_safety.distributed.policy_spec; "
    "this alias will be removed in a future release",
    DeprecationWarning,
    stacklevel=2,
)

from .distributed.policy_spec import *  # noqa: E402,F401,F403
