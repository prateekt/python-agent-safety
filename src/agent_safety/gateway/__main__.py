"""Run the policy gateway HTTP server."""

from __future__ import annotations

import os

from .server import GatewayConfig, PolicyGateway, serve_gateway


def main() -> None:
    host = os.environ.get("AGENT_SAFETY_GATEWAY_HOST", "0.0.0.0")
    port = int(os.environ.get("AGENT_SAFETY_GATEWAY_PORT", "8765"))
    gw = PolicyGateway(GatewayConfig(host=host, port=port))
    server = serve_gateway(gw, host=host, port=port)
    print(f"agent-safety gateway listening on {host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
