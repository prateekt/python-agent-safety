"""Exception hierarchy for agent-safety violations.

Every safety failure is an :class:`AgentSafetyError`, so a host application can
catch the whole family with one ``except`` clause and still discriminate between
a *permission* failure and a *content* failure when it wants to.
"""

from __future__ import annotations

from typing import Optional


class AgentSafetyError(Exception):
    """Base class for everything this library raises."""


class PermissionDenied(AgentSafetyError):
    """Raised when an agent attempts a capability the active policy forbids.

    Attributes:
        capability: The capability that was requested (e.g. ``"shell.exec"``).
        reason: Human-readable explanation of why it was denied.
    """

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

    def __init__(self, resource: str, limit: int, requested: int):
        self.resource = resource
        self.limit = limit
        self.requested = requested
        super().__init__(
            f"{resource} quota exceeded: requested {requested} but limit is {limit}"
        )


class GuardViolation(AgentSafetyError):
    """Raised when a prompt, input, or output fails a guard that cannot sanitize it.

    Attributes:
        guard: Name of the guard that tripped.
        stage: Where it tripped (``"prompt"``, ``"input"`` or ``"output"``).
        reason: Human-readable explanation.
    """

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

    def __init__(self, capability: str, tool: str, reason: str = "approval was not granted"):
        self.capability = capability
        self.tool = tool
        self.reason = reason
        super().__init__(f"call to {tool!r} ({capability!r}) denied: {reason}")


class RateLimitExceeded(AgentSafetyError):
    """Raised when calls arrive faster than a context's :class:`RateLimit` allows.

    Attributes:
        limit: The number of calls permitted per window.
        window: The window length in seconds.
        retry_after: Seconds to wait before the oldest call ages out of the window.
    """

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

    def __init__(self, label: str):
        self.label = label
        super().__init__(f"honeytoken {label!r} appeared in a value — possible exfiltration")


class RiskBudgetExceeded(AgentSafetyError):
    """Raised when an agent's cumulative action *risk* exceeds its budget.

    Attributes:
        limit: The risk budget.
        requested: The cumulative risk the offending call would reach.
    """

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

    def __init__(self, limit: float, spent: float):
        self.limit = limit
        self.spent = spent
        super().__init__(f"cost budget exceeded: spent ${spent:.4f} of a ${limit:.2f} budget")


class ExplanationRequired(AgentSafetyError):
    """Raised when an agent invokes a gated tool without an adequate rationale.

    A :class:`ReasoningGate` requires the agent to justify a sensitive action (a
    ``rationale=`` argument) before it runs. A missing, empty, or rejected
    rationale raises this — which, like every other safety block, is reported
    back to the model so it can retry *with* an explanation.

    Attributes:
        capability: The capability whose call required a rationale.
        tool: The tool the agent tried to invoke.
        reason: Why the rationale was inadequate.
    """

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
    breaker.

    Attributes:
        tool: Name of the tool being repeated.
        count: How many identical calls were seen (including the one that tripped).
        limit: The maximum identical calls the guard allowed.
    """

    def __init__(self, tool: str, count: int, limit: int):
        self.tool = tool
        self.count = count
        self.limit = limit
        super().__init__(
            f"loop detected: {tool!r} called identically {count} times "
            f"(limit {limit})"
        )
