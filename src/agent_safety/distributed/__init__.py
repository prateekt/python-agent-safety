"""Multi-process / multi-worker safety: envelopes, shared budgets, the gateway.

Only needed when one policy must govern agents across processes or machines.
Configure it with one explicit object:

    from agent_safety.distributed import DistributedConfig

    cfg = DistributedConfig(mode=DistributedMode.ENFORCE, signing_keys={...})
    with safely(allow="search", distributed=cfg, envelope=env, run=ctx):
        ...

See docs/DISTRIBUTED.md for the full deployment story (gateway, Redis budgets,
nonce replay protection, rollout modes).
"""

from .backends import (
    BackendQuota,
    BudgetBackend,
    BudgetCharge,
    BudgetLimits,
    ChargeResult,
    MemoryBackend,
    default_memory_backend,
)
from .config import (
    DistributedConfig,
    DistributedMode,
    canary_percent,
    distributed_mode,
    gateway_url,
    load_signing_keys,
    org_id_from_env,
    should_enforce_envelope,
    should_require_envelope,
    should_shadow_envelope,
)
from .envelope import CapabilityEnvelope, EnvelopeSigner, EnvelopeVerifier
from .events import MintResponse, ToolRequest, ToolResult
from .nonces import (
    DynamoNonceStore,
    MemoryNonceStore,
    MongoNonceStore,
    NonceStore,
    RedisNonceStore,
    SqlNonceStore,
    nonce_store_from_env,
)
from .policy_spec import PolicyRegistry, PolicySpec, safely_from_spec
from .run import RunContext, current_run, run_context

__all__ = [
    "DistributedConfig", "DistributedMode",
    "distributed_mode", "canary_percent", "gateway_url", "org_id_from_env",
    "load_signing_keys", "should_enforce_envelope", "should_require_envelope",
    "should_shadow_envelope",
    "CapabilityEnvelope", "EnvelopeSigner", "EnvelopeVerifier",
    "PolicySpec", "PolicyRegistry", "safely_from_spec",
    "RunContext", "current_run", "run_context",
    "BudgetBackend", "BudgetCharge", "BudgetLimits", "ChargeResult",
    "MemoryBackend", "BackendQuota", "default_memory_backend",
    "NonceStore", "MemoryNonceStore", "RedisNonceStore", "SqlNonceStore",
    "MongoNonceStore", "DynamoNonceStore", "nonce_store_from_env",
    "ToolRequest", "ToolResult", "MintResponse",
]
