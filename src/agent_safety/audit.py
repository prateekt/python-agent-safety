"""Deprecated module location — use :mod:`agent_safety.core.audit` instead."""

import warnings as _warnings

_warnings.warn(
    "agent_safety.audit has moved to agent_safety.core.audit; "
    "this alias will be removed in a future release",
    DeprecationWarning,
    stacklevel=2,
)

from .core.audit import *  # noqa: E402,F401,F403
