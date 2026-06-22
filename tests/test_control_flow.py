import asyncio

import pytest

from agent_safety import (
    ConcurrencyLimit,
    ListSink,
    PermissionSet,
    safely,
    safety_context,
    tool,
)
from agent_safety.exceptions import PermissionDenied

# -- monitor / dry-run mode ----------------------------------------------

@tool
def danger():
    return "did it"


def test_monitor_runs_anyway_and_logs_would_deny():
    audit = ListSink()
    with safely(allow="something.else", monitor=True, log=audit):
        assert danger() == "did it"          # not allowed, but monitor runs it
    decisions = [(e.action, e.decision) for e in audit.events]
    assert ("permission", "would_deny") in decisions


def test_enforce_still_blocks():
    with safely(allow="something.else"):
        with pytest.raises(PermissionDenied):
            danger()


def test_monitor_cannot_be_loosened_in_nested_block():
    # outer enforces; a nested monitor block must NOT disable enforcement
    with safety_context(PermissionSet.of("x"), enforce=True):
        with safety_context(enforce=False):
            with pytest.raises(PermissionDenied):
                danger()


def test_monitor_can_be_tightened_in_nested_block():
    # outer is monitor; a nested enforce block re-enables blocking
    with safety_context(PermissionSet.deny_all(), enforce=False):
        danger()                              # monitored, runs
        with safety_context(enforce=True):
            with pytest.raises(PermissionDenied):
                danger()


# -- idempotency ----------------------------------------------------------

def test_idempotent_caches_identical_calls():
    runs = {"n": 0}

    @tool("compute", cache=True)
    def compute(x):
        runs["n"] += 1
        return x * 2

    with safely(allow="compute"):
        assert compute(21) == 42
        assert compute(21) == 42
        assert compute(21) == 42
        assert runs["n"] == 1                 # ran only once
        compute(5)
        assert runs["n"] == 2                 # different args -> ran again


def test_idempotent_logs_cache_hit():
    audit = ListSink()

    @tool("lookup", cache=True)
    def lookup(k):
        return k.upper()

    with safely(allow="lookup", log=audit):
        lookup("a")
        lookup("a")
    assert ("cache", "hit") in [(e.action, e.decision) for e in audit.events]


def test_idempotent_async():
    runs = {"n": 0}

    @tool(cache=True)
    async def afetch(url):
        await asyncio.sleep(0)
        runs["n"] += 1
        return f"got {url}"

    async def run():
        with safely(allow="afetch"):
            await afetch("u")
            await afetch("u")
            return runs["n"]

    assert asyncio.run(run()) == 1


# -- concurrency ----------------------------------------------------------

def test_concurrency_caps_parallel_async_calls():
    state = {"now": 0, "peak": 0}

    @tool
    async def work(i):
        state["now"] += 1
        state["peak"] = max(state["peak"], state["now"])
        await asyncio.sleep(0.02)
        state["now"] -= 1
        return i

    async def run():
        with safely(allow="work", at_most=2):
            await asyncio.gather(*(work(i) for i in range(8)))

    asyncio.run(run())
    assert state["peak"] <= 2                  # never more than 2 at once


def test_shared_concurrency_caps_two_agents_together():
    state = {"now": 0, "peak": 0}
    shared = ConcurrencyLimit(2)

    @tool
    async def work(label):
        state["now"] += 1
        state["peak"] = max(state["peak"], state["now"])
        await asyncio.sleep(0.02)
        state["now"] -= 1
        return label

    async def agent(name):
        with safely(allow="work", at_most=shared):     # same object in both
            await asyncio.gather(*(work(f"{name}{i}") for i in range(4)))

    async def run():
        await asyncio.gather(agent("A"), agent("B"))   # 8 tasks total

    asyncio.run(run())
    assert state["peak"] <= 2                  # capped across both agents


def test_concurrency_sync():
    import threading

    state = {"now": 0, "peak": 0}
    lock = threading.Lock()

    @tool
    def work(i):
        with lock:
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
        import time
        time.sleep(0.02)
        with lock:
            state["now"] -= 1
        return i

    limit = ConcurrencyLimit(2)

    def run_one(i):
        with safety_context(PermissionSet.of("work"), concurrency=limit):
            work(i)

    threads = [threading.Thread(target=run_one, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert state["peak"] <= 2


def test_concurrency_rejects_bad_arg():
    with pytest.raises(ValueError):
        ConcurrencyLimit(0)


# -- regression: holes found and patched ---------------------------------

def test_monitor_mode_does_not_crash_on_reasoning_rationale():
    # Hole A: in monitor mode the reserved `rationale` kwarg must still be
    # stripped before the tool runs, or it reaches a tool that has no such param.
    @tool
    def act(x):
        return x

    with safely(allow="act", explain=True, monitor=True):
        assert act(7, rationale="dry run, just observing") == 7


def test_idempotent_cache_respects_current_context_output_guards():
    # Hole B: a value cached in a lax context must NOT leak past a stricter
    # context's output guards — guards are re-applied per call on cache hits.
    @tool("read_note", cache=True)
    def read_note():
        return "ping jane@secret.com"

    with safely(allow="read_note"):
        assert "jane@secret.com" in read_note()          # cached raw here
    with safely(allow="read_note", hide_secrets=True):
        out = read_note()                                 # cache hit, but re-guarded
        assert "jane@secret.com" not in out
        assert "[REDACTED:EMAIL]" in out


def test_shared_concurrency_limit_survives_multiple_event_loops():
    # Hole C: a shared ConcurrencyLimit reused across separate asyncio.run()
    # calls must not crash on a semaphore bound to a stale event loop.
    shared = ConcurrencyLimit(2)

    @tool
    async def work(i):
        await asyncio.sleep(0)
        return i

    async def run():
        with safely(allow="work", at_most=shared):
            return await asyncio.gather(*(work(i) for i in range(3)))

    assert asyncio.run(run()) == [0, 1, 2]
    assert asyncio.run(run()) == [0, 1, 2]   # second, fresh loop — must not crash
