"""SQL (DB-API 2.0) budget backend — bring your own connection.

The library does **not** host or bundle a database. Pass a connect callable
that returns a DB-API connection to your existing Postgres / MySQL / SQLite
(or any compliant driver). Schema is created on first use via
:meth:`SqlBudgetBackend.ensure_schema`.
"""

from __future__ import annotations

import time
import uuid
from threading import Lock
from typing import Any, Callable, Optional, Sequence

from ...core.exceptions import LoopDetected, QuotaExceeded, RateLimitExceeded, RiskBudgetExceeded
from . import BudgetBackend, BudgetCharge, BudgetLimits, ChargeResult, MemoryBackend

Connect = Callable[[], Any]


def _scope(org_id: str, task_id: str) -> str:
    return f"{org_id}:{task_id}" if org_id else task_id


def _prepare_conn(conn: Any, dialect: str) -> None:
    # Autocommit so we own BEGIN/COMMIT (avoids nested-tx issues with sqlite3).
    if dialect == "sqlite" and hasattr(conn, "isolation_level"):
        conn.isolation_level = None


class SqlBudgetBackend:
    """Atomic budget charging against a user-supplied DB-API connection.

    Example (SQLite — stdlib, no hosting)::

        import sqlite3
        backend = SqlBudgetBackend(lambda: sqlite3.connect("budgets.db"))
        backend.ensure_schema()

    Example (Postgres — your pool/driver)::

        import psycopg
        backend = SqlBudgetBackend(
            lambda: psycopg.connect(DATABASE_URL),
            dialect="postgres",
        )
        backend.ensure_schema()
    """

    def __init__(
        self,
        connect: Connect,
        *,
        org_id: str = "",
        dialect: str = "auto",
    ) -> None:
        self._connect = connect
        self._org_id = org_id
        self._dialect = dialect
        self._schema_ready = False
        self._lock = Lock()

    def _detect_dialect(self, conn: Any) -> str:
        if self._dialect != "auto":
            return self._dialect
        name = type(conn).__module__.split(".", 1)[0].lower()
        if "psycopg" in name or "pg8000" in name:
            return "postgres"
        if "pymysql" in name or "mysql" in name or "mariadb" in name:
            return "mysql"
        return "sqlite"

    def _q(self, sql: str, dialect: str) -> str:
        """Translate ``?`` placeholders to the dialect's style."""
        if dialect in ("postgres", "mysql"):
            return sql.replace("?", "%s")
        return sql

    def _exec(self, cur: Any, dialect: str, sql: str, params: Sequence[Any] = ()) -> Any:
        return cur.execute(self._q(sql, dialect), tuple(params))

    def ensure_schema(self) -> None:
        """Create tables if missing (idempotent). Call once at process start."""
        with self._lock:
            conn = self._connect()
            try:
                dialect = self._detect_dialect(conn)
                _prepare_conn(conn, dialect)
                cur = conn.cursor()
                stmts = [
                    """
                    CREATE TABLE IF NOT EXISTS agent_safety_budget (
                        scope_key TEXT PRIMARY KEY,
                        calls INTEGER NOT NULL DEFAULT 0,
                        tokens INTEGER NOT NULL DEFAULT 0,
                        risk INTEGER NOT NULL DEFAULT 0
                    )
                    """,
                    """
                    CREATE TABLE IF NOT EXISTS agent_safety_idem (
                        request_id TEXT PRIMARY KEY,
                        lease_id TEXT,
                        calls_used INTEGER NOT NULL,
                        created_at REAL NOT NULL
                    )
                    """,
                    """
                    CREATE TABLE IF NOT EXISTS agent_safety_rate (
                        entry_id TEXT PRIMARY KEY,
                        scope_key TEXT NOT NULL,
                        stamped_at REAL NOT NULL
                    )
                    """,
                    """
                    CREATE TABLE IF NOT EXISTS agent_safety_loop (
                        entry_id TEXT PRIMARY KEY,
                        scope_key TEXT NOT NULL,
                        signature TEXT NOT NULL,
                        stamped_at REAL NOT NULL
                    )
                    """,
                    """
                    CREATE TABLE IF NOT EXISTS agent_safety_lease (
                        lease_id TEXT PRIMARY KEY,
                        scope_key TEXT NOT NULL,
                        expires_at REAL NOT NULL
                    )
                    """,
                ]
                for stmt in stmts:
                    cur.execute(stmt)
                for idx in (
                    "CREATE INDEX IF NOT EXISTS agent_safety_rate_scope "
                    "ON agent_safety_rate(scope_key, stamped_at)",
                    "CREATE INDEX IF NOT EXISTS agent_safety_loop_scope "
                    "ON agent_safety_loop(scope_key, stamped_at)",
                    "CREATE INDEX IF NOT EXISTS agent_safety_lease_scope "
                    "ON agent_safety_lease(scope_key, expires_at)",
                ):
                    try:
                        cur.execute(idx)
                    except Exception:
                        pass
                conn.commit()
                self._schema_ready = True
            finally:
                conn.close()

    def charge(self, req: BudgetCharge, limits: BudgetLimits) -> ChargeResult:
        if not self._schema_ready:
            self.ensure_schema()

        org = req.org_id or self._org_id
        scope = _scope(org, req.task_id)
        now = time.time()
        lease_id = uuid.uuid4().hex

        with self._lock:
            conn = self._connect()
            dialect = self._detect_dialect(conn)
            _prepare_conn(conn, dialect)
            try:
                cur = conn.cursor()
                if dialect == "sqlite":
                    cur.execute("BEGIN IMMEDIATE")
                else:
                    cur.execute("BEGIN")

                self._exec(
                    cur, dialect,
                    "SELECT lease_id, calls_used FROM agent_safety_idem WHERE request_id = ?",
                    (req.request_id,),
                )
                row = cur.fetchone()
                if row is not None:
                    conn.commit()
                    return ChargeResult(
                        lease_id=row[0] or None,
                        calls_used=int(row[1]),
                        cached=True,
                    )

                if dialect == "postgres":
                    self._exec(
                        cur, dialect,
                        "SELECT calls, tokens, risk FROM agent_safety_budget "
                        "WHERE scope_key = ? FOR UPDATE",
                        (scope,),
                    )
                else:
                    self._exec(
                        cur, dialect,
                        "SELECT calls, tokens, risk FROM agent_safety_budget WHERE scope_key = ?",
                        (scope,),
                    )
                brow = cur.fetchone()
                calls = int(brow[0]) if brow else 0
                tokens = int(brow[1]) if brow else 0
                risk_used = int(brow[2]) if brow else 0

                if limits.max_calls is not None and calls + req.call_n > limits.max_calls:
                    conn.rollback()
                    raise QuotaExceeded("calls", limits.max_calls, calls + req.call_n)

                if (
                    limits.max_tokens is not None
                    and req.tokens > 0
                    and tokens + req.tokens > limits.max_tokens
                ):
                    conn.rollback()
                    raise QuotaExceeded("tokens", limits.max_tokens, tokens + req.tokens)

                if limits.max_risk is not None and req.risk > 0 and risk_used + req.risk > limits.max_risk:
                    conn.rollback()
                    raise RiskBudgetExceeded(limits.max_risk, risk_used + req.risk)

                if limits.rate_per_second is not None:
                    cutoff = now - limits.rate_window
                    self._exec(
                        cur, dialect,
                        "DELETE FROM agent_safety_rate WHERE scope_key = ? AND stamped_at < ?",
                        (scope, cutoff),
                    )
                    self._exec(
                        cur, dialect,
                        "SELECT COUNT(*) FROM agent_safety_rate WHERE scope_key = ?",
                        (scope,),
                    )
                    cnt = int(cur.fetchone()[0])
                    if cnt + req.call_n > limits.rate_per_second:
                        conn.rollback()
                        raise RateLimitExceeded(limits.rate_per_second, limits.rate_window, 1.0)
                    for i in range(req.call_n):
                        self._exec(
                            cur, dialect,
                            "INSERT INTO agent_safety_rate(entry_id, scope_key, stamped_at) "
                            "VALUES (?, ?, ?)",
                            (f"{req.request_id}:{i}:{uuid.uuid4().hex[:8]}", scope, now),
                        )

                if limits.max_identical is not None:
                    self._exec(
                        cur, dialect,
                        "INSERT INTO agent_safety_loop(entry_id, scope_key, signature, stamped_at) "
                        "VALUES (?, ?, ?, ?)",
                        (uuid.uuid4().hex, scope, req.signature, now),
                    )
                    self._exec(
                        cur, dialect,
                        "SELECT signature FROM agent_safety_loop WHERE scope_key = ? "
                        "ORDER BY stamped_at DESC LIMIT ?",
                        (scope, limits.loop_history),
                    )
                    recent = [r[0] for r in cur.fetchall()]
                    identical = sum(1 for s in recent if s == req.signature)
                    if identical > limits.max_identical:
                        conn.rollback()
                        raise LoopDetected("tool", limits.max_identical, identical)

                if limits.max_concurrent is not None:
                    self._exec(
                        cur, dialect,
                        "DELETE FROM agent_safety_lease WHERE scope_key = ? AND expires_at <= ?",
                        (scope, now),
                    )
                    self._exec(
                        cur, dialect,
                        "SELECT COUNT(*) FROM agent_safety_lease WHERE scope_key = ?",
                        (scope,),
                    )
                    active = int(cur.fetchone()[0])
                    if active >= limits.max_concurrent:
                        conn.rollback()
                        raise QuotaExceeded("concurrency", limits.max_concurrent, active + 1)
                    self._exec(
                        cur, dialect,
                        "INSERT INTO agent_safety_lease(lease_id, scope_key, expires_at) "
                        "VALUES (?, ?, ?)",
                        (lease_id, scope, now + limits.lease_ttl),
                    )
                else:
                    lease_id = ""

                new_calls = calls + req.call_n
                new_tokens = tokens + (req.tokens if req.tokens > 0 else 0)
                new_risk = risk_used + (req.risk if req.risk > 0 else 0)
                if brow is None:
                    self._exec(
                        cur, dialect,
                        "INSERT INTO agent_safety_budget(scope_key, calls, tokens, risk) "
                        "VALUES (?, ?, ?, ?)",
                        (scope, new_calls, new_tokens, new_risk),
                    )
                else:
                    self._exec(
                        cur, dialect,
                        "UPDATE agent_safety_budget SET calls = ?, tokens = ?, risk = ? "
                        "WHERE scope_key = ?",
                        (new_calls, new_tokens, new_risk, scope),
                    )

                self._exec(
                    cur, dialect,
                    "INSERT INTO agent_safety_idem(request_id, lease_id, calls_used, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (req.request_id, lease_id or None, new_calls, now),
                )
                conn.commit()
                return ChargeResult(
                    lease_id=lease_id or None,
                    calls_used=new_calls,
                    cached=False,
                )
            except (QuotaExceeded, RateLimitExceeded, LoopDetected, RiskBudgetExceeded):
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise
            finally:
                conn.close()

    def release_concurrency(self, task_id: str, lease_id: str, *, org_id: str = "") -> None:
        if not self._schema_ready:
            self.ensure_schema()
        with self._lock:
            conn = self._connect()
            dialect = self._detect_dialect(conn)
            _prepare_conn(conn, dialect)
            try:
                cur = conn.cursor()
                self._exec(
                    cur,
                    dialect,
                    "DELETE FROM agent_safety_lease WHERE lease_id = ?",
                    (lease_id,),
                )
                conn.commit()
            finally:
                conn.close()


def sql_backend(
    connect: Optional[Connect] = None,
    *,
    org_id: str = "",
    dialect: str = "auto",
) -> BudgetBackend:
    """Return a SQL backend if *connect* given, else in-memory fallback."""
    if connect is None:
        return MemoryBackend()
    backend = SqlBudgetBackend(connect, org_id=org_id, dialect=dialect)
    backend.ensure_schema()
    return backend
