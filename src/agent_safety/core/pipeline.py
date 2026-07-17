"""The one enforcement pipeline every guarded tool call goes through.

Whether a tool is a local ``def``, an ``async def``, or a remote MCP tool, the
call is admitted through exactly the same ordered checklist:

 1.  **monitor short-circuit** — in monitor (dry-run) mode, record what would
     happen and run the tool unblocked;
 2.  **charge call** — quotas, rate limits, and deadlines;
 3.  **memory check** — Python-heap growth against the block's budget;
 4.  **permission check** — the capability must be allowed (default-deny);
 5.  **risk charge** — the tool's ``risk`` weight against any risk budget;
 6.  **rationale** — pop and validate ``rationale=`` if a reasoning gate covers
     the capability;
 7.  **approval gate** — a human / callable must say yes;
 8.  **constitution gate** — a model judge clears the call against its rules;
 9.  **preview gate** — the tool's "what would this do?" text is approved;
10.  **cache lookup** — a cached result short-circuits execution (output guards
     still re-apply, so a cached value can never bypass redaction);
11.  **loop check + audit** — runaway-loop detection, then the call is audited;
12.  **input guards** — every argument is filtered;
13.  **concurrency + timeout** — the call runs holding a concurrency slot,
     bounded by the per-call timeout;
14.  **output guards** — the result is filtered before the caller sees it.

:func:`run_tool_call` drives the checklist for sync tools and
:func:`arun_tool_call` for async ones (awaiting async gates); both share every
step helper in this module, so the two can never drift apart.
"""

from __future__ import annotations

import functools
import inspect
import warnings
from contextlib import AsyncExitStack, ExitStack, asynccontextmanager, contextmanager
from dataclasses import dataclass, field
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

from .action import Action
from .audit import AuditEvent
from .context import current_policy
from .gates import RATIONALE_KWARG
from .guards import Guard, Stage, run_guards
from .policy import Policy
from .runtime import acall_with_timeout, call_with_timeout

_CACHE_MAX = 256


@dataclass
class ToolCallSpec:
    """Everything the pipeline needs to know about one guarded tool.

    Built once per decorated function (or per MCP call) and handed to
    :func:`run_tool_call` / :func:`arun_tool_call` together with the invocation.
    """

    capability: str
    tool: str
    input_guards: Tuple[Guard, ...] = ()
    output_guards: Tuple[Guard, ...] = ()
    risk: int = 0
    preview: Optional[Callable[..., Any]] = None
    # A dict marks the tool as cacheable (``cache=True``); ``None`` disables caching.
    cache: Optional[Dict[str, Any]] = None
    cache_order: List[str] = field(default_factory=list)

    @classmethod
    def for_tool(
        cls,
        capability: str,
        tool: str,
        *,
        input_guards: Iterable[Guard] = (),
        output_guards: Iterable[Guard] = (),
        risk: int = 0,
        preview: Optional[Callable[..., Any]] = None,
        cached: bool = False,
    ) -> "ToolCallSpec":
        return cls(
            capability=capability,
            tool=tool,
            input_guards=tuple(input_guards),
            output_guards=tuple(output_guards),
            risk=risk,
            preview=preview,
            cache={} if cached else None,
        )


# -- shared step helpers ----------------------------------------------------

def _signature(spec: ToolCallSpec, args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> str:
    """A stable key for one tool call, used for loop detection and caching."""
    return f"{spec.tool}|{spec.capability}|{args!r}|{tuple(sorted(kwargs.items()))!r}"


def _strip_rationale(policy: Policy, spec: ToolCallSpec, kwargs: Dict[str, Any]) -> Dict[str, Any]:
    """Drop the reserved ``rationale`` kwarg when a reasoning gate covers the
    capability — used in monitor mode, where the reasoning check is skipped but
    the reserved keyword still must not reach the underlying tool.
    """
    if policy.requires_reasoning(spec.capability):
        return {k: v for k, v in kwargs.items() if k != RATIONALE_KWARG}
    return kwargs


def _admit(
    policy: Policy, spec: ToolCallSpec, args: Tuple[Any, ...], kwargs: Dict[str, Any]
) -> Tuple[Dict[str, Any], Action]:
    """Steps 2–6: budgets, memory, permission, risk, and rationale.

    Returns the kwargs with any reserved ``rationale`` removed, and the
    :class:`Action` describing the call for the gates that follow.
    """
    policy.charge_call()
    policy.check_memory()
    policy.require(spec.capability)
    policy.charge_risk(spec.risk)
    rationale: Optional[str] = None
    if policy.requires_reasoning(spec.capability):
        raw = kwargs.get(RATIONALE_KWARG)
        rationale = raw if isinstance(raw, str) else None
        kwargs = {k: v for k, v in kwargs.items() if k != RATIONALE_KWARG}
        policy.check_reasoning(
            Action(spec.capability, spec.tool, args, dict(kwargs)), rationale
        )
    request = Action(spec.capability, spec.tool, args, dict(kwargs), reason=rationale or "")
    return kwargs, request


def _preview_text(spec: ToolCallSpec, args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> str:
    return str(spec.preview(*args, **kwargs)) if spec.preview is not None else ""


def _cache_key(spec: ToolCallSpec, args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> Optional[str]:
    return _signature(spec, args, kwargs) if spec.cache is not None else None


def _cache_hit(policy: Policy, spec: ToolCallSpec, key: Optional[str]) -> bool:
    if key is None or spec.cache is None or key not in spec.cache:
        return False
    # Cache the RAW result and re-apply *this* context's output guards, so a
    # cached value can never bypass redaction in a stricter scope.
    policy.audit(AuditEvent("cache", "hit", capability=spec.capability))
    return True


def _cache_put(spec: ToolCallSpec, key: str, value: Any) -> None:
    assert spec.cache is not None
    spec.cache[key] = value
    spec.cache_order.append(key)
    if len(spec.cache_order) > _CACHE_MAX:
        spec.cache.pop(spec.cache_order.pop(0), None)


def _guard_inputs(
    policy: Policy, spec: ToolCallSpec, args: Tuple[Any, ...], kwargs: Dict[str, Any]
) -> Tuple[Tuple[Any, ...], Dict[str, Any]]:
    """Steps 11–12: loop-check, audit the invocation, then input-guard every argument.

    Runs *after* permission/approval so the loop signature and audit reflect a
    call that was actually authorised.
    """
    policy.check_loop(spec.tool, _signature(spec, args, kwargs))
    policy.audit(AuditEvent("tool_call", "invoke", capability=spec.capability))
    gargs = tuple(
        run_guards(spec.input_guards, policy.check_input(a), Stage.INPUT) for a in args
    )
    gkwargs = {
        k: run_guards(spec.input_guards, policy.check_input(v), Stage.INPUT)
        for k, v in kwargs.items()
    }
    return gargs, gkwargs


def _finish(policy: Policy, spec: ToolCallSpec, result: Any) -> Any:
    """Step 14: output guards."""
    result = policy.check_output(result)
    return run_guards(spec.output_guards, result, Stage.OUTPUT)


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


# -- drivers ------------------------------------------------------------------

def run_tool_call(
    spec: ToolCallSpec,
    func: Callable[..., Any],
    args: Tuple[Any, ...],
    kwargs: Dict[str, Any],
) -> Any:
    """Enforce the full pipeline around a synchronous ``func(*args, **kwargs)``."""
    policy = current_policy()
    if not policy.enforce:                      # 1. monitor / dry-run mode
        policy.note_monitor(spec.capability)
        return func(*args, **_strip_rationale(policy, spec, kwargs))
    kwargs, request = _admit(policy, spec, args, kwargs)          # 2–6
    policy.check_approval(request)                                # 7
    policy.check_constitution(request)                            # 8
    if spec.preview is not None and policy.requires_preview(spec.capability):
        policy.check_preview(request, _preview_text(spec, args, kwargs))  # 9
    key = _cache_key(spec, args, kwargs)
    if _cache_hit(policy, spec, key):                             # 10
        assert spec.cache is not None and key is not None
        return _finish(policy, spec, spec.cache[key])
    gargs, gkwargs = _guard_inputs(policy, spec, args, kwargs)    # 11–12
    with _hold_concurrency(policy):                               # 13
        result = call_with_timeout(func, gargs, gkwargs, policy.timeout)
    if key is not None:
        _cache_put(spec, key, result)
    return _finish(policy, spec, result)                          # 14


async def arun_tool_call(
    spec: ToolCallSpec,
    func: Callable[..., Awaitable[Any]],
    args: Tuple[Any, ...],
    kwargs: Dict[str, Any],
) -> Any:
    """Enforce the full pipeline around an ``await func(*args, **kwargs)``.

    Identical to :func:`run_tool_call` step for step, but gates with async
    approvers/judges are awaited instead of raising.
    """
    policy = current_policy()
    if not policy.enforce:                      # 1. monitor / dry-run mode
        policy.note_monitor(spec.capability)
        return await func(*args, **_strip_rationale(policy, spec, kwargs))
    kwargs, request = _admit(policy, spec, args, kwargs)          # 2–6
    await policy.check_approval_async(request)                    # 7
    await policy.check_constitution_async(request)                # 8
    if spec.preview is not None and policy.requires_preview(spec.capability):
        await policy.check_preview_async(request, _preview_text(spec, args, kwargs))  # 9
    key = _cache_key(spec, args, kwargs)
    if _cache_hit(policy, spec, key):                             # 10
        assert spec.cache is not None and key is not None
        return _finish(policy, spec, spec.cache[key])
    gargs, gkwargs = _guard_inputs(policy, spec, args, kwargs)    # 11–12
    async with _hold_concurrency_async(policy):                   # 13
        result = await acall_with_timeout(func, gargs, gkwargs, policy.timeout)
    if key is not None:
        _cache_put(spec, key, result)
    return _finish(policy, spec, result)                          # 14


# -- registration ------------------------------------------------------------

F = TypeVar("F", bound=Callable[..., object])
AF = TypeVar("AF", bound=Callable[..., Awaitable[object]])


def make_tool_wrapper(
    func: Callable[..., Any],
    capability: str,
    *,
    input_guards: Iterable[Guard] = (),
    output_guards: Iterable[Guard] = (),
    cache: bool = False,
    risk: int = 0,
    preview: Optional[Callable[..., Any]] = None,
) -> Callable[..., Any]:
    """Wrap *func* (sync or async — detected automatically) with the full pipeline.

    Set ``cache=True`` for a side-effect-free tool to reuse the result of
    identical calls. ``risk`` weights the call against any active risk budget.
    ``preview`` is a function of the same arguments that returns a description
    of what the call would do, shown to an active preview gate before it runs.
    """
    spec = ToolCallSpec.for_tool(
        capability,
        func.__name__,
        input_guards=input_guards,
        output_guards=output_guards,
        risk=risk,
        preview=preview,
        cached=cache,
    )

    if inspect.iscoroutinefunction(func):
        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            return await arun_tool_call(spec, func, args, kwargs)

        async_wrapper.__agent_capability__ = capability  # type: ignore[attr-defined]
        return async_wrapper

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return run_tool_call(spec, func, args, kwargs)

    wrapper.__agent_capability__ = capability  # type: ignore[attr-defined]
    return wrapper


def _deprecated_decorator(
    old_name: str,
    capability: str,
    *,
    input_guards: Iterable[Guard] = (),
    output_guards: Iterable[Guard] = (),
    idempotent: bool = False,
    risk: int = 0,
    preview: Optional[Callable[..., Any]] = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    warnings.warn(
        f"@{old_name}(...) is deprecated; use @tool(...) from agent_safety "
        "(it handles sync and async automatically, and idempotent= is now cache=)",
        DeprecationWarning,
        stacklevel=3,
    )

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        return make_tool_wrapper(
            func,
            capability,
            input_guards=input_guards,
            output_guards=output_guards,
            cache=idempotent,
            risk=risk,
            preview=preview,
        )

    return decorator


def guarded_tool(
    capability: str,
    *,
    input_guards: Iterable[Guard] = (),
    output_guards: Iterable[Guard] = (),
    idempotent: bool = False,
    risk: int = 0,
    preview: Optional[Callable[..., Any]] = None,
) -> Callable[[F], F]:
    """Deprecated alias of ``@tool(...)`` for synchronous tools."""
    return _deprecated_decorator(  # type: ignore[return-value]
        "guarded_tool", capability,
        input_guards=input_guards, output_guards=output_guards,
        idempotent=idempotent, risk=risk, preview=preview,
    )


def guarded_async_tool(
    capability: str,
    *,
    input_guards: Iterable[Guard] = (),
    output_guards: Iterable[Guard] = (),
    idempotent: bool = False,
    risk: int = 0,
    preview: Optional[Callable[..., Any]] = None,
) -> Callable[[AF], AF]:
    """Deprecated alias of ``@tool(...)`` for asynchronous tools."""
    return _deprecated_decorator(  # type: ignore[return-value]
        "guarded_async_tool", capability,
        input_guards=input_guards, output_guards=output_guards,
        idempotent=idempotent, risk=risk, preview=preview,
    )
