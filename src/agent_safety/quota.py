"""Deprecated module location — use :mod:`agent_safety.core.quota` instead."""

import warnings as _warnings

_warnings.warn(
    "agent_safety.quota has moved to agent_safety.core.quota; "
    "this alias will be removed in a future release",
    DeprecationWarning,
    stacklevel=2,
)

from .core.quota import *  # noqa: E402,F401,F403
