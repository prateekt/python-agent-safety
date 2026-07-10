# Redis failover

1. Confirm Sentinel promoted a new primary (`redis-cli -p 26379 sentinel get-master-addr-by-name mymaster`).
2. Gateway should fail-closed while Redis is unavailable (circuit breaker opens).
3. Verify `/readyz` returns 503 until Redis recovers.
4. No manual budget reconciliation needed — idempotency keys prevent double-charge on replay.
