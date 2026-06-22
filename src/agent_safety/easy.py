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
``ask=``             ask before acting: ``True`` (console) or your own yes/no function
``explain=``         require a ``rationale="..."`` with each call
``log=``             watch what happens: ``True`` (print) or your own recorder
===================  ===================================================

Everything the power API offers is still here (``safety_context``, ``PermissionSet``,
``Quota``, the guard objects …) — ``safely`` just builds those for you.
"""

from __future__ import annotations

import inspect
from contextlib import contextmanager
from typing import Any, Callable, Iterable, Iterator, List, Optional, Union

from .approval import ApprovalGate, ApprovalRequest
from .audit import AuditEvent, AuditSink
from .context import safety_context
from .decorators import guarded_async_tool, guarded_tool
from .guards import (
    DenyPattern,
    Guard,
    MaxLength,
    PromptInjectionGuard,
    RedactPII,
    SecretScanner,
    UnicodeSanitizer,
)
from .limits import ConcurrencyLimit, Deadline, LoopGuard, RateLimit
from .permissions import PermissionSet
from .policy import Policy
from .quota import Quota
from .reasoning import ReasoningGate

_Names = Union[str, Iterable[str], None]


# -- @tool ----------------------------------------------------------------

def _make_tool(
    func: Callable[..., Any], capability: str, cache: bool = False
) -> Callable[..., Any]:
    decorate = guarded_async_tool if inspect.iscoroutinefunction(func) else guarded_tool
    return decorate(capability, idempotent=cache)(func)


def tool(
    capability: Union[str, Callable[..., Any], None] = None, *, cache: bool = False
) -> Any:
    """Mark a function as a tool an agent may call.

    ``@tool`` names the capability after the function; ``@tool("my.capability")``
    names it yourself. Works on ``def`` and ``async def`` automatically. Pass
    ``cache=True`` for a pure tool to reuse the result of identical calls.
    """
    if callable(capability):                       # bare @tool
        return _make_tool(capability, capability.__name__, cache)

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        return _make_tool(func, capability or func.__name__, cache)

    return decorator


# -- safely(...) ----------------------------------------------------------

def _as_list(value: _Names) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


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
    ask: Union[bool, Callable[[ApprovalRequest], Any], None] = None,
    explain: Union[bool, str, Iterable[str], None] = None,
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

    with safety_context(
        _permissions(allow, deny),
        quota=quota,
        rate_limit=rate,
        deadline=Deadline(seconds) if seconds else None,
        concurrency=_concurrency(at_most),
        input_guards=_input_guards(max_input, block, block_injections, clean_text),
        output_guards=output_guards,
        loop_guard=LoopGuard(no_repeats) if no_repeats else None,
        approval=_approval(ask),
        reasoning=_reasoning(explain),
        enforce=False if monitor else None,
        audit=_audit(log),
    ) as policy:
        yield policy
