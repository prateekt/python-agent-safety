"""A :class:`Policy` bundles permissions, guards, quotas, and audit sinks.

It is the single object an agent's runtime consults to answer:

* *May I do this?* — :meth:`Policy.require`
* *Is this content safe to send/use?* — :meth:`Policy.check_prompt` / ``check_input`` / ``check_output``
* *Is there budget left?* — :meth:`Policy.charge_call` / :meth:`Policy.charge_tokens`

and which records every answer to its audit sinks. None of this is tied to a
particular model provider — the same policy governs a Claude, OpenAI, or Gemini
agent unchanged.

Policies are immutable. To restrict an agent further you :meth:`narrow` an
existing policy, which can only add denies, guards, quotas, and sinks — never
grant new capabilities. That one-way ratchet is what the ``with`` context relies on.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field, replace
from typing import Iterable, Optional, Tuple

from .approval import ApprovalGate, ApprovalRequest
from .audit import AuditEvent, AuditSink
from .exceptions import (
    ApprovalDenied,
    DeadlineExceeded,
    ExplanationRequired,
    LoopDetected,
    PermissionDenied,
    RateLimitExceeded,
)
from .guards import Guard, Stage, run_guards
from .limits import ConcurrencyLimit, Deadline, LoopGuard, RateLimit
from .permissions import PermissionSet
from .quota import Quota
from .reasoning import ReasoningGate, ReasoningRequest
from .tracing import current_span


@dataclass(frozen=True)
class Explanation:
    """Why the active policy allows or denies a capability."""

    capability: str
    allowed: bool
    reason: str

    def __str__(self) -> str:
        verb = "allowed" if self.allowed else "denied"
        return f"{self.capability!r} is {verb}: {self.reason}"


@dataclass(frozen=True)
class Policy:
    """An immutable safety policy."""

    permissions: PermissionSet = field(default_factory=PermissionSet.deny_all)
    prompt_guards: Tuple[Guard, ...] = ()
    input_guards: Tuple[Guard, ...] = ()
    output_guards: Tuple[Guard, ...] = ()
    quotas: Tuple[Quota, ...] = ()
    rate_limits: Tuple[RateLimit, ...] = ()
    deadlines: Tuple[Deadline, ...] = ()
    concurrency_limits: Tuple[ConcurrencyLimit, ...] = ()
    loop_guards: Tuple[LoopGuard, ...] = ()
    approvals: Tuple[ApprovalGate, ...] = ()
    reasonings: Tuple[ReasoningGate, ...] = ()
    auditors: Tuple[AuditSink, ...] = ()
    # When False the policy is in *monitor* (dry-run) mode: guarded tool calls
    # are not blocked, but a would-be permission denial is recorded to audit.
    enforce: bool = True

    # -- audit ------------------------------------------------------------
    def audit(self, event: AuditEvent) -> None:
        # Stamp the active trace span unless the event already carries one.
        if event.span is None:
            span = current_span()
            if span is not None:
                event = replace(event, span=span)
        for sink in self.auditors:
            sink(event)

    # -- permission checks ------------------------------------------------
    def allows(self, capability: str) -> bool:
        return self.permissions.allows(capability)

    def require(self, capability: str) -> None:
        """Raise :class:`PermissionDenied` unless *capability* is allowed."""
        if self.permissions.allows(capability):
            self.audit(AuditEvent("permission", "allow", capability=capability))
        else:
            self.audit(AuditEvent("permission", "deny", capability=capability))
            raise PermissionDenied(capability)

    def note_monitor(self, capability: str) -> None:
        """Monitor (dry-run) accounting: record whether *capability* would be
        denied, without blocking. Used when :attr:`enforce` is ``False`` so you
        can see what a policy *would* stop before turning it on for real.
        """
        decision = "allow" if self.permissions.allows(capability) else "would_deny"
        self.audit(AuditEvent("permission", decision, capability=capability))
        self.audit(AuditEvent("tool_call", "invoke", capability=capability))

    # -- content checks ---------------------------------------------------
    def check_prompt(self, value: object) -> object:
        return self._guard(self.prompt_guards, value, Stage.PROMPT)

    def check_input(self, value: object) -> object:
        return self._guard(self.input_guards, value, Stage.INPUT)

    def check_output(self, value: object) -> object:
        return self._guard(self.output_guards, value, Stage.OUTPUT)

    def _guard(self, guards: Tuple[Guard, ...], value: object, stage: Stage) -> object:
        result = run_guards(guards, value, stage)
        if guards:
            decision = "sanitize" if result != value else "ok"
            self.audit(AuditEvent("guard", decision, stage=stage.value))
        return result

    # -- quotas, rate limits & deadlines ---------------------------------
    def charge_call(self, n: int = 1) -> None:
        for quota in self.quotas:
            quota.charge_call(n)
        for limiter in self.rate_limits:
            try:
                for _ in range(n):
                    limiter.charge()
            except RateLimitExceeded:
                self.audit(AuditEvent("rate_limit", "deny", detail=limiter.name))
                raise
        for deadline in self.deadlines:
            try:
                deadline.charge()
            except DeadlineExceeded:
                self.audit(AuditEvent("deadline", "deny", detail=deadline.name))
                raise
        if self.quotas or self.rate_limits:
            self.audit(AuditEvent("quota", "charge", detail=f"calls+{n}"))

    def charge_tokens(self, n: int) -> None:
        for quota in self.quotas:
            quota.charge_tokens(n)
        if self.quotas:
            self.audit(AuditEvent("quota", "charge", detail=f"tokens+{n}"))

    # -- loop detection ---------------------------------------------------
    def check_loop(self, tool: str, signature: str) -> None:
        """Record a call against every active :class:`LoopGuard`.

        Raises :class:`~agent_safety.exceptions.LoopDetected` if the agent is
        repeating *tool* with the same arguments beyond the allowed count.
        """
        for guard in self.loop_guards:
            try:
                guard.record(tool, signature)
            except LoopDetected:
                self.audit(AuditEvent("loop", "deny", detail=tool))
                raise

    # -- human-in-the-loop approval --------------------------------------
    def check_approval(self, request: ApprovalRequest) -> None:
        """Consult every matching :class:`ApprovalGate` synchronously.

        Raises :class:`~agent_safety.exceptions.ApprovalDenied` if any approver
        declines, or ``RuntimeError`` if a matching gate has an async approver
        (use ``@guarded_async_tool`` for those).
        """
        for gate in self.approvals:
            if not gate.covers(request.capability):
                continue
            if gate.is_async:
                raise RuntimeError(
                    f"{gate.name} has an async approver; the tool requiring "
                    f"{request.capability!r} must be a @guarded_async_tool"
                )
            req = self._request_for(request, gate)
            self._decide_approval(req, gate, bool(gate.approver(req)))

    async def check_approval_async(self, request: ApprovalRequest) -> None:
        """Async counterpart of :meth:`check_approval`; awaits async approvers."""
        for gate in self.approvals:
            if not gate.covers(request.capability):
                continue
            req = self._request_for(request, gate)
            result = gate.approver(req)
            if inspect.isawaitable(result):
                result = await result
            self._decide_approval(req, gate, bool(result))

    @staticmethod
    def _request_for(request: ApprovalRequest, gate: ApprovalGate) -> ApprovalRequest:
        if gate.reason and not request.reason:
            return replace(request, reason=gate.reason)
        return request

    def _decide_approval(
        self, request: ApprovalRequest, gate: ApprovalGate, approved: bool
    ) -> None:
        self.audit(AuditEvent(
            "approval", "allow" if approved else "deny",
            capability=request.capability, detail=request.tool,
        ))
        if not approved:
            raise ApprovalDenied(
                request.capability, request.tool,
                gate.reason or "approval was not granted",
            )

    # -- reasoning (explainability) --------------------------------------
    def requires_reasoning(self, capability: str) -> bool:
        """Whether any active :class:`ReasoningGate` gates *capability*."""
        return any(gate.covers(capability) for gate in self.reasonings)

    def check_reasoning(self, request: ReasoningRequest, rationale: Optional[str]) -> None:
        """Require an adequate rationale for *request*, recording it to audit.

        Raises :class:`~agent_safety.exceptions.ExplanationRequired` if a matching
        gate is unsatisfied.
        """
        for gate in self.reasonings:
            if not gate.covers(request.capability):
                continue
            problem = gate.evaluate(rationale, request)
            if problem is not None:
                self.audit(AuditEvent(
                    "reasoning", "missing", capability=request.capability, detail=problem,
                ))
                raise ExplanationRequired(request.capability, request.tool, problem)
            self.audit(AuditEvent(
                "reasoning", "recorded", capability=request.capability,
                detail=(rationale or "").strip()[:200],
            ))

    # -- introspection ----------------------------------------------------
    def explain(self, capability: str) -> Explanation:
        """Explain why *capability* is allowed or denied, citing the pattern.

        A debugging aid for least-privilege: it reports the winning deny pattern,
        the matching allow pattern, or that nothing granted the capability.
        """
        from fnmatch import fnmatchcase

        cap = capability.strip()
        perms = self.permissions
        denied = [p for p in perms.deny if fnmatchcase(cap, p)]
        if denied:
            return Explanation(capability, False, f"denied by pattern {sorted(denied)[0]!r}")
        allowed = [p for p in perms.allow if fnmatchcase(cap, p)]
        if allowed:
            return Explanation(capability, True, f"allowed by pattern {sorted(allowed)[0]!r}")
        return Explanation(capability, False, "no allow pattern matches (default-deny)")

    # -- narrowing (one-way ratchet) -------------------------------------
    def narrow(
        self,
        permissions: Optional[PermissionSet] = None,
        *,
        prompt_guards: Iterable[Guard] = (),
        input_guards: Iterable[Guard] = (),
        output_guards: Iterable[Guard] = (),
        quotas: Iterable[Quota] = (),
        rate_limits: Iterable[RateLimit] = (),
        deadlines: Iterable[Deadline] = (),
        concurrency_limits: Iterable[ConcurrencyLimit] = (),
        loop_guards: Iterable[LoopGuard] = (),
        approvals: Iterable[ApprovalGate] = (),
        reasonings: Iterable[ReasoningGate] = (),
        auditors: Iterable[AuditSink] = (),
    ) -> "Policy":
        """Return a stricter child policy.

        Permissions are *intersected* with ``self`` (capabilities can only be
        removed); guards, quotas, rate limits, deadlines, loop guards, approval
        gates, reasoning gates, and audit sinks are all appended to the existing
        ones. Every field can only add restrictions, never remove them — the
        one-way ratchet the ``with`` context relies on.
        """
        new_perms = self.permissions
        if permissions is not None:
            new_perms = self.permissions.intersect(permissions)
        return replace(
            self,
            permissions=new_perms,
            prompt_guards=self.prompt_guards + tuple(prompt_guards),
            input_guards=self.input_guards + tuple(input_guards),
            output_guards=self.output_guards + tuple(output_guards),
            quotas=self.quotas + tuple(quotas),
            rate_limits=self.rate_limits + tuple(rate_limits),
            deadlines=self.deadlines + tuple(deadlines),
            concurrency_limits=self.concurrency_limits + tuple(concurrency_limits),
            loop_guards=self.loop_guards + tuple(loop_guards),
            approvals=self.approvals + tuple(approvals),
            reasonings=self.reasonings + tuple(reasonings),
            auditors=self.auditors + tuple(auditors),
        )
