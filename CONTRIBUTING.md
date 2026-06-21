# Contributing to agent_safety

Thanks for your interest. This project keeps a deliberately small, dependency-free
core — contributions should preserve that.

## Principles

1. **The core stays standard-library only.** `src/agent_safety/` must import
   nothing outside the stdlib. Provider SDKs (`anthropic`, `openai`, `google-genai`)
   may appear *only* in optional, env-gated tests (`tests/test_*_live.py`).
2. **Provider-agnostic.** Nothing in the core may branch on a model provider. New
   provider support belongs in `integrations.py` as a new dialect plus a live test.
3. **Fail safe and only narrow.** Any new policy field must be appended by
   `Policy.narrow` so a nested `safety_context` can only tighten, never widen.
4. **Honest scope.** Guards are heuristics; document them as a foundation, not a
   guarantee. Don't oversell.

## Setup

```bash
pip install -e ".[dev]"
python -m pytest                 # 63 tests, no network, no keys
python examples/providers.py     # one policy across Anthropic/OpenAI/Gemini
```

## Tests

- Unit tests are offline and run in CI on Python 3.9–3.12.
- Live provider checks (`tests/test_{anthropic,openai,gemini}_live.py`) are
  **skipped** unless the matching SDK is installed *and* a key is in the
  environment (`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY`).
  **Never hardcode a key** — they are read from `os.environ` only.

## Adding a guard

Implement the `Guard` protocol — an object with a `name` attribute and a
`check(self, value, stage)` method that returns the (possibly transformed) value
or raises `GuardViolation`. Add it to `guards.py`, export it from `__init__.py`,
and cover it in `tests/test_guards.py`.

## Adding a provider dialect

Extend `ToolSpec.schema`, `ToolRegistry.schemas`, and `ToolRegistry.tool_result`
with the new dialect, add it to `DIALECTS`, cover the schema/dispatch/result
shapes in `tests/test_integrations.py`, and add an env-gated live test.

## Pull requests

Keep PRs focused, include tests, and make sure `python -m pytest` is green.
