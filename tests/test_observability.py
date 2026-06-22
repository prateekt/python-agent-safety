from agent_safety import (
    Explanation,
    MetricsSink,
    PermissionSet,
    Policy,
    current_span,
    guarded_tool,
    safety_context,
    trace_span,
)
from agent_safety.audit import AuditEvent


@guarded_tool("x.do")
def do_thing():
    return "done"


# -- trace_span -----------------------------------------------------------

def test_span_nesting_and_path():
    assert current_span() is None
    with trace_span("plan"):
        assert current_span() == "plan"
        with trace_span("search"):
            assert current_span() == "plan.search"
        assert current_span() == "plan"
    assert current_span() is None


def test_audit_events_are_stamped_with_span():
    events = []
    with safety_context(PermissionSet.of("x.do"), audit=[events.append]):
        with trace_span("step1"):
            do_thing()
    spans = {e.span for e in events if e.action == "tool_call"}
    assert spans == {"step1"}


# -- MetricsSink ----------------------------------------------------------

def test_metrics_sink_counts():
    m = MetricsSink()
    with safety_context(PermissionSet.of("x.do"), audit=[m]):
        do_thing()
        do_thing()
    assert m.counts["permission/allow"] == 2
    assert m.counts["tool_call/invoke"] == 2
    assert m.total("permission") == 2
    assert m.total() >= 4


# -- Policy.explain -------------------------------------------------------

def test_explain_allowed_denied_and_default():
    p = Policy(permissions=PermissionSet.of("filesystem.*", deny=["filesystem.delete"]))

    allowed = p.explain("filesystem.read")
    assert isinstance(allowed, Explanation)
    assert allowed.allowed and "filesystem.*" in allowed.reason

    denied = p.explain("filesystem.delete")
    assert not denied.allowed and "filesystem.delete" in denied.reason

    missing = p.explain("network.http")
    assert not missing.allowed and "default-deny" in missing.reason


def test_audit_event_dict_includes_span_when_set():
    e = AuditEvent("tool_call", "invoke", span="plan.search")
    assert e.to_dict()["span"] == "plan.search"
    assert "span" not in AuditEvent("tool_call", "invoke").to_dict()
