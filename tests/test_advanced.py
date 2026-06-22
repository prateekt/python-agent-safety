import asyncio

import pytest

from agent_safety import (
    Honeytoken,
    ListSink,
    Stage,
    guarded_async_tool,
    safely,
    tool,
)
from agent_safety.exceptions import (
    ApprovalDenied,
    ConstitutionViolation,
    HoneytokenTripped,
    RiskBudgetExceeded,
)

# -- constitutional rules -------------------------------------------------

@tool
def send_email(to):
    return f"sent to {to}"


def test_constitution_allows_and_blocks():
    def judge(action, rule):
        return action.kwargs.get("to", "").endswith("@corp.com")

    with safely(allow="send_email", rule="only email internal addresses", judge=judge):
        assert send_email(to="ceo@corp.com") == "sent to ceo@corp.com"
        with pytest.raises(ConstitutionViolation) as ei:
            send_email(to="stranger@evil.com")
    assert ei.value.rule == "only email internal addresses"


def test_constitution_is_audited():
    audit = ListSink()
    with safely(allow="send_email", rule="r", judge=lambda a, r: False, log=audit):
        with pytest.raises(ConstitutionViolation):
            send_email(to="x")
    assert ("constitution", "deny") in [(e.action, e.decision) for e in audit.events]


def test_rule_without_judge_is_rejected():
    with pytest.raises(TypeError):
        with safely(allow="send_email", rule="something"):
            pass


def test_async_judge_on_sync_tool_raises_runtime_error():
    async def judge(action, rule):
        return True

    with safely(allow="send_email", rule="r", judge=judge):
        with pytest.raises(RuntimeError):
            send_email(to="x")


def test_async_judge_on_async_tool():
    @guarded_async_tool("net.act")
    async def act(x):
        await asyncio.sleep(0)
        return x

    async def judge(action, rule):
        await asyncio.sleep(0)
        return action.args == (1,)

    async def run(val):
        with safely(allow="net.act", rule="only 1 allowed", judge=judge):
            return await act(val)

    assert asyncio.run(run(1)) == 1
    with pytest.raises(ConstitutionViolation):
        asyncio.run(run(2))


# -- honeytoken -----------------------------------------------------------

def test_honeytoken_guard_trips():
    g = Honeytoken("sk-CANARY-1", label="aws")
    assert g.check("nothing here", Stage.INPUT) == "nothing here"
    with pytest.raises(HoneytokenTripped) as ei:
        g.check("leak sk-CANARY-1 now", Stage.INPUT)
    assert ei.value.label == "aws"


def test_honeytoken_via_safely_catches_exfiltration():
    @tool
    def post(data):
        return "posted"

    with safely(allow="post", honeytoken="sk-CANARY-2"):
        assert post("normal") == "posted"
        with pytest.raises(HoneytokenTripped):
            post("here is sk-CANARY-2")


def test_honeytoken_rejects_empty():
    with pytest.raises(ValueError):
        Honeytoken("")


# -- risk budget ----------------------------------------------------------

def test_risk_budget_blocks_when_exceeded():
    @tool("db.delete", risk=10)
    def delete():
        return "deleted"

    @tool("search", risk=1)
    def search():
        return "ok"

    with safely(allow="*", risk_budget=12):
        search()
        search()          # risk 2
        delete()          # +10 = 12, ok
        with pytest.raises(RiskBudgetExceeded):
            delete()      # +10 = 22 > 12


def test_zero_risk_tools_never_trip_budget():
    @tool("cheap")  # risk defaults to 0
    def cheap():
        return "ok"

    with safely(allow="cheap", risk_budget=1):
        for _ in range(50):
            cheap()       # no risk weight -> never charges


# -- action previews ------------------------------------------------------

def test_preview_shows_and_gates():
    seen = {}

    @tool("files.delete", preview=lambda paths: f"would delete {len(paths)} files")
    def rmfiles(paths):
        return f"deleted {len(paths)}"

    def approver(text, action):
        seen["text"] = text
        return "2" in text          # only approve when it's 2 files

    with safely(allow="files.delete", preview=approver):
        assert rmfiles(["a", "b"]) == "deleted 2"
        assert seen["text"] == "would delete 2 files"
        with pytest.raises(ApprovalDenied):
            rmfiles(["x", "y", "z"])


def test_tool_without_preview_is_not_gated():
    @tool("plain")
    def plain():
        return "ok"

    # a preview gate is active, but this tool declares no preview fn -> runs free
    with safely(allow="plain", preview=lambda text, action: False):
        assert plain() == "ok"
