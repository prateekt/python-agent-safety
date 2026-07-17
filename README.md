# agent_safety

[![CI](https://github.com/prateekt/python-agent-safety/actions/workflows/ci.yml/badge.svg)](https://github.com/prateekt/python-agent-safety/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/agent-safety)](https://pypi.org/project/agent-safety/)
![Python](https://img.shields.io/badge/python-3.9%2B-blue)
![Dependencies](https://img.shields.io/badge/dependencies-none%20(stdlib)-brightgreen)
![Typed](https://img.shields.io/badge/typed-mypy%20strict-blue)
![Providers](https://img.shields.io/badge/providers-Claude%20%7C%20OpenAI%20%7C%20Gemini-8A2BE2)
![License](https://img.shields.io/badge/license-MIT-green)

**Least-privilege runtime control for AI agents — governing what they're allowed
to *do*, not just what they say.**

Content scanners answer *"is this text safe?"* Agents need a different question:
*"is this agent allowed to **do** this?"* — read that file, call that API, spend
that budget. `agent_safety` is the **action layer**: an agent can only invoke the
capabilities you granted, on the budgets you set, and even a successful prompt
injection can't reach a tool it was never given. Zero dependencies, ~12 µs per
tool call, provider- and MCP-agnostic.

## Install

```bash
pip install agent-safety
```

## Start here — two ideas

Mark a function with `@tool`, then run it inside a `safely(...)` block that says,
in plain words, what's allowed:

```python
from agent_safety import tool, safely

@tool
def read_file(path):
    return open(path).read()

with safely(allow="read_file", calls=10, hide_secrets=True):
    text = read_file("notes.txt")   # allowed, budget-counted, secrets scrubbed
    # anything you didn't allow simply can't run here
```

That's the whole model. Outside a `safely` block nothing is allowed, so accidents
can't happen; inside one, only what you named can run. Nested blocks can only
*tighten* the rules, never loosen them.

> **New here?** Walk through [**TUTORIAL.md**](TUTORIAL.md) — a complete,
> running safe agent in about 10 minutes, offline, no API keys.

## Every option is a plain keyword

Reach for one when you need it; ignore the rest:

```python
with safely(
    allow=["read_file", "search"],  # what the code may do  (or allow="everything")
    deny="delete",                  # ...except this (deny always wins)
    calls=25,                       # most tool calls
    total_seconds=30,               # total time budget
    timeout=10,                     # no single call may hang past 10s
    budget="$5",                    # most money spent (with metered(...))
    hide_secrets=True,              # scrub emails / API keys from results
    block_injections=True,          # reject "ignore previous instructions" inputs
    no_repeats=3,                   # stop a runaway loop
    ask=True,                       # ask you (y/n) before each action
    monitor=True,                   # dry run: block nothing, log what WOULD block
    log=True,                       # print every decision
):
    ...
```

The full keyword reference is in [**docs/GUIDE.md**](docs/GUIDE.md).

**Already have tools?** Wrap them in bulk — no edits — and start from a
ready-made profile:

```python
from agent_safety import guard_tools, safely, Profiles

safe_search, safe_fetch = guard_tools(search, fetch)

with safely(allow=["search", "fetch"], **Profiles.hardened()):
    safe_search("agent safety")
```

**Cap what a model run costs.** Wrap the model call once with `metered` and every
request charges its own call, tokens, and dollars against the block's budget:

```python
from agent_safety import metered, safely

ask = metered(client.messages.create)
with safely(allow="...", budget="$100"):
    resp = ask(model="claude-opus-4-8", messages=[...])   # stops at $100 of spend
```

## Plug it into a real agent loop

`ToolRegistry` declares each tool once, emits schemas in your provider's dialect
(`"anthropic"` / `"openai"` / `"gemini"`), and dispatches the model's tool calls
through the same safety pipeline. `guard_mcp(session)` does the same for any
[MCP](https://modelcontextprotocol.io) server:

```python
from agent_safety import ToolRegistry, parse_tool_calls, guard_mcp, safely

registry = ToolRegistry()

@registry.tool("weather.read")
def get_weather(city: str) -> str:
    """Get the weather for a city."""       # schema inferred from the signature
    return f"sunny in {city}"

with safely(allow="weather.read", calls=20):
    tools = registry.schemas("openai")       # only the tools the policy allows
    # ... model requests a call ...
    for call in parse_tool_calls("openai", response):
        result = registry.safe_dispatch("openai", call.id, call.name, call.arguments)
        # a denied call comes back as an error result — the loop never crashes
```

## Measured, not asserted

The CI-gated [attack scorecard](benchmarks/SCORECARD.md) shows **13/13 known
agent attacks contained, 4/4 legitimate actions allowed**. The durable guarantee
is least privilege: even an injection that slips past the heuristics cannot
invoke a capability the policy never granted. Guards are tripwires and scrubbers,
not a complete security boundary — the honest scope is in
[THREAT_MODEL.md](THREAT_MODEL.md).

## Go deeper

| You want to… | Read |
|---|---|
| Build your first agent, step by step | [TUTORIAL.md](TUTORIAL.md) |
| Look up any `safely` keyword | [docs/GUIDE.md](docs/GUIDE.md) |
| Compose the core objects yourself (policies, guards, gates, rollback) | [docs/ADVANCED.md](docs/ADVANCED.md) |
| See the exact order every tool call is checked in | [docs/PIPELINE.md](docs/PIPELINE.md) |
| Govern agents across processes / machines (envelopes, gateway, shared budgets) | [docs/DISTRIBUTED.md](docs/DISTRIBUTED.md) |
| Migrate from an older version | [docs/MIGRATION.md](docs/MIGRATION.md) |
| Point an AI coding agent at this repo | [AGENTS.md](AGENTS.md) |
| Run the examples in order | [examples/README.md](examples/README.md) |

The package splits the same way: `agent_safety` (the golden path, ~18 names),
`agent_safety.core` (the engine), `agent_safety.integrations` (providers + MCP),
`agent_safety.distributed` (multi-process).

## Development

```bash
pip install -e ".[dev]"
python -m pytest                 # full suite, incl. the attack scorecard
python -m ruff check src && python -m mypy
```

MIT licensed. Security reports: [SECURITY.md](SECURITY.md). History:
[CHANGELOG.md](CHANGELOG.md).
