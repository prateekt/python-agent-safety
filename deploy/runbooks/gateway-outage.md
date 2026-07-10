# Gateway outage

1. Check pod logs and `/healthz`.
2. Scale replicas: `kubectl scale deployment agent-safety-gateway --replicas=3`
3. Workers fail-closed on mint failures — agents pause tool calls until gateway recovers.
4. Audit events buffered locally should flush via `POST /v1/audit` when gateway returns.
