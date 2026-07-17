"""Deprecated module location — use :mod:`agent_safety.integrations.mcp` instead."""

import warnings as _warnings

_warnings.warn(
    "agent_safety.mcp has moved to agent_safety.integrations.mcp; "
    "this alias will be removed in a future release",
    DeprecationWarning,
    stacklevel=2,
)

from .integrations.mcp import *  # noqa: E402,F401,F403
