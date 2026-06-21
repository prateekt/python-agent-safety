# agent_safety

**Idiomatic, provider-agnostic Python constructs for AI-agent safety.**

Wrapping an LLM agent safely means answering, on every step: *"Is the agent
allowed to do this?"*, *"Is this content safe to send or use?"*, *"Is there
budget left?"* — and *"can I see what happened?"* `agent_safety` answers all
four with constructs Python developers already know — a `with` block, a
decorator, and small composable objects — instead of a sprawling config format.

It is **pure standard library** (no dependencies) and **provider-agnostic**: the
same guarded tools and the same `safety_context` govern a **Claude, OpenAI, or
Gemini** agent unchanged. The only per-provider difference — tool-schema shape
and how each SDK reports a tool call — is absorbed by `ToolRegistry`.

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
| `Compose([...])` | chain guards, threading the transformed value |

### 4. Quotas and audit

`Quota(max_calls=…, max_tokens=…)` is a live budget charged (alongside any
enclosing quota) on every guarded call; report tokens from whatever your model's
usage object gives you via `charge_tokens(...)`. Audit sinks (`ListSink`,
`JsonlSink`, or any callable) receive an `AuditEvent` for every permission
decision, guard action, and quota charge — a tamper-evident record of what the
agent tried.

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
python examples/quickstart.py      # narrated single-provider walkthrough
python examples/providers.py       # one policy across Anthropic/OpenAI/Gemini
python -m pytest                   # 63 tests, standard library only

# Optional live check against the real Gemini API (your key, never hardcoded):
GEMINI_API_KEY=... python -m pytest tests/test_gemini_live.py -v
```

## Layout

```
src/agent_safety/
  permissions.py   PermissionSet — capability allow/deny + intersect
  guards.py        Stage, Guard protocol, built-in guards
  quota.py         Quota — call/token budgets
  audit.py         AuditEvent, ListSink / JsonlSink
  policy.py        Policy — permissions + guards + quotas + auditors, one-way narrow()
  context.py       safety_context(), require(), check_*(), charge_*()
  decorators.py    guarded_tool / guarded_async_tool
  integrations.py  ToolRegistry — schema dialects + neutral dispatch
  exceptions.py    AgentSafetyError / PermissionDenied / GuardViolation / QuotaExceeded
```

## Scope & honesty

The guards are **heuristics and a structural foundation**, not a complete
security boundary by themselves. The durable guarantee is *least privilege*: even
a prompt injection that slips past the regex tripwire still cannot invoke a
capability the active `PermissionSet` never granted, spend past its `Quota`, or
exfiltrate a secret the `RedactPII` output guard scrubbed — and every attempt is
on the audit trail. Layer real moderation / secret-scanning behind the same
`Guard` interface for production use.
