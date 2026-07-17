"""Run the policy gateway HTTP server."""

from __future__ import annotations

import base64
import os
from typing import Any

from ..backends import MemoryBackend
from ..backends.redis_backend import redis_backend
from ..config import load_signing_keys, org_id_from_env
from .server import GatewayConfig, PolicyGateway, serve_gateway


def _decode_secret(raw: str) -> bytes:
    try:
        return base64.b64decode(raw)
    except Exception:
        return raw.encode("utf-8")


def _signing_secret() -> tuple[bytes, str]:
    keys = load_signing_keys()
    kid = os.environ.get("AGENT_SAFETY_SIGNING_KID", "default")
    if keys:
        if kid in keys:
            return keys[kid], kid
        first_kid, first_secret = next(iter(keys.items()))
        return first_secret, first_kid
    raw = os.environ.get("AGENT_SAFETY_SIGNING_SECRET", "").strip()
    if raw:
        return _decode_secret(raw), kid
    return os.urandom(32), kid


def _jwt_secret() -> bytes:
    raw = os.environ.get("AGENT_SAFETY_JWT_SECRET", "").strip()
    if raw:
        return _decode_secret(raw)
    return os.urandom(32)


def _backend() -> Any:
    redis_url = os.environ.get("AGENT_SAFETY_REDIS_URL", "").strip()
    if redis_url:
        try:
            import redis  # type: ignore[import-not-found]

            client = redis.Redis.from_url(redis_url)
            return redis_backend(client, org_id=org_id_from_env())
        except Exception:
            pass
    return MemoryBackend()


def main() -> None:
    host = os.environ.get("AGENT_SAFETY_GATEWAY_HOST", "0.0.0.0")
    port = int(os.environ.get("AGENT_SAFETY_GATEWAY_PORT", "8765"))
    secret, kid = _signing_secret()
    gw = PolicyGateway(GatewayConfig(
        host=host,
        port=port,
        signing_secret=secret,
        jwt_secret=_jwt_secret(),
        kid=kid,
        org_id=org_id_from_env(),
        backend=_backend(),
    ))
    server = serve_gateway(gw, host=host, port=port)
    print(f"agent-safety gateway listening on {host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
