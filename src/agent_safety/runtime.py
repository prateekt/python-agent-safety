"""Hard per-call runtime limits — so nothing hangs the agent.

The cumulative ``Deadline`` (``safely(seconds=...)``) bounds *total* wall-clock across
a run; this module adds a per-call ``timeout`` that stops a *single* call which hangs
or deadlocks. Two strategies, picked automatically:

* **async** calls use :func:`asyncio.wait_for`, which cancels the coroutine cleanly;
* **sync** calls use a ``SIGALRM`` timer when available (Unix, main thread) — which
  interrupts the call in place — and otherwise fall back to a worker thread that is
  abandoned on timeout (the agent is unblocked even though the thread can't be killed).

No third-party dependencies; all stdlib.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import signal
import threading
from typing import Any, Awaitable, Callable, Mapping, Optional, Sequence

from .exceptions import TimeoutExceeded


def _can_use_signal() -> bool:
    return hasattr(signal, "SIGALRM") and threading.current_thread() is threading.main_thread()


def _with_signal(
    func: Callable[..., Any], args: Sequence[Any], kwargs: Mapping[str, Any], seconds: float
) -> Any:
    def _handler(signum: int, frame: Any) -> None:
        raise TimeoutExceeded(seconds)

    previous = signal.signal(signal.SIGALRM, _handler)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        return func(*args, **kwargs)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _with_thread(
    func: Callable[..., Any], args: Sequence[Any], kwargs: Mapping[str, Any], seconds: float
) -> Any:
    ctx = contextvars.copy_context()  # carry the active policy into the worker
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(lambda: ctx.run(func, *args, **kwargs))
    try:
        return future.result(timeout=seconds)
    except concurrent.futures.TimeoutError:
        raise TimeoutExceeded(seconds) from None
    finally:
        executor.shutdown(wait=False)  # don't block on a hung worker


def call_with_timeout(
    func: Callable[..., Any],
    args: Sequence[Any],
    kwargs: Mapping[str, Any],
    seconds: Optional[float],
) -> Any:
    """Run ``func(*args, **kwargs)``, raising :class:`TimeoutExceeded` after *seconds*."""
    if not seconds:
        return func(*args, **kwargs)
    if _can_use_signal():
        return _with_signal(func, args, kwargs, seconds)
    return _with_thread(func, args, kwargs, seconds)


async def acall_with_timeout(
    func: Callable[..., Awaitable[Any]],
    args: Sequence[Any],
    kwargs: Mapping[str, Any],
    seconds: Optional[float],
) -> Any:
    """Await ``func(*args, **kwargs)``, raising :class:`TimeoutExceeded` after *seconds*."""
    if not seconds:
        return await func(*args, **kwargs)
    try:
        return await asyncio.wait_for(func(*args, **kwargs), timeout=seconds)
    except asyncio.TimeoutError:
        raise TimeoutExceeded(seconds) from None
