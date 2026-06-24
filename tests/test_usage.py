import asyncio
from types import SimpleNamespace as NS

import pytest

from agent_safety import (
    PermissionSet,
    Quota,
    charge_usage,
    extract_tokens,
    metered,
    safety_context,
)
from agent_safety.exceptions import QuotaExceeded

# -- extract_tokens across provider shapes --------------------------------

def test_extract_gemini():
    assert extract_tokens(NS(usage_metadata=NS(total_token_count=150))) == 150


def test_extract_openai():
    assert extract_tokens(NS(usage=NS(total_tokens=200))) == 200


def test_extract_openai_split_fields():
    assert extract_tokens(NS(usage=NS(prompt_tokens=120, completion_tokens=80))) == 200


def test_extract_anthropic_input_plus_output():
    assert extract_tokens(NS(usage=NS(input_tokens=100, output_tokens=50))) == 150


def test_extract_from_dict():
    assert extract_tokens({"usage": {"total_tokens": 75}}) == 75
    assert extract_tokens({"usage_metadata": {"total_token_count": 42}}) == 42


def test_extract_unknown_returns_none():
    assert extract_tokens(NS(text="hi")) is None
    assert extract_tokens({"foo": "bar"}) is None
    assert extract_tokens("just a string") is None


def test_extract_ignores_bools():
    # a stray bool must not be read as a token count
    assert extract_tokens(NS(usage=NS(total_tokens=True))) is None


# -- charge_usage ---------------------------------------------------------

def test_charge_usage_charges_active_quota():
    q = Quota(max_tokens=1000)
    with safety_context(quota=q):
        charged = charge_usage(NS(usage=NS(input_tokens=100, output_tokens=50)))
    assert charged == 150
    assert q.tokens_used == 150


def test_charge_usage_no_tokens_is_noop():
    q = Quota(max_tokens=1000)
    with safety_context(quota=q):
        assert charge_usage(NS(text="no usage here")) == 0
    assert q.tokens_used == 0


def test_charge_usage_enforces_budget():
    q = Quota(max_tokens=100)
    with safety_context(quota=q):
        with pytest.raises(QuotaExceeded):
            charge_usage(NS(usage=NS(total_tokens=200)))


# -- metered --------------------------------------------------------------

def test_metered_charges_call_and_tokens():
    def fake_model(prompt):
        return NS(usage=NS(total_tokens=300), text=f"re: {prompt}")

    ask = metered(fake_model)
    q = Quota(max_calls=10, max_tokens=1000)
    with safety_context(PermissionSet.allow_all(), quota=q):
        resp = ask("hello")

    assert resp.text == "re: hello"        # the response passes through
    assert q.calls_used == 1               # one call charged
    assert q.tokens_used == 300            # tokens charged automatically


def test_metered_charges_call_before_request():
    # if the call budget is already spent, the request is never made
    made = {"called": False}

    def fake_model(_):
        made["called"] = True
        return NS(usage=NS(total_tokens=1))

    ask = metered(fake_model)
    q = Quota(max_calls=1)
    with safety_context(PermissionSet.allow_all(), quota=q):
        ask("first")                       # uses the one allowed call
        with pytest.raises(QuotaExceeded):
            ask("second")
    assert made["called"] is True          # only the first actually ran


def test_metered_async():
    async def afetch(prompt):
        await asyncio.sleep(0)
        return NS(usage=NS(input_tokens=10, output_tokens=5), text=prompt)

    ask = metered(afetch)

    async def run():
        q = Quota(max_calls=5, max_tokens=1000)
        with safety_context(PermissionSet.allow_all(), quota=q):
            resp = await ask("x")
            return resp.text, q.calls_used, q.tokens_used

    assert asyncio.run(run()) == ("x", 1, 15)
