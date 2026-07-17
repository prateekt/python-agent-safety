"""The one object every safety hook receives: an :class:`Action`.

An approver, a model judge, a rationale validator, a preview approver — they all
ask the same question ("should *this* tool call happen?") and so they all get the
same :class:`Action`. One shape to learn, used everywhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Tuple


@dataclass(frozen=True)
class Action:
    """One tool call an agent wants to make, handed to a safety hook.

    Attributes:
        capability: The capability the tool requires (e.g. ``"shell.exec"``).
        tool: The tool's name.
        args: Positional arguments the agent passed, *before* input guards run.
        kwargs: Keyword arguments the agent passed, *before* input guards run.
        reason: Optional note from a gate explaining why it is asking.
    """

    capability: str
    tool: str
    args: Tuple[Any, ...] = ()
    kwargs: Dict[str, Any] = field(default_factory=dict)
    reason: str = ""
