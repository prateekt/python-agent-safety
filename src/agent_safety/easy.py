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
``seconds=``         a time budget, in seconds
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

import inspect
from contextlib import contextmanager
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Union

from .approval import ApprovalGate, ApprovalRequest
from .audit import AuditEvent, AuditSink
from .constitution import ConstitutionGate
from .context import safety_context
from .decorators import guarded_async_tool, guarded_tool
from .guards import (
    DenyPattern,
    Guard,
    Honeytoken,
    MaxLength,
    PromptInjectionGuard,
    RedactPII,
    SecretScanner,
    UnicodeSanitizer,
)
from .limits import ConcurrencyLimit, Deadline, LoopGuard, RateLimit
from .permissions import PermissionSet
from .policy import Policy
from .preview import PreviewGate
from .quota import CostBudget, Quota, RiskBudget
from .reasoning import ReasoningGate

_Names = Union[str, Iterable[str], None]


# -- @tool ----------------------------------------------------------------

def _make_tool(
    func: Callable[..., Any],
    capability: str,
    cache: bool = False,
    risk: int = 0,
    preview: Optional[Callable[..., Any]] = None,
) -> Callable[..., Any]:
    decorate = guarded_async_tool if inspect.iscoroutinefunction(func) else guarded_tool
    return decorate(capability, idempotent=cache, risk=risk, preview=preview)(func)


def tool(
    capability: Union[str, Callable[..., Any], None] = None,
    *,
    cache: bool = False,
    risk: int = 0,
    preview: Optional[Callable[..., Any]] = None,
) -> Any:
    """Mark a function as a tool an agent may call.

    ``@tool`` names the capability after the function; ``@tool("my.capability")``
    names it yourself. Works on ``def`` and ``async def`` automatically. Pass
    ``cache=True`` for a pure tool to reuse the result of identical calls,
    ``risk=N`` to weight it against a risk budget, or ``preview=fn`` to describe
    what a call would do for a preview gate.
    """
    if callable(capability):                       # bare @tool
        return _make_tool(capability, capability.__name__, cache, risk, preview)

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        return _make_tool(func, capability or func.__name__, cache, risk, preview)

    return decorator


def guard(*funcs: Callable[..., Any]) -> Any:
    """Wrap functions you already have as guarded tools — without editing them.

    Like applying ``@tool`` to each, in bulk. Handy for adding safety to an
    existing toolset::

        safe_search, safe_fetch = guard(search, fetch)

    Returns the single wrapped function, or a tuple when you pass several. Call
    them inside a ``safely(...)`` block, exactly like a ``@tool``.
    """
    wrapped = tuple(tool(f) for f in funcs)
    return wrapped[0] if len(wrapped) == 1 else wrapped


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


def _console_approver(request: ApprovalRequest) -> bool:
    answer = input(f"Allow {request.tool}({request.capability})? [y/N] ")
    return answer.strip().lower() in ("y", "yes")


def _approval(ask: Union[bool, Callable[[ApprovalRequest], Any], None]) -> Optional[ApprovalGate]:
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
    seconds: Optional[float] = None,
    at_most: Union[int, ConcurrencyLimit, None] = None,
    hide_secrets: bool = False,
    max_input: Optional[int] = None,
    block: _Names = None,
    block_injections: bool = False,
    clean_text: bool = False,
    no_repeats: Optional[int] = None,
    risk_budget: Optional[int] = None,
    budget: Union[str, float, None] = None,
    ask: Union[bool, Callable[[ApprovalRequest], Any], None] = None,
    explain: Union[bool, str, Iterable[str], None] = None,
    rule: Union[str, Iterable[str], None] = None,
    judge: Optional[Callable[..., Any]] = None,
    preview: Optional[Callable[..., Any]] = None,
    honeytoken: Optional[str] = None,
    monitor: bool = False,
    log: Any = None,
) -> Iterator[Policy]:
    """Run a block of code under simple, plain-English safety rules.

    See the module docstring for every keyword. All are optional; the common
    case is ``with safely(allow="read_file", calls=10):``.
    """
    quota = Quota(max_calls=calls, max_tokens=tokens) if (calls or tokens) else None
    rate: Optional[RateLimit] = None
    if per_second is not None:
        rate = RateLimit(per_second=per_second)
    elif per_minute is not None:
        rate = RateLimit(per_minute=per_minute)
    output_guards: List[Guard] = [RedactPII(), SecretScanner()] if hide_secrets else []
    input_guards = _input_guards(max_input, block, block_injections, clean_text)
    if honeytoken:
        input_guards.append(Honeytoken(honeytoken))

    with safety_context(
        _permissions(allow, deny),
        quota=quota,
        rate_limit=rate,
        deadline=Deadline(seconds) if seconds else None,
        concurrency=_concurrency(at_most),
        risk_budget=RiskBudget(risk_budget) if risk_budget else None,
        cost_budget=CostBudget(_money(budget)) if budget is not None else None,
        input_guards=input_guards,
        output_guards=output_guards,
        loop_guard=LoopGuard(no_repeats) if no_repeats else None,
        approval=_approval(ask),
        reasoning=_reasoning(explain),
        constitution=_constitution(rule, judge),
        preview=_preview(preview),
        enforce=False if monitor else None,
        audit=_audit(log),
    ) as policy:
        yield policy
