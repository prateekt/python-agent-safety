"""Deprecated module location — the decorators live in :mod:`agent_safety.core.pipeline`.

Prefer ``@tool`` from :mod:`agent_safety` — it replaces both ``guarded_tool``
and ``guarded_async_tool``.
"""

import warnings as _warnings

_warnings.warn(
    "agent_safety.decorators has moved to agent_safety.core.pipeline; "
    "prefer @tool from agent_safety (it replaces guarded_tool/guarded_async_tool)",
    DeprecationWarning,
    stacklevel=2,
)

from .core.pipeline import (  # noqa: E402,F401
    guarded_async_tool,
    guarded_tool,
    make_tool_wrapper,
)
