"""The easy front door: ``@tool`` and ``safely(...)``.

This is the *same* safety engine as the rest of the library, but every option is a
plain keyword — nothing to import, nothing to construct. If you can write a ``with``
block and a decorator, you can use it::

    from agent_safety import tool, safely

    @tool
    def read_file(path):
        return open(path).read()

    with safely(allow="read_file", calls=10, hide_secrets=True):
        text = read_file("notes.txt")   # allowed, budget-counted, secrets scrubbed

Two things to know:

* a ``@tool`` only runs **inside** a ``safely(...)`` block that allows it — that's
  the whole point (outside one, nothing is allowed, so accidents can't happen);
* every keyword below is optional. Reach for one when you need it; ignore the rest.

``safely(...)`` keywords:

===================  ===================================================
``allow=``           what the code may do — a name, a list, or ``"everything"``
``deny=``            things to forbid even if allowed (deny always wins)
``calls=``           most tool calls allowed
``tokens=``          most model tokens allowed (you report them)
``per_second=``      most calls per second  (also ``per_minute=``)
``total_seconds=``   a *total* time budget, in seconds
``timeout=``         max seconds for any *single* call — stops hangs/deadlocks
``memory=``          cap Python-heap growth in the block: ``memory="500MB"``
``at_most=``         most tool calls running at once (waits for a free slot)
``monitor=``         dry run: don't block anything, just log what *would* be blocked
``hide_secrets=``    scrub emails / keys / secrets out of results
``max_input=``       reject inputs longer than this many characters
``block=``           text pattern(s) to reject
``block_injections=`` reject "ignore previous instructions"-style inputs
``clean_text=``      strip hidden/invisible characters from inputs
``no_repeats=``      stop after N identical calls (runaway loop)
``risk_budget=``     cap total *risk* (weight tools with ``@tool(..., risk=N)``)
``budget=``          cap *money spent*: ``budget="$100"`` (pair with ``metered(call, model=...)``)
``ask=``             ask before acting: ``True`` (console) or your own yes/no function
``explain=``         require a ``rationale="..."`` with each call
``rule=`` + ``judge=``   enforce a plain-English rule via a model judge
``preview=``         approve a tool's "what would this do?" preview before it runs
``honeytoken=``      trip if a planted canary secret ever appears (exfiltration)
``log=``             watch what happens: ``True`` (print) or your own recorder
===================  ===================================================

Everything the power API offers is still here (``safety_context``, ``PermissionSet``,
``Quota``, the guard objects …) — ``safely`` just builds those for you.
"""

from __future__ import annotations

import warnings
from contextlib import contextmanager
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Union

from .core.action import Action
from .core.audit import AuditEvent, AuditSink
from .core.context import safety_context
from .core.exceptions import PermissionDenied
from .core.gates import ApprovalGate, ConstitutionGate, PreviewGate, ReasoningGate
from .core.guards import (
    DenyPattern,
    Guard,
    Honeytoken,
    MaxLength,
    PromptInjectionGuard,
    RedactPII,
    SecretScanner,
    UnicodeSanitizer,
)
from .core.limits import ConcurrencyLimit, Deadline, LoopGuard, RateLimit
from .core.observability import StructuredLog
from .core.permissions import PermissionSet
from .core.pipeline import make_tool_wrapper
from .core.policy import Policy
from .core.quota import CostBudget, Quota, QuotaLike, RiskBudget
from .distributed.backends import BackendQuota, BudgetBackend, BudgetLimits
from .distributed.config import DistributedConfig
from .distributed.envelope import CapabilityEnvelope, EnvelopeVerifier
from .distributed.nonces import NonceStore
from .distributed.run import RunContext, run_context

_Names = Union[str, Iterable[str], None]


# -- @tool ----------------------------------------------------------------

def tool(
    capability: Union[str, Callable[..., Any], None] = None,
    *,
    cache: bool = False,
    risk: int = 0,
    preview: Optional[Callable[..., Any]] = None,
    input_guards: Iterable[Guard] = (),
    output_guards: Iterable[Guard] = (),
) -> Any:
    """Mark a function as a tool an agent may call — the one tool decorator.

    ``@tool`` names the capability after the function; ``@tool("my.capability")``
    names it yourself. Works on ``def`` and ``async def`` automatically. Pass
    ``cache=True`` for a pure tool to reuse the result of identical calls,
    ``risk=N`` to weight it against a risk budget, ``preview=fn`` to describe
    what a call would do for a preview gate, or ``input_guards=`` /
    ``output_guards=`` to attach per-tool guards on top of the policy's.
    """
    def decorate(func: Callable[..., Any], cap: str) -> Callable[..., Any]:
        return make_tool_wrapper(
            func, cap,
            input_guards=input_guards, output_guards=output_guards,
            cache=cache, risk=risk, preview=preview,
        )

    if callable(capability):                       # bare @tool
        return decorate(capability, capability.__name__)

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        return decorate(func, capability or func.__name__)

    return decorator


def guard_tools(*funcs: Callable[..., Any]) -> Any:
    """Wrap functions you already have as guarded tools — without editing them.

    Like applying ``@tool`` to each, in bulk. Handy for adding safety to an
    existing toolset::

        safe_search, safe_fetch = guard_tools(search, fetch)

    Returns the single wrapped function, or a tuple when you pass several. Call
    them inside a ``safely(...)`` block, exactly like a ``@tool``.
    """
    wrapped = tuple(tool(f) for f in funcs)
    return wrapped[0] if len(wrapped) == 1 else wrapped


def guard(*funcs: Callable[..., Any]) -> Any:
    """Deprecated alias of :func:`guard_tools` (the old name collided with the
    :class:`~agent_safety.guards.Guard` protocol)."""
    warnings.warn(
        "guard() is deprecated; use guard_tools() instead",
        DeprecationWarning,
        stacklevel=2,
    )
    return guard_tools(*funcs)


class Profiles:
    """Ready-made bundles of ``safely(...)`` settings — sensible defaults so you
    don't have to assemble them. Splat one in and add your ``allow=``::

        with safely(allow="search", **Profiles.hardened()):
            ...
    """

    @staticmethod
    def hardened() -> Dict[str, Any]:
        """Capability-agnostic safety hygiene: scrub secrets, block prompt
        injection, strip hidden characters, and stop runaway loops. Pair with
        your own ``allow=`` to choose *what* the agent may do."""
        return {
            "hide_secrets": True,
            "block_injections": True,
            "clean_text": True,
            "no_repeats": 5,
        }

    @staticmethod
    def observe() -> Dict[str, Any]:
        """Watch first, block nothing: monitor (dry-run) mode with printed
        decisions. Run your agent, read the log, then tighten ``allow=``."""
        return {"monitor": True, "log": True}


# -- safely(...) ----------------------------------------------------------

def _as_list(value: _Names) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


def _money(value: Union[str, float, int]) -> float:
    """Parse a dollar amount: ``'$100'``, ``'$1,000.50'``, ``100``, ``100.0``."""
    if isinstance(value, bool):  # guard: bool is an int subclass
        raise TypeError("budget= must be a dollar amount, not a bool")
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().lstrip("$").replace(",", "").strip()
    try:
        return float(text)
    except ValueError:
        raise ValueError(
            f"budget= must be a dollar amount like '$100' or 100.0, got {value!r}"
        ) from None


_BYTE_UNITS = (("TB", 1024 ** 4), ("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024), ("B", 1))


def _bytes(value: Union[str, int]) -> int:
    """Parse a memory size: ``'500MB'``, ``'1GB'``, ``'512KB'``, or a byte count."""
    if isinstance(value, bool):
        raise TypeError("memory= must be a size, not a bool")
    if isinstance(value, int):
        return value
    text = str(value).strip().upper().replace(" ", "")
    for unit, multiplier in _BYTE_UNITS:
        if text.endswith(unit):
            return int(float(text[: -len(unit)]) * multiplier)
    try:
        return int(float(text))  # a bare number is bytes
    except ValueError:
        raise ValueError(
            f"memory= must be a size like '500MB' or a byte count, got {value!r}"
        ) from None


def _permissions(allow: _Names, deny: _Names) -> Optional[PermissionSet]:
    allow_list = _as_list(allow)
    deny_list = _as_list(deny)
    if not allow_list and not deny_list:
        return None  # nothing specified -> inherit (top-level = allow all)
    allow_list = ["*" if a.lower() in ("everything", "all", "*") else a for a in allow_list]
    if not allow_list:                             # deny-only -> allow all, then subtract
        allow_list = ["*"]
    return PermissionSet.of(*allow_list, deny=deny_list)


def _input_guards(
    max_input: Optional[int], block: _Names, block_injections: bool, clean_text: bool
) -> List[Guard]:
    guards: List[Guard] = []
    if clean_text:
        guards.append(UnicodeSanitizer())
    if block_injections:
        guards.append(PromptInjectionGuard())
    for pattern in _as_list(block):
        guards.append(DenyPattern(pattern))
    if max_input:
        guards.append(MaxLength(max_input))
    return guards


def _concurrency(at_most: Union[int, ConcurrencyLimit, None]) -> Optional[ConcurrencyLimit]:
    if at_most is None:
        return None
    if isinstance(at_most, ConcurrencyLimit):
        return at_most  # a shared limit, e.g. capping several agents together
    return ConcurrencyLimit(at_most)


def _console_approver(request: Action) -> bool:
    answer = input(f"Allow {request.tool}({request.capability})? [y/N] ")
    return answer.strip().lower() in ("y", "yes")


def _approval(ask: Union[bool, Callable[[Action], Any], None]) -> Optional[ApprovalGate]:
    if not ask:
        return None
    approver = _console_approver if ask is True else ask
    if not callable(approver):
        raise TypeError("ask= must be True or a function(request) -> yes/no")
    return ApprovalGate(require=["*"], approver=approver)


def _reasoning(explain: Union[bool, str, Iterable[str], None]) -> Optional[ReasoningGate]:
    if not explain:
        return None
    patterns = ["*"] if explain is True else _as_list(explain)
    return ReasoningGate(require=patterns)


def _constitution(
    rule: Union[str, Iterable[str], None], judge: Optional[Callable[..., Any]]
) -> Optional[ConstitutionGate]:
    if not rule:
        return None
    if judge is None:
        raise TypeError("rule= needs a judge= function(action, rule) -> ok/not ok")
    return ConstitutionGate(rule, judge)


def _preview(approver: Optional[Callable[..., Any]]) -> Optional[PreviewGate]:
    if approver is None:
        return None
    return PreviewGate(approver)


class _PrintSink:
    """A dead-simple audit sink that prints each decision."""

    def __call__(self, event: AuditEvent) -> None:
        extra = event.capability or event.detail or ""
        print(f"[safely] {event.action}: {event.decision} {extra}".rstrip())


def _audit(log: Any) -> List[AuditSink]:
    if not log:
        return []
    if log is True:
        return [_PrintSink()]
    if callable(log):
        return [log]
    return list(log)


@contextmanager
def safely(
    allow: _Names = None,
    deny: _Names = None,
    *,
    calls: Optional[int] = None,
    tokens: Optional[int] = None,
    per_second: Optional[int] = None,
    per_minute: Optional[int] = None,
    total_seconds: Optional[float] = None,
    seconds: Optional[float] = None,  # deprecated alias of total_seconds
    at_most: Union[int, ConcurrencyLimit, None] = None,
    hide_secrets: bool = False,
    max_input: Optional[int] = None,
    block: _Names = None,
    block_injections: bool = False,
    clean_text: bool = False,
    no_repeats: Optional[int] = None,
    risk_budget: Optional[int] = None,
    budget: Union[str, float, None] = None,
    timeout: Optional[float] = None,
    memory: Union[str, int, None] = None,
    ask: Union[bool, Callable[[Action], Any], None] = None,
    explain: Union[bool, str, Iterable[str], None] = None,
    rule: Union[str, Iterable[str], None] = None,
    judge: Optional[Callable[..., Any]] = None,
    preview: Optional[Callable[..., Any]] = None,
    honeytoken: Optional[str] = None,
    monitor: bool = False,
    log: Any = None,
    run: Optional[RunContext] = None,
    envelope: Optional[CapabilityEnvelope] = None,
    envelope_keys: Optional[Dict[str, bytes]] = None,
    backend: Optional[BudgetBackend] = None,
    nonce_store: Optional[NonceStore] = None,
    distributed: Optional[DistributedConfig] = None,
) -> Iterator[Policy]:
    """Run a block of code under simple, plain-English safety rules.

    See the module docstring for every keyword. All are optional; the common
    case is ``with safely(allow="read_file", calls=10):``.

    Distributed keywords:

    * ``distributed=`` — a :class:`~agent_safety.distributed.DistributedConfig`
      controlling rollout mode, signing keys, and shared Redis. When omitted,
      one is read from the ``AGENT_SAFETY_*`` environment variables.
    * ``run=`` — install a :class:`~agent_safety.run.RunContext` for audit correlation.
    * ``envelope=`` — verify a signed :class:`~agent_safety.envelope.CapabilityEnvelope`
      on entry (hot path; no gateway hop per tool call).
    * ``envelope_keys=`` — map of ``kid -> secret`` for envelope verification
      (falls back to the config's signing keys).
    * ``backend=`` — shared :class:`~agent_safety.backends.BudgetBackend`; with
      ``run=``, tool call/token charges go to the backend (Redis/memory) instead of
      a process-local quota. When ``envelope=`` is also set, call budget was already
      charged at mint time — the backend is used for token charges only.
    * ``nonce_store=`` — shared :class:`~agent_safety.nonces.NonceStore` for
      cross-worker envelope replay protection (defaults to the config's Redis
      when set, else a process-local store).

    Rollout modes: ``enforce`` requires an envelope; ``canary`` requires one for
    a hash-fraction of ``task_id``s; ``shadow`` verifies when present but does
    not block on failure.
    """
    if seconds is not None:
        warnings.warn(
            "safely(seconds=...) is deprecated; use total_seconds= (it is the "
            "total time budget, distinct from the per-call timeout=)",
            DeprecationWarning,
            stacklevel=3,
        )
        if total_seconds is None:
            total_seconds = seconds
    cfg = distributed if distributed is not None else DistributedConfig.from_env()
    keys = dict(envelope_keys) if envelope_keys else cfg.signing_keys
    task_id = run.task_id if run is not None else (envelope.task_id if envelope else None)

    if cfg.should_require_envelope(task_id) and envelope is None:
        raise PermissionDenied("*", "capability envelope required in distributed mode")

    store = nonce_store
    if store is None and envelope is not None:
        store = cfg.nonce_store()

    verifier: Optional[EnvelopeVerifier] = None
    if envelope is not None and keys:
        verifier = EnvelopeVerifier(keys, nonce_store=store)

    run_mgr = run_context(run) if run is not None else None
    if run_mgr is not None:
        run_mgr.__enter__()

    try:
        if envelope is not None:
            if verifier is not None:
                try:
                    verifier.verify(envelope)
                except PermissionDenied as exc:
                    # Shadow: verify when present but do not block on failure.
                    # Enforce / canary (required) / explicit local envelope: fail closed.
                    if cfg.should_require_envelope(task_id):
                        raise
                    if cfg.should_shadow_envelope():
                        StructuredLog(
                            "warning",
                            f"envelope verify failed (shadow): {exc}",
                            task_id=task_id,
                            request_id=run.request_id if run else None,
                            capability=envelope.capability,
                            decision="shadow_allow",
                        ).emit()
                    else:
                        raise
            elif cfg.should_require_envelope(task_id):
                raise PermissionDenied(
                    envelope.capability,
                    "envelope_keys / AGENT_SAFETY_SIGNING_KEYS required to verify envelope",
                )

        shared_quota: Optional[BackendQuota] = None
        if backend is not None and run is not None:
            limits = BudgetLimits(
                max_calls=calls,
                max_tokens=tokens,
                rate_per_second=per_second if per_second is not None else (
                    per_minute if per_minute is not None else None
                ),
                rate_window=1.0 if per_second is not None else (60.0 if per_minute else 1.0),
                max_identical=no_repeats,
                max_concurrent=at_most if isinstance(at_most, int) else None,
                max_risk=risk_budget,
            )
            # Envelope mint already charged the call; only meter tokens on the backend.
            shared_quota = BackendQuota(
                backend,
                task_id=run.task_id,
                request_id=run.request_id,
                org_id=run.org_id or "",
                agent_id=run.agent_id,
                limits=limits,
                charge_calls=envelope is None,
            )

        quota: Optional[QuotaLike] = None
        if shared_quota is not None:
            quota = shared_quota
        elif calls or tokens:
            quota = Quota(max_calls=calls, max_tokens=tokens)

        rate: Optional[RateLimit] = None
        # When backend owns rate limits, skip local RateLimit to avoid double-charge.
        if shared_quota is None:
            if per_second is not None:
                rate = RateLimit(per_second=per_second)
            elif per_minute is not None:
                rate = RateLimit(per_minute=per_minute)

        output_guards: List[Guard] = [RedactPII(), SecretScanner()] if hide_secrets else []
        input_guards = _input_guards(max_input, block, block_injections, clean_text)
        if honeytoken:
            input_guards.append(Honeytoken(honeytoken))

        if envelope is not None:
            perms: Optional[PermissionSet] = PermissionSet.of(*envelope.allowed_capabilities)
        else:
            perms = _permissions(allow, deny)

        loop = None if shared_quota is not None else (LoopGuard(no_repeats) if no_repeats else None)
        conc = None if shared_quota is not None else _concurrency(at_most)
        risk = None if shared_quota is not None else (
            RiskBudget(risk_budget) if risk_budget else None
        )

        with safety_context(
            perms,
            quota=quota,
            rate_limit=rate,
            deadline=Deadline(total_seconds) if total_seconds else None,
            concurrency=conc,
            risk_budget=risk,
            cost_budget=CostBudget(_money(budget)) if budget is not None else None,
            timeout=timeout,
            memory=_bytes(memory) if memory is not None else None,
            input_guards=input_guards,
            output_guards=output_guards,
            loop_guard=loop,
            approval=_approval(ask),
            reasoning=_reasoning(explain),
            constitution=_constitution(rule, judge),
            preview=_preview(preview),
            enforce=False if monitor else None,
            audit=_audit(log),
        ) as policy:
            yield policy
    finally:
        if run_mgr is not None:
            run_mgr.__exit__(None, None, None)
