# Migration guide (0.9)

0.9 restructured the package around one golden path (`@tool` + `safely`) and
three subpackages (`core`, `integrations`, `distributed`). **Every old name
still works for this release** — old imports and keywords resolve through
deprecation shims that emit a `DeprecationWarning` telling you the new home.
Run your suite with warnings visible to find everything at once:

```bash
python -W error::DeprecationWarning -m pytest
```

## Renamed APIs

| Old | New | Notes |
|---|---|---|
| `@guarded_tool("cap", ...)` | `@tool("cap", ...)` | `@tool` handles sync *and* async automatically |
| `@guarded_async_tool("cap", ...)` | `@tool("cap", ...)` | same decorator |
| `idempotent=True` (decorator option) | `cache=True` | same behavior |
| `guard(f, g)` | `guard_tools(f, g)` | avoids the collision with the `Guard` protocol and `LoopGuard` |
| `safely(seconds=120)` | `safely(total_seconds=120)` | so it can't be confused with per-call `timeout=` |
| `ApprovalRequest`, `ReasoningRequest` | `Action` | they were already aliases; every hook (approver, judge, preview) receives an `Action` |
| `PolicySpec.to_policy()` | `safely_from_spec(spec)` / `safely(**spec.to_kwargs())` | `to_policy` was a partial translation; removed |

## Moved modules

The top level now exports ~18 names (the golden path, `metered`,
`ToolRegistry`, `parse_tool_calls`, `guard_mcp`, `PolicySpec`,
`safely_from_spec`, `DistributedConfig`, `Action`, `AuditEvent`, and the
exceptions you actually catch). Everything else moved into a subpackage:

| Old import | New import |
|---|---|
| `agent_safety.permissions` | `agent_safety.core.permissions` |
| `agent_safety.policy` | `agent_safety.core.policy` |
| `agent_safety.context` | `agent_safety.core.context` |
| `agent_safety.guards` | `agent_safety.core.guards` |
| `agent_safety.sandbox` | `agent_safety.core.sandbox` |
| `agent_safety.quota` | `agent_safety.core.quota` |
| `agent_safety.limits` | `agent_safety.core.limits` |
| `agent_safety.decorators` | `agent_safety.core.pipeline` (see below) |
| `agent_safety.approval` / `reasoning` / `constitution` / `preview` | `agent_safety.core.gates` (see below) |
| `agent_safety.exceptions` | `agent_safety.core.exceptions` |
| `agent_safety.audit` / `tracing` / `observability` | `agent_safety.core.audit` / `.tracing` / `.observability` |
| `agent_safety.usage` / `prices` | `agent_safety.core.usage` / `.prices` |
| `agent_safety.schema` / `validation` | `agent_safety.core.schema` / `.validation` |
| `agent_safety.transaction` / `runtime` / `action` | `agent_safety.core.transaction` / `.runtime` / `.action` |
| `agent_safety.integrations` (module) | `agent_safety.integrations.providers` |
| `agent_safety.mcp` | `agent_safety.integrations.mcp` |
| `agent_safety.distributed` (module) | `agent_safety.distributed.config` |
| `agent_safety.policy_spec` | `agent_safety.distributed.policy_spec` |
| `agent_safety.envelope` / `run` / `nonces` / `events` | `agent_safety.distributed.envelope` / `.run` / `.nonces` / `.events` |
| `agent_safety.backends` | `agent_safety.distributed.backends` |
| `agent_safety.gateway` | `agent_safety.distributed.gateway` |

The gateway entry point moved with it:

```bash
python -m agent_safety.distributed.gateway --port 8765   # was: -m agent_safety.gateway
```

Flat imports from the top level (e.g. `from agent_safety import PermissionSet`)
also still resolve with a warning pointing at the new location.

## Consolidated internals (behavior-preserving)

* **One pipeline.** The sync decorator, async decorator, `ToolRegistry`
  dispatch, and `guard_mcp` all run the same 14-step checklist in
  `agent_safety.core.pipeline` ([PIPELINE.md](PIPELINE.md)). MCP calls gained
  the checks they were missing (memory, risk, rationale, per-call timeout).
* **One gate protocol.** `ApprovalGate`, `ReasoningGate`, `ConstitutionGate`,
  and `PreviewGate` are subclasses of a single `Gate` in
  `agent_safety.core.gates`; behavior is unchanged.
* **Complete `PolicySpec`.** It now covers *every* declarative `safely` keyword
  and round-trips faithfully (`safely(**spec.to_kwargs())`); a JSON schema
  ships at `schemas/policy_spec.schema.json`. `narrow()`'s handling of
  `monitor` was fixed to match the runtime ratchet (a child can turn
  enforcement *on*, never off).
* **One distributed config.** `DistributedConfig` (+ `.from_env()`) replaces
  scattered `AGENT_SAFETY_*` env reads; the env vars still work as a fallback,
  and the old helpers (`distributed_mode()`, `should_require_envelope()`, …)
  now delegate to it.
* **Structured errors.** Every `AgentSafetyError` now carries a stable `code`,
  the `capability` involved (when known), a `retryable` flag, and `to_dict()` —
  additive, nothing to migrate. The code table is in [AGENTS.md](../AGENTS.md).

## Timeline

Deprecated aliases and module shims are kept for **one release** and will be
removed in the next minor version. Migrate at your leisure; the warnings name
the exact replacement in every case.
