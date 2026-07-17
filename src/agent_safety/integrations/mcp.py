"""Run Model Context Protocol (MCP) tool calls through a safety policy.

MCP is the standard agents increasingly use to reach tools on a server. The tools
live on the *other side* of an MCP session, so the usual ``@tool`` decorator
doesn't wrap them — but you can still govern every call. :func:`guard_mcp` wraps
any MCP-style session (anything with an ``async call_tool(name, arguments)``) so
that each call runs through the **same enforcement pipeline as a local tool**
(:mod:`agent_safety.pipeline`): permission-checked against the active
``safely(...)`` policy, counted against its budgets, gated, input-guarded,
timeout-bounded, output-guarded, and audited.

    safe = guard_mcp(session)
    with safely(allow=["search", "fs.read"], calls=20, hide_secrets=True):
        result = await safe.call_tool("search", {"q": "agent safety"})
        # blocked tools raise PermissionDenied; budgets and guards apply

By default a tool's MCP name *is* its capability (so ``allow="search"`` governs
the ``search`` tool); pass ``capability=`` to map names differently. There is no
dependency on the MCP SDK — the wrapper is duck-typed, so it works with the real
client or a stand-in.

Output guards apply to string results directly; structured results pass through
the same guards, which by design only transform strings — redact on your side if
the server returns secrets buried in structured data.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Mapping, Optional

from ..core.pipeline import ToolCallSpec, arun_tool_call


class SafeMCP:
    """Wraps an MCP session, enforcing the active policy on every tool call."""

    def __init__(self, session: Any, *, capability: Optional[Callable[[str], str]] = None):
        self._session = session
        self._capability = capability or (lambda name: name)
        self._specs: Dict[str, ToolCallSpec] = {}

    def _spec(self, name: str) -> ToolCallSpec:
        spec = self._specs.get(name)
        if spec is None:
            spec = ToolCallSpec.for_tool(self._capability(name), name)
            self._specs[name] = spec
        return spec

    async def call_tool(self, name: str, arguments: Optional[Mapping[str, Any]] = None) -> Any:
        """Run the call through the full enforcement pipeline, then dispatch."""
        args: Dict[str, Any] = dict(arguments or {})

        async def dispatch(**kwargs: Any) -> Any:
            return await self._session.call_tool(name, kwargs)

        return await arun_tool_call(self._spec(name), dispatch, (), args)

    def __getattr__(self, item: str) -> Any:
        # Pass through everything else (list_tools, close, ...) to the real session.
        return getattr(self._session, item)


def guard_mcp(session: Any, *, capability: Optional[Callable[[str], str]] = None) -> SafeMCP:
    """Wrap an MCP *session* so its tool calls obey the active ``safely`` policy."""
    return SafeMCP(session, capability=capability)
