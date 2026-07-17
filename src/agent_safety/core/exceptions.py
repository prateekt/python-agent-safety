"""Exception hierarchy for agent-safety violations.

Every safety failure is an :class:`AgentSafetyError`, so a host application can
catch the whole family with one ``except`` clause and still discriminate between
a *permission* failure and a *content* failure when it wants to.

Every exception is also **machine-readable** — for an agent loop that wants to
repair and retry rather than crash:

* :attr:`~AgentSafetyError.code` — a stable snake_case identifier (e.g.
  ``"permission_denied"``), safe to match on across versions;
* :attr:`~AgentSafetyError.capability` — the capability involved, when known;
* :attr:`~AgentSafetyError.retryable` — ``True`` when retrying the same action
  can succeed — after waiting (rate limit, timeout), or after supplying what
  was missing (a ``rationale=``, a shorter input). ``False`` means the policy
  itself said no; retrying unchanged is pointless.
* :meth:`~AgentSafetyError.to_dict` — the whole story as JSON-ready data, ideal
  for handing back to a model as a tool-error result.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


class AgentSafetyError(Exception):
    """Base class for everything this library raises.

    Attributes:
        code: Stable machine-readable identifier for the failure kind.
        capability: The capability involved, when known.
        retryable: Whether retrying (after waiting or fixing the input) can succeed.
    """

    code: str = "agent_safety_error"
    retryable: bool = False
    capability: Optional[str] = None

    #: Extra attribute names each subclass contributes to :meth:`to_dict`.
    _detail_fields: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        """A JSON-ready description — hand it to a model as a tool-error result."""
        d: Dict[str, Any] = {
            "error": self.code,
            "message": str(self),
            "retryable": self.retryable,
        }
        if self.capability is not None:
            d["capability"] = self.capability
        for name in self._detail_fields:
            value = getattr(self, name, None)
            if value is not None:
                d[name] = value
        return d


class PermissionDenied(AgentSafetyError):
    """Raised when an agent attempts a capability the active policy forbids.

    Attributes:
        capability: The capability that was requested (e.g. ``"shell.exec"``).
        reason: Human-readable explanation of why it was denied.
    """

    code = "permission_denied"
    retryable = False
    _detail_fields = ("reason",)

    def __init__(self, capability: str, reason: str = "not permitted in this context"):
        self.capability = capability
        self.reason = reason
        super().__init__(f"capability {capability!r} denied: {reason}")


class QuotaExceeded(AgentSafetyError):
    """Raised when an agent exhausts a resource budget scoped to a context.

    Attributes:
        resource: What ran out (e.g. ``"calls"`` or ``"tokens"``).
        limit: The budget that was exceeded.
        requested: How much the offending operation asked for.
    """

    code = "quota_exceeded"
    retryable = False
    _detail_fields = ("resource", "limit", "requested")

    def __init__(self, resource: str, limit: int, requested: int):
        self.resource = resource
        self.limit = limit
        self.requested = requested
        super().__init__(
            f"{resource} quota exceeded: requested {requested} but limit is {limit}"
        )


class GuardViolation(AgentSafetyError):
    """Raised when a prompt, input, or output fails a guard that cannot sanitize it.

    Retryable: a different (shorter, cleaner) input for the same capability may
    well pass.

    Attributes:
        guard: Name of the guard that tripped.
        stage: Where it tripped (``"prompt"``, ``"input"`` or ``"output"``).
        reason: Human-readable explanation.
    """

    code = "guard_violation"
    retryable = True
    _detail_fields = ("guard", "stage", "reason")

    def __init__(self, guard: str, stage: str, reason: str, *, value: Optional[object] = None):
        self.guard = guard
        self.stage = stage
        self.reason = reason
        self.value = value
        super().__init__(f"[{stage}] guard {guard!r} blocked value: {reason}")


class ApprovalDenied(AgentSafetyError):
    """Raised when a human-in-the-loop approver rejects a guarded tool call.

    Attributes:
        capability: The capability whose call was up for approval.
        tool: Name of the tool the agent tried to invoke.
        reason: Human-readable explanation of the denial.
    """

    code = "approval_denied"
    retryable = False
    _detail_fields = ("tool", "reason")

    def __init__(self, capability: str, tool: str, reason: str = "approval was not granted"):
        self.capability = capability
        self.tool = tool
        self.reason = reason
        super().__init__(f"call to {tool!r} ({capability!r}) denied: {reason}")


class RateLimitExceeded(AgentSafetyError):
    """Raised when calls arrive faster than a context's :class:`RateLimit` allows.

    Retryable: wait :attr:`retry_after` seconds and try again.

    Attributes:
        limit: The number of calls permitted per window.
        window: The window length in seconds.
        retry_after: Seconds to wait before the oldest call ages out of the window.
    """

    code = "rate_limited"
    retryable = True
    _detail_fields = ("limit", "window", "retry_after")

    def __init__(self, limit: int, window: float, retry_after: float):
        self.limit = limit
        self.window = window
        self.retry_after = retry_after
        super().__init__(
            f"rate limit exceeded: more than {limit} call(s) per {window:g}s "
            f"(retry in {retry_after:.3g}s)"
        )


class ConstitutionViolation(AgentSafetyError):
    """Raised when a model judge rules a tool call breaks a plain-English rule.

    Attributes:
        capability: The capability of the call that was judged.
        tool: The tool the agent tried to invoke.
        rule: The rule the call was found to violate.
    """

    code = "constitution_violation"
    retryable = False
    _detail_fields = ("tool", "rule")

    def __init__(self, capability: str, tool: str, rule: str):
        self.capability = capability
        self.tool = tool
        self.rule = rule
        super().__init__(f"call to {tool!r} ({capability!r}) violates the rule: {rule!r}")


class HoneytokenTripped(AgentSafetyError):
    """Raised when a planted canary secret appears in a guarded value.

    A honeytoken never legitimately flows through the agent, so its appearance is
    strong evidence the agent was hijacked and is trying to exfiltrate.

    Attributes:
        label: A name for the tripped canary (not the secret value itself).
    """

    code = "honeytoken_tripped"
    retryable = False
    _detail_fields = ("label",)

    def __init__(self, label: str):
        self.label = label
        super().__init__(f"honeytoken {label!r} appeared in a value — possible exfiltration")


class RiskBudgetExceeded(AgentSafetyError):
    """Raised when an agent's cumulative action *risk* exceeds its budget.

    Attributes:
        limit: The risk budget.
        requested: The cumulative risk the offending call would reach.
    """

    code = "risk_budget_exceeded"
    retryable = False
    _detail_fields = ("limit", "requested")

    def __init__(self, limit: int, requested: int):
        self.limit = limit
        self.requested = requested
        super().__init__(f"risk budget exceeded: would reach {requested} but limit is {limit}")


class CostBudgetExceeded(AgentSafetyError):
    """Raised when cumulative spend exceeds a money budget (in USD).

    Attributes:
        limit: The budget, in dollars.
        spent: Total spend that pushed it over (in dollars).
    """

    code = "cost_budget_exceeded"
    retryable = False
    _detail_fields = ("limit", "spent")

    def __init__(self, limit: float, spent: float):
        self.limit = limit
        self.spent = spent
        super().__init__(f"cost budget exceeded: spent ${spent:.4f} of a ${limit:.2f} budget")


class TimeoutExceeded(AgentSafetyError):
    """Raised when a single guarded call runs longer than its ``timeout``.

    Retryable: a transient hang may not repeat.

    Attributes:
        seconds: The per-call timeout that was exceeded.
    """

    code = "timeout"
    retryable = True
    _detail_fields = ("seconds",)

    def __init__(self, seconds: float):
        self.seconds = seconds
        super().__init__(f"call exceeded its {seconds}s timeout")


class MemoryBudgetExceeded(AgentSafetyError):
    """Raised when a block's Python-heap growth exceeds its ``memory`` budget.

    Attributes:
        limit: The memory budget, in bytes.
        used: Bytes allocated within the block when it tripped.
    """

    code = "memory_budget_exceeded"
    retryable = False
    _detail_fields = ("limit", "used")

    def __init__(self, limit: int, used: int):
        self.limit = limit
        self.used = used
        super().__init__(
            f"memory budget exceeded: used {used:,} bytes of a {limit:,}-byte budget"
        )


class ExplanationRequired(AgentSafetyError):
    """Raised when an agent invokes a gated tool without an adequate rationale.

    Retryable: repeat the call **with** a ``rationale="..."`` argument.

    Attributes:
        capability: The capability whose call required a rationale.
        tool: The tool the agent tried to invoke.
        reason: Why the rationale was inadequate.
    """

    code = "explanation_required"
    retryable = True
    _detail_fields = ("tool", "reason")

    def __init__(self, capability: str, tool: str, reason: str = "a rationale is required"):
        self.capability = capability
        self.tool = tool
        self.reason = reason
        super().__init__(f"call to {tool!r} ({capability!r}) needs a rationale: {reason}")


class DeadlineExceeded(AgentSafetyError):
    """Raised when a context's wall-clock :class:`Deadline` has elapsed.

    Attributes:
        budget: The deadline in seconds.
        elapsed: How long had elapsed when the call was attempted.
    """

    code = "deadline_exceeded"
    retryable = False
    _detail_fields = ("budget", "elapsed")

    def __init__(self, budget: float, elapsed: float):
        self.budget = budget
        self.elapsed = elapsed
        super().__init__(
            f"deadline exceeded: {elapsed:.3g}s elapsed of a {budget:g}s budget"
        )


class RollbackError(AgentSafetyError):
    """Raised when one or more compensating actions fail during an explicit abort.

    Only :meth:`Transaction.abort` raises this. When a ``with rollback()`` block
    unwinds because the body raised, the *body's* exception propagates instead and
    any compensation failures are left on ``Transaction.compensation_errors``.

    Attributes:
        errors: The exceptions raised by individual compensators, in unwind order.
    """

    code = "rollback_failed"
    retryable = False

    def __init__(self, errors: "list[BaseException]"):
        self.errors = list(errors)
        joined = "; ".join(repr(e) for e in self.errors)
        super().__init__(
            f"{len(self.errors)} compensation(s) failed during rollback: {joined}"
        )


class LoopDetected(AgentSafetyError):
    """Raised when an agent repeats the same tool call beyond the allowed count.

    The classic runaway-agent failure mode: the model gets stuck invoking one
    tool with identical arguments. A :class:`LoopGuard` trips this as a circuit
    breaker. Retryable only with *different* arguments — repeating the identical
    call will keep tripping.

    Attributes:
        tool: Name of the tool being repeated.
        count: How many identical calls were seen (including the one that tripped).
        limit: The maximum identical calls the guard allowed.
    """

    code = "loop_detected"
    retryable = False
    _detail_fields = ("tool", "count", "limit")

    def __init__(self, tool: str, count: int, limit: int):
        self.tool = tool
        self.count = count
        self.limit = limit
        super().__init__(
            f"loop detected: {tool!r} called identically {count} times "
            f"(limit {limit})"
        )
