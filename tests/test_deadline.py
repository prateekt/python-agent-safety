import pytest

from agent_safety import Deadline, DeadlineExceeded, PermissionSet, guarded_tool, safety_context


def test_deadline_starts_on_first_call_and_expires():
    d = Deadline(10)
    d.charge(now=100.0)            # starts the clock, no raise
    d.charge(now=105.0)           # within budget
    with pytest.raises(DeadlineExceeded) as ei:
        d.charge(now=111.0)       # 11s elapsed > 10s budget
    assert ei.value.budget == 10
    assert ei.value.elapsed == pytest.approx(11.0)


def test_deadline_remaining_and_reset():
    d = Deadline(10)
    assert d.remaining() == 10            # full budget before first call
    d.charge(now=0.0)
    assert d.remaining(now=4.0) == pytest.approx(6.0)
    d.reset()
    assert d.remaining() == 10


def test_deadline_rejects_nonpositive():
    with pytest.raises(ValueError):
        Deadline(0)


@guarded_tool("x.do")
def do_thing():
    return "done"


def test_deadline_in_context_allows_within_budget():
    # A generous budget: a couple of instant calls never exceed it.
    with safety_context(PermissionSet.of("x.do"), deadline=Deadline(60)):
        assert do_thing() == "done"
        assert do_thing() == "done"
