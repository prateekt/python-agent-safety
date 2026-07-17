# The `safely(...)` guide — every keyword

`@tool` and `safely(...)` are the whole recommended API. This page is the full
reference for both. If a keyword here isn't enough, the objects underneath are
documented in [ADVANCED.md](ADVANCED.md).

## `@tool` — mark a function the agent may call

```python
from agent_safety import tool

@tool                              # capability = function name
def read_file(path): ...

@tool("fs.read")                   # or name the capability yourself
def read_file(path): ...

@tool("db.wipe", risk=10)          # weight it against a risk budget
def wipe(): ...

@tool(cache=True)                  # pure function? reuse identical results
def slow_lookup(q): ...

@tool("files.delete", preview=lambda paths: f"would delete {len(paths)} files")
def delete(paths): ...             # a preview gate can show this before running
```

Works on `def` and `async def` automatically. A `@tool` outside any `safely`
block raises `PermissionDenied` — nothing runs by accident. Options:

| Option | What it does |
|---|---|
| `cache=True` | reuse the result of identical calls (side-effect-free tools only) |
| `risk=N` | weight this tool against `safely(risk_budget=...)` |
| `preview=fn` | a function of the same arguments describing what the call *would* do, for `safely(preview=...)` |
| `input_guards=[...]` / `output_guards=[...]` | per-tool guards on top of the policy's (see [ADVANCED.md](ADVANCED.md)) |

Have existing functions you can't edit? `guard_tools(f, g, ...)` wraps them in
bulk, exactly like applying `@tool` to each.

## `safely(...)` — what's allowed right now

All keywords are optional and compose freely. Nesting `safely` blocks only ever
*tightens*: permissions intersect, budgets stack, guards append.

### What the agent may do

| Keyword | Example | Meaning |
|---|---|---|
| `allow=` | `allow="read_file"`, `allow=["a", "b"]`, `allow="everything"` | capabilities that may run; glob patterns like `"fs.*"` work |
| `deny=` | `deny="fs.delete"` | forbid even if allowed — deny always wins |

### How much, how fast, how long

| Keyword | Example | Meaning |
|---|---|---|
| `calls=` | `calls=25` | most tool calls in the block |
| `tokens=` | `tokens=500_000` | most model tokens (reported via `metered` / `charge_tokens`) |
| `budget=` | `budget="$20"` | most dollars spent (with `metered`, below) |
| `per_second=` / `per_minute=` | `per_second=5` | sliding-window speed limit |
| `total_seconds=` | `total_seconds=120` | wall-clock budget for the whole block |
| `timeout=` | `timeout=20` | hard cap on any *single* call — stops hangs |
| `memory=` | `memory="500MB"` | cap Python-heap growth inside the block |
| `at_most=` | `at_most=4` | most tool calls running at once |
| `no_repeats=` | `no_repeats=3` | circuit-break identical repeated calls |
| `risk_budget=` | `risk_budget=20` | cap cumulative risk (tools declare `risk=N`) |

`timeout` interrupts a hung call (async: cancels the coroutine; sync: a `SIGALRM`
timer on Unix, else a worker thread abandoned on timeout). `memory` is a
Python-heap guardrail, not an OS sandbox. Both raise typed exceptions.

### Input and output hygiene

| Keyword | Example | Meaning |
|---|---|---|
| `hide_secrets=True` | | scrub emails / cards / API keys / provider credentials from results |
| `max_input=` | `max_input=10_000` | reject over-long tool inputs |
| `block=` | `block="rm -rf"` | reject inputs matching a regex (string or list) |
| `block_injections=True` | | reject "ignore previous instructions"-style inputs |
| `clean_text=True` | | strip invisible / bidi unicode used to smuggle instructions |
| `honeytoken=` | `honeytoken="sk-CANARY-9f3x"` | plant a fake secret; if it ever appears in a call, the run trips (exfiltration tripwire) |

### Gates — checks before an action runs

| Keyword | Example | Meaning |
|---|---|---|
| `ask=` | `ask=True` or `ask=my_fn` | approval before each action: console y/n, or your `fn(action) -> bool` (sync or async) |
| `explain=` | `explain=True` or `explain=["shell.*"]` | require a `rationale="..."` argument with matching calls; recorded to audit |
| `rule=` + `judge=` | `rule="never email customers", judge=fn` | plain-English rules judged by any callable (a model); a "no" blocks the call |
| `preview=` | `preview=approve_fn` | for tools that declare `preview=`, show "what this would do" and require a yes |

Every gate hook receives the same `Action` object (`.capability`, `.tool`,
`.args`, `.kwargs`, `.reason`).

### Watching and mode

| Keyword | Example | Meaning |
|---|---|---|
| `monitor=True` | | dry run — block nothing, log every would-be denial. Adopt safely on a working agent, then turn enforcement on |
| `log=` | `log=True` or `log=my_sink` | print every decision, or send each `AuditEvent` to your callable / list of sinks |

```python
with safely(allow="read_file", monitor=True, log=True):
    delete_everything()    # runs — but logs:  permission: would_deny  delete_everything
```

Monitor mode obeys the ratchet: a nested block can switch monitor → enforce,
never enforce → monitor.

### Distributed (multi-process) keywords

`run=`, `envelope=`, `envelope_keys=`, `backend=`, `nonce_store=`, and
`distributed=` govern agents across processes and machines — see
[DISTRIBUTED.md](DISTRIBUTED.md).

## Money and tokens, automatically — `metered`

The model round-trip is the one thing the library can't see (it never makes the
call), so wrap it once:

```python
from agent_safety import metered, safely

ask = metered(client.messages.create)          # wrap once
with safely(allow="...", budget="$100", tokens=2_000_000):
    resp = ask(model="claude-opus-4-8", messages=[...])
    # each call charges its call count, tokens, and dollar cost;
    # crossing the budget raises CostBudgetExceeded
```

`metered` reads `model=` from each call and prices it from a built-in table
(override with `price=Price(input=3.0, output=15.0)` — $ per 1M tokens). It
understands Anthropic / OpenAI / Gemini usage shapes, cache-read/write tokens,
and streaming, with no SDK dependency. Gemini binds the model to the client, so
name it once: `metered(gm.generate_content, model="gemini-1.5-pro")`.

## Profiles — sensible bundles

```python
from agent_safety import safely, Profiles

with safely(allow="search", **Profiles.hardened()):   # scrub + block injections + no loops
    ...

with safely(**Profiles.observe()):                    # monitor + log: watch first
    ...
```

## Many agents at once

The active policy lives in a `contextvars.ContextVar`, so every thread and every
`asyncio` task gets its *own* rules — several agents are just several `safely`
blocks, fully isolated. Share one `ConcurrencyLimit` (`at_most=shared_limit`) to
cap their combined parallelism. See
[`examples/multi_agent.py`](../examples/multi_agent.py).

## Errors — catch one family, or be precise

Every block raises a subclass of `AgentSafetyError`, so one handler catches all
safety stops:

```python
from agent_safety import AgentSafetyError

try:
    with safely(allow="ask_pdf", budget="$2", calls=10, timeout=60):
        print(ask_pdf("document.pdf", PROMPT))
except AgentSafetyError as e:
    print("blocked:", e)
    print(e.to_dict())   # {'error': 'permission_denied', 'retryable': False, ...}
```

Every exception carries a stable `code`, a `capability` when known, a
`retryable` flag, and `to_dict()` — ideal for handing back to a model as a tool
error it can repair from. The full code table is in [AGENTS.md](../AGENTS.md).

## Serializable policies — `PolicySpec`

Everything declarative about a `safely` block can be captured, shipped as JSON,
and replayed:

```python
from agent_safety import PolicySpec, safely_from_spec, safely

spec = PolicySpec(allow=("search",), calls=10, hide_secrets=True)
spec.to_dict()                       # JSON-ready; schema: schemas/policy_spec.schema.json
with safely_from_spec(spec):         # same as safely(**spec.to_kwargs())
    ...
```

Callable hooks (a custom approver, judge, or log sink) can't be serialized —
pass them at runtime: `safely_from_spec(spec, ask=fn, judge=fn, log=fn)`.

## Where next

* The exact order every call is checked in: [PIPELINE.md](PIPELINE.md)
* The objects under the keywords (policies, guards, gates, rollback, audit):
  [ADVANCED.md](ADVANCED.md)
* Multi-process enforcement: [DISTRIBUTED.md](DISTRIBUTED.md)
