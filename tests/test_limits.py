import pytest

from agent_safety import (
    LoopDetected,
    LoopGuard,
    PermissionSet,
    RateLimit,
    RateLimitExceeded,
    guarded_tool,
    safety_context,
)

# -- RateLimit (deterministic via the `now=` hook) ------------------------

def test_rate_limit_allows_up_to_limit_then_blocks():
    rl = RateLimit(per_second=3)
    rl.charge(now=0.0)
    rl.charge(now=0.0)
    rl.charge(now=0.0)
    with pytest.raises(RateLimitExceeded) as ei:
        rl.charge(now=0.0)
    assert ei.value.limit == 3
    assert ei.value.retry_after == pytest.approx(1.0)


def test_rate_limit_window_slides():
    rl = RateLimit(per_second=2)
    rl.charge(now=0.0)
    rl.charge(now=0.0)
    with pytest.raises(RateLimitExceeded):
        rl.charge(now=0.5)
    # once the window has passed, the old calls age out
    rl.charge(now=1.0)
    rl.charge(now=1.0)


def test_rate_limit_retry_after_is_partial():
    rl = RateLimit(per_second=1)
    rl.charge(now=10.0)
    with pytest.raises(RateLimitExceeded) as ei:
        rl.charge(now=10.25)
    assert ei.value.retry_after == pytest.approx(0.75)


def test_rate_limit_remaining():
    rl = RateLimit(max_calls=5, per_seconds=2.0)
    rl.charge(now=0.0)
    rl.charge(now=0.0)
    assert rl.remaining(now=0.0) == 3


def test_rate_limit_constructors():
    assert RateLimit(per_minute=60).window == 60.0
    assert RateLimit(per_second=5).limit == 5
    assert RateLimit(max_calls=10, per_seconds=1.5).window == 1.5


def test_rate_limit_requires_spec():
    with pytest.raises(ValueError):
        RateLimit()


@guarded_tool("x.do")
def do_thing():
    return "done"


def test_rate_limit_in_context():
    # Three calls land effectively instantly, well within one second.
    rl = RateLimit(per_second=2)
    with safety_context(PermissionSet.of("x.do"), rate_limit=rl):
        do_thing()
        do_thing()
        with pytest.raises(RateLimitExceeded):
            do_thing()


# -- LoopGuard ------------------------------------------------------------

def test_loop_guard_trips_on_repeats():
    lg = LoopGuard(max_identical=2)
    lg.record("tool", "sig-A")
    lg.record("tool", "sig-A")
    with pytest.raises(LoopDetected) as ei:
        lg.record("tool", "sig-A")
    assert ei.value.tool == "tool"
    assert ei.value.limit == 2
    assert ei.value.count == 3


def test_loop_guard_distinct_calls_dont_trip():
    lg = LoopGuard(max_identical=2)
    for i in range(10):
        lg.record("tool", f"sig-{i}")  # all different -> never trips


def test_loop_guard_validates_args():
    with pytest.raises(ValueError):
        LoopGuard(max_identical=0)
    with pytest.raises(ValueError):
        LoopGuard(max_identical=5, history=5)


@guarded_tool("y.do")
def do_with(value):
    return value


def test_loop_guard_in_context():
    lg = LoopGuard(max_identical=2)
    with safety_context(PermissionSet.of("y.do"), loop_guard=lg):
        do_with(1)
        do_with(1)
        with pytest.raises(LoopDetected):
            do_with(1)              # third identical call trips
        do_with(2)                  # different args -> fine


def test_rate_limit_and_loop_audited():
    from agent_safety import ListSink

    audit = ListSink()
    with safety_context(
        PermissionSet.of("x.do"), rate_limit=RateLimit(per_second=1), audit=[audit]
    ):
        do_thing()
        with pytest.raises(RateLimitExceeded):
            do_thing()
    assert ("rate_limit", "deny") in [(e.action, e.decision) for e in audit.events]
