"""agent_safety — least-privilege safety for AI agents, in two ideas.

Mark a function with ``@tool``, then run it inside a ``safely(...)`` block that
says, in plain words, what's allowed::

    from agent_safety import tool, safely

    @tool
    def read_file(path):
        return open(path).read()

    with safely(allow="read_file", calls=10, hide_secrets=True):
        text = read_file("notes.txt")   # allowed, budget-counted, secrets scrubbed
        # anything you didn't allow simply can't run here

Every ``safely`` option is a plain keyword (``calls=``, ``ask=``, ``monitor=``,
…) — see :mod:`agent_safety.easy` for the full list, or the README's cheat
sheet. Provider-agnostic: the same policy governs a Claude, OpenAI, Gemini, or
MCP agent unchanged.

Going deeper:

* ``agent_safety.core`` — the engine (``Policy``, ``PermissionSet``, guards,
  budgets, gates, the call pipeline) for advanced composition;
* ``agent_safety.integrations`` — provider tool dialects (``ToolRegistry``)
  and the MCP wrapper (``guard_mcp``);
* ``agent_safety.distributed`` — envelopes, shared budgets, and the policy
  gateway, for governing agents across processes and machines.
"""

from __future__ import annotations

from typing import Any

from .core.action import Action
from .core.audit import AuditEvent
from .core.exceptions import (
    AgentSafetyError,
    ApprovalDenied,
    GuardViolation,
    PermissionDenied,
    QuotaExceeded,
)
from .core.usage import metered
from .distributed.config import DistributedConfig
from .distributed.policy_spec import PolicySpec, safely_from_spec
from .easy import Profiles, guard_tools, safely, tool
from .easy import guard as guard  # deprecated alias of guard_tools; warns when called
from .integrations.mcp import guard_mcp
from .integrations.providers import ToolRegistry, parse_tool_calls

__version__ = "0.9.0"

# The stable public surface. Everything else lives in agent_safety.core,
# agent_safety.integrations, and agent_safety.distributed; the old flat names
# still resolve (with a DeprecationWarning) via __getattr__ below.
__all__ = [
    # the golden path
    "tool",
    "safely",
    "guard_tools",
    "Profiles",
    # token & cost accounting for model calls
    "metered",
    # agent-loop integration
    "ToolRegistry",
    "parse_tool_calls",
    "guard_mcp",
    # serializable config + explicit distributed setup
    "PolicySpec",
    "safely_from_spec",
    "DistributedConfig",
    # what hooks receive / what audit emits
    "Action",
    "AuditEvent",
    # the exceptions you actually catch
    "AgentSafetyError",
    "PermissionDenied",
    "GuardViolation",
    "QuotaExceeded",
    "ApprovalDenied",
]

# Old flat top-level names -> their new home. Kept importable for one release;
# each access warns once so code can migrate gradually.
_MOVED = {
    # core: context helpers
    "safety_context": "core.context",
    "current_policy": "core.context",
    "in_context": "core.context",
    "require": "core.context",
    "is_allowed": "core.context",
    "check_prompt": "core.context",
    "check_input": "core.context",
    "check_output": "core.context",
    "charge_call": "core.context",
    "charge_tokens": "core.context",
    "charge_cost": "core.context",
    # core: policy & permissions
    "PermissionSet": "core.permissions",
    "Policy": "core.policy",
    "Explanation": "core.policy",
    # core: decorators (deprecated in favour of @tool)
    "guarded_tool": "core.pipeline",
    "guarded_async_tool": "core.pipeline",
    "make_tool_wrapper": "core.pipeline",
    # core: guards
    "Guard": "core.guards",
    "Stage": "core.guards",
    "MaxLength": "core.guards",
    "DenyPattern": "core.guards",
    "PromptInjectionGuard": "core.guards",
    "RedactPII": "core.guards",
    "SecretScanner": "core.guards",
    "UnicodeSanitizer": "core.guards",
    "Honeytoken": "core.guards",
    "Compose": "core.guards",
    "run_guards": "core.guards",
    "PathBoundary": "core.sandbox",
    "NetworkAllowlist": "core.sandbox",
    # core: budgets & limits
    "Quota": "core.quota",
    "QuotaLike": "core.quota",
    "RiskBudget": "core.quota",
    "CostBudget": "core.quota",
    "RateLimit": "core.limits",
    "Deadline": "core.limits",
    "ConcurrencyLimit": "core.limits",
    "LoopGuard": "core.limits",
    # core: gates
    "ApprovalGate": "core.gates",
    "PreviewGate": "core.gates",
    "ConstitutionGate": "core.gates",
    "ReasoningGate": "core.gates",
    # core: reasoning trace
    "thought_trace": "core.reasoning",
    "record_thought": "core.reasoning",
    "current_trace": "core.reasoning",
    "ThoughtTrace": "core.reasoning",
    "Thought": "core.reasoning",
    # core: tracing & audit sinks
    "trace_span": "core.tracing",
    "current_span": "core.tracing",
    "AuditSink": "core.audit",
    "ListSink": "core.audit",
    "JsonlSink": "core.audit",
    "MetricsSink": "core.audit",
    "HashChainSink": "core.audit",
    # core: usage & prices
    "charge_usage": "core.usage",
    "extract_tokens": "core.usage",
    "extract_usage": "core.usage",
    "Price": "core.usage",
    "TokenUsage": "core.usage",
    "price_for": "core.prices",
    # core: schema & validation
    "tool_schema": "core.schema",
    "tool_description": "core.schema",
    "Param": "core.schema",
    "validate_args": "core.validation",
    # core: transactions
    "rollback": "core.transaction",
    "async_rollback": "core.transaction",
    "Transaction": "core.transaction",
    # core: observability
    "CircuitBreaker": "core.observability",
    "PrometheusMetrics": "core.observability",
    "StructuredLog": "core.observability",
    # core: remaining exceptions
    "RateLimitExceeded": "core.exceptions",
    "LoopDetected": "core.exceptions",
    "RollbackError": "core.exceptions",
    "ExplanationRequired": "core.exceptions",
    "DeadlineExceeded": "core.exceptions",
    "ConstitutionViolation": "core.exceptions",
    "HoneytokenTripped": "core.exceptions",
    "RiskBudgetExceeded": "core.exceptions",
    "CostBudgetExceeded": "core.exceptions",
    "TimeoutExceeded": "core.exceptions",
    "MemoryBudgetExceeded": "core.exceptions",
    # integrations
    "ToolSpec": "integrations.providers",
    "ToolCall": "integrations.providers",
    "DIALECTS": "integrations.providers",
    "SafeMCP": "integrations.mcp",
    # distributed
    "RunContext": "distributed.run",
    "current_run": "distributed.run",
    "run_context": "distributed.run",
    "BudgetBackend": "distributed.backends",
    "BudgetCharge": "distributed.backends",
    "BudgetLimits": "distributed.backends",
    "ChargeResult": "distributed.backends",
    "MemoryBackend": "distributed.backends",
    "BackendQuota": "distributed.backends",
    "default_memory_backend": "distributed.backends",
    "SqlBudgetBackend": "distributed.backends.sql_backend",
    "sql_backend": "distributed.backends.sql_backend",
    "RedisBudgetBackend": "distributed.backends.redis_backend",
    "redis_backend": "distributed.backends.redis_backend",
    "MongoBudgetBackend": "distributed.backends.mongo_backend",
    "mongo_backend": "distributed.backends.mongo_backend",
    "DynamoBudgetBackend": "distributed.backends.dynamo_backend",
    "dynamo_backend": "distributed.backends.dynamo_backend",
    "PolicyRegistry": "distributed.policy_spec",
    "CapabilityEnvelope": "distributed.envelope",
    "EnvelopeSigner": "distributed.envelope",
    "EnvelopeVerifier": "distributed.envelope",
    "NonceStore": "distributed.nonces",
    "MemoryNonceStore": "distributed.nonces",
    "RedisNonceStore": "distributed.nonces",
    "SqlNonceStore": "distributed.nonces",
    "MongoNonceStore": "distributed.nonces",
    "DynamoNonceStore": "distributed.nonces",
    "nonce_store_from_env": "distributed.nonces",
    "ToolRequest": "distributed.events",
    "ToolResult": "distributed.events",
    "MintResponse": "distributed.events",
    "DistributedMode": "distributed.config",
    "distributed_mode": "distributed.config",
    "canary_percent": "distributed.config",
    "gateway_url": "distributed.config",
    "org_id_from_env": "distributed.config",
    "load_signing_keys": "distributed.config",
    "should_enforce_envelope": "distributed.config",
    "should_require_envelope": "distributed.config",
    "should_shadow_envelope": "distributed.config",
}


def __getattr__(name: str) -> Any:
    """Resolve old flat names (with a warning) so existing code keeps working."""
    import warnings

    if name in ("ApprovalRequest", "ReasoningRequest"):
        warnings.warn(
            f"{name} is a deprecated alias of Action; import Action instead",
            DeprecationWarning,
            stacklevel=2,
        )
        return Action
    target = _MOVED.get(name)
    if target is not None:
        import importlib

        warnings.warn(
            f"importing {name} from agent_safety is deprecated; "
            f"use agent_safety.{target} instead",
            DeprecationWarning,
            stacklevel=2,
        )
        module = importlib.import_module(f".{target}", __name__)
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
