import asyncio

import pytest

from agent_safety import (
    MaxLength,
    PermissionDenied,
    PermissionSet,
    RedactPII,
    guarded_async_tool,
    safety_context,
)
from agent_safety.exceptions import GuardViolation


@guarded_async_tool("net.fetch", input_guards=[MaxLength(20)], output_guards=[RedactPII()])
async def fetch(url: str) -> str:
    await asyncio.sleep(0)
    return f"{url} -> contact a@b.com"


def test_async_tool_denied_without_context():
    async def run():
        await fetch("http://x")

    with pytest.raises(PermissionDenied):
        asyncio.run(run())


def test_async_tool_allowed_and_output_guarded():
    async def run():
        with safety_context(PermissionSet.of("net.fetch")):
            return await fetch("http://x")

    out = asyncio.run(run())
    assert "a@b.com" not in out
    assert "[REDACTED:EMAIL]" in out


def test_async_tool_input_guard():
    async def run():
        with safety_context(PermissionSet.of("net.fetch")):
            await fetch("http://this-url-is-definitely-too-long")

    with pytest.raises(GuardViolation):
        asyncio.run(run())


def test_async_context_isolation_between_tasks():
    # Each task carries its own policy via contextvars.
    async def privileged():
        with safety_context(PermissionSet.of("net.fetch")):
            return await fetch("http://ok")

    async def unprivileged():
        with safety_context(PermissionSet.deny_all()):
            try:
                await fetch("http://x")
                return "allowed"
            except PermissionDenied:
                return "denied"

    async def run():
        return await asyncio.gather(privileged(), unprivileged())

    ok, denied = asyncio.run(run())
    assert "[REDACTED:EMAIL]" in ok
    assert denied == "denied"
