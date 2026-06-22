import asyncio

import pytest

from agent_safety import safely, tool
from agent_safety.exceptions import (
    ApprovalDenied,
    ExplanationRequired,
    GuardViolation,
    LoopDetected,
    PermissionDenied,
    QuotaExceeded,
    RateLimitExceeded,
)


@tool
def read_file(path: str) -> str:
    return f"contents of {path}; email jane@corp.com"


@tool
def delete_all() -> str:
    return "gone"


# -- @tool ----------------------------------------------------------------

def test_tool_defaults_capability_to_function_name():
    with safely(allow="read_file"):
        assert read_file("a.txt").startswith("contents of a.txt")


def test_tool_custom_capability():
    @tool("danger.zone")
    def risky() -> str:
        return "ok"

    with safely(allow="danger.zone"):
        assert risky() == "ok"
    with safely(allow="something.else"):
        with pytest.raises(PermissionDenied):
            risky()


def test_tool_on_async_function():
    @tool
    async def fetch(url: str) -> str:
        await asyncio.sleep(0)
        return url

    async def run():
        with safely(allow="fetch"):
            return await fetch("http://ok")

    assert asyncio.run(run()) == "http://ok"


# -- allow / deny ---------------------------------------------------------

def test_allow_grants_only_named():
    with safely(allow="read_file"):
        read_file("x")
        with pytest.raises(PermissionDenied):
            delete_all()


def test_allow_everything():
    with safely(allow="everything"):
        assert delete_all() == "gone"


def test_deny_only_allows_the_rest():
    with safely(deny="delete_all"):
        read_file("x")                  # allowed (everything except the denied)
        with pytest.raises(PermissionDenied):
            delete_all()


def test_allow_list():
    with safely(allow=["read_file", "delete_all"]):
        read_file("x")
        delete_all()


# -- budgets --------------------------------------------------------------

def test_calls_budget():
    with safely(allow="read_file", calls=2):
        read_file("a")
        read_file("b")
        with pytest.raises(QuotaExceeded):
            read_file("c")


def test_per_second_rate_limit():
    with safely(allow="read_file", per_second=2):
        read_file("a")
        read_file("b")
        with pytest.raises(RateLimitExceeded):
            read_file("c")


def test_no_repeats():
    with safely(allow="read_file", no_repeats=2):
        read_file("same")
        read_file("same")
        with pytest.raises(LoopDetected):
            read_file("same")


# -- content rules --------------------------------------------------------

def test_hide_secrets():
    with safely(allow="read_file", hide_secrets=True):
        out = read_file("x")
        assert "jane@corp.com" not in out
        assert "[REDACTED:EMAIL]" in out


def test_block_pattern_and_max_input():
    with safely(allow="read_file", block="rm -rf"):
        with pytest.raises(GuardViolation):
            read_file("please rm -rf /")
    with safely(allow="read_file", max_input=5):
        with pytest.raises(GuardViolation):
            read_file("way too long")


# -- ask / explain / log --------------------------------------------------

def test_ask_with_custom_approver():
    with safely(allow="delete_all", ask=lambda req: False):
        with pytest.raises(ApprovalDenied):
            delete_all()
    with safely(allow="delete_all", ask=lambda req: True):
        assert delete_all() == "gone"


def test_explain_requires_rationale():
    with safely(allow="read_file", explain=True):
        read_file("x", rationale="I need this file to answer the question")
        with pytest.raises(ExplanationRequired):
            read_file("x")


def test_log_collects_into_a_list():
    events = []
    with safely(allow="read_file", log=events.append):
        read_file("x")
    actions = [(e.action, e.decision) for e in events]
    assert ("permission", "allow") in actions
    assert ("tool_call", "invoke") in actions


def test_no_rules_allows_everything_at_top_level():
    # An empty safely() is the trusted-host bootstrap: it permits, but still wraps.
    with safely():
        assert read_file("x").startswith("contents")
