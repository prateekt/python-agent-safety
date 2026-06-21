"""The ``with`` construct at the heart of the library.

``safety_context`` is a context manager that installs an active :class:`Policy`
for the duration of a block and restores the previous one on exit — including
when the block raises. The current policy lives in a :class:`contextvars.ContextVar`,
so it is correct under threads and ``asyncio`` tasks without any locking.

The crucial invariant: entering a nested ``safety_context`` *narrows* the
policy (see :meth:`Policy.narrow`). Code can voluntarily drop privileges for a
risky sub-step, but nothing inside the block can hand itself back capabilities
it was not given. When the block exits, the broader policy returns.

    with safety_context(PermissionSet.of("filesystem.read")):
        require("filesystem.read")          # ok
        with safety_context(input_guards=[MaxLength(2000)]):
            require("filesystem.read")      # still ok, now also length-capped
            require("shell.exec")           # -> PermissionDenied
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from typing import Iterable, Iterator

from .guards import Guard
from .permissions import PermissionSet
from .policy import Policy

# The root sentinel. Outside any ``safety_context`` the effective policy is
# deny-all, so stray agent code that forgot to establish a context fails safe.
# Entering a top-level context, however, represents *trusted host code* dropping
# to least privilege, so it bootstraps from full authority (see ``safety_context``).
_ROOT = Policy()

# The active policy for the current thread/task.
_current: "contextvars.ContextVar[Policy]" = contextvars.ContextVar(
    "agent_safety_policy", default=_ROOT
)


def current_policy() -> Policy:
    """Return the policy in force for the current thread/task."""
    return _current.get()


def require(capability: str) -> None:
    """Assert the current policy allows *capability*, else ``PermissionDenied``."""
    current_policy().require(capability)


def is_allowed(capability: str) -> bool:
    """Non-raising check against the current policy."""
    return current_policy().allows(capability)


def check_prompt(value: object) -> object:
    """Run the active prompt guards over *value*."""
    return current_policy().check_prompt(value)


def check_input(value: object) -> object:
    """Run the active input guards over *value*."""
    return current_policy().check_input(value)


def check_output(value: object) -> object:
    """Run the active output guards over *value*."""
    return current_policy().check_output(value)


@contextmanager
def safety_context(
    permissions: PermissionSet = None,
    *,
    policy: Policy = None,
    prompt_guards: Iterable[Guard] = (),
    input_guards: Iterable[Guard] = (),
    output_guards: Iterable[Guard] = (),
) -> Iterator[Policy]:
    """Scope a narrowed safety policy to a ``with`` block.

    Args:
        permissions: Capabilities to intersect with the current policy. The
            block can do at most what *both* the outer policy and this set allow.
        policy: Use this full policy as the base instead of the current one.
            It is still narrowed by the current policy, preserving de-escalation.
        prompt_guards / input_guards / output_guards: Guards appended for the
            duration of the block.

    Yields:
        The effective :class:`Policy` in force inside the block.
    """
    base = current_policy()
    if base is _ROOT:
        # Top-level context: trusted host code defines the trust ceiling. Start
        # from full authority so the supplied permissions are granted as-is;
        # every nested context can then only narrow from here.
        base = Policy(permissions=PermissionSet.allow_all())
    if policy is not None:
        # Even an explicitly supplied policy may only narrow the current one.
        effective = base.narrow(
            policy.permissions,
            prompt_guards=policy.prompt_guards,
            input_guards=policy.input_guards,
            output_guards=policy.output_guards,
        )
    else:
        effective = base
    effective = effective.narrow(
        permissions,
        prompt_guards=prompt_guards,
        input_guards=input_guards,
        output_guards=output_guards,
    )
    token = _current.set(effective)
    try:
        yield effective
    finally:
        _current.reset(token)
