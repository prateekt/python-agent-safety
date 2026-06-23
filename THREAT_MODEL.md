# Threat model

This document states what `agent_safety` defends, what it assumes, and what it
does **not** cover — so you can decide where it fits and what to layer around it.

## Assets

- **Capabilities** the agent can reach (filesystem, network, shell, your tools).
- **Sensitive data** flowing through the agent (secrets, PII, customer data).
- **Resources** (model tokens, API spend, wall-clock time, downstream load).
- **The audit trail** — the record of what the agent tried.

## Trust boundaries

| Component | Trust |
|---|---|
| The **model / agent** and any content it ingests (tool outputs, user input, retrieved docs) | **Untrusted** — may be adversarial (prompt injection). |
| Your **host code** that opens a `safely(...)` / `safety_context(...)` block | **Trusted** — it defines the trust ceiling. |
| The **tools** you register | Trusted code, but invoked with untrusted arguments. |

The core idea: an untrusted model drives, but it can only act *through* tools your
trusted host code allowed, under budgets your host set. Even a successful prompt
injection cannot invoke a capability that was never granted.

## In scope (what the library mitigates)

- **Excessive agency** — least-privilege `PermissionSet` (deny-all default, deny-wins,
  one-way `narrow()` ratchet), `ApprovalGate`, `ReasoningGate`, `ConstitutionGate`,
  `PreviewGate`.
- **Sensitive-data disclosure / exfiltration** — `RedactPII`, `SecretScanner`,
  `Honeytoken`, `NetworkAllowlist` (egress allow-list + SSRF/private-IP block),
  `PathBoundary` (filesystem confinement).
- **Unbounded consumption** — `Quota`, `RateLimit`, `Deadline`, `ConcurrencyLimit`,
  `RiskBudget`, `LoopGuard`.
- **Prompt injection (blast-radius reduction)** — `PromptInjectionGuard` and
  `UnicodeSanitizer` are tripwires; least privilege is the real containment.
- **Improper output handling** — output guards + tool-input schema validation.
- **Auditability** — every decision emits an `AuditEvent`; `trace_span` and
  `MetricsSink` for structure.

## Out of scope (you must handle elsewhere)

- **Real isolation.** This is policy, not a sandbox. A tool that runs shell or
  code still does so in-process; run it behind containers / gVisor / a microVM.
- **TOCTOU / DNS rebinding.** `PathBoundary` resolves symlinks and `NetworkAllowlist`
  rejects private-IP literals, but neither defeats a time-of-check/time-of-use race
  or a public name resolving to a private address. Enforce in the OS / HTTP client too.
- **High-accuracy detection.** The content guards are heuristics; pair with real
  moderation / DLP / secret-scanning behind the `Guard` interface.
- **Model/data poisoning, vector-store and embedding attacks, misinformation** —
  not addressed.
- **The judge itself.** A `ConstitutionGate` is only as good as its model judge,
  which is probabilistic and itself a prompt-injection target — keep hard limits
  underneath.

## Residual risks

- A missed injection that uses an **already-granted** capability in a harmful way.
- A guard heuristic that fails to match a novel encoding.
- A tool whose own implementation is unsafe regardless of arguments.

## Mapping to the OWASP LLM Top 10 (2025)

| Risk | How `agent_safety` helps |
|---|---|
| **LLM01 Prompt Injection** | `PromptInjectionGuard`, `UnicodeSanitizer` tripwires; **least privilege** so a missed injection can't act beyond what was granted. |
| **LLM02 Sensitive Information Disclosure** | `RedactPII`, `SecretScanner`, `Honeytoken`, `NetworkAllowlist`, `PathBoundary`. |
| **LLM03 Supply Chain** | **Zero runtime dependencies** (stdlib only). |
| **LLM04 Data & Model Poisoning** | Out of scope. |
| **LLM05 Improper Output Handling** | Output guards, `RedactPII`, tool-input schema validation. |
| **LLM06 Excessive Agency** | **Core focus** — `PermissionSet` least privilege + the `narrow()` ratchet, approval / reasoning / constitution / preview gates, risk budget. |
| **LLM07 System Prompt Leakage** | `PromptInjectionGuard` (reveal-prompt patterns), output redaction. |
| **LLM08 Vector & Embedding Weaknesses** | Out of scope. |
| **LLM09 Misinformation** | Out of scope (a `ConstitutionGate` judge can help at the margins). |
| **LLM10 Unbounded Consumption** | `Quota`, `RateLimit`, `Deadline`, `ConcurrencyLimit`, `RiskBudget`, `LoopGuard`. |
