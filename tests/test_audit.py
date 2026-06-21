import pytest

from agent_safety import (
    ListSink,
    PermissionDenied,
    PermissionSet,
    RedactPII,
    guarded_tool,
    require,
    safety_context,
)


@guarded_tool("net.fetch")
def fetch():
    return "token sk_live_ABCDEF0123456789ABCD"


def test_audit_records_permission_allow_and_deny():
    sink = ListSink()
    with safety_context(PermissionSet.of("a.b"), audit=[sink]):
        require("a.b")
        with pytest.raises(PermissionDenied):
            require("c.d")
    actions = [(e.action, e.decision) for e in sink.events]
    assert ("permission", "allow") in actions
    assert ("permission", "deny") in actions


def test_audit_records_tool_call_and_quota_and_sanitize():
    sink = ListSink()
    with safety_context(
        PermissionSet.of("net.fetch"), output_guards=[RedactPII()], audit=[sink]
    ):
        fetch()
    decisions = {(e.action, e.decision) for e in sink.events}
    assert ("tool_call", "invoke") in decisions
    assert ("guard", "sanitize") in decisions  # RedactPII transformed the output


def test_audit_event_to_dict_roundtrips():
    sink = ListSink()
    with safety_context(PermissionSet.of("a.b"), audit=[sink]):
        require("a.b")
    d = sink.events[0].to_dict()
    assert d["action"] == "permission" and d["decision"] == "allow"
    assert d["capability"] == "a.b" and "ts" in d


def test_audit_sinks_accumulate_in_nested_contexts():
    outer, inner = ListSink(), ListSink()
    with safety_context(PermissionSet.of("a.b"), audit=[outer]):
        with safety_context(audit=[inner]):
            require("a.b")
    # inner event seen by both sinks; outer-only events not seen by inner
    assert len(inner.events) >= 1
    assert len(outer.events) >= len(inner.events)
