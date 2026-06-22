import asyncio

import pytest

from agent_safety import (
    ExplanationRequired,
    ListSink,
    PermissionSet,
    ReasoningGate,
    current_trace,
    guarded_async_tool,
    guarded_tool,
    record_thought,
    safety_context,
    thought_trace,
)


@guarded_tool("shell.exec")
def run_shell(cmd: str) -> str:
    return f"$ {cmd}"


@guarded_tool("data.read")
def read_data(key: str) -> str:
    return f"value:{key}"


def test_rationale_required_and_passes():
    gate = ReasoningGate(require=["shell.exec"])
    with safety_context(PermissionSet.of("shell.exec"), reasoning=gate):
        assert run_shell("ls", rationale="listing the build dir to verify output") == "$ ls"


def test_missing_rationale_raises():
    gate = ReasoningGate(require=["shell.exec"])
    with safety_context(PermissionSet.of("shell.exec"), reasoning=gate):
        with pytest.raises(ExplanationRequired):
            run_shell("ls")


def test_short_rationale_rejected_by_min_length():
    gate = ReasoningGate(require=["shell.exec"], min_length=15)
    with safety_context(PermissionSet.of("shell.exec"), reasoning=gate):
        with pytest.raises(ExplanationRequired):
            run_shell("ls", rationale="why")


def test_validator_can_reject():
    gate = ReasoningGate(require=["shell.exec"], validator=lambda r, req: "because" in r.lower())
    with safety_context(PermissionSet.of("shell.exec"), reasoning=gate):
        assert run_shell("ls", rationale="Because I must check output")
        with pytest.raises(ExplanationRequired):
            run_shell("ls", rationale="just running it")


def test_rationale_stripped_before_tool_runs():
    # The underlying function has no `rationale` param; it must not receive one.
    gate = ReasoningGate(require=["data.read"])
    with safety_context(PermissionSet.of("data.read"), reasoning=gate):
        assert read_data("k", rationale="need this key for the report") == "value:k"


def test_uncovered_capability_keeps_rationale_arg():
    # No gate covers data.read here, so `rationale` is NOT reserved; if a tool had
    # such a param it would pass through. Our tool doesn't, so passing it errors.
    with safety_context(PermissionSet.of("data.read")):
        with pytest.raises(TypeError):
            read_data("k", rationale="x")  # not intercepted -> unexpected kwarg


def test_reasoning_is_audited():
    audit = ListSink()
    gate = ReasoningGate(require=["shell.exec"])
    with safety_context(PermissionSet.of("shell.exec"), reasoning=gate, audit=[audit]):
        run_shell("ls", rationale="checking output before deploy")
    decisions = [(e.action, e.decision) for e in audit.events]
    assert ("reasoning", "recorded") in decisions


def test_async_tool_requires_rationale():
    @guarded_async_tool("net.fetch")
    async def fetch(url: str) -> str:
        await asyncio.sleep(0)
        return url

    gate = ReasoningGate(require=["net.fetch"])

    async def run(with_reason):
        with safety_context(PermissionSet.of("net.fetch"), reasoning=gate):
            if with_reason:
                return await fetch("http://x", rationale="fetching the manifest to parse it")
            return await fetch("http://x")

    assert asyncio.run(run(True)) == "http://x"
    with pytest.raises(ExplanationRequired):
        asyncio.run(run(False))


def test_empty_gate_rejected():
    with pytest.raises(ValueError):
        ReasoningGate(require=[])


# -- thought trace --------------------------------------------------------

def test_thought_trace_collects_and_audits():
    audit = ListSink()
    with safety_context(PermissionSet.allow_all(), audit=[audit]):
        with thought_trace() as trace:
            record_thought("first I will read the config")
            record_thought("then I will summarize it")
            assert current_trace() is trace
    assert [t.text for t in trace] == [
        "first I will read the config",
        "then I will summarize it",
    ]
    assert ("thought", "record") in [(e.action, e.decision) for e in audit.events]


def test_record_thought_outside_trace_is_safe():
    # No active trace -> still safe (and still audited if a sink is present).
    with safety_context(PermissionSet.allow_all()):
        record_thought("a stray thought")  # does not raise
    assert current_trace() is None
