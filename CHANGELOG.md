# Changelog

All notable changes to `agent_safety` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project aims to follow
[Semantic Versioning](https://semver.org/) from 1.0 onward (pre-1.0, minor
versions may include additive API changes).

## [0.8.0]

### Added
- `guard(*funcs)` — wrap existing functions as guarded tools in bulk, without
  editing them.
- `Profiles` — ready-made `safely(...)` setting bundles: `Profiles.hardened()`
  (secret-scrubbing, injection-blocking, hidden-char-stripping, loop-stopping
  hygiene) and `Profiles.observe()` (monitor/dry-run + logging).
- `SECURITY.md`, `THREAT_MODEL.md` (with an OWASP LLM Top 10 mapping), and an
  overhead benchmark (`examples/benchmark.py`).
- PyPI-ready packaging: dynamic version, project URLs, full classifiers.

### Changed
- Unified `ApprovalRequest` and `ReasoningRequest` into a single `Action` — the
  one object every safety hook (approver, judge, validator, preview) receives.
  The old names remain importable as aliases.

## [0.7.0]

### Added
- Constitutional rules: `ConstitutionGate` / `safely(rule=, judge=)` —
  plain-English rules judged by a model callable (`ConstitutionViolation`).
- `Honeytoken` guard — canary-secret exfiltration tripwire (`HoneytokenTripped`).
- `RiskBudget` / `@tool(risk=N)` / `safely(risk_budget=N)` — spend "danger,"
  not calls (`RiskBudgetExceeded`).
- `PreviewGate` / `@tool(preview=fn)` / `safely(preview=approver)` — approve a
  tool's "what would this do?" preview before it runs.
- `guard_mcp` / `SafeMCP` — run Model Context Protocol tool calls through the
  active policy (duck-typed, no MCP SDK dependency).

### Fixed
- MCP calls bypassed the approval/constitution/loop gates; `SafeMCP` now runs the
  full policy-level pipeline.

## [0.6.0]

### Added
- Monitor / dry-run mode: `safely(monitor=True)` / `safety_context(enforce=False)`.
- Idempotency: `@tool(cache=True)` / `guarded_tool(idempotent=True)`.
- `ConcurrencyLimit` / `safely(at_most=N)` — cap simultaneous tool calls, sharable
  across agents.

### Fixed
- Monitor mode crashed on the reserved `rationale=` kwarg.
- The idempotency cache leaked results past a stricter context's output guards
  (now caches raw and re-guards per call).
- A shared `ConcurrencyLimit` crashed across event loops (now per-loop).

## [0.5.0]

### Added
- The beginner front door: `@tool` and `safely(...)`, a plain-keyword facade over
  the power API. `TUTORIAL.md` and runnable examples.

## [0.4.0]

### Added
- Explainability: `ReasoningGate` (require a `rationale=`), `thought_trace()` /
  `record_thought()`.
- Tool-input validation (`@registry.tool(validate=True)`).
- Security guards: `SecretScanner`, `UnicodeSanitizer`.
- `Deadline` wall-clock budget.
- Tracing & metrics: `trace_span()`, `MetricsSink`.
- `Policy.explain()`, `PermissionSet.to_dict/from_dict`, `parse_tool_calls`.

## [0.3.0]

### Added
- Sandbox guards: `PathBoundary`, `NetworkAllowlist` (SSRF).
- Human-in-the-loop `ApprovalGate`.
- `RateLimit`, `LoopGuard`.
- Schema-from-signature inference, transactional `rollback()`.
- `py.typed`, strict mypy, ruff, CI lint job.

## [0.2.0]

### Added
- Initial public surface: `PermissionSet`, `Policy`, `safety_context`,
  `guarded_tool` / `guarded_async_tool`, core guards, `Quota`, audit sinks,
  `ToolRegistry` (Claude/OpenAI/Gemini dialects).
