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
from contextlib import AsyncExitStack, ExitStack, asynccontextmanager, contextmanager
from typing import (
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    Dict,
    Iterable,
    Iterator,
    List,
    Optional,
    Tuple,
    TypeVar,
)

from .approval import ApprovalRequest
from .audit import AuditEvent
from .context import current_policy
from .guards import Guard, Stage, run_guards
from .policy import Policy
from .reasoning import RATIONALE_KWARG, ReasoningRequest

F = TypeVar("F", bound=Callable[..., object])
AF = TypeVar("AF", bound=Callable[..., Awaitable[object]])

_CACHE_MAX = 256


def _signature(
    capability: str, tool: str, args: Tuple[Any, ...], kwargs: Dict[str, Any]
) -> str:
    """A stable key for one tool call, used for loop detection and caching."""
    return f"{tool}|{capability}|{args!r}|{tuple(sorted(kwargs.items()))!r}"


@contextmanager
def _hold_concurrency(policy: Policy) -> Iterator[None]:
    """Acquire every active sync concurrency slot for the duration of a call."""
    with ExitStack() as stack:
        for limit in policy.concurrency_limits:
            stack.enter_context(limit.hold_sync())
        yield


@asynccontextmanager
async def _hold_concurrency_async(policy: Policy) -> AsyncIterator[None]:
    async with AsyncExitStack() as stack:
        for limit in policy.concurrency_limits:
            await stack.enter_async_context(limit.hold_async())
        yield


def _cache_put(cache: Dict[str, Any], order: List[str], key: str, value: Any) -> None:
    cache[key] = value
    order.append(key)
    if len(order) > _CACHE_MAX:
        cache.pop(order.pop(0), None)


def _strip_rationale(policy: Policy, capability: str, kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """Drop the reserved ``rationale`` kwarg when a reasoning gate covers
    *capability* — used in monitor mode, where the reasoning check is skipped but
    the reserved keyword still must not reach the underlying tool.
    """
    if policy.requires_reasoning(capability):
        return {k: v for k, v in kwargs.items() if k != RATIONALE_KWARG}
    return kwargs


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
    idempotent: bool = False,
) -> Callable[[F], F]:
    """Wrap a synchronous tool callable with the full safety pipeline.

    Set ``idempotent=True`` for a side-effect-free tool to cache its result by
    call signature: repeated identical calls return the cached value instead of
    re-running. (Use only for pure tools — never for one that sends an email.)
    """
    extra_in = tuple(input_guards)
    extra_out = tuple(output_guards)

    def decorator(func: F) -> F:
        tool = func.__name__
        cache: Optional[Dict[str, Any]] = {} if idempotent else None
        order: List[str] = []

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            policy = current_policy()
            if not policy.enforce:                      # monitor / dry-run mode
                policy.note_monitor(capability)
                return func(*args, **_strip_rationale(policy, capability, kwargs))
            policy.charge_call()
            policy.require(capability)
            rationale, kwargs = _extract_reasoning(policy, capability, tool, args, kwargs)
            policy.check_approval(_request(capability, tool, args, kwargs, rationale))
            key = _signature(capability, tool, args, kwargs) if cache is not None else ""
            if cache is not None and key in cache:
                # Cache the RAW result and re-apply *this* context's output guards,
                # so a cached value can never bypass redaction in a stricter scope.
                policy.audit(AuditEvent("cache", "hit", capability=capability))
                return _exit(policy, extra_out, cache[key])
            gargs, gkwargs = _guard_inputs(policy, capability, tool, extra_in, args, kwargs)
            with _hold_concurrency(policy):
                result = func(*gargs, **gkwargs)
            if cache is not None:
                _cache_put(cache, order, key, result)
            return _exit(policy, extra_out, result)

        wrapper.__agent_capability__ = capability  # type: ignore[attr-defined]
        return wrapper  # type: ignore[return-value]

    return decorator


def guarded_async_tool(
    capability: str,
    *,
    input_guards: Iterable[Guard] = (),
    output_guards: Iterable[Guard] = (),
    idempotent: bool = False,
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
        cache: Optional[Dict[str, Any]] = {} if idempotent else None
        order: List[str] = []

        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            policy = current_policy()
            if not policy.enforce:                      # monitor / dry-run mode
                policy.note_monitor(capability)
                return await func(*args, **_strip_rationale(policy, capability, kwargs))
            policy.charge_call()
            policy.require(capability)
            rationale, kwargs = _extract_reasoning(policy, capability, tool, args, kwargs)
            await policy.check_approval_async(_request(capability, tool, args, kwargs, rationale))
            key = _signature(capability, tool, args, kwargs) if cache is not None else ""
            if cache is not None and key in cache:
                # Cache the RAW result and re-apply *this* context's output guards,
                # so a cached value can never bypass redaction in a stricter scope.
                policy.audit(AuditEvent("cache", "hit", capability=capability))
                return _exit(policy, extra_out, cache[key])
            gargs, gkwargs = _guard_inputs(policy, capability, tool, extra_in, args, kwargs)
            async with _hold_concurrency_async(policy):
                result = await func(*gargs, **gkwargs)
            if cache is not None:
                _cache_put(cache, order, key, result)
            return _exit(policy, extra_out, result)

        wrapper.__agent_capability__ = capability  # type: ignore[attr-defined]
        return wrapper  # type: ignore[return-value]

    return decorator
