import asyncio

import pytest

from agent_safety import (
    ApprovalDenied,
    ApprovalGate,
    ApprovalRequest,
    ListSink,
    PermissionDenied,
    PermissionSet,
    ToolRegistry,
    guarded_async_tool,
    guarded_tool,
    safety_context,
)


@guarded_tool("shell.exec")
def run_shell(cmd: str) -> str:
    return f"$ {cmd}"


@guarded_tool("filesystem.read")
def read_file(path: str) -> str:
    return f"contents of {path}"


def test_approval_allows_when_approver_says_yes():
    gate = ApprovalGate(require=["shell.exec"], approver=lambda req: True)
    with safety_context(PermissionSet.of("shell.exec"), approval=gate):
        assert run_shell("ls") == "$ ls"


def test_approval_blocks_when_approver_says_no():
    gate = ApprovalGate(require=["shell.exec"], approver=lambda req: False,
                        reason="needs a human")
    with safety_context(PermissionSet.of("shell.exec"), approval=gate):
        with pytest.raises(ApprovalDenied) as ei:
            run_shell("rm -rf /")
    assert ei.value.tool == "run_shell"
    assert ei.value.capability == "shell.exec"
    assert "human" in ei.value.reason


def test_approval_only_gates_listed_capabilities():
    calls = []
    gate = ApprovalGate(require=["shell.exec"], approver=lambda req: calls.append(req) or True)
    with safety_context(PermissionSet.of("shell.exec", "filesystem.read"), approval=gate):
        read_file("a.txt")            # not covered -> approver never consulted
        run_shell("ls")               # covered
    assert len(calls) == 1
    assert calls[0].capability == "shell.exec"


def test_approval_request_carries_args():
    seen = {}

    def approver(req: ApprovalRequest) -> bool:
        seen["req"] = req
        return True

    gate = ApprovalGate(require=["shell.*"], approver=approver)
    with safety_context(PermissionSet.of("shell.exec"), approval=gate):
        run_shell("whoami")
    assert seen["req"].tool == "run_shell"
    assert seen["req"].args == ("whoami",)


def test_permission_denied_before_approval():
    # If the capability isn't granted at all, the approver is never reached.
    consulted = []
    gate = ApprovalGate(require=["shell.exec"], approver=lambda r: consulted.append(r) or True)
    with safety_context(PermissionSet.of("filesystem.read"), approval=gate):
        with pytest.raises(PermissionDenied):
            run_shell("ls")
    assert consulted == []


def test_approval_is_audited():
    audit = ListSink()
    gate = ApprovalGate(require=["shell.exec"], approver=lambda r: False)
    with safety_context(PermissionSet.of("shell.exec"), approval=gate, audit=[audit]):
        with pytest.raises(ApprovalDenied):
            run_shell("ls")
    decisions = [(e.action, e.decision) for e in audit.events]
    assert ("approval", "deny") in decisions


def test_approval_denied_via_safe_dispatch_returns_error():
    registry = ToolRegistry()

    @registry.tool("shell.exec", description="run", parameters={"type": "object"})
    def run(cmd: str) -> str:
        return cmd

    gate = ApprovalGate(require=["shell.exec"], approver=lambda r: False)
    with safety_context(PermissionSet.of("shell.exec"), approval=gate):
        msg = registry.safe_dispatch("openai", "id1", "run", '{"cmd": "ls"}')
    assert msg["role"] == "tool"
    assert "denied" in msg["content"]


# -- async approvers ------------------------------------------------------

@guarded_async_tool("net.fetch")
async def fetch(url: str) -> str:
    await asyncio.sleep(0)
    return f"got {url}"


def test_async_approver_allows():
    async def approver(req):
        await asyncio.sleep(0)
        return True

    gate = ApprovalGate(require=["net.fetch"], approver=approver)

    async def run():
        with safety_context(PermissionSet.of("net.fetch"), approval=gate):
            return await fetch("http://x")

    assert asyncio.run(run()) == "got http://x"


def test_async_approver_denies():
    async def approver(req):
        return False

    gate = ApprovalGate(require=["net.fetch"], approver=approver)

    async def run():
        with safety_context(PermissionSet.of("net.fetch"), approval=gate):
            await fetch("http://x")

    with pytest.raises(ApprovalDenied):
        asyncio.run(run())


def test_async_approver_on_sync_tool_raises_runtime_error():
    async def approver(req):
        return True

    gate = ApprovalGate(require=["shell.exec"], approver=approver)
    with safety_context(PermissionSet.of("shell.exec"), approval=gate):
        with pytest.raises(RuntimeError):
            run_shell("ls")  # sync tool can't await an async approver


def test_sync_approver_works_on_async_tool():
    gate = ApprovalGate(require=["net.fetch"], approver=lambda r: True)

    async def run():
        with safety_context(PermissionSet.of("net.fetch"), approval=gate):
            return await fetch("http://ok")

    assert asyncio.run(run()) == "got http://ok"


def test_empty_gate_rejected():
    with pytest.raises(ValueError):
        ApprovalGate(require=[], approver=lambda r: True)
