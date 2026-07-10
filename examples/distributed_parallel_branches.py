"""Parallel branches sharing one task_id budget via MemoryBackend."""

from __future__ import annotations

import threading
import uuid

from agent_safety.backends import BudgetCharge, BudgetLimits, MemoryBackend
from agent_safety.policy_spec import PolicySpec


def main() -> None:
    backend = MemoryBackend()
    spec = PolicySpec(calls=3)
    limits = spec.budget_limits()
    task_id = uuid.uuid4().hex
    errors = []

    def branch(name: str, idx: int) -> None:
        try:
            backend.charge(
                BudgetCharge(
                    task_id=task_id,
                    request_id=f"{name}-{idx}",
                    signature=f"branch-{name}",
                ),
                limits,
            )
            print(f"{name} branch {idx} charged OK")
        except Exception as exc:
            errors.append(str(exc))
            print(f"{name} branch {idx} blocked: {exc}")

    threads = []
    for i in range(4):
        t = threading.Thread(target=branch, args=("risk", i))
        threads.append(t)
        t.start()
    for t in threads:
        t.join()

    print(f"Completed with {len(errors)} denials (expected 1 on 4th call)")


if __name__ == "__main__":
    main()
