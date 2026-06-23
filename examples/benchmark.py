"""Measure the per-call overhead `agent_safety` adds to a tool call.

    python examples/benchmark.py

The safety pipeline runs on every tool call, so the honest question is "how much
does wrapping cost?" This times a trivial tool with and without the wrapper, plus
a realistic hardened config, and reports microseconds per call. Numbers vary by
machine; run it on yours.
"""

import time

from agent_safety import Profiles, safely, tool

N = 200_000


def _bench(label, fn, warmup=2000):
    # Vary the argument so loop detection (in the hardened profile) sees distinct
    # calls rather than a repeated identical one.
    for i in range(warmup):
        fn(i)
    start = time.perf_counter()
    for i in range(N):
        fn(i)
    per_call_us = (time.perf_counter() - start) / N * 1e6
    print(f"  {label:<34} {per_call_us:7.3f} µs/call")
    return per_call_us


def raw(x):
    return x


@tool
def guarded(x):
    return x


def main() -> None:
    print(f"agent_safety per-call overhead  ({N:,} calls each)\n")

    base = _bench("plain function (no safety)", raw)

    with safely(allow="guarded"):
        minimal = _bench("guarded, allow only", guarded)

    with safely(allow="guarded", calls=10**9, per_second=10**9):
        budgeted = _bench("guarded + quota + rate limit", guarded)

    with safely(allow="guarded", **Profiles.hardened()):
        hardened = _bench("guarded + Profiles.hardened()", guarded)

    print("\noverhead vs a plain call:")
    print(f"  least-privilege only : {minimal - base:6.3f} µs/call")
    print(f"  + budgets            : {budgeted - base:6.3f} µs/call")
    print(f"  + hardened hygiene   : {hardened - base:6.3f} µs/call")
    print("\n(For comparison, a single LLM/tool round-trip is typically 100ms–several seconds.)")


if __name__ == "__main__":
    main()
