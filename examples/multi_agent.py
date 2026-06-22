"""Many agents, each safe on its own terms.

    python examples/multi_agent.py

Because a policy lives in a context variable, every thread and every asyncio task
automatically gets its *own* rules. So running several agents at once is just
running several `safely(...)` blocks at once — they don't leak into each other.

This shows two things:
  1. Two agents with DIFFERENT powers running concurrently — one may send on the
     network, the other may not, same tool function.
  2. A shared `ConcurrencyLimit` capping their COMBINED parallelism (e.g. "no more
     than 2 calls to the flaky API at any moment, across all agents").
"""

import asyncio

from agent_safety import ConcurrencyLimit, safely, tool
from agent_safety.exceptions import PermissionDenied

# A shared cap: at most 2 tool calls run at once, total, across every agent.
SHARED = ConcurrencyLimit(2)

# Track how many run at once so we can prove the cap holds.
live = {"now": 0, "peak": 0}


@tool
async def call_api(agent_name, i):
    live["now"] += 1
    live["peak"] = max(live["peak"], live["now"])
    await asyncio.sleep(0.05)
    live["now"] -= 1
    return f"{agent_name} got result {i}"


@tool
async def send_email(to):
    return f"email sent to {to}"


async def researcher():
    # Allowed to call the API, but NOT to send email.
    with safely(allow="call_api", at_most=SHARED):
        results = await asyncio.gather(*(call_api("researcher", i) for i in range(3)))
        print("  researcher did:", len(results), "API calls")
        try:
            await send_email("boss@corp.com")
        except PermissionDenied:
            print("  researcher tried to email — blocked (it only has API access)")


async def messenger():
    # Allowed to send email AND call the API.
    with safely(allow=["send_email", "call_api"], at_most=SHARED):
        await asyncio.gather(*(call_api("messenger", i) for i in range(3)))
        print("  messenger sent:", await send_email("user@corp.com"))


async def main():
    print("Running two agents at once, each with its own rules...")
    await asyncio.gather(researcher(), messenger())
    print(f"\nMost API calls running at any one moment: {live['peak']} (shared cap was 2)")


if __name__ == "__main__":
    asyncio.run(main())
