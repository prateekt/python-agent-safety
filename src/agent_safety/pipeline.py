"""Deprecated module location — use :mod:`agent_safety.core.pipeline` instead."""

import warnings as _warnings

_warnings.warn(
    "agent_safety.pipeline has moved to agent_safety.core.pipeline; "
    "this alias will be removed in a future release",
    DeprecationWarning,
    stacklevel=2,
)

from .core.pipeline import *  # noqa: E402,F401,F403
