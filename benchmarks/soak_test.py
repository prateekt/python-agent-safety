"""Short soak test for memory backend lease cleanup."""

from __future__ import annotations

import time
import uuid

from agent_safety.backends import BudgetCharge, BudgetLimits, MemoryBackend


def main() -> None:
    backend = MemoryBackend()
    limits = BudgetLimits(max_calls=1_000_000, max_concurrent=2, lease_ttl=0.1)
    start = time.perf_counter()
    iterations = 2000
    for i in range(iterations):
        result = backend.charge(
            BudgetCharge(task_id="soak", request_id=str(i), signature=f"s{i % 50}"),
            limits,
        )
        if result.lease_id:
            backend.release_concurrency("soak", result.lease_id)
    elapsed = time.perf_counter() - start
    print(f"soak: {iterations} charges in {elapsed:.2f}s")


if __name__ == "__main__":
    main()
