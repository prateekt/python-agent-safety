"""Benchmark local vs gateway mint latency."""

from __future__ import annotations

import statistics
import time
import uuid

from agent_safety.backends import MemoryBackend
from agent_safety.envelope import EnvelopeVerifier
from agent_safety.gateway.server import GatewayConfig, PolicyGateway
from agent_safety.policy_spec import PolicySpec


def _p99(samples: list) -> float:
    if not samples:
        return 0.0
    samples = sorted(samples)
    idx = int(len(samples) * 0.99) - 1
    return samples[max(0, idx)]


def bench_envelope_verify(n: int = 1000) -> None:
    secret = b"bench-signing-secret-32bytes!!!"
    gw = PolicyGateway(GatewayConfig(signing_secret=secret))
    spec = PolicySpec(allow=("x",), calls=1000)
    resp = gw.mint({
        "task_id": "t",
        "request_id": "r",
        "capability": "x",
        "policy_spec": spec.to_dict(),
    })
    from agent_safety.envelope import CapabilityEnvelope
    env = CapabilityEnvelope.from_dict(resp.envelope)
    verifier = EnvelopeVerifier({"default": secret})

    samples = []
    for _ in range(n):
        start = time.perf_counter()
        verifier.verify(env)
        samples.append((time.perf_counter() - start) * 1e6)
    print(f"envelope verify p50={statistics.median(samples):.1f}µs p99={_p99(samples):.1f}µs")


def bench_memory_charge(n: int = 1000) -> None:
    from agent_safety.backends import BudgetCharge, BudgetLimits, MemoryBackend
    backend = MemoryBackend()
    limits = BudgetLimits(max_calls=n + 1)
    samples = []
    for i in range(n):
        start = time.perf_counter()
        backend.charge(
            BudgetCharge(task_id="t", request_id=str(i), signature=f"s{i}"),
            limits,
        )
        samples.append((time.perf_counter() - start) * 1e3)
    print(f"memory charge p50={statistics.median(samples):.3f}ms p99={_p99(samples):.3f}ms")


if __name__ == "__main__":
    bench_envelope_verify()
    bench_memory_charge()
