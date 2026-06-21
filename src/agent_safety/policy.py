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

from dataclasses import dataclass, field, replace
from typing import Iterable, Tuple

from .audit import AuditEvent, AuditSink
from .exceptions import PermissionDenied
from .guards import Guard, Stage, run_guards
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

    # -- quotas -----------------------------------------------------------
    def charge_call(self, n: int = 1) -> None:
        for quota in self.quotas:
            quota.charge_call(n)
        if self.quotas:
            self.audit(AuditEvent("quota", "charge", detail=f"calls+{n}"))

    def charge_tokens(self, n: int) -> None:
        for quota in self.quotas:
            quota.charge_tokens(n)
        if self.quotas:
            self.audit(AuditEvent("quota", "charge", detail=f"tokens+{n}"))

    # -- narrowing (one-way ratchet) -------------------------------------
    def narrow(
        self,
        permissions: PermissionSet = None,
        *,
        prompt_guards: Iterable[Guard] = (),
        input_guards: Iterable[Guard] = (),
        output_guards: Iterable[Guard] = (),
        quotas: Iterable[Quota] = (),
        auditors: Iterable[AuditSink] = (),
    ) -> "Policy":
        """Return a stricter child policy.

        Permissions are *intersected* with ``self`` (capabilities can only be
        removed); guards, quotas, and audit sinks are appended to the existing
        ones.
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
            auditors=self.auditors + tuple(auditors),
        )
