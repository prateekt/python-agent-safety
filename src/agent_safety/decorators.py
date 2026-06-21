"""Decorators that wire a tool function into the active safety policy.

``@guarded_tool`` is the ergonomic front door: annotate the functions an agent
is allowed to call, and every invocation is automatically

1. permission-checked against the current :class:`Policy`,
2. input-guarded (each positional/keyword argument is filtered), and
3. output-guarded (the return value is filtered before the caller sees it).

Because it reads the *current* policy at call time, the same decorated tool is
fully privileged at the top level and automatically constrained inside a
narrower ``safety_context`` — no extra plumbing.
"""

from __future__ import annotations

import functools
from typing import Callable, Iterable, TypeVar

from .context import current_policy
from .guards import Guard, Stage, run_guards

F = TypeVar("F", bound=Callable[..., object])


def guarded_tool(
    capability: str,
    *,
    input_guards: Iterable[Guard] = (),
    output_guards: Iterable[Guard] = (),
) -> Callable[[F], F]:
    """Wrap a tool callable with a capability check and input/output guards.

    Args:
        capability: The capability the caller must hold (e.g. ``"shell.exec"``).
        input_guards: Extra guards applied to every argument, on top of the
            policy's own input guards.
        output_guards: Extra guards applied to the return value, on top of the
            policy's own output guards.
    """

    extra_in = tuple(input_guards)
    extra_out = tuple(output_guards)

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            policy = current_policy()
            policy.require(capability)

            # Filter inputs: policy guards first, then any tool-specific ones.
            args = tuple(
                run_guards(extra_in, policy.check_input(a), Stage.INPUT) for a in args
            )
            kwargs = {
                k: run_guards(extra_in, policy.check_input(v), Stage.INPUT)
                for k, v in kwargs.items()
            }

            result = func(*args, **kwargs)

            # Filter output: policy guards first, then tool-specific ones.
            result = policy.check_output(result)
            result = run_guards(extra_out, result, Stage.OUTPUT)
            return result

        wrapper.__agent_capability__ = capability  # type: ignore[attr-defined]
        return wrapper  # type: ignore[return-value]

    return decorator
