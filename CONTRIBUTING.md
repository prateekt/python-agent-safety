# Contributing to agent_safety

Thanks for your interest. This project keeps a deliberately small, dependency-free
core — contributions should preserve that.

## Principles

1. **The core stays standard-library only.** `src/agent_safety/` must import
   nothing outside the stdlib. Provider SDKs (`anthropic`, `openai`, `google-genai`)
   may appear *only* in optional, env-gated tests (`tests/test_*_live.py`).
2. **Provider-agnostic.** Nothing in the core may branch on a model provider. New
   provider support belongs in `integrations/providers.py` as a new dialect plus a
   live test.
3. **Fail safe and only narrow.** Any new policy field must be appended by
   `Policy.narrow` so a nested `safety_context` can only tighten, never widen.
4. **Honest scope.** Guards are heuristics; document them as a foundation, not a
   guarantee. Don't oversell.

## Setup

```bash
pip install -e ".[dev]"
python -m pytest                 # full suite, no network, no keys
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

## Package layout

- `agent_safety/easy.py` — the beginner front door (`tool`, `safely`,
  `guard_tools`, `Profiles`); the top-level `__init__.py` exports a curated
  ~18-name surface and nothing else.
- `agent_safety/core/` — the engine: permissions, guards, budgets, gates, the
  enforcement pipeline, audit, exceptions.
- `agent_safety/integrations/` — provider dialects (`providers.py`) and MCP
  (`mcp.py`).
- `agent_safety/distributed/` — envelopes, shared budgets, gateway, rollout
  config.

## The easy facade (`easy.py`)

`tool` and `safely(...)` are a **thin, beginner-facing wrapper** over the core —
`safely` just translates plain keywords (`allow=`, `calls=`, `hide_secrets=`
…) into the real objects (`PermissionSet`, `Quota`, guards, gates) and calls
`safety_context`. Keep it that way: a new keyword should map to existing
constructs, never add enforcement logic of its own. Engine behavior belongs in
`core/`; `easy.py` only makes it approachable. New declarative keywords must
also be added to `PolicySpec` (`distributed/policy_spec.py`) and the JSON schema
(`schemas/policy_spec.schema.json`) so serialization stays complete.

## Adding a guard

Implement the `Guard` protocol — an object with a `name` attribute and a
`check(self, value, stage)` method that returns the (possibly transformed) value
or raises `GuardViolation`. Content guards live in `core/guards.py`;
resource/sandbox guards (filesystem, network) live in `core/sandbox.py`. Export
it from `core/__init__.py`, and cover it in `tests/test_guards.py` or
`tests/test_sandbox.py`.

## Adding a budget or gate

`RateLimit`/`Deadline`/`LoopGuard` (`core/limits.py`) and the gates
(`core/gates.py`, all subclasses of the single `Gate` base) plug into `Policy`
as new fields. Any such field **must** be appended by `Policy.narrow` (never
able to widen), threaded through `safety_context`, and enforced as a step in
`core/pipeline.py` — there is exactly **one** enforcement checklist
(documented in [docs/PIPELINE.md](docs/PIPELINE.md)) shared by the sync, async,
registry, and MCP paths, so a new check lands everywhere at once. Decisions go
to the audit sinks via `Policy.audit`, which stamps the active `trace_span`
automatically.

## Adding a provider dialect

Extend `ToolSpec.schema`, `ToolRegistry.schemas`, and `ToolRegistry.tool_result`
with the new dialect, add it to `DIALECTS`, cover the schema/dispatch/result
shapes in `tests/test_integrations.py`, and add an env-gated live test.

## Schema inference

`core/schema.py` derives a tool's JSON-Schema `parameters` and description from
its signature, type hints, and docstring; `ToolRegistry.tool` uses it whenever
those are omitted. New type mappings go in `schema._schema_for_type` with a case
in `tests/test_schema.py`. It must stay provider-neutral — the per-dialect
shaping stays in `integrations/providers.py`, which consumes the neutral object
this produces.

## Pull requests

Keep PRs focused, include tests, and make sure `python -m pytest` is green.

## Releasing

The package version is sourced dynamically from `agent_safety.__version__`, so it
lives in exactly one place (`src/agent_safety/__init__.py`). Publishing uses PyPI
**Trusted Publishing** (OIDC) via `.github/workflows/publish.yml` — no API token
is stored in the repo.

One-time setup on PyPI (project owner): add a *pending publisher* under the
`agent-safety` project — owner `prateekt`, repo `python-agent-safety`, workflow
`publish.yml`, environment `pypi`.

To cut a release:

1. Bump `__version__` in `src/agent_safety/__init__.py` and add a `CHANGELOG.md` entry.
2. Merge to `main` (CI green).
3. Tag it: `git tag -a vX.Y.Z -m "agent_safety vX.Y.Z" && git push origin vX.Y.Z`.
4. Publish a **GitHub Release** for that tag — this triggers `publish.yml`, which
   re-runs the tests, builds, `twine check`s, and uploads to PyPI.

For the very first publish (the `publish.yml` workflow didn't exist at the `v0.8.0`
tag), run it once manually: **Actions → Publish to PyPI → Run workflow** on `main`.

Verify a build locally before releasing:

```bash
python -m build && python -m twine check dist/*
```
