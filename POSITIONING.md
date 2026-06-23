# Positioning

## The category: action governance, not content filtering

Most "AI safety" tooling answers one question: **"is this *text* safe?"** —
moderation, PII detection, prompt-injection scanners. That made sense for
chatbots, where the only output is words.

Agents are different. They don't just emit text — they **take actions**: read
files, call APIs, run code, spend money. The dangerous question becomes
**"is this agent allowed to *do* this?"** — which no content scanner answers.
These are the two risks at the top of every agent post-mortem, and the two that
the OWASP LLM Top 10 calls out specifically:

- **LLM06 — Excessive Agency** (the agent did something it shouldn't be able to)
- **LLM10 — Unbounded Consumption** (it did it 10,000 times)

`agent_safety` owns that lane:

> **Least-privilege runtime control for AI agents — governing what they're
> allowed to *do*, not just what they say.**

The engineer-friendly analogies:

- **IAM / least-privilege for AI agents.**
- **The seccomp / capabilities layer between a model and your tools.**

## Complementary to scanners, not competing with them

We deliberately do **not** try to out-detect the content-filtering tools. Our
built-in guards are honest standard-library heuristics; for high-accuracy
detection you plug a real detector (Presidio, LLM Guard, Lakera, a moderation API)
in behind the same `Guard` interface.

```
            ┌─────────────── agent_safety ───────────────┐
   model →  │  content layer (your detectors as Guards)   │ → tool
            │  ACTION LAYER  ← this is the differentiator  │
            │  least privilege · budgets · approval ·      │
            │  constitutional rules · audit                │
            └─────────────────────────────────────────────┘
```

"Bring your detectors; we govern the actions" is a stronger, non-overlapping
story than "our regexes vs. their ML."

## How it stacks up

| Player | Their lane | Relationship |
|---|---|---|
| LLM Guard, Lakera, Guardrails AI | content scanning / output validation | **complementary** — run them as `Guard`s; we add the capability layer |
| NeMo Guardrails | conversational / dialogue rails (Colang DSL) | different problem (chat, not tools); heavier |
| OPA / Cerbos / oso | general authorization engines | we're the **agent-native, in-process, zero-config** version — no service or DSL |
| Agent frameworks (LangChain, CrewAI, …) | orchestration | the safety layer they don't ship — integrate, don't compete |

## Why it can win this lane

- **Zero dependencies, ~12 µs/call** — drops into a hot path without a second thought.
- **Least privilege as the spine** — the one-way `narrow()` ratchet means even a
  successful prompt injection can't invoke a capability that was never granted.
- **Measured, not asserted** — a public [attack scorecard](benchmarks/SCORECARD.md)
  (CI-gated) shows what's actually contained.
- **Provider- and framework-agnostic** — the same policy governs Claude, OpenAI,
  Gemini, and MCP tool calls.

## Who it's for

- **App teams** shipping agents that touch real systems and need a blast-radius limit.
- **Platform / security teams** who want a uniform, auditable policy across every
  agent and provider.
- **Framework authors** who want a safety layer to recommend or embed.

## What still makes it *the* standard (honest roadmap)

Code gets you eligible; these get you adopted:

1. A **language-agnostic gateway/proxy** so non-Python agents are governed too —
   moving from "a library" to "a control point."
2. Shipped **detector adapters** (Presidio / LLM Guard / Lakera) and **framework
   adapters** (LangChain / OpenAI Agents SDK / LlamaIndex / CrewAI).
3. An **enterprise management plane** — policy-as-config, versioning, RBAC, and
   audit export to OpenTelemetry / SIEM.
4. **Proof** — adoption signals, a security audit, and references from NIST AI RMF
   / ISO 42001 / MITRE ATLAS alongside the existing OWASP mapping.
