import asyncio
import time

import pytest

from agent_safety import metered, safely, tool
from agent_safety.easy import _bytes
from agent_safety.exceptions import MemoryBudgetExceeded, TimeoutExceeded

# -- per-call timeout (no hangups) ----------------------------------------

@tool
def _slow(seconds):
    time.sleep(seconds)
    return "done"


def test_timeout_stops_a_hanging_call():
    with safely(allow="*", timeout=0.1):
        with pytest.raises(TimeoutExceeded):
            _slow(2.0)


def test_timeout_lets_a_fast_call_through():
    with safely(allow="*", timeout=1.0):
        assert _slow(0.01) == "done"


def test_no_timeout_means_no_limit():
    with safely(allow="*"):
        assert _slow(0.01) == "done"


def test_timeout_tightens_when_nested():
    with safely(allow="*", timeout=10.0):
        with safely(allow="*", timeout=0.1):       # inner is stricter -> wins
            with pytest.raises(TimeoutExceeded):
                _slow(2.0)


def test_timeout_on_metered_call():
    def slow_model(prompt):
        time.sleep(2.0)
        return prompt

    ask = metered(slow_model)
    with safely(allow="*", timeout=0.1):
        with pytest.raises(TimeoutExceeded):
            ask("hi")


def test_timeout_async():
    @tool
    async def aslow(seconds):
        await asyncio.sleep(seconds)
        return "done"

    async def run():
        with safely(allow="*", timeout=0.1):
            await aslow(2.0)

    with pytest.raises(TimeoutExceeded):
        asyncio.run(run())


# -- memory guardrail -----------------------------------------------------

def test_memory_stops_runaway_allocation():
    chunks = []

    @tool
    def grow(_):
        chunks.append(b"x" * 2_000_000)   # 2 MB each, kept alive
        return len(chunks)

    with safely(allow="*", memory="3MB"):
        with pytest.raises(MemoryBudgetExceeded):
            for i in range(100):
                grow(i)


def test_memory_under_budget_passes():
    @tool
    def small(_):
        return b"x" * 1000

    with safely(allow="*", memory="50MB"):
        for i in range(10):
            small(i)                       # well under budget -> no raise


# -- byte-size parsing ----------------------------------------------------

@pytest.mark.parametrize("given,expected", [
    ("500MB", 500 * 1024 ** 2),
    ("1GB", 1024 ** 3),
    ("512KB", 512 * 1024),
    ("1.5GB", int(1.5 * 1024 ** 3)),
    ("2 GB", 2 * 1024 ** 3),
    (1024, 1024),
])
def test_bytes_parsing(given, expected):
    assert _bytes(given) == expected


def test_bytes_rejects_garbage():
    with pytest.raises(ValueError):
        _bytes("lots")
