# Operations guide — distributed agent safety

## SLOs

| Service | Availability | p99 latency |
|---|---|---|
| Gateway `mint` | 99.9% | < 10 ms |
| Redis `charge` | 99.95% | < 5 ms |
| Worker envelope verify | 99.99% | < 100 µs |

## Environment variables

These are read by the library / gateway process:

| Variable | Read by | Purpose |
|---|---|---|
| `AGENT_SAFETY_DISTRIBUTED` | `safely()`, helpers | `local`, `shadow`, `canary`, or `enforce` |
| `AGENT_SAFETY_CANARY_PERCENT` | helpers | 0–100; fraction of `task_id`s that require envelopes in `canary` (default `10`) |
| `AGENT_SAFETY_GATEWAY_URL` | `GatewayClient` | Default gateway base URL when `base_url` omitted |
| `AGENT_SAFETY_ORG_ID` | gateway CLI | Tenant namespace for Redis key prefixing |
| `AGENT_SAFETY_SIGNING_KEYS` | `safely()`, gateway CLI | Path to JSON `{"kid": "<base64-secret>", ...}` **or** the JSON string itself |
| `AGENT_SAFETY_SIGNING_SECRET` | gateway CLI | Single HMAC secret (base64 or raw) if not using the keys map |
| `AGENT_SAFETY_SIGNING_KID` | gateway CLI | Key id to sign with (default `default`) |
| `AGENT_SAFETY_JWT_SECRET` | gateway CLI | HS256 secret for service JWTs |
| `AGENT_SAFETY_REDIS_URL` | gateway CLI | Optional Redis URL for shared budgets (`redis://...`) |
| `AGENT_SAFETY_GATEWAY_HOST` / `_PORT` | gateway CLI | Bind address (default `0.0.0.0:8765`) |

Workers should receive **verify** secrets via `AGENT_SAFETY_SIGNING_KEYS` or
`safely(..., envelope_keys=...)`. Never fetch secrets from `GET /v1/keys` —
that endpoint returns **SHA-256 fingerprints** only (for rotation checks).

## Health checks

- `GET /healthz` — process alive
- `GET /readyz` — circuit breaker closed
- `GET /metrics` — Prometheus text exposition
- `GET /v1/keys` — `kid -> sha256:…` fingerprints (not raw secrets)

## Key rotation

1. Add new `kid` + secret to worker `AGENT_SAFETY_SIGNING_KEYS`.
2. Configure gateway to sign with the new `kid` (`AGENT_SAFETY_SIGNING_KID`).
3. Overlap window: keep both kids trusted on workers (e.g. 24h).
4. Remove the old `kid` after overlap; confirm fingerprints via `/v1/keys`.

## Rollout

Modes are enforced inside `safely(...)`:

1. **Shadow** — if an envelope is supplied, verify it; on failure log and continue.
2. **Canary** — require a valid envelope for a stable hash-fraction of `task_id`s
   (`AGENT_SAFETY_CANARY_PERCENT`).
3. **Enforce** — every `safely(...)` without an envelope fails closed.

## Shared budgets

Pass `backend=` + `run=` to `safely(...)` so each tool call/token charge hits the
shared store (Memory or Redis). When `envelope=` is also set, the **call** was
already charged at gateway mint time — the backend meters **tokens** only on the
hot path.

## Alerting

- `mint` p99 > 20 ms for 5 min
- Gateway 5xx > 0.1%
- `loop_detected_total` spike > 3× baseline
