"""Human-in-the-loop approval for high-risk tool calls.

Least privilege says *what* an agent may do; an approval gate adds *who says so,
right now*. An :class:`ApprovalGate` names a set of sensitive capabilities and a
callable that must say "yes" before any matching tool actually runs — a CLI
prompt, a Slack round-trip, a policy service, anything.

It composes with everything else: the gate is consulted **after** the permission
check and quota charge but **before** the tool executes, every decision is
audited, and a denial raises :class:`~agent_safety.exceptions.ApprovalDenied`,
which :meth:`ToolRegistry.safe_dispatch` turns into an error result handed back
to the model instead of crashing the loop.

The approver may be **sync or async**. A synchronous approver works with both
``@guarded_tool`` and ``@guarded_async_tool``; an async approver (a coroutine
function) may only be used under ``@guarded_async_tool`` — calling it from a
sync tool raises ``RuntimeError`` rather than silently skipping approval.

    def cli_prompt(req: ApprovalRequest) -> bool:
        return input(f"Allow {req.tool}{req.args}? [y/N] ").lower() == "y"

    with safety_context(
        PermissionSet.of("shell.exec", "filesystem.*"),
        approval=ApprovalGate(require=["shell.exec", "filesystem.delete"],
                              approver=cli_prompt),
    ):
        run_shell("ls")        # shell.exec -> prompts for a human yes/no
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from typing import Any, Awaitable, Callable, Dict, Iterable, Tuple, Union

# An approver returns truthy to allow, falsy to deny — sync or via a coroutine.
Approver = Callable[["ApprovalRequest"], Union[bool, Awaitable[bool]]]


@dataclass(frozen=True)
class ApprovalRequest:
    """The context handed to an approver so it can make an informed decision.

    Attributes:
        capability: The capability the tool requires (e.g. ``"shell.exec"``).
        tool: The tool's name.
        args: Positional arguments the agent passed, *before* input guards run.
        kwargs: Keyword arguments the agent passed, *before* input guards run.
        reason: Optional note from the gate explaining why approval is required.
    """

    capability: str
    tool: str
    args: Tuple[Any, ...] = ()
    kwargs: Dict[str, Any] = field(default_factory=dict)
    reason: str = ""


class ApprovalGate:
    """Require explicit approval before any tool whose capability it covers runs.

    Args:
        require: Capability patterns (glob ``*`` wildcards, like
            :class:`~agent_safety.permissions.PermissionSet`) that need approval.
        approver: Callable taking an :class:`ApprovalRequest` and returning a
            truthy/falsy decision; may be a coroutine function.
        reason: Optional human-readable note attached to each request.
    """

    def __init__(self, require: Iterable[str], approver: Approver, *, reason: str = ""):
        self.patterns = tuple(p.strip() for p in require if p and p.strip())
        if not self.patterns:
            raise ValueError("an ApprovalGate must require at least one capability")
        self.approver = approver
        self.reason = reason
        self.is_async = inspect.iscoroutinefunction(approver)
        self.name = "approval_gate(" + ", ".join(self.patterns) + ")"

    def covers(self, capability: str) -> bool:
        """Whether this gate requires approval for *capability*."""
        return any(fnmatchcase(capability, p) for p in self.patterns)
