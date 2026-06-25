"""Govern an MCP server's tools with the same safely(...) policy.

    python examples/mcp_agent.py

MCP tools live on the *other side* of a session, so guard_mcp(session) wraps the
session itself: every call_tool runs through the active policy first — permission-
checked, budget-counted, input-guarded, and audited — before it reaches the server.
No MCP SDK dependency: the wrapper is duck-typed against `async call_tool(...)`, so
a stand-in works exactly like the real client (swap in your real session and it's
identical).
"""

import asyncio

from agent_safety import guard_mcp, safely
from agent_safety.exceptions import PermissionDenied, QuotaExceeded


class FakeMCPSession:
    """Stand-in for a real MCP client session (anything with `async call_tool`)."""

    async def call_tool(self, name, arguments):
        if name == "search":
            return f"top hit for {arguments.get('q')!r}: agent_safety governs MCP too"
        if name == "notes.read":
            return "meeting notes — ping bob@secret.com to confirm the rollout"
        return f"{name}({dict(arguments)}) done"

    async def list_tools(self):            # any non-tool method passes straight through
        return ["search", "notes.read", "shell.exec"]


async def main():
    safe = guard_mcp(FakeMCPSession())     # wrap once; same object, now governed

    print("1) Allowed tools run, and secrets in the result are scrubbed:")
    with safely(allow=["search", "notes.read"], hide_secrets=True, log=True):
        print("  ", await safe.call_tool("search", {"q": "least privilege"}))
        print("  ", await safe.call_tool("notes.read", {}))     # the email is redacted

    print("\n2) A tool we didn't allow is blocked before it reaches the server:")
    with safely(allow=["search"]):
        try:
            await safe.call_tool("shell.exec", {"cmd": "rm -rf /"})
        except PermissionDenied:
            print("   shell.exec was not allowed. Blocked.")

    print("\n3) Budgets apply to MCP calls too — only 2:")
    with safely(allow=["search"], calls=2):
        await safe.call_tool("search", {"q": "one"})
        await safe.call_tool("search", {"q": "two"})
        try:
            await safe.call_tool("search", {"q": "three"})
        except QuotaExceeded as e:
            print("   out of budget:", e)

    print("\n4) Non-tool methods pass straight through to the real session:")
    print("   list_tools ->", await safe.list_tools())


asyncio.run(main())
