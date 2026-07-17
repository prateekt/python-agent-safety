"""The safety engine: policies, guards, budgets, gates, and the call pipeline.

Everything here is what ``tool`` / ``safely`` drive for you. Import from this
package when you need the underlying objects (custom guards, explicit
``Policy`` manipulation, the pipeline internals):

    from agent_safety.core import Policy, PermissionSet, safety_context
    from agent_safety.core.guards import Guard, RedactPII
"""

from .action import Action
from .audit import AuditEvent, AuditSink, HashChainSink, JsonlSink, ListSink, MetricsSink
from .context import (
    charge_call,
    charge_cost,
    charge_tokens,
    check_input,
    check_output,
    check_prompt,
    current_policy,
    in_context,
    is_allowed,
    require,
    safety_context,
)
from .exceptions import AgentSafetyError
from .gates import (
    ApprovalGate,
    ConstitutionGate,
    Gate,
    PreviewGate,
    ReasoningGate,
)
from .guards import (
    Compose,
    DenyPattern,
    Guard,
    Honeytoken,
    MaxLength,
    PromptInjectionGuard,
    RedactPII,
    SecretScanner,
    Stage,
    UnicodeSanitizer,
    run_guards,
)
from .limits import ConcurrencyLimit, Deadline, LoopGuard, RateLimit
from .permissions import PermissionSet
from .pipeline import make_tool_wrapper
from .policy import Explanation, Policy
from .quota import CostBudget, Quota, QuotaLike, RiskBudget
from .reasoning import Thought, ThoughtTrace, current_trace, record_thought, thought_trace
from .sandbox import NetworkAllowlist, PathBoundary
from .schema import Param, tool_description, tool_schema
from .tracing import current_span, trace_span
from .transaction import Transaction, async_rollback, rollback
from .usage import Price, TokenUsage, charge_usage, extract_tokens, extract_usage, metered
from .validation import validate_args

__all__ = [
    "Action",
    "AuditEvent", "AuditSink", "HashChainSink", "JsonlSink", "ListSink", "MetricsSink",
    "safety_context", "current_policy", "in_context", "require", "is_allowed",
    "check_prompt", "check_input", "check_output",
    "charge_call", "charge_tokens", "charge_cost",
    "AgentSafetyError",
    "Gate", "ApprovalGate", "ConstitutionGate", "PreviewGate", "ReasoningGate",
    "Guard", "Stage", "MaxLength", "DenyPattern", "PromptInjectionGuard",
    "RedactPII", "SecretScanner", "UnicodeSanitizer", "Honeytoken", "Compose", "run_guards",
    "ConcurrencyLimit", "Deadline", "LoopGuard", "RateLimit",
    "PermissionSet", "Policy", "Explanation",
    "make_tool_wrapper",
    "Quota", "QuotaLike", "RiskBudget", "CostBudget",
    "thought_trace", "record_thought", "current_trace", "ThoughtTrace", "Thought",
    "PathBoundary", "NetworkAllowlist",
    "Param", "tool_schema", "tool_description", "validate_args",
    "trace_span", "current_span",
    "rollback", "async_rollback", "Transaction",
    "metered", "charge_usage", "extract_tokens", "extract_usage", "Price", "TokenUsage",
]
