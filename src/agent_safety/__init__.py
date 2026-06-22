"""agent_safety — idiomatic Python constructs for AI-agent safety.

Provider-agnostic by design: the safety primitives govern a tool-calling agent
the same way whether the model is **Claude, OpenAI, or Gemini**. The core has no
LLM SDK dependency; :mod:`agent_safety.integrations` provides the thin per-provider
glue (tool-schema dialects + a neutral dispatch).

The library expresses agent safety through familiar Python constructs:

* a **context manager** (``with safety_context(...)``) that scopes an agent's
  permissions, guards, quotas, and audit sinks — and can only ever *narrow* them
  inside nested blocks;
* **permission sets** (allow/deny capability patterns, deny-wins, default-deny);
* **guards** — composable checks/transforms applied to prompts, tool inputs, and
  outputs — surfaced via the ``@guarded_tool`` / ``@guarded_async_tool`` decorators;
* **quotas** (call/token budgets) and **audit hooks** (a record of every decision).

Quick start::

    from agent_safety import safety_context, guarded_tool, PermissionSet
    from agent_safety import MaxLength, RedactPII, PromptInjectionGuard, Quota

    @guarded_tool("filesystem.read")
    def read_file(path: str) -> str:
        return open(path).read()

    with safety_context(
        PermissionSet.of("filesystem.read"),
        output_guards=[RedactPII()],
        prompt_guards=[PromptInjectionGuard(), MaxLength(8000)],
        quota=Quota(max_calls=25),
    ):
        contents = read_file("notes.txt")   # allowed, PII-redacted, budget-charged
"""

from __future__ import annotations

from .approval import ApprovalGate, ApprovalRequest
from .audit import AuditEvent, AuditSink, JsonlSink, ListSink, MetricsSink
from .context import (
    charge_call,
    charge_tokens,
    check_input,
    check_output,
    check_prompt,
    current_policy,
    is_allowed,
    require,
    safety_context,
)
from .decorators import guarded_async_tool, guarded_tool
from .exceptions import (
    AgentSafetyError,
    ApprovalDenied,
    DeadlineExceeded,
    ExplanationRequired,
    GuardViolation,
    LoopDetected,
    PermissionDenied,
    QuotaExceeded,
    RateLimitExceeded,
    RollbackError,
)
from .guards import (
    Compose,
    DenyPattern,
    Guard,
    MaxLength,
    PromptInjectionGuard,
    RedactPII,
    SecretScanner,
    Stage,
    UnicodeSanitizer,
    run_guards,
)
from .integrations import DIALECTS, ToolCall, ToolRegistry, ToolSpec, parse_tool_calls
from .limits import Deadline, LoopGuard, RateLimit
from .permissions import PermissionSet
from .policy import Explanation, Policy
from .quota import Quota
from .reasoning import (
    ReasoningGate,
    ReasoningRequest,
    Thought,
    ThoughtTrace,
    current_trace,
    record_thought,
    thought_trace,
)
from .sandbox import NetworkAllowlist, PathBoundary
from .schema import Param, tool_description, tool_schema
from .tracing import current_span, trace_span
from .transaction import Transaction, async_rollback, rollback
from .validation import validate_args

__version__ = "0.4.0"

__all__ = [
    # context / ``with`` construct
    "safety_context",
    "current_policy",
    "require",
    "is_allowed",
    "check_prompt",
    "check_input",
    "check_output",
    "charge_call",
    "charge_tokens",
    # permissions / policy
    "PermissionSet",
    "Policy",
    # decorators
    "guarded_tool",
    "guarded_async_tool",
    # guards
    "Guard",
    "Stage",
    "MaxLength",
    "DenyPattern",
    "PromptInjectionGuard",
    "RedactPII",
    "SecretScanner",
    "UnicodeSanitizer",
    "Compose",
    "run_guards",
    # sandbox guards
    "PathBoundary",
    "NetworkAllowlist",
    # quota & limits
    "Quota",
    "RateLimit",
    "Deadline",
    "LoopGuard",
    # human-in-the-loop approval
    "ApprovalGate",
    "ApprovalRequest",
    # explainability / reasoning
    "ReasoningGate",
    "ReasoningRequest",
    "thought_trace",
    "record_thought",
    "current_trace",
    "ThoughtTrace",
    "Thought",
    # tracing & metrics
    "trace_span",
    "current_span",
    "MetricsSink",
    # audit
    "AuditEvent",
    "AuditSink",
    "ListSink",
    "JsonlSink",
    # provider integrations
    "ToolRegistry",
    "ToolSpec",
    "ToolCall",
    "parse_tool_calls",
    "DIALECTS",
    # schema inference & validation
    "tool_schema",
    "tool_description",
    "Param",
    "validate_args",
    # transactional rollback
    "rollback",
    "async_rollback",
    "Transaction",
    # introspection
    "Explanation",
    # exceptions
    "AgentSafetyError",
    "PermissionDenied",
    "GuardViolation",
    "QuotaExceeded",
    "RateLimitExceeded",
    "LoopDetected",
    "ApprovalDenied",
    "RollbackError",
    "ExplanationRequired",
    "DeadlineExceeded",
]
