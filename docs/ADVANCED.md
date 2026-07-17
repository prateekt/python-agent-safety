# Advanced: the objects under the keywords

Every `safely(...)` keyword is sugar over a small set of composable objects in
`agent_safety.core`. You never *need* this layer — reach for it when you want a
custom guard, your own gate wiring, transactional rollback, or fine-grained
audit/tracing. Import from the subpackage:

```python
from agent_safety.core import (
    safety_context, PermissionSet, Quota, RateLimit, Deadline, LoopGuard,
    ApprovalGate, ReasoningGate, ConstitutionGate, PreviewGate,
    RedactPII, SecretScanner, PathBoundary, NetworkAllowlist,
    rollback, trace_span, ListSink, JsonlSink, MetricsSink,
)
```

## `safety_context` — what `safely` compiles down to

`safely(...)` builds a `Policy` and installs it with `safety_context(...)` for
the duration of the block, restoring the previous policy on exit (even on
exceptions). Nested contexts **intersect** permissions and **append** guards,
quotas, and audit sinks, so a sub-step can drop privileges but nothing inside
can grant itself a capability it wasn't given. Outside any context the policy is
**deny-all**. Backed by `contextvars`, so it's correct under threads *and*
`asyncio` tasks.

```python
with safety_context(PermissionSet.of("filesystem.*", "network.http")):
    is_allowed("network.http")            # True
    with safety_context(PermissionSet.of("filesystem.read")):
        is_allowed("filesystem.write")    # False — narrowed
        # even PermissionSet.allow_all() here cannot widen it
    is_allowed("network.http")            # True again — outer scope restored
```

## `PermissionSet` — capabilities, deny-wins, default-deny

`PermissionSet.of("filesystem.*", deny=["filesystem.delete"])` — glob patterns,
explicit deny overrides allow, anything unmatched is forbidden, and
`intersect()` can only ever *narrow*. That one-way ratchet is what the `with`
block relies on. `to_dict()` / `from_dict()` round-trip it.

## Guards — filter prompts, inputs, and outputs

A guard is any object with `check(value, stage) -> value` that passes,
**sanitizes**, or raises. Built-ins:

| Guard | What it does |
|---|---|
| `MaxLength(n)` | block over-long values |
| `DenyPattern(regex)` | block values matching a banned pattern |
| `PromptInjectionGuard()` | tripwire for "ignore previous instructions"-style attacks |
| `RedactPII()` | replace emails / cards / SSNs / API keys with `[REDACTED:…]` |
| `SecretScanner()` | detect provider credentials (AWS/GitHub/Slack/Google keys, JWTs, PEM keys) |
| `UnicodeSanitizer()` | strip invisible / bidi / tag characters used for hidden injection |
| `Honeytoken(token)` | trip if a planted canary secret appears in a value |
| `PathBoundary(root)` | confine a filesystem path to `root` — blocks `../` traversal and symlink escapes |
| `NetworkAllowlist(hosts=…)` | confine a URL to approved hosts/schemes; block private-IP / `localhost` (SSRF) |
| `Compose([...])` | chain guards, threading the transformed value |

The last two are *sandbox* guards: they constrain the **resource** a value
points at rather than its content, so they belong in `input_guards=[…]` on the
tools that touch the filesystem or network:

```python
from agent_safety import tool
from agent_safety.core import PathBoundary, NetworkAllowlist

@tool("filesystem.read", input_guards=[PathBoundary("/srv/data")])
def read_file(path: str) -> str:          # "../../etc/passwd" -> GuardViolation
    return open(path).read()

@tool("network.http", input_guards=[NetworkAllowlist(["api.weather.com"])])
def fetch(url: str) -> str:               # http://169.254.169.254/ -> GuardViolation
    ...
```

Writing your own guard is one method — return the (possibly transformed) value
or raise `GuardViolation`:

```python
from agent_safety.core import Stage

class NoShouting:
    def check(self, value, stage):
        if stage is Stage.OUTPUT and isinstance(value, str):
            return value.lower()
        return value
```

## Budgets — the objects behind `calls=`, `per_second=`, …

| Construct | `safely` keyword | Bounds |
|---|---|---|
| `Quota(max_calls=…, max_tokens=…)` | `calls=`, `tokens=` | total calls / tokens |
| `CostBudget(usd)` | `budget="$20"` | total dollars (with `metered`) |
| `RateLimit(per_second=5)` | `per_second=`, `per_minute=` | sliding-window speed |
| `Deadline(seconds=30)` | `total_seconds=` | wall clock, from the first action |
| `ConcurrencyLimit(4)` | `at_most=` | calls running at once — share one object across agents to cap them together |
| `RiskBudget(20)` | `risk_budget=` | cumulative declared risk |
| `LoopGuard(max_identical=3)` | `no_repeats=` | circuit-break identical repeats |

All are charged on every guarded call alongside the ones already in scope, so an
inner limit can be tighter but never looser.

```python
with safety_context(
    PermissionSet.of("*"),
    quota=Quota(max_calls=200, max_tokens=500_000),
    rate_limit=RateLimit(per_second=5),     # 6th call in a second -> RateLimitExceeded
    deadline=Deadline(seconds=30),          # past 30s of work      -> DeadlineExceeded
    loop_guard=LoopGuard(max_identical=3),  # 4th identical call    -> LoopDetected
):
    ...
```

## Gates — one protocol, four implementations

All four gates live in `agent_safety.core.gates` and share one `Gate` base:
`covers(capability) -> bool` plus a check that receives the same `Action` object
(`.capability`, `.tool`, `.args`, `.kwargs`, `.reason`). Approvers and judges
may be sync **or** async (async ones require async tools).

### `ApprovalGate` — a human (or service) must say yes

```python
def cli_ok(action) -> bool:
    return input(f"Allow {action.tool}{action.args}? [y/N] ").lower() == "y"

with safety_context(
    PermissionSet.of("shell.exec", "filesystem.*"),
    approval=ApprovalGate(require=["shell.exec", "filesystem.delete"], approver=cli_ok),
):
    run_shell("ls")          # prompts; a "no" raises ApprovalDenied
    read_file("notes.txt")   # not gated -> runs straight through
```

### `ReasoningGate` — make the agent say *why*

For matching capabilities the agent must pass `rationale="…"` with the call; the
rationale is validated, recorded to audit, handed to any approver, then stripped
before the tool runs. Missing or thin → `ExplanationRequired` (reported back to
the model so it retries *with* an explanation).

```python
with safety_context(
    PermissionSet.of("shell.exec"),
    reasoning=ReasoningGate(require=["shell.exec"], min_length=20),
):
    run_shell("rm build/*", rationale="Clearing stale build artifacts for a clean rebuild")
```

A `validator=fn` can hold the rationale to a quality bar (e.g. LLM-as-judge).
Separately, `thought_trace()` + `record_thought("…")` timestamp the agent's
narrated reasoning onto the audit trail. Note the rationale is an
*accountability* record, not a correctness check — a model can still
rationalize; pair it with the other gates.

### `ConstitutionGate` — plain-English rules, judged by a model

```python
ConstitutionGate(rules=["never email a customer without prior consent"], judge=my_model)
```

A "no" raises `ConstitutionViolation`. It's probabilistic — keep the hard
`allow=` and budgets underneath as the real backstop.

### `PreviewGate` — approve "what this would do"

Tools declaring `@tool(..., preview=fn)` produce a human-readable preview of the
pending call; the gate shows it to the approver before anything runs.

## `rollback()` — undo on failure

Least privilege limits what an agent *can* do; rollback handles the
irreversible things it *did* do when a later step fails. Record a compensating
action next to each forward action — commit on clean exit, unwind LIFO on an
exception, then re-raise:

```python
with rollback() as tx:
    row = create_record(payload)
    tx.on_undo(delete_record, row.id)
    send_email(row.email)
    tx.on_undo(send_retraction, row.email)
    charge_card(row)      # raises -> retraction, then delete, then the error propagates
```

It's best-effort, in-process compensation — not a distributed transaction. A
compensation that itself fails is recorded on `tx.compensation_errors` and
audited without stopping the rest; the original exception is never masked.
`tx.commit()` is an explicit point-of-no-return; `async_rollback()` awaits
coroutine compensations.

## Audit, tracing, metrics

Audit sinks (`ListSink`, `JsonlSink`, `HashChainSink`, or any callable) receive
an `AuditEvent` for every permission decision, guard action, quota/rate charge,
gate outcome, loop trip, and rollback — a record of what the agent *tried*, not
just what it did. Wrap work in `trace_span("plan")` and each event is stamped
with the dotted span path, turning the flat log into a causal tree. A
`MetricsSink` keeps running counts (`m.counts["permission/deny"]`) instead of
storing every event.

```python
events = ListSink()
with safely(allow="search", log=events):
    with trace_span("research"):
        search("safety")
print([e.kind for e in events.events])
```

## Schemas and validation

`tool_schema(fn)` derives a JSON-Schema parameter spec from a function
signature (used by `ToolRegistry` for provider dialects); `validate_args`
checks a call's arguments against it. `Param` lets you annotate richer
constraints than the type hints carry.

## Scope, honestly

Guards are heuristics and scrubbers, not a complete security boundary. The
durable guarantee is least privilege: an injection that slips past a regex
tripwire still cannot invoke a capability the `PermissionSet` never granted,
spend past a budget, or skip a gate — and every attempt lands on the audit
trail. The sandbox guards are pre-flight intent checks, not an OS sandbox: run
them in front of real OS/network isolation. The full trust-boundary analysis is
in [THREAT_MODEL.md](../THREAT_MODEL.md).
