# AGENTS.md — for AI coding agents working in this repo or against this library

`agent_safety` is a zero-dependency Python library for least-privilege runtime
control of AI agents. This file is the fast path: the public surface, the golden
pattern, the error codes, and the rules for going deeper.

## The golden path (use this unless told otherwise)

```python
from agent_safety import tool, safely

@tool                                    # or @tool("capability.name")
def read_file(path):
    return open(path).read()

with safely(allow="read_file", calls=10, hide_secrets=True):
    read_file("notes.txt")              # outside a safely block, @tool functions raise
```

All policy is plain keywords on `safely(...)` — full reference in
[docs/GUIDE.md](docs/GUIDE.md). Nested blocks only tighten, never widen.

## The public surface (import only these from `agent_safety`)

| Name | Purpose |
|---|---|
| `tool` | decorator marking a function (sync or async) an agent may call |
| `safely` | context manager scoping what's allowed — all keywords optional |
| `guard_tools` | bulk-wrap existing functions, same effect as `@tool` |
| `Profiles` | ready-made keyword bundles (`Profiles.hardened()`, `Profiles.observe()`) |
| `metered` | wrap a model-call function so tokens/dollars charge the block's budget |
| `ToolRegistry` | declare tools once; emit schemas + dispatch for `"anthropic"` / `"openai"` / `"gemini"` |
| `parse_tool_calls` | extract tool calls from a provider response |
| `guard_mcp` | wrap an MCP session so remote tools go through the same pipeline |
| `PolicySpec` | complete serializable form of a `safely` block (JSON) |
| `safely_from_spec` | replay a `PolicySpec` (`safely_from_spec(spec, ask=fn, ...)` for callables) |
| `DistributedConfig` | explicit multi-process configuration (`.from_env()` for env vars) |
| `Action` | the object every hook (approver, judge, preview) receives |
| `AuditEvent` | what audit sinks receive |
| `AgentSafetyError` | base exception — catch this to catch every safety stop |
| `PermissionDenied`, `GuardViolation`, `QuotaExceeded`, `ApprovalDenied` | the specific exceptions worth catching individually |

Everything else lives in `agent_safety.core` (engine objects),
`agent_safety.integrations` (providers, MCP), and `agent_safety.distributed`
(envelopes, backends, gateway). **Do not import from those subpackages unless
the task genuinely needs custom guards/gates, provider internals, or
multi-process enforcement** — the top-level names cover normal use. Old flat
imports still work but emit `DeprecationWarning`; never write new code against
them ([docs/MIGRATION.md](docs/MIGRATION.md)).

## Errors are structured — repair programmatically

Every exception has `code` (stable string), `capability` (when known),
`retryable`, and `to_dict()`. Feed `to_dict()` back to a model as a tool error.

| `code` | Exception | Retryable | Typical repair |
|---|---|---|---|
| `permission_denied` | `PermissionDenied` | no | add the capability to `allow=` (or don't — that's the point) |
| `quota_exceeded` | `QuotaExceeded` | no | raise `calls=`/`tokens=` or start a new block |
| `guard_violation` | `GuardViolation` | yes | change the offending input/output |
| `approval_denied` | `ApprovalDenied` | no | a human said no |
| `rate_limited` | `RateLimitExceeded` | yes | back off, retry |
| `deadline_exceeded` | `DeadlineExceeded` | no | block's `total_seconds=` is spent |
| `timeout` | `TimeoutExceeded` | yes | single call exceeded `timeout=` |
| `loop_detected` | `LoopDetected` | no | agent repeated an identical call past `no_repeats=` |
| `explanation_required` | `ExplanationRequired` | yes | retry the call with `rationale="..."` |
| `constitution_violation` | `ConstitutionViolation` | no | the judge rejected it under `rule=` |
| `risk_budget_exceeded` | `RiskBudgetExceeded` | no | cumulative `risk=` spent |
| `cost_budget_exceeded` | `CostBudgetExceeded` | no | `budget="$N"` spent |
| `memory_budget_exceeded` | `MemoryBudgetExceeded` | no | `memory=` cap hit |
| `honeytoken_tripped` | `HoneytokenTripped` | no | planted canary appeared — treat as compromise |
| `rollback_failed` | `RollbackError` | no | a compensation failed; inspect `compensation_errors` |

## Machine-readable policy

`PolicySpec` serializes every declarative `safely` keyword; validate JSON
against [`schemas/policy_spec.schema.json`](schemas/policy_spec.schema.json).
Callables (`ask=`, `judge=`, `log=`) are supplied at runtime via
`safely_from_spec(spec, ask=fn)`.

## Working on this repo

* Layout: `src/agent_safety/` — `easy.py` (facade), `core/` (engine),
  `integrations/`, `distributed/`. Tests in `tests/`; runnable examples in
  `examples/` (ordered index in `examples/README.md`).
* Every tool call flows through the single 14-step pipeline in
  `core/pipeline.py` — documented in [docs/PIPELINE.md](docs/PIPELINE.md). New
  checks go there, nowhere else.
* The core must stay stdlib-only; provider SDKs appear only in env-gated live
  tests. Any new `Policy` field must be handled by `Policy.narrow` so nesting
  can only tighten. New `safely` keywords must also land in `PolicySpec` and the
  JSON schema. See [CONTRIBUTING.md](CONTRIBUTING.md).
* Verify with:

```bash
python -m pytest                              # full suite incl. attack scorecard
python -m ruff check src tests examples && python -m mypy
```

* Ignore `agent-safety-patent/` — unrelated to the library.
