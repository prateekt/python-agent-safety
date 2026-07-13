"""Shared nonce spend stores for envelope replay protection."""

from __future__ import annotations

import os
import time
from threading import Lock
from typing import Any, Dict, Optional, Protocol


class NonceStore(Protocol):
    """Record spent envelope nonces; return False on replay."""

    def spend(self, nonce: str, expires_at: float) -> bool:
        """Mark *nonce* spent until *expires_at*. False if already spent."""
        ...


class MemoryNonceStore:
    """Process-local spent-nonce set (thread-safe). Share one instance across workers in-process."""

    def __init__(self) -> None:
        self._spent: Dict[str, float] = {}
        self._lock = Lock()

    def spend(self, nonce: str, expires_at: float) -> bool:
        now = time.time()
        with self._lock:
            expired = [n for n, exp in self._spent.items() if exp <= now]
            for n in expired:
                self._spent.pop(n, None)
            if nonce in self._spent:
                return False
            self._spent[nonce] = expires_at
            return True


class RedisNonceStore:
    """Cross-process nonce spend via Redis ``SET NX`` with TTL."""

    def __init__(self, client: Any, *, key_prefix: str = "agent_safety:nonce:") -> None:
        self._client = client
        self._prefix = key_prefix

    def spend(self, nonce: str, expires_at: float) -> bool:
        ttl = max(1, int(expires_at - time.time()) + 1)
        # SET key value NX EX ttl — True if set, None/False if exists
        return bool(self._client.set(f"{self._prefix}{nonce}", "1", nx=True, ex=ttl))


_default_memory: Optional[MemoryNonceStore] = None


def default_memory_nonce_store() -> MemoryNonceStore:
    global _default_memory
    if _default_memory is None:
        _default_memory = MemoryNonceStore()
    return _default_memory


def nonce_store_from_env() -> NonceStore:
    """Redis nonce store when ``AGENT_SAFETY_REDIS_URL`` is set, else process-local memory."""
    url = os.environ.get("AGENT_SAFETY_REDIS_URL", "").strip()
    if url:
        try:
            import redis  # type: ignore[import-not-found]

            return RedisNonceStore(redis.Redis.from_url(url))
        except Exception:
            pass
    return default_memory_nonce_store()
