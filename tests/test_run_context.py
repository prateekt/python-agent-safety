"""Tests for audit hash chain and run context stamping."""

from agent_safety import safely, tool
from agent_safety.audit import AuditEvent, HashChainSink, ListSink
from agent_safety.run import RunContext, run_context


@tool
def echo(msg: str) -> str:
    return msg


def test_hash_chain_links_events():
    inner = ListSink()
    chain = HashChainSink(inner)
    chain(AuditEvent("permission", "allow", capability="x"))
    chain(AuditEvent("tool_call", "invoke", capability="x"))
    assert chain.events[0].prev_hash == "0" * 64
    assert chain.events[1].prev_hash == chain.events[0].event_hash
    assert len(chain.head_hash) == 64


def test_audit_stamps_run_context():
    sink = ListSink()
    ctx = RunContext.new(agent_id="worker", org_id="acme")

    with run_context(ctx):
        with safely(allow="echo", log=sink):
            echo("hi")

    assert any(e.task_id == ctx.task_id for e in sink.events)
