"""Shared nonce spend stores for envelope replay protection."""

from __future__ import annotations

import time
from threading import Lock
from typing import Any, Callable, Dict, Optional, Protocol, Sequence


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


class SqlNonceStore:
    """DB-API nonce store — bring your own connection (SQLite / Postgres / MySQL).

    Does not host a database; pass a connect callable to your existing store.
    """

    def __init__(
        self,
        connect: Callable[[], Any],
        *,
        dialect: str = "auto",
    ) -> None:
        self._connect = connect
        self._dialect = dialect
        self._ready = False
        self._lock = Lock()

    def _detect(self, conn: Any) -> str:
        if self._dialect != "auto":
            return self._dialect
        name = type(conn).__module__.split(".", 1)[0].lower()
        if "psycopg" in name or "pg8000" in name:
            return "postgres"
        if "pymysql" in name or "mysql" in name or "mariadb" in name:
            return "mysql"
        return "sqlite"

    def _q(self, sql: str, dialect: str) -> str:
        if dialect in ("postgres", "mysql"):
            return sql.replace("?", "%s")
        return sql

    def _exec(self, cur: Any, dialect: str, sql: str, params: Sequence[Any] = ()) -> Any:
        return cur.execute(self._q(sql, dialect), tuple(params))

    def ensure_schema(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                dialect = self._detect(conn)
                if dialect == "sqlite" and hasattr(conn, "isolation_level"):
                    conn.isolation_level = None
                cur = conn.cursor()
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS agent_safety_nonce (
                        nonce TEXT PRIMARY KEY,
                        expires_at REAL NOT NULL
                    )
                    """
                )
                conn.commit()
                self._ready = True
            finally:
                conn.close()

    def spend(self, nonce: str, expires_at: float) -> bool:
        if not self._ready:
            self.ensure_schema()
        with self._lock:
            conn = self._connect()
            dialect = self._detect(conn)
            if dialect == "sqlite" and hasattr(conn, "isolation_level"):
                conn.isolation_level = None
            try:
                cur = conn.cursor()
                if dialect == "sqlite":
                    cur.execute("BEGIN IMMEDIATE")
                else:
                    cur.execute("BEGIN")
                now = time.time()
                self._exec(
                    cur,
                    dialect,
                    "DELETE FROM agent_safety_nonce WHERE expires_at <= ?",
                    (now,),
                )
                self._exec(
                    cur,
                    dialect,
                    "SELECT 1 FROM agent_safety_nonce WHERE nonce = ?",
                    (nonce,),
                )
                if cur.fetchone() is not None:
                    conn.rollback()
                    return False
                try:
                    self._exec(
                        cur,
                        dialect,
                        "INSERT INTO agent_safety_nonce(nonce, expires_at) VALUES (?, ?)",
                        (nonce, expires_at),
                    )
                    conn.commit()
                    return True
                except Exception:
                    conn.rollback()
                    return False
            finally:
                conn.close()


class MongoNonceStore:
    """MongoDB nonce store — bring your own ``pymongo`` client."""

    def __init__(
        self,
        client: Any,
        *,
        db_name: str = "agent_safety",
        collection: str = "nonce",
    ) -> None:
        self._client = client
        self._db_name = db_name
        self._collection = collection
        self._lock = Lock()
        self._ready = False

    def ensure_indexes(self) -> None:
        with self._lock:
            coll = self._client[self._db_name][self._collection]
            # TTL on expires_at (epoch seconds) — Mongo expects a date; store as datetime-like
            # via expireAfterSeconds on a real Date field would be ideal; we prune manually too.
            try:
                coll.create_index("expires_at")
            except Exception:
                pass
            self._ready = True

    def spend(self, nonce: str, expires_at: float) -> bool:
        if not self._ready:
            try:
                self.ensure_indexes()
            except Exception:
                self._ready = True
        with self._lock:
            coll = self._client[self._db_name][self._collection]
            now = time.time()
            try:
                coll.delete_many({"expires_at": {"$lte": now}})
            except Exception:
                pass
            try:
                coll.insert_one({"_id": nonce, "expires_at": expires_at})
                return True
            except Exception:
                return False


class DynamoNonceStore:
    """DynamoDB nonce store — bring your own boto3 DynamoDB client + table.

    Expects partition key ``pk`` (string). Items use ``pk=NONCE#<nonce>``.
    """

    def __init__(self, client: Any, *, table_name: str) -> None:
        self._client = client
        self._table = table_name
        self._lock = Lock()

    def spend(self, nonce: str, expires_at: float) -> bool:
        if expires_at <= time.time():
            return True  # already expired — treat as fresh spend that won't stick
        with self._lock:
            try:
                self._client.put_item(
                    TableName=self._table,
                    Item={
                        "pk": {"S": f"NONCE#{nonce}"},
                        "expires_at": {"N": str(expires_at)},
                    },
                    ConditionExpression="attribute_not_exists(pk)",
                )
                return True
            except Exception as exc:
                if "ConditionalCheckFailed" in type(exc).__name__ or "ConditionalCheckFailed" in str(
                    exc
                ):
                    # Existing item — reject if still unexpired
                    resp = self._client.get_item(
                        TableName=self._table,
                        Key={"pk": {"S": f"NONCE#{nonce}"}},
                        ConsistentRead=True,
                    )
                    item = resp.get("Item")
                    if item is None:
                        return True
                    existing = float((item.get("expires_at") or {}).get("N", "0"))
                    if existing <= time.time():
                        # overwrite expired
                        self._client.put_item(
                            TableName=self._table,
                            Item={
                                "pk": {"S": f"NONCE#{nonce}"},
                                "expires_at": {"N": str(expires_at)},
                            },
                        )
                        return True
                    return False
                raise


_default_memory: Optional[MemoryNonceStore] = None


def default_memory_nonce_store() -> MemoryNonceStore:
    global _default_memory
    if _default_memory is None:
        _default_memory = MemoryNonceStore()
    return _default_memory


def nonce_store_from_env() -> NonceStore:
    """Redis nonce store when ``AGENT_SAFETY_REDIS_URL`` is set, else process-local memory."""
    from .config import DistributedConfig

    store: NonceStore = DistributedConfig.from_env().nonce_store()
    return store
