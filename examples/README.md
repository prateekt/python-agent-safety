# Examples, in learning order

Every example runs offline — no API keys, no network. From the repo root:

```bash
pip install -e .
python examples/easy.py
```

Work through them in order; each one introduces a little more than the last.

| # | File | What you'll learn |
|---|---|---|
| 1 | [`easy.py`](easy.py) | the whole golden path: `@tool`, `safely(allow=...)`, budgets, `hide_secrets`, `explain` — start here |
| 2 | [`first_agent.py`](first_agent.py) | a complete agent loop (a tiny offline "model" + `ToolRegistry` + `parse_tool_calls`) — the companion to [TUTORIAL.md](../TUTORIAL.md) |
| 3 | [`providers.py`](providers.py) | the same tools and the same policy driving Anthropic / OpenAI / Gemini tool-calling shapes |
| 4 | [`hardening.py`](hardening.py) | the security layers: `PathBoundary`, `NetworkAllowlist`, rate limits, loop breaking, `ask=`, `explain=`, `rollback()` |
| 5 | [`multi_agent.py`](multi_agent.py) | several agents at once, each in its own `safely` block, sharing one `ConcurrencyLimit` |
| 6 | [`mcp_agent.py`](mcp_agent.py) | `guard_mcp` — the same policy governing a (simulated) MCP server's remote tools |
| 7 | [`advanced_tour.py`](advanced_tour.py) | what the keywords compile down to: `safety_context`, `PermissionSet`, prompt guards from `agent_safety.core` |
| 8 | [`benchmark.py`](benchmark.py) | measure the per-call overhead of the pipeline on your machine |

## Distributed (multi-process) — read [docs/DISTRIBUTED.md](../docs/DISTRIBUTED.md) first

| # | File | Pattern |
|---|---|---|
| 9 | [`distributed_event_loop.py`](distributed_event_loop.py) | planner → gateway mint → worker verifies a signed envelope and runs the tool |
| 10 | [`distributed_handoff.py`](distributed_handoff.py) | a supervisor delegates with `PolicySpec.narrow()` — the worker gets strictly fewer powers |
| 11 | [`distributed_parallel_branches.py`](distributed_parallel_branches.py) | parallel workers drawing down one shared budget (the 4th call trips `QuotaExceeded`) |
