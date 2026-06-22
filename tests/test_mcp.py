import asyncio

import pytest

from agent_safety import ListSink, guard_mcp, safely
from agent_safety.exceptions import (
    ApprovalDenied,
    ConstitutionViolation,
    GuardViolation,
    PermissionDenied,
)


class FakeSession:
    """A stand-in MCP session: records calls, echoes a result."""

    def __init__(self, result="ok"):
        self.calls = []
        self._result = result

    async def call_tool(self, name, arguments):
        await asyncio.sleep(0)
        self.calls.append((name, arguments))
        return self._result

    async def list_tools(self):
        return ["search", "fetch"]


def test_mcp_enforces_permission():
    session = FakeSession()
    safe = guard_mcp(session)

    async def run():
        with safely(allow="search"):
            assert await safe.call_tool("search", {"q": "x"}) == "ok"
            with pytest.raises(PermissionDenied):
                await safe.call_tool("delete", {})

    asyncio.run(run())
    assert session.calls == [("search", {"q": "x"})]   # the blocked call never reached the server


def test_mcp_enforces_approval():
    safe = guard_mcp(FakeSession())

    async def run():
        with safely(allow="*", ask=lambda req: False):
            await safe.call_tool("anything", {})

    with pytest.raises(ApprovalDenied):
        asyncio.run(run())


def test_mcp_enforces_constitution():
    safe = guard_mcp(FakeSession())

    async def run():
        with safely(allow="*", rule="no deletes", judge=lambda a, r: "delete" not in a.tool):
            await safe.call_tool("delete_db", {})

    with pytest.raises(ConstitutionViolation):
        asyncio.run(run())


def test_mcp_applies_input_guards():
    session = FakeSession()
    safe = guard_mcp(session)

    async def run():
        with safely(allow="echo", block="rm -rf"):     # a blocking input guard
            await safe.call_tool("echo", {"cmd": "please rm -rf /"})

    with pytest.raises(GuardViolation):
        asyncio.run(run())
    assert session.calls == []                          # blocked before reaching the server


def test_mcp_guards_string_results():
    safe = guard_mcp(FakeSession(result="contact a@b.com"))

    async def run():
        with safely(allow="x", hide_secrets=True):
            return await safe.call_tool("x", {})

    out = asyncio.run(run())
    assert "[REDACTED:EMAIL]" in out


def test_mcp_monitor_mode_runs_but_logs():
    audit = ListSink()
    safe = guard_mcp(FakeSession())

    async def run():
        with safely(allow="nothing", monitor=True, log=audit):
            return await safe.call_tool("danger", {})

    assert asyncio.run(run()) == "ok"              # not blocked in monitor mode
    assert ("permission", "would_deny") in [(e.action, e.decision) for e in audit.events]


def test_mcp_passes_through_other_methods():
    safe = guard_mcp(FakeSession())
    assert asyncio.run(safe.list_tools()) == ["search", "fetch"]
