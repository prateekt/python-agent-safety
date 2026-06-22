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
from typing import Any, Awaitable, Callable, Dict, Iterable, Optional, Tuple, TypeVar

from .approval import ApprovalRequest
from .audit import AuditEvent
from .context import current_policy
from .guards import Guard, Stage, run_guards
from .policy import Policy
from .reasoning import RATIONALE_KWARG, ReasoningRequest

F = TypeVar("F", bound=Callable[..., object])
AF = TypeVar("AF", bound=Callable[..., Awaitable[object]])


def _signature(
    capability: str, tool: str, args: Tuple[Any, ...], kwargs: Dict[str, Any]
) -> str:
    """A stable key for one tool call, used to detect identical-call loops."""
    return f"{tool}|{capability}|{args!r}|{tuple(sorted(kwargs.items()))!r}"


def _precheck(capability: str) -> Policy:
    """Charge the call and assert the capability before anything irreversible."""
    policy = current_policy()
    policy.charge_call()
    policy.require(capability)
    return policy


def _request(
    capability: str,
    tool: str,
    args: Tuple[Any, ...],
    kwargs: Dict[str, Any],
    rationale: Optional[str] = None,
) -> ApprovalRequest:
    # The agent's rationale (if any) rides along so a human approver can see it.
    return ApprovalRequest(capability, tool, args, dict(kwargs), reason=rationale or "")


def _extract_reasoning(
    policy: Policy,
    capability: str,
    tool: str,
    args: Tuple[Any, ...],
    kwargs: Dict[str, Any],
) -> Tuple[Optional[str], Dict[str, Any]]:
    """Pop the reserved ``rationale`` kwarg and run the reasoning check.

    Only intercepts ``rationale`` when a :class:`ReasoningGate` actually gates the
    capability, so tools with a real ``rationale`` parameter are untouched
    otherwise. Returns the rationale and the kwargs with it removed.
    """
    if not policy.requires_reasoning(capability):
        return None, kwargs
    raw = kwargs.get(RATIONALE_KWARG)
    rationale = raw if isinstance(raw, str) else None
    remaining = {k: v for k, v in kwargs.items() if k != RATIONALE_KWARG}
    policy.check_reasoning(ReasoningRequest(capability, tool, args, dict(remaining)), rationale)
    return rationale, remaining


def _guard_inputs(
    policy: Policy,
    capability: str,
    tool: str,
    extra_in: Tuple[Guard, ...],
    args: Tuple[Any, ...],
    kwargs: Dict[str, Any],
) -> Tuple[Tuple[Any, ...], Dict[str, Any]]:
    """Loop-check, audit the invocation, then input-guard every argument.

    Runs *after* permission/approval so the loop signature and audit reflect a
    call that was actually authorised.
    """
    policy.check_loop(tool, _signature(capability, tool, args, kwargs))
    policy.audit(AuditEvent("tool_call", "invoke", capability=capability))
    gargs = tuple(run_guards(extra_in, policy.check_input(a), Stage.INPUT) for a in args)
    gkwargs = {
        k: run_guards(extra_in, policy.check_input(v), Stage.INPUT)
        for k, v in kwargs.items()
    }
    return gargs, gkwargs


def _exit(policy: Policy, extra_out: Tuple[Guard, ...], result: Any) -> Any:
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
        tool = func.__name__

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            policy = _precheck(capability)
            rationale, kwargs = _extract_reasoning(policy, capability, tool, args, kwargs)
            policy.check_approval(_request(capability, tool, args, kwargs, rationale))
            gargs, gkwargs = _guard_inputs(policy, capability, tool, extra_in, args, kwargs)
            return _exit(policy, extra_out, func(*gargs, **gkwargs))

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
        tool = func.__name__

        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            policy = _precheck(capability)
            rationale, kwargs = _extract_reasoning(policy, capability, tool, args, kwargs)
            await policy.check_approval_async(_request(capability, tool, args, kwargs, rationale))
            gargs, gkwargs = _guard_inputs(policy, capability, tool, extra_in, args, kwargs)
            return _exit(policy, extra_out, await func(*gargs, **gkwargs))

        wrapper.__agent_capability__ = capability  # type: ignore[attr-defined]
        return wrapper  # type: ignore[return-value]

    return decorator
