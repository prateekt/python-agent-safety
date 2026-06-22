"""Action previews: see what a destructive call *would* do before it runs.

For a risky tool, guessing from the arguments isn't enough — you want to see the
concrete effect. Give the tool a ``preview`` function that describes what it would
do, and install a :class:`PreviewGate`: before the tool runs, the preview is shown
to an approver, and only an explicit "yes" lets it proceed.

    @tool("files.delete", preview=lambda paths: f"would delete {len(paths)} files: {paths}")
    def delete(paths): ...

    with safely(allow="files.delete", preview=console_ok):  # console_ok(text, action) -> bool
        delete(["build/a", "build/b"])
        # -> shows "would delete 2 files: ['build/a', 'build/b']"  [approve? y/N]

Only tools that declare a ``preview`` function are gated; others run normally. The
approver may be sync or async (async requires ``@guarded_async_tool``). A rejected
preview raises :class:`~agent_safety.exceptions.ApprovalDenied`.
"""

from __future__ import annotations

import inspect
from fnmatch import fnmatchcase
from typing import Any, Callable, Iterable

# An approver sees the preview text and the action; truthy = go ahead.
PreviewApprover = Callable[[str, Any], Any]


class PreviewGate:
    """Require approval of a tool's preview before the tool runs.

    Args:
        approver: ``(preview_text, action) -> bool``; truthy means proceed.
        require: Capability patterns (glob ``*``) the gate applies to.
    """

    def __init__(self, approver: PreviewApprover, *, require: Iterable[str] = ("*",)):
        self.approver = approver
        self.patterns = tuple(p.strip() for p in require if p and p.strip()) or ("*",)
        self.is_async = inspect.iscoroutinefunction(approver)
        self.name = "preview_gate(" + ", ".join(self.patterns) + ")"

    def covers(self, capability: str) -> bool:
        return any(fnmatchcase(capability, p) for p in self.patterns)
