# agent_safety

[![CI](https://github.com/prateekt/python-agent-safety/actions/workflows/ci.yml/badge.svg)](https://github.com/prateekt/python-agent-safety/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/agent-safety)](https://pypi.org/project/agent-safety/)
![Python](https://img.shields.io/badge/python-3.9%2B-blue)
![Dependencies](https://img.shields.io/badge/dependencies-none%20(stdlib)-brightgreen)
![Typed](https://img.shields.io/badge/typed-mypy%20strict-blue)
![Providers](https://img.shields.io/badge/providers-Claude%20%7C%20OpenAI%20%7C%20Gemini-8A2BE2)
![License](https://img.shields.io/badge/license-MIT-green)

**Idiomatic, provider-agnostic Python constructs for AI-agent safety.**

Wrapping an LLM agent safely means answering, on every step: *"Is the agent
allowed to do this?"*, *"Is this content safe to send or use?"*, *"Is there
budget left?"* — and *"can I see what happened?"* `agent_safety` answers all
four with constructs Python developers already know — a `with` block, a
decorator, and small composable objects — instead of a sprawling config format.

It is **pure standard library** (no dependencies) and **provider-agnostic**: the
same guarded tools govern a **Claude, OpenAI, or Gemini** agent unchanged. The
overhead is **~12 µs per tool call** ([benchmark](examples/benchmark.py)) —
negligible against a model round-trip.

## Install

```bash
pip install agent-safety        # zero runtime dependencies
# from source: pip install -e ".[dev]"
```

## Start here

Two ideas. Mark a function with `@tool`, then run it inside a `safely(...)` block
that says, in plain words, what's allowed:

```python
from agent_safety import tool, safely

@tool
def read_file(path):
    return open(path).read()

with safely(allow="read_file", calls=10, hide_secrets=True):
    text = read_file("notes.txt")   # allowed, budget-counted, secrets scrubbed
    # anything you didn't allow simply can't run here
```

Every option is a plain keyword — reach for one when you need it, ignore the rest:

```python
with safely(
    allow=["read_file", "search"],  # what the code may do  (or allow="everything")
    deny="delete",                  # ...except this (deny always wins)
    calls=25,                       # most tool calls
    per_second=5,                   # speed limit
    seconds=30,                     # time budget
    at_most=4,                      # most tool calls running at once
    hide_secrets=True,              # scrub emails / API keys from results
    block_injections=True,          # reject "ignore previous instructions" inputs
    no_repeats=3,                   # stop a runaway loop
    ask=True,                       # ask you (y/n) before each action
    explain=True,                   # require a rationale="..." with each call
    monitor=True,                   # dry run: don't block, just log what WOULD block
    log=True,                       # print every decision
):
    ...
```

**Already have tools?** Wrap them in bulk with `guard` — no edits — and reach for
a ready-made profile instead of assembling settings:

```python
from agent_safety import guard, safely, Profiles

safe_search, safe_fetch = guard(search, fetch)   # your existing functions, now guarded

with safely(allow=["search", "fetch"], **Profiles.hardened()):  # paranoid defaults
    safe_search("agent safety")
```

That's the whole beginner surface. When you outgrow it, every keyword maps to a
real object you can use directly — read on.

> **New here?** Walk through [**TUTORIAL.md**](TUTORIAL.md) — build a complete,
> running safe agent in about 10 minutes, no API keys needed.

## The full version

Under the hood `safely` builds the same objects you can wire by hand for full
control: a `with safety_context(...)`, a `PermissionSet`, guard objects, `Quota`,
and so on. The same per-provider glue is absorbed by `ToolRegistry`.

```python
from agent_safety import (
    safety_context, guarded_tool, PermissionSet,
    MaxLength, RedactPII, PromptInjectionGuard, Quota, ListSink,
)

@guarded_tool("filesystem.read")
def read_file(path: str) -> str:
    return open(path).read()

audit = ListSink()
with safety_context(
    PermissionSet.of("filesystem.read"),          # capabilities the agent gets
    prompt_guards=[PromptInjectionGuard(), MaxLength(8000)],
    output_guards=[RedactPII()],                  # scrub secrets from results
    quota=Quota(max_calls=25, max_tokens=200_000),# resource budget
    audit=[audit],                                # record every decision
):
    text = read_file("notes.txt")   # allowed, PII-redacted, budget-charged, audited
    # any tool needing shell.exec / network.* here -> PermissionDenied
```

## The core ideas

### 1. A `with` block scopes everything — and can only de-escalate

`safety_context(...)` installs a policy for the duration of a block and restores
the previous one on exit (even on exceptions). Nested contexts **intersect**
permissions and **append** guards, quotas, and audit sinks, so a sub-step can
drop privileges but nothing inside can grant itself a capability it wasn't given.
Outside any context the policy is **deny-all** (fail-safe). Backed by
`contextvars`, so it's correct under threads *and* `asyncio` tasks.

```python
with safety_context(PermissionSet.of("filesystem.*", "network.http")):
    is_allowed("network.http")            # True
    with safety_context(PermissionSet.of("filesystem.read")):
        is_allowed("filesystem.write")    # False — narrowed
        is_allowed("network.http")        # False — narrowed
        # even safety_context(PermissionSet.allow_all()) here cannot widen it
    is_allowed("network.http")            # True again — outer scope restored
```

### 2. Permission sets: capabilities, deny-wins, default-deny

`PermissionSet.of("filesystem.*", deny=["filesystem.delete"])` — glob patterns,
explicit deny overrides allow, anything unmatched is forbidden, and `intersect()`
can only ever *narrow*. That one-way ratchet is what the `with` block relies on.

### 3. Guards filter prompts, inputs, and outputs

Composable `check(value, stage)` objects that pass, **sanitize**, or block:

| Guard | What it does |
|---|---|
| `MaxLength(n)` | block over-long values (prompt-stuffing / runaway cost) |
| `DenyPattern(regex)` | block values matching a banned pattern |
| `PromptInjectionGuard()` | tripwire for "ignore previous instructions"-style attacks |
| `RedactPII()` | replace emails / cards / SSNs / API keys with `[REDACTED:…]` |
| `SecretScanner()` | detect provider credentials (AWS/GitHub/Slack/Google keys, JWTs, PEM keys) and redact or block |
| `UnicodeSanitizer()` | strip invisible / bidi / tag characters used for hidden prompt injection |
| `Honeytoken(token)` | trip if a planted canary secret appears in a value (exfiltration tripwire) |
| `PathBoundary(root)` | confine a filesystem path to `root` — block `../` traversal and symlink escapes |
| `NetworkAllowlist(hosts=…)` | confine a URL to approved hosts/schemes; block private-IP / `localhost` targets (SSRF) |
| `Compose([...])` | chain guards, threading the transformed value |

The last two are *sandbox* guards: they constrain the **resource** a value points
at rather than its content, so they belong in `input_guards=[…]` on the tools that
touch the filesystem or the network.

```python
@guarded_tool("filesystem.read", input_guards=[PathBoundary("/srv/data")])
def read_file(path: str) -> str:          # "../../etc/passwd" -> GuardViolation
    return open(path).read()

@guarded_tool("network.http", input_guards=[NetworkAllowlist(["api.weather.com"])])
def fetch(url: str) -> str:               # http://169.254.169.254/ -> GuardViolation
    ...
```

### 4. Budgets: quotas, rate limits, deadlines, loop detection

Ways to bound *how much*, *how fast*, and *how long* an agent acts, all charged on
every guarded call alongside the ones already in scope (so an inner limit can be
tighter but never looser):

| Construct | Bounds |
|---|---|
| `Quota(max_calls=…, max_tokens=…)` | the **total** calls/tokens an agent may spend |
| `RateLimit(per_second=5)` | a **sliding-window** burst cap (also `per_minute=`, or `max_calls=/per_seconds=`) |
| `Deadline(seconds=30)` | a **wall-clock** budget, timed from the first action |
| `ConcurrencyLimit(4)` | most tool calls running **at once** — share one across agents to cap them together |
| `RiskBudget(20)` | spend **danger**, not calls — weight tools with `@tool(..., risk=N)` and cap the total |
| `LoopGuard(max_identical=3)` | a **circuit breaker** for an agent stuck repeating one tool with the same args |

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

Report tokens from whatever your model's usage object gives you via
`charge_tokens(...)`.

**Many agents at once.** Because the policy lives in a `contextvars.ContextVar`,
every thread and every `asyncio` task automatically gets its *own* rules — so
running several agents concurrently is just several `safely(...)` blocks, fully
isolated. Give each agent different powers, and share one `ConcurrencyLimit` to
cap their *combined* parallelism. See [`examples/multi_agent.py`](examples/multi_agent.py).

### 5. Monitor mode (dry run)

Adopting least privilege on a *working* agent is scary — what if you forgot to
allow something? Run in monitor mode first: nothing is blocked, but every would-be
denial is recorded. Watch the log, see exactly which capabilities the agent needs,
then turn enforcement on.

```python
with safely(allow="read_file", monitor=True, log=True):
    delete_everything()    # runs — but logs  permission: would_deny  delete_everything
```

Monitor mode obeys the same ratchet: a nested block can switch monitor → enforce
(tighten), never enforce → monitor.

### 6. Human-in-the-loop approval

`ApprovalGate(require=[…capabilities…], approver=fn)` requires an explicit "yes"
before any matching tool runs. The approver is any callable (CLI prompt, Slack
round-trip, policy service) and may be **sync or async**; it's consulted *after*
the permission check but *before* the tool executes, and a denial raises
`ApprovalDenied` — which `safe_dispatch` reports back to the model instead of
crashing the loop.

```python
def cli_ok(req) -> bool:
    return input(f"Allow {req.tool}{req.args}? [y/N] ").lower() == "y"

with safety_context(
    PermissionSet.of("shell.exec", "filesystem.*"),
    approval=ApprovalGate(require=["shell.exec", "filesystem.delete"], approver=cli_ok),
):
    run_shell("ls")          # shell.exec -> prompts for a human yes/no
    read_file("notes.txt")   # not gated -> runs straight through
```

### 7. Explainability: make the agent say *why*

Least privilege limits *what* an agent may do; a `ReasoningGate` makes it justify
*why* before it does it. For matching capabilities the agent must supply a
`rationale="…"` with the call; the rationale is validated, recorded to the audit
trail, and handed to any approver — then stripped before the tool runs. A missing
or thin rationale raises `ExplanationRequired`, reported back to the model so it
retries *with* an explanation.

```python
with safety_context(
    PermissionSet.of("shell.exec"),
    reasoning=ReasoningGate(require=["shell.exec"], min_length=20),
):
    run_shell("rm build/*", rationale="Clearing stale build artifacts before a clean rebuild")
    # run_shell("rm build/*")  ->  ExplanationRequired
```

Separately, `thought_trace()` + `record_thought("…")` let the agent narrate its
reasoning inside a block; each step is timestamped onto the audit trail and
stamped with the active `trace_span`, giving a replayable record of *stated intent*
alongside the decisions it triggered. A validator (`ReasoningGate(..., validator=fn)`)
can even hold the rationale to a quality bar — e.g. an LLM-as-judge.

### 8. Transactional rollback — undo on failure

Least privilege limits what an agent *can* do; rollback handles the irreversible
things it *did* do when a later step fails. `rollback()` is a `with` block that
records a **compensating action** next to each forward action — commit on a clean
exit, unwind LIFO on an exception, then re-raise:

```python
with rollback() as tx:
    row = create_record(payload)
    tx.on_undo(delete_record, row.id)       # how to undo the line above
    send_email(row.email)
    tx.on_undo(send_retraction, row.email)
    charge_card(row)                         # raises -> retraction, then delete,
                                             #           then the error propagates
```

It's a best-effort, in-process unwind (your compensations, not a DB transaction):
a compensation that itself fails is recorded on `tx.compensation_errors` and
audited without stopping the rest, and the body's original exception is never
masked. `tx.commit()` is an explicit point-of-no-return; `async_rollback()` awaits
coroutine compensations. Every begin/commit/compensation hits the same audit sinks.

### 9. Audit, tracing & metrics

Audit sinks (`ListSink`, `JsonlSink`, or any callable) receive an `AuditEvent` for
every permission decision, guard action, quota/rate charge, approval, reasoning,
loop trip, and rollback/compensation — a tamper-evident record of what the agent
tried. Wrap work in `trace_span("plan")` and each event is stamped with the dotted
span path, turning the flat log into a causal tree; drop in a `MetricsSink` to get
running counts (`m.counts["permission/deny"]`) instead of storing every event.

### 10. Constitutional rules, previews & honeytokens

Three checks that go past static permissions:

```python
with safely(
    allow="*",
    rule="never email a customer without prior consent",  # judged by a model
    judge=my_model,                                        # judge(action, rule) -> ok?
    preview=approve_fn,                                    # see a tool's preview, then y/n
    honeytoken="sk-CANARY-9f3x",                           # planted secret = exfil tripwire
):
    ...
```

- **`rule=` + `judge=`** — state a safety rule in plain English; a model (any
  callable, provider-agnostic) judges each matching call and a "no" raises
  `ConstitutionViolation`. It's probabilistic, so keep the hard `allow=`/limits
  underneath as the real backstop.
- **`preview=`** — a tool with a `@tool(..., preview=fn)` describes what it would
  do; the approver sees that preview and approves or rejects *before* it runs.
- **`honeytoken=`** — plant a fake secret an attacker would grab; if it ever shows
  up in an outbound call, `HoneytokenTripped` fires — strong evidence of a hijack.

**MCP.** `guard_mcp(session)` wraps any Model Context Protocol session so every
remote tool call runs through the active `safely(...)` policy — permission,
budgets, approval, constitutional rules, loop/concurrency, input guards, and audit
— with no MCP SDK dependency:

```python
safe = guard_mcp(session)
with safely(allow=["search", "fs.read"], calls=20, hide_secrets=True):
    result = await safe.call_tool("search", {"q": "agent safety"})
```

### `@guarded_tool` / `@guarded_async_tool`

Decorate the functions an agent may call. Every invocation is quota-charged,
permission-checked against the *current* policy, input/output-guarded, and
audited — sync or async:

```python
@guarded_async_tool("shell.exec", input_guards=[DenyPattern(r"rm\s+-rf")])
async def run(cmd: str) -> str:
    ...
```

## Works with Claude, OpenAI, and Gemini

The safety primitives have no SDK dependency. `ToolRegistry` declares each tool
once, emits schemas in any provider's dialect, and dispatches a tool call —
parsed from *any* provider's response — through the full safety pipeline.

```python
from agent_safety import ToolRegistry, PermissionSet, RedactPII, safety_context

registry = ToolRegistry()

@registry.tool("weather.read", description="Get weather for a city.",
               parameters={"type": "object",
                           "properties": {"city": {"type": "string"}},
                           "required": ["city"]})
def get_weather(city: str) -> str:
    return f"sunny in {city}"

tools = registry.schemas("openai")   # or "anthropic" / "gemini"
```

### Or let the signature write the schema

Hand-writing JSON Schema that just restates the signature is duplication. Omit
`parameters` (and `description`) and `agent_safety` infers them from the type
hints and docstring — `Annotated`/`Param` carry per-field descriptions and
constraints, `Literal`/`Enum` become enums, defaults become optional fields:

```python
from typing import Annotated, Literal
from agent_safety import ToolRegistry, Param

registry = ToolRegistry()

@registry.tool("weather.read")
def get_weather(
    city: Annotated[str, "city name, e.g. 'Paris'"],
    units: Literal["metric", "imperial"] = "metric",
    days: Annotated[int, Param(description="forecast horizon", minimum=1, maximum=14)] = 3,
) -> str:
    """Get the weather forecast for a city."""   # -> the tool description
    ...

tools = registry.schemas("anthropic")
# {'name': 'get_weather', 'description': 'Get the weather forecast for a city.',
#  'input_schema': {'type': 'object', 'properties': {
#     'city':  {'type': 'string', 'description': "city name, e.g. 'Paris'"},
#     'units': {'enum': ['metric', 'imperial'], 'type': 'string', 'default': 'metric'},
#     'days':  {'type': 'integer', 'description': 'forecast horizon',
#               'minimum': 1, 'maximum': 14, 'default': 3}},
#   'required': ['city']}}
```

An explicit `parameters=` / `description=` always wins, and `tool_schema(fn)` is
exported if you want the schema without the registry. Pure standard library — no
Pydantic.

Pass `@registry.tool(..., validate=True)` to close the loop: each call's arguments
are checked against that schema (types, `enum`, `required`, ranges, array items)
*before* dispatch, so a hallucinated or malformed call is reported back to the
model instead of reaching your function as the wrong type.

`parse_tool_calls(provider, response)` does the other half — it normalizes the
tool calls out of a raw Anthropic / OpenAI / Gemini response into `ToolCall`s ready
to feed straight to `safe_dispatch`, with no SDK dependency.

Then the per-provider loop is ~5 lines; the safety-relevant call is identical:

```python
# Anthropic — tool_use block: name + dict input
result = registry.safe_dispatch("anthropic", block.id, block.name, block.input)

# OpenAI — tool_calls: name + JSON-string arguments (dispatch parses it)
result = registry.safe_dispatch("openai", call.id, call.function.name, call.function.arguments)

# Gemini — function_call part: name + dict args
result = registry.safe_dispatch("gemini", "n/a", part.function_call.name, dict(part.function_call.args))
```

`safe_dispatch` returns a provider-native tool-result message in every case —
including when a call is **denied or guarded**, so a blocked tool is reported
back to the model instead of crashing your loop. Wrap the loop in a
`safety_context` and the same least-privilege, redaction, quota, and audit apply
no matter which model is driving.

## Run it

```bash
cd python-agent-safety
pip install -e ".[dev]"
python examples/easy.py            # the simplest possible intro (@tool + safely)
python examples/first_agent.py     # a complete offline agent loop (see TUTORIAL.md)
python examples/multi_agent.py     # two agents, different powers, a shared concurrency cap
python examples/quickstart.py      # narrated single-provider walkthrough
python examples/hardening.py       # sandbox + limits + approval + reasoning + rollback
python examples/providers.py       # one policy across Anthropic/OpenAI/Gemini
python examples/benchmark.py       # per-call overhead on your machine
python -m pytest                   # 240 tests, standard library only
python -m ruff check . && python -m mypy   # lint + strict type-check (matches CI)

# Optional live check against the real Gemini API (your key, never hardcoded):
GEMINI_API_KEY=... python -m pytest tests/test_gemini_live.py -v
```

## Layout

```
src/agent_safety/
  easy.py          tool / safely / guard / Profiles — the beginner front door
  permissions.py   PermissionSet — capability allow/deny + intersect (+ to_dict/from_dict)
  guards.py        Stage, Guard protocol, content + security guards (Secret/Unicode)
  sandbox.py       PathBoundary, NetworkAllowlist — filesystem/SSRF resource guards
  quota.py         Quota (call/token budgets) + RiskBudget (per-action risk)
  limits.py        RateLimit + Deadline + ConcurrencyLimit + LoopGuard
  action.py        Action — the one object every safety hook (approver/judge/...) receives
  approval.py      ApprovalGate — human-in-the-loop gating
  preview.py       PreviewGate — approve a tool's "what would this do?" preview
  constitution.py  ConstitutionGate — plain-English rules judged by a model
  reasoning.py     ReasoningGate + thought_trace / record_thought — explainability
  transaction.py   rollback() / async_rollback() — compensating (saga) transactions
  mcp.py           guard_mcp — run MCP tool calls through the active policy
  tracing.py       trace_span() / current_span() — causal span paths on audit events
  audit.py         AuditEvent, ListSink / JsonlSink / MetricsSink
  policy.py        Policy — the immutable bundle of all of the above; narrow(), explain()
  context.py       safety_context(), require(), check_*(), charge_*()
  decorators.py    guarded_tool / guarded_async_tool
  schema.py        tool_schema / Param — derive JSON-Schema from a signature
  validation.py    validate_args — check tool inputs against the declared schema
  integrations.py  ToolRegistry — schema dialects, neutral dispatch, parse_tool_calls
  exceptions.py    AgentSafetyError + PermissionDenied / GuardViolation / QuotaExceeded /
                   RateLimitExceeded / DeadlineExceeded / RiskBudgetExceeded / LoopDetected /
                   ApprovalDenied / ExplanationRequired / ConstitutionViolation /
                   HoneytokenTripped / RollbackError
  py.typed         PEP 561 marker — ships inline type information to consumers
```

## Scope & honesty

The guards are **heuristics and a structural foundation**, not a complete
security boundary by themselves. The durable guarantee is *least privilege*: even
a prompt injection that slips past the regex tripwire still cannot invoke a
capability the active `PermissionSet` never granted, spend past its `Quota`,
`RateLimit`, or `Deadline`, repeat itself past a `LoopGuard`, run a gated tool
without an `ApprovalGate` "yes" or a `ReasoningGate` rationale, or exfiltrate a
secret the `RedactPII` / `SecretScanner` output guard scrubbed — and every attempt
is on the audit trail. (The reasoning rationale is an *accountability* record, not
a correctness check — a model can still rationalize; pair it with a validator and
the gates above.)

The sandbox guards are likewise *pre-flight intent checks*, not an OS sandbox:
`PathBoundary` resolves symlinks and `NetworkAllowlist` rejects private-IP
literals, but neither defeats a TOCTOU race or DNS rebinding on its own. Run them
**in front of** a real OS/network sandbox and a DNS-aware HTTP client, and layer
real moderation / secret-scanning behind the same `Guard` interface — not instead
of these, but with them.

`rollback()` is best-effort, in-process compensation, not a distributed
transaction: your compensators run sequentially and can themselves fail (which is
recorded, not hidden). It bounds the blast radius of a failed multi-step action;
it does not give you atomicity across external systems.

For the full picture — trust boundaries, what's in and out of scope, residual
risks, and a mapping to the **OWASP LLM Top 10** — see
[**THREAT_MODEL.md**](THREAT_MODEL.md). To report a vulnerability, see
[**SECURITY.md**](SECURITY.md). Release history is in
[**CHANGELOG.md**](CHANGELOG.md).
