"""The ``with`` construct at the heart of the library.

``safety_context`` is a context manager that installs an active :class:`Policy`
for the duration of a block and restores the previous one on exit — including
when the block raises. The current policy lives in a :class:`contextvars.ContextVar`,
so it is correct under threads and ``asyncio`` tasks without any locking.

The crucial invariant: entering a nested ``safety_context`` *narrows* the
policy (see :meth:`Policy.narrow`). Code can voluntarily drop privileges for a
risky sub-step, but nothing inside the block can hand itself back capabilities
it was not given. When the block exits, the broader policy returns.

    with safety_context(PermissionSet.of("filesystem.read")):
        require("filesystem.read")          # ok
        with safety_context(input_guards=[MaxLength(2000)]):
            require("filesystem.read")      # still ok, now also length-capped
            require("shell.exec")           # -> PermissionDenied
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from dataclasses import replace
from typing import Iterable, Iterator, Optional, Tuple, TypeVar, Union

from .approval import ApprovalGate
from .audit import AuditSink
from .constitution import ConstitutionGate
from .guards import Guard
from .limits import ConcurrencyLimit, Deadline, LoopGuard, RateLimit
from .permissions import PermissionSet
from .policy import Policy
from .preview import PreviewGate
from .quota import Quota, RiskBudget
from .reasoning import ReasoningGate

_T = TypeVar("_T")


def _as_tuple(value: Union[_T, Iterable[_T], None], cls: type) -> Tuple[_T, ...]:
    """Normalize ``None`` / a single object / an iterable of them to a tuple."""
    if value is None:
        return ()
    if isinstance(value, cls):
        return (value,)  # type: ignore[return-value]
    return tuple(value)  # type: ignore[arg-type]

# The root sentinel. Outside any ``safety_context`` the effective policy is
# deny-all, so stray agent code that forgot to establish a context fails safe.
# Entering a top-level context, however, represents *trusted host code* dropping
# to least privilege, so it bootstraps from full authority (see ``safety_context``).
_ROOT = Policy()

# The active policy for the current thread/task.
_current: "contextvars.ContextVar[Policy]" = contextvars.ContextVar(
    "agent_safety_policy", default=_ROOT
)


def current_policy() -> Policy:
    """Return the policy in force for the current thread/task."""
    return _current.get()


def require(capability: str) -> None:
    """Assert the current policy allows *capability*, else ``PermissionDenied``."""
    current_policy().require(capability)


def is_allowed(capability: str) -> bool:
    """Non-raising check against the current policy."""
    return current_policy().allows(capability)


def check_prompt(value: object) -> object:
    """Run the active prompt guards over *value*."""
    return current_policy().check_prompt(value)


def check_input(value: object) -> object:
    """Run the active input guards over *value*."""
    return current_policy().check_input(value)


def check_output(value: object) -> object:
    """Run the active output guards over *value*."""
    return current_policy().check_output(value)


def charge_call(n: int = 1) -> None:
    """Charge *n* calls against every quota in the active context."""
    current_policy().charge_call(n)


def charge_tokens(n: int) -> None:
    """Charge *n* tokens against every quota in the active context.

    Report whatever your model's usage object gives you — Claude
    ``usage.output_tokens``, OpenAI ``usage.total_tokens``, Gemini
    ``usage_metadata.total_token_count``.
    """
    current_policy().charge_tokens(n)


@contextmanager
def safety_context(
    permissions: Optional[PermissionSet] = None,
    *,
    policy: Optional[Policy] = None,
    prompt_guards: Iterable[Guard] = (),
    input_guards: Iterable[Guard] = (),
    output_guards: Iterable[Guard] = (),
    quota: Optional[Quota] = None,
    rate_limit: Union[RateLimit, Iterable[RateLimit], None] = None,
    deadline: Union[Deadline, Iterable[Deadline], None] = None,
    concurrency: Union[ConcurrencyLimit, Iterable[ConcurrencyLimit], None] = None,
    risk_budget: Union[RiskBudget, Iterable[RiskBudget], None] = None,
    loop_guard: Union[LoopGuard, Iterable[LoopGuard], None] = None,
    approval: Union[ApprovalGate, Iterable[ApprovalGate], None] = None,
    reasoning: Union[ReasoningGate, Iterable[ReasoningGate], None] = None,
    constitution: Union[ConstitutionGate, Iterable[ConstitutionGate], None] = None,
    preview: Union[PreviewGate, Iterable[PreviewGate], None] = None,
    enforce: Optional[bool] = None,
    audit: Iterable[AuditSink] = (),
) -> Iterator[Policy]:
    """Scope a narrowed safety policy to a ``with`` block.

    Args:
        permissions: Capabilities to intersect with the current policy. The
            block can do at most what *both* the outer policy and this set allow.
        policy: Use this full policy as the base instead of the current one.
            It is still narrowed by the current policy, preserving de-escalation.
        prompt_guards / input_guards / output_guards: Guards appended for the
            duration of the block.
        quota: A resource budget charged (alongside any enclosing quotas) for the
            duration of the block.
        rate_limit: One or more :class:`RateLimit` sliding-window caps, charged
            on every guarded call alongside any enclosing limits.
        deadline: One or more :class:`Deadline` wall-clock budgets.
        loop_guard: One or more :class:`LoopGuard` circuit breakers that trip on
            repeated identical tool calls.
        concurrency: One or more :class:`ConcurrencyLimit` caps on how many
            guarded tool calls may run at once (share one across agents to cap
            their combined parallelism).
        approval: One or more :class:`ApprovalGate` human-in-the-loop gates that
            must approve a matching capability before its tool runs.
        reasoning: One or more :class:`ReasoningGate` gates that require the agent
            to supply a ``rationale=`` justifying a matching capability's call.
        enforce: ``False`` puts the block in *monitor* (dry-run) mode — guarded
            tool calls are not blocked, but a would-be permission denial is
            recorded to audit. Nested blocks can switch monitor → enforce
            (tighten) but never enforce → monitor (the one-way ratchet).
        audit: Audit sinks that receive every safety decision made inside the block.

    Yields:
        The effective :class:`Policy` in force inside the block.
    """
    outer = current_policy()
    top_level = outer is _ROOT
    base = outer
    if top_level:
        # Top-level context: trusted host code defines the trust ceiling. Start
        # from full authority so the supplied permissions are granted as-is;
        # every nested context can then only narrow from here. Monitor mode, like
        # permissions, is the host's choice to make here.
        base = Policy(
            permissions=PermissionSet.allow_all(),
            enforce=(enforce if enforce is not None else True),
        )
    if policy is not None:
        # Even an explicitly supplied policy may only narrow the current one;
        # all of its restrictive fields are carried over (and appended), never
        # its permissions widened.
        effective = base.narrow(
            policy.permissions,
            prompt_guards=policy.prompt_guards,
            input_guards=policy.input_guards,
            output_guards=policy.output_guards,
            quotas=policy.quotas,
            rate_limits=policy.rate_limits,
            deadlines=policy.deadlines,
            concurrency_limits=policy.concurrency_limits,
            risk_budgets=policy.risk_budgets,
            loop_guards=policy.loop_guards,
            approvals=policy.approvals,
            reasonings=policy.reasonings,
            constitutions=policy.constitutions,
            previews=policy.previews,
            auditors=policy.auditors,
        )
    else:
        effective = base
    effective = effective.narrow(
        permissions,
        prompt_guards=prompt_guards,
        input_guards=input_guards,
        output_guards=output_guards,
        quotas=(quota,) if quota is not None else (),
        rate_limits=_as_tuple(rate_limit, RateLimit),
        deadlines=_as_tuple(deadline, Deadline),
        concurrency_limits=_as_tuple(concurrency, ConcurrencyLimit),
        risk_budgets=_as_tuple(risk_budget, RiskBudget),
        loop_guards=_as_tuple(loop_guard, LoopGuard),
        approvals=_as_tuple(approval, ApprovalGate),
        reasonings=_as_tuple(reasoning, ReasoningGate),
        constitutions=_as_tuple(constitution, ConstitutionGate),
        previews=_as_tuple(preview, PreviewGate),
        auditors=audit,
    )
    # Monitor mode can only tighten in nested blocks: a child may switch
    # monitor -> enforce, never enforce -> monitor.
    if not top_level and enforce is not None:
        effective = replace(effective, enforce=(outer.enforce or enforce))
    token = _current.set(effective)
    try:
        yield effective
    finally:
        _current.reset(token)
