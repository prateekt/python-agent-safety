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
python -m pytest                 # 106 tests, no network, no keys
python -m ruff check . && python -m mypy   # lint + strict type-check (CI gates on both)
python examples/providers.py     # one policy across Anthropic/OpenAI/Gemini
```

## Tests

- Unit tests are offline and run in CI on Python 3.9–3.12.
- Live provider checks (`tests/test_{anthropic,openai,gemini}_live.py`) are
  **skipped** unless the matching SDK is installed *and* a key is in the
  environment (`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY`).
  **Never hardcode a key** — they are read from `os.environ` only.

## Lint & types

CI runs `ruff check .` and `mypy` (strict, on `src/`) as a separate job. The
package ships a `py.typed` marker, so the public surface must stay fully typed.
We keep the classic `typing` spellings (`Tuple`, `Optional`, …) for clean 3.9
support — the pyupgrade (`UP`) ruleset is intentionally off.

## The easy facade (`easy.py`)

`tool` and `safely(...)` are a **thin, beginner-facing wrapper** over the power
API — `safely` just translates plain keywords (`allow=`, `calls=`, `hide_secrets=`
…) into the real objects (`PermissionSet`, `Quota`, guards, gates) and calls
`safety_context`. Keep it that way: a new keyword should map to existing
constructs, never add enforcement logic of its own. Engine behavior belongs in the
underlying module; `easy.py` only makes it approachable.

## Adding a guard

Implement the `Guard` protocol — an object with a `name` attribute and a
`check(self, value, stage)` method that returns the (possibly transformed) value
or raises `GuardViolation`. Content guards live in `guards.py`; resource/sandbox
guards (filesystem, network) live in `sandbox.py`. Export it from `__init__.py`,
and cover it in `tests/test_guards.py` or `tests/test_sandbox.py`.

## Adding a budget or gate

`RateLimit`/`Deadline`/`LoopGuard` (`limits.py`), `ApprovalGate` (`approval.py`),
and `ReasoningGate` (`reasoning.py`) plug into `Policy` as new fields. Any such
field **must** be appended by `Policy.narrow` (never able to widen), threaded
through `safety_context`, and enforced in `decorators.py` so both the sync and
async tool paths honour it. Decisions go to the audit sinks via `Policy.audit`,
which stamps the active `trace_span` automatically.

## Adding a provider dialect

Extend `ToolSpec.schema`, `ToolRegistry.schemas`, and `ToolRegistry.tool_result`
with the new dialect, add it to `DIALECTS`, cover the schema/dispatch/result
shapes in `tests/test_integrations.py`, and add an env-gated live test.

## Schema inference

`schema.py` derives a tool's JSON-Schema `parameters` and description from its
signature, type hints, and docstring; `ToolRegistry.tool` uses it whenever those
are omitted. New type mappings go in `schema._schema_for_type` with a case in
`tests/test_schema.py`. It must stay provider-neutral — the per-dialect shaping
stays in `integrations.py`, which consumes the neutral object this produces.

## Pull requests

Keep PRs focused, include tests, and make sure `python -m pytest` is green.
