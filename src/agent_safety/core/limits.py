"""Behavioural limits: how *fast* and how *repetitively* an agent may act.

:class:`~agent_safety.quota.Quota` caps the *total* an agent may spend. The two
limits here cap its *dynamics* — the two ways a tool-calling loop most often goes
wrong even while staying under a total budget:

* :class:`RateLimit` — a sliding-window cap on calls per unit time, so a buggy or
  adversarial loop can't hammer a downstream API in a burst.
* :class:`LoopGuard` — a circuit breaker for the classic runaway agent that gets
  stuck calling the *same tool with the same arguments* over and over.

Both are small, thread-safe, mutable counters (like ``Quota``, and unlike the
immutable :class:`~agent_safety.policy.Policy`) and compose through nesting: a
``safety_context`` adds them to the ones already in scope, so an inner limit can
be tighter but never loosens an outer one. Standard-library only.

Time is measured with :func:`time.monotonic`, so the limits are immune to wall-
clock adjustments.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from contextlib import asynccontextmanager, contextmanager
from threading import BoundedSemaphore, Lock
from typing import AsyncIterator, Deque, Dict, Iterator, Optional, Tuple

from .exceptions import DeadlineExceeded, LoopDetected, RateLimitExceeded


class RateLimit:
    """Allow at most *limit* calls within any rolling *window* of seconds.

    Construct it the way that reads best:

        RateLimit(per_second=5)            # 5 calls / 1s
        RateLimit(per_minute=100)          # 100 calls / 60s
        RateLimit(max_calls=10, per_seconds=2.0)   # explicit

    Charging is done for you by the active context on every guarded tool call
    (alongside any :class:`Quota`); the call that would breach the window raises
    :class:`~agent_safety.exceptions.RateLimitExceeded` *before* the tool runs,
    carrying a ``retry_after`` hint.
    """

    def __init__(
        self,
        max_calls: Optional[int] = None,
        per_seconds: Optional[float] = None,
        *,
        per_second: Optional[int] = None,
        per_minute: Optional[int] = None,
    ):
        if per_second is not None:
            limit, window = per_second, 1.0
        elif per_minute is not None:
            limit, window = per_minute, 60.0
        elif max_calls is not None and per_seconds is not None:
            limit, window = max_calls, float(per_seconds)
        else:
            raise ValueError(
                "specify per_second=, per_minute=, or both max_calls= and per_seconds="
            )
        if limit <= 0:
            raise ValueError("rate limit must be positive")
        if window <= 0:
            raise ValueError("window must be positive")
        self.limit = limit
        self.window = window
        self.name = f"rate_limit({limit}/{window:g}s)"
        self._times: Deque[float] = deque()
        self._lock = Lock()

    def charge(self, now: Optional[float] = None) -> None:
        """Record one call now, or raise if it would exceed the window."""
        ts = time.monotonic() if now is None else now
        with self._lock:
            horizon = ts - self.window
            while self._times and self._times[0] <= horizon:
                self._times.popleft()
            if len(self._times) >= self.limit:
                retry_after = self._times[0] + self.window - ts
                raise RateLimitExceeded(self.limit, self.window, max(0.0, retry_after))
            self._times.append(ts)

    def remaining(self, now: Optional[float] = None) -> int:
        """Calls still allowed in the current window (best-effort snapshot)."""
        ts = time.monotonic() if now is None else now
        with self._lock:
            horizon = ts - self.window
            live = sum(1 for t in self._times if t > horizon)
            return max(0, self.limit - live)

    def __str__(self) -> str:
        return self.name


class Deadline:
    """A wall-clock budget for a context: at most *seconds* of elapsed time.

    Where :class:`~agent_safety.quota.Quota` caps *how much* and
    :class:`RateLimit` caps *how fast*, a deadline caps *how long*. The clock
    starts on the first guarded call inside the context (so it measures working
    time, not setup), and a call made after the budget elapses raises
    :class:`~agent_safety.exceptions.DeadlineExceeded`.
    """

    def __init__(self, seconds: float):
        if seconds <= 0:
            raise ValueError("deadline must be positive")
        self.seconds = float(seconds)
        self.name = f"deadline({self.seconds:g}s)"
        self._start: Optional[float] = None
        self._lock = Lock()

    def charge(self, now: Optional[float] = None) -> None:
        """Start the clock on first call; raise once the budget has elapsed."""
        ts = time.monotonic() if now is None else now
        with self._lock:
            if self._start is None:
                self._start = ts
                return
            elapsed = ts - self._start
            if elapsed > self.seconds:
                raise DeadlineExceeded(self.seconds, elapsed)

    def remaining(self, now: Optional[float] = None) -> float:
        """Seconds left in the budget (the full budget before the first call)."""
        with self._lock:
            if self._start is None:
                return self.seconds
        ts = time.monotonic() if now is None else now
        return max(0.0, self.seconds - (ts - self._start))

    def reset(self) -> None:
        """Restart the clock at the next call."""
        with self._lock:
            self._start = None

    def __str__(self) -> str:
        return self.name


class LoopGuard:
    """Trip when one tool is called identically more than *max_identical* times.

    A *signature* is the tool name plus its arguments; the guard keeps the last
    *history* signatures and, on each new call, counts how many recent calls
    share it. Once that count exceeds ``max_identical`` it raises
    :class:`~agent_safety.exceptions.LoopDetected`, breaking the agent out of a
    no-progress loop before it burns the rest of its budget.

    The window is the recent-history buffer, so occasional repeats (a tool
    legitimately called twice) don't trip it — only a run of identical calls
    concentrated in the recent history does.

    Args:
        max_identical: How many identical calls to tolerate before tripping
            (the next one raises). Must be >= 1.
        history: How many recent calls to remember when counting repeats.
    """

    def __init__(self, max_identical: int = 3, *, history: int = 64):
        if max_identical < 1:
            raise ValueError("max_identical must be >= 1")
        if history < max_identical + 1:
            raise ValueError("history must exceed max_identical")
        self.max_identical = max_identical
        self.name = f"loop_guard(max_identical={max_identical})"
        self._recent: Deque[str] = deque(maxlen=history)
        self._lock = Lock()

    def record(self, tool: str, signature: str) -> None:
        """Note a call to *tool* with *signature*; raise if it's looping."""
        with self._lock:
            self._recent.append(signature)
            count = self._recent.count(signature)
            if count > self.max_identical:
                raise LoopDetected(tool, count, self.max_identical)

    def __str__(self) -> str:
        return self.name


class ConcurrencyLimit:
    """Allow at most *max_concurrent* guarded tool calls to run at the same time.

    A semaphore held around each tool's execution. Unlike the other limits it
    doesn't reject — it *waits* until a slot is free, then proceeds. Share one
    instance across several agents' ``safely(...)`` blocks to cap their combined
    parallelism (e.g. "no more than 4 calls to the flaky API at once, total").

    Works under both threads (sync tools) and ``asyncio`` (async tools). For async,
    the cap applies per event loop — agents sharing a loop are capped together, and
    reusing one instance across separate ``asyncio.run`` calls is safe.
    """

    def __init__(self, max_concurrent: int):
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be >= 1")
        self.max_concurrent = max_concurrent
        self.name = f"concurrency({max_concurrent})"
        self._sync_sem = BoundedSemaphore(max_concurrent)
        # An asyncio.Semaphore is bound to one event loop, so keep one per loop:
        # tasks in the same loop share the cap; a fresh loop (e.g. a second
        # asyncio.run) gets its own instead of crashing on a cross-loop future.
        self._async_sems: Dict[int, Tuple[asyncio.AbstractEventLoop, asyncio.Semaphore]] = {}
        self._init_lock = Lock()

    def _async_semaphore(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        key = id(loop)
        with self._init_lock:
            entry = self._async_sems.get(key)
            if entry is None or entry[0] is not loop:   # new loop, or id reused
                semaphore = asyncio.Semaphore(self.max_concurrent)
                self._async_sems[key] = (loop, semaphore)
                return semaphore
            return entry[1]

    @contextmanager
    def hold_sync(self) -> Iterator[None]:
        self._sync_sem.acquire()
        try:
            yield
        finally:
            self._sync_sem.release()

    @asynccontextmanager
    async def hold_async(self) -> AsyncIterator[None]:
        async with self._async_semaphore():
            yield

    def __str__(self) -> str:
        return self.name
