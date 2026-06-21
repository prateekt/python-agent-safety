# agent_safety

**Idiomatic Python constructs for AI-agent safety.**

Wrapping an LLM agent safely usually means answering two questions on every
step: *"Is the agent allowed to do this?"* and *"Is this content safe to send
or use?"* `agent_safety` answers both with constructs Python developers already
know — a `with` block, a decorator, and small composable objects — instead of a
sprawling config format.

It is pure standard library (no dependencies) and mirrors the same
"bounds + clamp" safety idea this repo already uses for the physical heater
controller, applied to AI agents instead of pumps and valves.

```python
from agent_safety import (
    safety_context, guarded_tool, PermissionSet,
    MaxLength, RedactPII, PromptInjectionGuard,
)

@guarded_tool("filesystem.read")
def read_file(path: str) -> str:
    return open(path).read()

with safety_context(
    PermissionSet.of("filesystem.read"),          # capabilities the agent gets
    prompt_guards=[PromptInjectionGuard(), MaxLength(8000)],
    output_guards=[RedactPII()],                  # scrub secrets from results
):
    text = read_file("notes.txt")   # allowed, and PII-redacted on the way out
    # any tool needing shell.exec / network.* here -> PermissionDenied
```

## The three ideas

### 1. A `with` block scopes permissions — and can only de-escalate

`safety_context(...)` installs a policy for the duration of the block and
restores the previous one on exit (even on exceptions). Nested contexts
**intersect** permissions, so a sub-step can voluntarily drop privileges but
nothing inside can grant itself capabilities it wasn't given.

```python
with safety_context(PermissionSet.of("filesystem.*", "network.http")):
    is_allowed("network.http")            # True
    with safety_context(PermissionSet.of("filesystem.read")):
        is_allowed("filesystem.write")    # False — narrowed
        is_allowed("network.http")        # False — narrowed
        # even safety_context(PermissionSet.allow_all()) here cannot widen it
    is_allowed("network.http")            # True again — outer scope restored
```

Outside any context the policy is **deny-all**, so agent code that forgot to
establish a context fails safe rather than running with full power. The first
`safety_context` you enter represents trusted host code dropping to least
privilege, so it grants exactly what you ask for.

### 2. Permission sets: capabilities, deny-wins, default-deny

A *capability* is a dotted name (`filesystem.write`, `shell.exec`,
`network.http`). A `PermissionSet` holds allow/deny glob patterns:

```python
PermissionSet.of("filesystem.*", deny=["filesystem.delete"])
```

* **Default deny** — anything not matched by an allow pattern is forbidden.
* **Deny wins** — an explicit deny overrides any allow, so "all of `filesystem.*`
  except `delete`" is expressible and tamper-resistant.
* **`intersect()` only narrows** — combining two sets never grants a new
  capability. This is the property the `with` ratchet relies on.

### 3. Guards filter prompts, inputs, and outputs

A guard inspects a value and either passes it, returns a **sanitized** version,
or raises `GuardViolation` to block it. They run at three stages — `PROMPT`,
`INPUT`, `OUTPUT`:

| Guard | What it does |
|---|---|
| `MaxLength(n)` | block over-long values (prompt-stuffing / runaway cost) |
| `DenyPattern(regex)` | block values matching a banned pattern |
| `PromptInjectionGuard()` | tripwire for "ignore previous instructions"-style attacks |
| `RedactPII()` | replace emails / cards / SSNs / API keys with `[REDACTED:…]` |
| `Compose([...])` | chain guards, threading the transformed value |

Guards are just objects with a `check(value, stage)` method, so writing your own
(a JSON-schema validator, a moderation API call, an allow-list) is a few lines.

### `@guarded_tool` ties it together

Decorate the functions an agent may call. Every invocation is permission-checked
against the *current* policy, its arguments pass through the input guards, and
its return value passes through the output guards — so the same tool is fully
privileged in trusted code and automatically constrained inside a narrower
`safety_context`.

```python
@guarded_tool("shell.exec", input_guards=[DenyPattern(r"rm\s+-rf")])
def run(cmd: str) -> str:
    ...
```

## Run it

```bash
cd agent_safety
python examples/quickstart.py        # narrated walkthrough
python -m pytest                     # 37 tests, standard library only
```

## Layout

```
src/agent_safety/
  permissions.py   PermissionSet — capability allow/deny + intersect
  guards.py        Stage, Guard protocol, built-in guards
  policy.py        Policy — permissions + guards, one-way narrow()
  context.py       safety_context(), require(), is_allowed(), check_*()
  decorators.py    guarded_tool()
  exceptions.py    AgentSafetyError / PermissionDenied / GuardViolation
```

## Scope & honesty

These guards are **heuristics and a structural foundation**, not a complete
security boundary by themselves. The durable guarantee here is *least privilege*:
even a prompt injection that slips past the regex tripwire still cannot invoke a
capability the active `PermissionSet` never granted. Layer real moderation /
secret-scanning behind the same `Guard` interface for production use.
