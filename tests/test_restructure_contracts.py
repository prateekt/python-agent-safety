"""Contract tests for the 0.9 restructure.

Four promises the new layout makes:

1. ``PolicySpec`` is a *faithful* serializable form of ``safely`` — keywords
   round-trip through ``from_safely_kwargs`` / ``to_kwargs`` / ``to_dict``.
2. MCP calls run the full pipeline — the same allow/deny/budget outcomes as a
   local ``@tool`` function.
3. All four gates implement the one ``Gate`` protocol.
4. Every exception is machine-readable: stable ``code``, ``retryable``,
   ``to_dict()``.
"""

import asyncio

import pytest

from agent_safety import (
    Action,
    PolicySpec,
    guard_mcp,
    safely,
    safely_from_spec,
    tool,
)
from agent_safety.core import exceptions as exc
from agent_safety.core.gates import (
    ApprovalGate,
    ConstitutionGate,
    Gate,
    PreviewGate,
    ReasoningGate,
)

# -- 1. PolicySpec round-trip fidelity ---------------------------------------

KWARGS = dict(
    allow=["search", "fs.read"],
    deny=["fs.delete"],
    calls=10,
    tokens=1000,
    per_second=5,
    total_seconds=30.0,
    at_most=2,
    no_repeats=3,
    risk_budget=20,
    budget="$5",
    timeout=10.0,
    max_input=500,
    block=["rm -rf"],
    block_injections=True,
    clean_text=True,
    honeytoken="sk-CANARY",
    hide_secrets=True,
    monitor=True,
    log=True,
)


def test_spec_round_trips_every_declarative_keyword():
    spec = PolicySpec.from_safely_kwargs(**KWARGS)
    back = spec.to_kwargs()
    # Every keyword we put in comes back with equivalent meaning.
    assert sorted(back["allow"]) == sorted(KWARGS["allow"])
    assert sorted(back["deny"]) == sorted(KWARGS["deny"])
    for key in ("calls", "tokens", "per_second", "total_seconds", "at_most",
                "no_repeats", "risk_budget", "timeout", "max_input",
                "block_injections", "clean_text", "honeytoken", "hide_secrets",
                "monitor", "log"):
        assert back[key] == KWARGS[key] if not isinstance(KWARGS[key], list) else True
    assert list(back["block"]) == KWARGS["block"]
    assert back["budget"] == 5.0                     # "$5" parsed to dollars


def test_spec_survives_json_round_trip():
    spec = PolicySpec.from_safely_kwargs(**KWARGS)
    clone = PolicySpec.from_dict(spec.to_dict())
    assert clone == spec
    assert clone.policy_hash() == spec.policy_hash()


def test_spec_replay_enforces_like_the_original():
    @tool
    def ping():
        return "pong"

    spec = PolicySpec.from_safely_kwargs(allow="ping", calls=1)
    with safely_from_spec(spec):
        assert ping() == "pong"
        with pytest.raises(exc.QuotaExceeded):
            ping()                                   # calls=1 enforced
    with safely_from_spec(spec):
        with pytest.raises(exc.PermissionDenied):
            _other()                                 # never granted


@tool
def _other():
    return "nope"


# -- 2. MCP parity with local tools -------------------------------------------

class _Session:
    async def call_tool(self, name, arguments):
        return "remote-ok"


def _mcp(name, policy_kwargs, repeats=1):
    safe = guard_mcp(_Session())

    async def run():
        result = None
        with safely(**policy_kwargs):
            for _ in range(repeats):
                result = await safe.call_tool(name, {"q": "hello world"})
        return result

    return lambda: asyncio.run(run())


def _local(name, policy_kwargs, repeats=1):
    @tool(name)
    def local_tool(q):
        return "local-ok"

    def run():
        result = None
        with safely(**policy_kwargs):
            for _ in range(repeats):
                result = local_tool(q="hello world")
        return result

    return run


@pytest.mark.parametrize(
    "policy, repeats, expected",
    [
        (dict(allow="cap.read"), 1, None),                                   # allowed
        (dict(allow="other"), 1, exc.PermissionDenied),                      # denied
        (dict(allow="cap.read", calls=1), 2, exc.QuotaExceeded),             # budget
        (dict(allow="cap.read", max_input=3), 1, exc.GuardViolation),        # input guard
        (dict(allow="cap.read", ask=lambda a: False), 1, exc.ApprovalDenied),  # gate
    ],
)
def test_mcp_and_local_agree(policy, repeats, expected):
    for runner in (
        _local("cap.read", policy, repeats),
        _mcp("cap.read", policy, repeats),
    ):
        if expected is None:
            assert runner().endswith("-ok")
        else:
            with pytest.raises(expected):
                runner()


# -- 3. One Gate protocol ------------------------------------------------------

def test_all_gates_share_the_protocol():
    gates = [
        ApprovalGate(require=["x.*"], approver=lambda a: True),
        ReasoningGate(require=["x.*"]),
        ConstitutionGate(rules=["be nice"], judge=lambda a, r: True),
        PreviewGate(require=["x.*"], approver=lambda a, p: True),
    ]
    action = Action("x.do", "do", (), {})
    for gate in gates:
        assert isinstance(gate, Gate)
        assert gate.covers(action.capability) is True
        assert gate.covers("unrelated.cap") in (True, False)


# -- 4. Structured errors -------------------------------------------------------

def test_every_exception_has_stable_code_and_to_dict():
    subclasses = [
        exc.PermissionDenied, exc.QuotaExceeded, exc.GuardViolation,
        exc.ApprovalDenied, exc.RateLimitExceeded, exc.ConstitutionViolation,
        exc.HoneytokenTripped, exc.RiskBudgetExceeded, exc.CostBudgetExceeded,
        exc.TimeoutExceeded, exc.MemoryBudgetExceeded, exc.ExplanationRequired,
        exc.DeadlineExceeded, exc.RollbackError, exc.LoopDetected,
    ]
    codes = set()
    for cls in subclasses:
        assert issubclass(cls, exc.AgentSafetyError)
        assert cls.code != exc.AgentSafetyError.code, cls
        assert isinstance(cls.retryable, bool)
        codes.add(cls.code)
    assert len(codes) == len(subclasses)             # codes are unique


def test_error_to_dict_is_machine_readable():
    @tool("locked.cap")
    def locked():
        return "never"

    with safely(allow="nothing"):
        try:
            locked()
        except exc.PermissionDenied as e:
            d = e.to_dict()
            assert d["error"] == "permission_denied"
            assert d["retryable"] is False
            assert d["capability"] == "locked.cap"
            assert "locked.cap" in d["message"]
        else:
            pytest.fail("expected PermissionDenied")
