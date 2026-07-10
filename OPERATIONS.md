# Operations guide — distributed agent safety

## SLOs

| Service | Availability | p99 latency |
|---|---|---|
| Gateway `mint` | 99.9% | < 10 ms |
| Redis `charge` | 99.95% | < 5 ms |
| Worker envelope verify | 99.99% | < 100 µs |

## Environment variables

| Variable | Purpose |
|---|---|
| `AGENT_SAFETY_DISTRIBUTED` | `local`, `shadow`, `canary`, or `enforce` |
| `AGENT_SAFETY_GATEWAY_URL` | Gateway base URL for workers |
| `AGENT_SAFETY_ORG_ID` | Tenant namespace for Redis key prefixing |
| `AGENT_SAFETY_SIGNING_KEYS` | Path to JSON map of `kid -> base64 secret` for workers |

## Health checks

- `GET /healthz` — process alive
- `GET /readyz` — backend reachable, circuit breaker closed
- `GET /metrics` — Prometheus text exposition

## Key rotation

1. Add new `kid` to worker public key map.
2. Configure gateway to sign with new `kid`.
3. Overlap window: 24h where both keys are trusted.
4. Remove old `kid` after overlap.

## Rollout

1. **Shadow** — mint envelopes but enforce locally; compare audit decisions.
2. **Canary** — enforce envelopes for subset of `task_id`s.
3. **Enforce** — all tool workers require valid envelopes.

## Alerting

- `mint` p99 > 20 ms for 5 min
- Gateway 5xx > 0.1%
- `loop_detected_total` spike > 3× baseline
