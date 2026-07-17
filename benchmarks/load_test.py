"""Load test for gateway mint throughput."""

from __future__ import annotations

import statistics
import time
import uuid

from agent_safety.distributed.backends import MemoryBackend
from agent_safety.distributed.gateway.server import GatewayConfig, PolicyGateway
from agent_safety.distributed.policy_spec import PolicySpec


def main() -> None:
    secret = b"load-test-signing-secret-32b!!!"
    backend = MemoryBackend()
    gw = PolicyGateway(GatewayConfig(require_auth=False, signing_secret=secret, backend=backend))
    spec = PolicySpec(allow=("tool",), calls=100_000)

    n = 500
    latencies = []
    start = time.perf_counter()
    for i in range(n):
        t0 = time.perf_counter()
        resp = gw.mint({
            "task_id": uuid.uuid4().hex,
            "request_id": str(i),
            "capability": "tool",
            "policy_spec": spec.to_dict(),
        })
        latencies.append((time.perf_counter() - t0) * 1000)
        assert resp.ok
    elapsed = time.perf_counter() - start
    print(f"mint {n} requests in {elapsed:.2f}s ({n/elapsed:.0f}/s)")
    print(f"p50={statistics.median(latencies):.2f}ms max={max(latencies):.2f}ms")


if __name__ == "__main__":
    main()
