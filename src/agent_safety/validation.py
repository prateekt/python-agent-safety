"""Deprecated module location — use :mod:`agent_safety.core.validation` instead."""

import warnings as _warnings

_warnings.warn(
    "agent_safety.validation has moved to agent_safety.core.validation; "
    "this alias will be removed in a future release",
    DeprecationWarning,
    stacklevel=2,
)

from .core.validation import *  # noqa: E402,F401,F403
