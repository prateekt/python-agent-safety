import pytest

from agent_safety import (
    PermissionSet,
    Quota,
    QuotaExceeded,
    charge_tokens,
    guarded_tool,
    safety_context,
)


def test_quota_charges_calls():
    q = Quota(max_calls=2)
    q.charge_call()
    q.charge_call()
    with pytest.raises(QuotaExceeded) as ei:
        q.charge_call()
    assert ei.value.resource == "calls"


def test_quota_charges_tokens():
    q = Quota(max_tokens=100)
    q.charge_tokens(60)
    q.charge_tokens(40)
    with pytest.raises(QuotaExceeded):
        q.charge_tokens(1)


def test_quota_remaining():
    q = Quota(max_calls=5, max_tokens=1000)
    q.charge_call(2)
    q.charge_tokens(250)
    assert q.remaining_calls() == 3
    assert q.remaining_tokens() == 750


def test_quota_none_is_unlimited():
    q = Quota()
    for _ in range(1000):
        q.charge_call()
    assert q.remaining_calls() is None


@guarded_tool("x.do")
def do_thing():
    return "done"


def test_guarded_tool_charges_context_quota():
    q = Quota(max_calls=2)
    with safety_context(PermissionSet.of("x.do"), quota=q):
        do_thing()
        do_thing()
        with pytest.raises(QuotaExceeded):
            do_thing()
    # the third call is rejected by charge_call before incrementing, so used == 2
    assert q.calls_used == 2


def test_nested_quotas_both_charged():
    outer = Quota(max_calls=10)
    inner = Quota(max_calls=1)
    with safety_context(PermissionSet.of("x.do"), quota=outer):
        do_thing()
        with safety_context(quota=inner):
            do_thing()
            with pytest.raises(QuotaExceeded):
                do_thing()  # inner exhausted
    # outer is charged before the inner quota rejects, so the attempt counts: 3
    assert outer.calls_used == 3
    assert inner.calls_used == 1


def test_charge_tokens_helper():
    q = Quota(max_tokens=50)
    with safety_context(PermissionSet.allow_all(), quota=q):
        charge_tokens(30)
        with pytest.raises(QuotaExceeded):
            charge_tokens(30)
