# Security Policy

## Reporting a vulnerability

Please report security issues privately via GitHub's **"Report a vulnerability"**
(Security → Advisories) on
[prateekt/python-agent-safety](https://github.com/prateekt/python-agent-safety/security),
**not** in a public issue. We aim to acknowledge within a few days and to discuss
a coordinated disclosure timeline.

## Supported versions

Pre-1.0, only the latest minor release receives fixes. Pin a version you have
reviewed.

## What this project is — and is not

`agent_safety` is a **defense-in-depth policy layer**, not a security boundary by
itself. Its durable guarantee is **least privilege**: code running inside a
`safely(...)` / `safety_context(...)` block can only invoke capabilities the
active `PermissionSet` granted, can't spend past its budgets, and has its inputs
and outputs filtered — and every attempt is on the audit trail.

The content guards (`PromptInjectionGuard`, `RedactPII`, `SecretScanner`,
`UnicodeSanitizer`) are **standard-library heuristics**, not complete detectors.
They are a starting point you extend; for production, layer real
moderation / secret-scanning / DLP behind the same `Guard` interface, and run the
whole thing **in front of** a real OS/network sandbox (containers, gVisor,
microVMs) and a DNS-aware HTTP client.

See [`THREAT_MODEL.md`](THREAT_MODEL.md) for the trust boundaries, what is in and
out of scope, residual risks, and a mapping to the OWASP LLM Top 10.

## Reducing your own attack surface

- The library has **zero runtime dependencies** (standard library only), which
  minimizes supply-chain exposure.
- It is **fail-safe by default**: outside any context the policy is deny-all.
- Permissions can only ever **narrow** in nested scopes (a one-way ratchet), so a
  sub-step can drop privileges but nothing inside can grant itself more.
