"""Distributed event-driven agent loop (stdlib queue as event bus).

    python examples/distributed_event_loop.py

Simulates: Planner -> ToolRequest -> Gateway mint -> Tool Worker -> ToolResult
"""

from __future__ import annotations

import queue
import threading
import uuid

from agent_safety import ToolRegistry, safely
from agent_safety.distributed.backends import MemoryBackend
from agent_safety.distributed.events import ToolRequest, ToolResult
from agent_safety.distributed.gateway.client import GatewayClient
from agent_safety.distributed.gateway.server import GatewayConfig, PolicyGateway, serve_gateway
from agent_safety.distributed.policy_spec import PolicySpec
from agent_safety.distributed.run import RunContext

registry = ToolRegistry()


@registry.tool("weather.read")
def get_weather(city: str) -> str:
    return f"Sunny in {city}"


def main() -> None:
    secret = b"dev-signing-secret-32-bytes-long!!"
    spec = PolicySpec(allow=("weather.read",), calls=5, no_repeats=3)
    backend = MemoryBackend()
    gw = PolicyGateway(GatewayConfig(require_auth=False, signing_secret=secret, backend=backend, port=18765))
    gw.config.registry.publish(spec)
    serve_gateway(gw, port=18765)

    bus: queue.Queue = queue.Queue()
    client = GatewayClient("http://127.0.0.1:18765")
    keys = {"default": secret}
    task_id = uuid.uuid4().hex

    def planner() -> None:
        request = ToolRequest(
            request_id=uuid.uuid4().hex,
            task_id=task_id,
            capability="weather.read",
            tool="get_weather",
            arguments={"city": "Paris"},
            policy_spec_hash=spec.policy_hash(),
        )
        bus.put(request)
        result: ToolResult = bus.get(timeout=5)
        print("Planner received:", result)

    def tool_worker() -> None:
        request: ToolRequest = bus.get(timeout=5)
        mint_body = {
            "task_id": request.task_id,
            "request_id": request.request_id,
            "capability": request.capability,
            "tool": request.tool,
            "policy_spec": spec.to_dict(),
            "signature": f"{request.tool}|{request.capability}|{tuple(sorted(request.arguments.items()))!r}|()",
        }
        envelope = client.fetch_envelope(mint_body)
        if envelope is None:
            bus.put(ToolResult(request.request_id, request.task_id, ok=False, error="mint failed"))
            return

        ctx = RunContext(
            task_id=request.task_id,
            agent_id="tool-worker",
            request_id=request.request_id,
        )
        with safely(envelope=envelope, envelope_keys=keys, run=ctx):
            result = registry.safe_dispatch(
                "anthropic",
                "tu_1",
                request.tool,
                request.arguments,
            )
        bus.put(ToolResult(request.request_id, request.task_id, ok=True, result=result))

    t1 = threading.Thread(target=planner)
    t2 = threading.Thread(target=tool_worker)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    print("Done.")


if __name__ == "__main__":
    main()
