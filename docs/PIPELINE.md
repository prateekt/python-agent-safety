# The enforcement pipeline

Every guarded tool call — a local `@tool`, an `async def` tool, a
`ToolRegistry.dispatch`, or a remote MCP call through `guard_mcp` — is admitted
through **one** ordered checklist, implemented once in
`agent_safety/core/pipeline.py` (`run_tool_call` for sync, `arun_tool_call` for
async; both share every step helper, so they cannot drift apart).

## The ordered checklist

| # | Step | What happens | Raises on failure |
|---|---|---|---|
| 1 | **Monitor short-circuit** | in monitor (dry-run) mode, record what *would* happen, then run the tool unblocked | — |
| 2 | **Charge call** | count the call against every quota, rate limit, and deadline in scope | `QuotaExceeded`, `RateLimitExceeded`, `DeadlineExceeded` |
| 3 | **Memory check** | Python-heap growth vs. the block's `memory=` budget | `MemoryBudgetExceeded` |
| 4 | **Permission check** | the capability must be allowed (default-deny; deny wins) | `PermissionDenied` |
| 5 | **Risk charge** | the tool's `risk=N` weight vs. any risk budget | `RiskBudgetExceeded` |
| 6 | **Rationale** | if a reasoning gate covers the capability, pop and validate the `rationale=` kwarg | `ExplanationRequired` |
| 7 | **Approval gate** | every covering approver must say yes | `ApprovalDenied` |
| 8 | **Constitution gate** | every covering judge must clear the call against its rules | `ConstitutionViolation` |
| 9 | **Preview gate** | if the tool declares `preview=`, show it to covering approvers | `ApprovalDenied` |
| 10 | **Cache lookup** | for `cache=True` tools, a cached result short-circuits execution — output guards (step 14) still re-apply, so a cached value can never bypass redaction in a stricter scope | — |
| 11 | **Loop check + audit** | runaway-loop detection on the call signature, then the invocation is audited | `LoopDetected` |
| 12 | **Input guards** | every argument runs through the policy's and the tool's input guards | `GuardViolation`, `HoneytokenTripped` |
| 13 | **Concurrency + timeout** | the call runs holding a concurrency slot, bounded by the per-call `timeout=` | `TimeoutExceeded` |
| 14 | **Output guards** | the result is filtered (redacted / blocked) before the caller sees it | `GuardViolation`, `HoneytokenTripped` |

Every step emits an `AuditEvent` to the sinks in scope, so the audit trail shows
not just what ran, but what was checked on the way.

## Why the order matters

* Budgets (2) are charged **before** the permission check (4), so a blocked call
  still burns budget — an attacker can't probe for free.
* The loop signature and invocation audit (11) run **after** the gates, so they
  reflect a call that was actually authorized.
* The idempotency cache (10) stores the **raw** result and re-applies the current
  context's output guards on every hit, so entering a stricter scope re-redacts.
* In monitor mode (1) the reserved `rationale=` kwarg is still stripped before
  the tool runs, so dry runs don't leak it into tool signatures.

## Parity across surfaces

| Surface | Driver |
|---|---|
| `@tool` on a `def` | `run_tool_call` |
| `@tool` on an `async def` | `arun_tool_call` |
| `ToolRegistry.dispatch` / `safe_dispatch` (and async variants) | same wrappers |
| `guard_mcp(session).call_tool(...)` | `arun_tool_call` |

MCP calls get the **full** pipeline — the same allow/deny, budgets, gates,
guards, timeout, and audit as a local tool. (MCP tools have no decorator, so the
per-tool extras — `risk=`, `preview=`, `cache=` — default to off.)

Async gates (an async approver or judge) require an async tool; calling one from
a sync tool raises `RuntimeError` rather than silently skipping the check.
