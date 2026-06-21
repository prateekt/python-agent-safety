"""agent_safety — idiomatic Python constructs for AI-agent safety.

The library expresses agent safety through three familiar Python constructs:

* a **context manager** (``with safety_context(...)``) that scopes an agent's
  permissions and can only ever *narrow* them inside nested blocks;
* **permission sets** (allow/deny capability patterns, deny-wins, default-deny);
* **guards** — small composable checks/transforms applied to prompts, tool
  inputs, and outputs — surfaced via the ``@guarded_tool`` decorator.

Quick start::

    from agent_safety import safety_context, guarded_tool, PermissionSet
    from agent_safety import MaxLength, RedactPII, PromptInjectionGuard

    @guarded_tool("filesystem.read")
    def read_file(path: str) -> str:
        return open(path).read()

    with safety_context(
        PermissionSet.of("filesystem.read"),
        output_guards=[RedactPII()],
        prompt_guards=[PromptInjectionGuard(), MaxLength(8000)],
    ):
        contents = read_file("notes.txt")   # allowed + PII-redacted
        # read_file inside a tighter context that drops the capability -> denied
"""

from __future__ import annotations

from .context import (
    check_input,
    check_output,
    check_prompt,
    current_policy,
    is_allowed,
    require,
    safety_context,
)
from .decorators import guarded_tool
from .exceptions import AgentSafetyError, GuardViolation, PermissionDenied
from .guards import (
    Compose,
    DenyPattern,
    Guard,
    MaxLength,
    PromptInjectionGuard,
    RedactPII,
    Stage,
    run_guards,
)
from .permissions import PermissionSet
from .policy import Policy

__version__ = "0.1.0"

__all__ = [
    # context / ``with`` construct
    "safety_context",
    "current_policy",
    "require",
    "is_allowed",
    "check_prompt",
    "check_input",
    "check_output",
    # permissions
    "PermissionSet",
    "Policy",
    # decorator
    "guarded_tool",
    # guards
    "Guard",
    "Stage",
    "MaxLength",
    "DenyPattern",
    "PromptInjectionGuard",
    "RedactPII",
    "Compose",
    "run_guards",
    # exceptions
    "AgentSafetyError",
    "PermissionDenied",
    "GuardViolation",
]
