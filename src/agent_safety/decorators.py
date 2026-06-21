"""Decorators that wire a tool function into the active safety policy.

``@guarded_tool`` (sync) and ``@guarded_async_tool`` (async) are the ergonomic
front door: annotate the functions an agent may call, and every invocation is

1. counted against the active quota,
2. permission-checked against the current :class:`Policy`,
3. input-guarded (each argument is filtered),
4. executed, and
5. output-guarded (the return value is filtered before the caller sees it),

with each step emitting an audit event. Because it reads the *current* policy at
call time, the same decorated tool is fully privileged at the top level and
automatically constrained inside a narrower ``safety_context`` — no extra
plumbing, and nothing tied to a specific model provider.
"""

from __future__ import annotations

import functools
from typing import Awaitable, Callable, Iterable, TypeVar

from .audit import AuditEvent
from .context import current_policy
from .guards import Guard, Stage, run_guards

F = TypeVar("F", bound=Callable[..., object])
AF = TypeVar("AF", bound=Callable[..., Awaitable[object]])


def _enter(capability, extra_in, args, kwargs):
    """Shared pre-call pipeline: quota, permission, input guards."""
    policy = current_policy()
    policy.charge_call()
    policy.require(capability)
    policy.audit(AuditEvent("tool_call", "invoke", capability=capability))
    args = tuple(run_guards(extra_in, policy.check_input(a), Stage.INPUT) for a in args)
    kwargs = {
        k: run_guards(extra_in, policy.check_input(v), Stage.INPUT)
        for k, v in kwargs.items()
    }
    return policy, args, kwargs


def _exit(policy, extra_out, result):
    """Shared post-call pipeline: output guards."""
    result = policy.check_output(result)
    return run_guards(extra_out, result, Stage.OUTPUT)


def guarded_tool(
    capability: str,
    *,
    input_guards: Iterable[Guard] = (),
    output_guards: Iterable[Guard] = (),
) -> Callable[[F], F]:
    """Wrap a synchronous tool callable with the full safety pipeline."""
    extra_in = tuple(input_guards)
    extra_out = tuple(output_guards)

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            policy, args, kwargs = _enter(capability, extra_in, args, kwargs)
            return _exit(policy, extra_out, func(*args, **kwargs))

        wrapper.__agent_capability__ = capability  # type: ignore[attr-defined]
        return wrapper  # type: ignore[return-value]

    return decorator


def guarded_async_tool(
    capability: str,
    *,
    input_guards: Iterable[Guard] = (),
    output_guards: Iterable[Guard] = (),
) -> Callable[[AF], AF]:
    """Async counterpart of :func:`guarded_tool`.

    The contextvar-backed policy is preserved across ``await`` points, so an
    async tool is governed by exactly the ``safety_context`` it was called in —
    correct under ``asyncio`` concurrency without any locking.
    """
    extra_in = tuple(input_guards)
    extra_out = tuple(output_guards)

    def decorator(func: AF) -> AF:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            policy, args, kwargs = _enter(capability, extra_in, args, kwargs)
            return _exit(policy, extra_out, await func(*args, **kwargs))

        wrapper.__agent_capability__ = capability  # type: ignore[attr-defined]
        return wrapper  # type: ignore[return-value]

    return decorator
