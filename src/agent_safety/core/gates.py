"""Gates: "should this tool call happen?" hooks, all built the same way.

A *gate* names a set of capabilities (glob patterns, like
:class:`~agent_safety.permissions.PermissionSet`) and a callable that must say
yes before a matching tool call runs. The library ships four, and they differ
only in what the callable sees:

* :class:`ApprovalGate` — ``approver(action) -> bool``: a human / service says
  yes or no to the raw call.
* :class:`PreviewGate` — ``approver(preview_text, action) -> bool``: same, but
  shown the tool's own "what would this do?" description.
* :class:`ConstitutionGate` — ``judge(action, rule) -> bool``: a model judges
  the call against plain-English rules.
* :class:`ReasoningGate` — validates the agent's ``rationale="..."`` argument
  (optionally via ``validator(rationale, action) -> bool``).

Every hook receives the same :class:`~agent_safety.action.Action` object, every
gate shares the same :meth:`Gate.covers` capability matching, and every gate's
callable may be **sync or async** (async ones require an async tool — calling
one from a sync tool raises ``RuntimeError`` rather than silently skipping the
check). All four are enforced at the same point of the single tool-call
pipeline (:mod:`agent_safety.pipeline`), between the permission check and the
tool's execution.
"""

from __future__ import annotations

import inspect
from fnmatch import fnmatchcase
from typing import Any, Awaitable, Callable, Iterable, List, Optional, Tuple, Union

from .action import Action

RATIONALE_KWARG = "rationale"

# An approver returns truthy to allow, falsy to deny — sync or via a coroutine.
Approver = Callable[[Action], Union[bool, Awaitable[bool]]]

# A judge decides whether an action obeys a rule — truthy = compliant.
Judge = Callable[[Any, str], Any]

# An approver that sees the preview text and the action; truthy = go ahead.
PreviewApprover = Callable[[str, Any], Any]

# A validator decides whether a rationale is adequate for a given action.
RationaleValidator = Callable[[str, Action], bool]


class Gate:
    """Shared shape of every gate: capability patterns + an async-aware hook.

    Subclasses set :attr:`patterns`, :attr:`name`, and :attr:`is_async` through
    :meth:`_init_gate`; :meth:`covers` is identical for all of them.
    """

    patterns: Tuple[str, ...]
    name: str
    is_async: bool

    def _init_gate(
        self,
        require: Iterable[str],
        hook: Optional[Callable[..., Any]],
        kind: str,
        *,
        allow_empty: bool = False,
    ) -> None:
        self.patterns = tuple(p.strip() for p in require if p and p.strip())
        if not self.patterns:
            if not allow_empty:
                raise ValueError(f"a {type(self).__name__} must require at least one capability")
            self.patterns = ("*",)
        self.is_async = inspect.iscoroutinefunction(hook) if hook is not None else False
        self.name = f"{kind}(" + ", ".join(self.patterns) + ")"

    def covers(self, capability: str) -> bool:
        """Whether this gate applies to *capability*."""
        return any(fnmatchcase(capability, p) for p in self.patterns)


class ApprovalGate(Gate):
    """Require explicit approval before any tool whose capability it covers runs.

    Args:
        require: Capability patterns (glob ``*`` wildcards) that need approval.
        approver: Callable taking an :class:`~agent_safety.action.Action` and
            returning a truthy/falsy decision; may be a coroutine function.
        reason: Optional human-readable note attached to each request.
    """

    def __init__(self, require: Iterable[str], approver: Approver, *, reason: str = ""):
        self._init_gate(require, approver, "approval_gate")
        self.approver = approver
        self.reason = reason


class PreviewGate(Gate):
    """Require approval of a tool's preview before the tool runs.

    Only tools that declare a ``preview`` function are gated; others run
    normally. A rejected preview raises
    :class:`~agent_safety.exceptions.ApprovalDenied`.

    Args:
        approver: ``(preview_text, action) -> bool``; truthy means proceed.
        require: Capability patterns (glob ``*``) the gate applies to.
    """

    def __init__(self, approver: PreviewApprover, *, require: Iterable[str] = ("*",)):
        self._init_gate(require, approver, "preview_gate", allow_empty=True)
        self.approver = approver


class ConstitutionGate(Gate):
    """Require a model judge to clear each matching tool call against *rules*.

    Args:
        rules: One rule, or several; the call must obey every matching rule.
        judge: ``(action, rule) -> bool``; truthy means the action complies.
        require: Capability patterns (glob ``*``) the rules apply to.
    """

    def __init__(
        self,
        rules: Union[str, Iterable[str]],
        judge: Judge,
        *,
        require: Iterable[str] = ("*",),
    ):
        raw = [rules] if isinstance(rules, str) else list(rules)
        self.rules: List[str] = [r.strip() for r in raw if r and r.strip()]
        if not self.rules:
            raise ValueError("a ConstitutionGate needs at least one rule")
        self._init_gate(require, judge, "constitution", allow_empty=True)
        self.judge = judge
        self.name = "constitution(" + "; ".join(self.rules) + ")"


class ReasoningGate(Gate):
    """Require an explanation before any tool whose capability it covers runs.

    Args:
        require: Capability patterns (glob ``*``) that must be justified.
        min_length: Minimum rationale length, in stripped characters.
        validator: Optional ``(rationale, action) -> bool`` for richer checks
            (e.g. an LLM-as-judge or a keyword requirement).
    """

    def __init__(
        self,
        require: Iterable[str],
        *,
        min_length: int = 1,
        validator: Optional[RationaleValidator] = None,
    ):
        self._init_gate(require, None, "reasoning_gate")
        if min_length < 1:
            raise ValueError("min_length must be >= 1")
        self.min_length = min_length
        self.validator = validator

    def evaluate(self, rationale: Optional[str], request: Action) -> Optional[str]:
        """Return ``None`` if the rationale is adequate, else why it is not."""
        text = (rationale or "").strip()
        if not text:
            return "a rationale is required for this action"
        if len(text) < self.min_length:
            return f"rationale is too short (< {self.min_length} characters)"
        if self.validator is not None and not self.validator(text, request):
            return "rationale was rejected by the validator"
        return None
