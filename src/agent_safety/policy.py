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
from .exceptions import ApprovalDenied, LoopDetected, PermissionDenied, RateLimitExceeded
from .guards import Guard, Stage, run_guards
from .limits import LoopGuard, RateLimit
from .permissions import PermissionSet
from .quota import Quota


@dataclass(frozen=True)
class Policy:
    """An immutable safety policy."""

    permissions: PermissionSet = field(default_factory=PermissionSet.deny_all)
    prompt_guards: Tuple[Guard, ...] = ()
    input_guards: Tuple[Guard, ...] = ()
    output_guards: Tuple[Guard, ...] = ()
    quotas: Tuple[Quota, ...] = ()
    rate_limits: Tuple[RateLimit, ...] = ()
    loop_guards: Tuple[LoopGuard, ...] = ()
    approvals: Tuple[ApprovalGate, ...] = ()
    auditors: Tuple[AuditSink, ...] = ()

    # -- audit ------------------------------------------------------------
    def audit(self, event: AuditEvent) -> None:
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

    # -- quotas & rate limits --------------------------------------------
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
        loop_guards: Iterable[LoopGuard] = (),
        approvals: Iterable[ApprovalGate] = (),
        auditors: Iterable[AuditSink] = (),
    ) -> "Policy":
        """Return a stricter child policy.

        Permissions are *intersected* with ``self`` (capabilities can only be
        removed); guards, quotas, rate limits, loop guards, approval gates, and
        audit sinks are all appended to the existing ones. Every field can only
        add restrictions, never remove them — the one-way ratchet the ``with``
        context relies on.
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
            loop_guards=self.loop_guards + tuple(loop_guards),
            approvals=self.approvals + tuple(approvals),
            auditors=self.auditors + tuple(auditors),
        )
