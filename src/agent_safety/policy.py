"""A :class:`Policy` bundles a permission set with stage-specific guards.

It is the single object an agent's runtime consults to answer two questions:

* *May I do this?* — :meth:`Policy.require`
* *Is this content safe to send/use?* — :meth:`Policy.check_prompt`,
  :meth:`Policy.check_input`, :meth:`Policy.check_output`

Policies are immutable. To restrict an agent further you :meth:`narrow` an
existing policy, which can only ever add denies and guards — never grant new
capabilities. That one-way ratchet is what the ``with`` context relies on.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Iterable, Tuple

from .exceptions import PermissionDenied
from .guards import Guard, Stage, run_guards
from .permissions import PermissionSet


@dataclass(frozen=True)
class Policy:
    """An immutable safety policy: permissions + prompt/input/output guards."""

    permissions: PermissionSet = field(default_factory=PermissionSet.deny_all)
    prompt_guards: Tuple[Guard, ...] = ()
    input_guards: Tuple[Guard, ...] = ()
    output_guards: Tuple[Guard, ...] = ()

    # -- permission checks ------------------------------------------------
    def allows(self, capability: str) -> bool:
        return self.permissions.allows(capability)

    def require(self, capability: str) -> None:
        """Raise :class:`PermissionDenied` unless *capability* is allowed."""
        if not self.permissions.allows(capability):
            raise PermissionDenied(capability)

    # -- content checks ---------------------------------------------------
    def check_prompt(self, value: object) -> object:
        return run_guards(self.prompt_guards, value, Stage.PROMPT)

    def check_input(self, value: object) -> object:
        return run_guards(self.input_guards, value, Stage.INPUT)

    def check_output(self, value: object) -> object:
        return run_guards(self.output_guards, value, Stage.OUTPUT)

    # -- narrowing (one-way ratchet) -------------------------------------
    def narrow(
        self,
        permissions: PermissionSet = None,
        *,
        prompt_guards: Iterable[Guard] = (),
        input_guards: Iterable[Guard] = (),
        output_guards: Iterable[Guard] = (),
    ) -> "Policy":
        """Return a stricter child policy.

        Permissions are *intersected* with ``self`` (so capabilities can only be
        removed), and any supplied guards are appended to the existing ones.
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
        )
