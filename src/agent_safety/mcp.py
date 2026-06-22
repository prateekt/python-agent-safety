"""Run Model Context Protocol (MCP) tool calls through a safety policy.

MCP is the standard agents increasingly use to reach tools on a server. The tools
live on the *other side* of an MCP session, so the usual ``@guarded_tool`` doesn't
wrap them — but you can still govern every call. :func:`guard_mcp` wraps any
MCP-style session (anything with an ``async call_tool(name, arguments)``) so that
each call is permission-checked against the active ``safely(...)`` policy,
counted against its budgets, input-guarded, and audited — exactly like a local
tool.

    safe = guard_mcp(session)
    with safely(allow=["search", "fs.read"], calls=20, hide_secrets=True):
        result = await safe.call_tool("search", {"q": "agent safety"})
        # blocked tools raise PermissionDenied; budgets and input guards apply

By default a tool's MCP name *is* its capability (so ``allow="search"`` governs
the ``search`` tool); pass ``capability=`` to map names differently. There is no
dependency on the MCP SDK — the wrapper is duck-typed, so it works with the real
client or a stand-in.

Scope: every MCP call honours the policy-level gates — permission, quota / rate /
deadline budgets, approval, constitutional rules, loop detection, concurrency,
input guards, and audit. The per-*tool*-decorator features (a tool's ``risk``
weight, ``preview`` function, idempotency cache, and the reasoning ``rationale``)
have no MCP analogue and are not applied. Output guarding of a structured MCP
result is best-effort (applied only to plain-string results); redact on your side
if the server returns structured data.
"""

from __future__ import annotations

from contextlib import AsyncExitStack
from typing import Any, Callable, Dict, Mapping, Optional

from .approval import ApprovalRequest
from .audit import AuditEvent
from .context import current_policy


class SafeMCP:
    """Wraps an MCP session, enforcing the active policy on every tool call."""

    def __init__(self, session: Any, *, capability: Optional[Callable[[str], str]] = None):
        self._session = session
        self._capability = capability or (lambda name: name)

    async def call_tool(self, name: str, arguments: Optional[Mapping[str, Any]] = None) -> Any:
        """Run the call through the active policy, then dispatch to the server."""
        args: Dict[str, Any] = dict(arguments or {})
        capability = self._capability(name)
        policy = current_policy()
        if not policy.enforce:                      # monitor / dry-run mode
            policy.note_monitor(capability)
            return await self._session.call_tool(name, args)
        policy.charge_call()
        policy.require(capability)
        request = ApprovalRequest(capability, name, (), dict(args))
        await policy.check_approval_async(request)
        await policy.check_constitution_async(request)
        policy.check_loop(name, f"mcp:{name}:{tuple(sorted(args.items()))!r}")
        policy.audit(AuditEvent("tool_call", "invoke", capability=capability))
        guarded = {key: policy.check_input(value) for key, value in args.items()}
        async with AsyncExitStack() as stack:
            for limit in policy.concurrency_limits:
                await stack.enter_async_context(limit.hold_async())
            result = await self._session.call_tool(name, guarded)
        if isinstance(result, str):                 # best-effort output guarding
            return policy.check_output(result)
        return result

    def __getattr__(self, item: str) -> Any:
        # Pass through everything else (list_tools, close, ...) to the real session.
        return getattr(self._session, item)


def guard_mcp(session: Any, *, capability: Optional[Callable[[str], str]] = None) -> SafeMCP:
    """Wrap an MCP *session* so its tool calls obey the active ``safely`` policy."""
    return SafeMCP(session, capability=capability)
