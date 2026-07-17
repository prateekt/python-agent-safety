"""Transactional rollback for irreversible agent actions (the saga pattern).

An agent step often takes actions that can't be undone by discarding a value: it
creates a record, sends an email, charges a card. If a *later* action in the same
logical step fails, those earlier effects are left dangling.

``rollback()`` gives you a ``with`` block that records a **compensating action**
next to each forward action. On a clean exit it commits and the compensations are
discarded; if the block raises, the registered compensations run in LIFO order to
unwind what already happened, and then the original exception propagates.

    with rollback() as tx:
        row = create_record(payload)
        tx.on_undo(delete_record, row.id)        # how to undo the line above
        send_email(row.email)
        tx.on_undo(send_retraction, row.email)
        charge_card(row)                          # raises -> retraction, then delete,
                                                  #           then the error re-raises

This is a *best-effort* unwind, not a database transaction: compensations are your
own code, run in-process, and a compensation that itself fails is recorded on
``tx.compensation_errors`` (and audited) without stopping the others. Every begin
/ commit / compensation is emitted to the active policy's audit sinks, so an
aborted step lands on the same trail as every other safety decision.

Use ``async_rollback()`` when the forward or compensation actions are coroutines.
"""

from __future__ import annotations

import inspect
from contextlib import asynccontextmanager, contextmanager
from typing import Any, AsyncIterator, Callable, Dict, Iterator, List, Tuple

from .audit import AuditEvent
from .context import current_policy
from .exceptions import RollbackError

_Undo = Tuple[Callable[..., Any], Tuple[Any, ...], Dict[str, Any]]


class Transaction:
    """Records compensating actions and unwinds them in LIFO order on failure.

    A transaction is in one of three states: ``"open"`` (accepting actions),
    ``"committed"`` (work kept, compensations dropped), or ``"rolled_back"``
    (compensations have run). It is normally driven by :func:`rollback` /
    :func:`async_rollback`, but :meth:`commit` and :meth:`abort` can be called
    explicitly inside the block.
    """

    def __init__(self) -> None:
        self._undo: List[_Undo] = []
        self._state = "open"
        self.compensation_errors: List[BaseException] = []

    @property
    def state(self) -> str:
        return self._state

    @property
    def committed(self) -> bool:
        return self._state == "committed"

    def __len__(self) -> int:
        return len(self._undo)

    def on_undo(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Callable[..., Any]:
        """Register ``fn(*args, **kwargs)`` to compensate the action just taken.

        Returns *fn* so it can be used inline. Registrations run in reverse order
        (most recent first) if the transaction rolls back.
        """
        if self._state != "open":
            raise RuntimeError(f"cannot register an undo on a {self._state} transaction")
        self._undo.append((fn, args, kwargs))
        return fn

    def commit(self) -> None:
        """Keep the work permanently: drop all compensations (point of no return)."""
        if self._state != "open":
            return
        kept = len(self._undo)
        self._undo.clear()
        self._state = "committed"
        current_policy().audit(AuditEvent("rollback", "commit", detail=f"{kept} action(s) kept"))

    def abort(self) -> None:
        """Run all registered compensations now (sync).

        Raises :class:`~agent_safety.exceptions.RollbackError` if any compensator
        failed. Inside a ``with rollback()`` block you rarely need this — letting
        the block raise compensates automatically — but it lets you unwind
        deliberately without raising your own exception.
        """
        if self._state != "open":
            return
        errors = self._drain_sync()
        if errors:
            raise RollbackError(errors)

    async def abort_async(self) -> None:
        """Async counterpart of :meth:`abort`; awaits coroutine compensators."""
        if self._state != "open":
            return
        errors = await self._drain_async()
        if errors:
            raise RollbackError(errors)

    # -- internal unwinding ------------------------------------------------
    def _drain_sync(self) -> List[BaseException]:
        pending = len(self._undo)
        errors: List[BaseException] = []
        while self._undo:
            fn, args, kwargs = self._undo.pop()
            try:
                result = fn(*args, **kwargs)
                if inspect.isawaitable(result):
                    closer = getattr(result, "close", None)
                    if callable(closer):
                        closer()  # don't leave the coroutine un-awaited
                    raise RuntimeError(
                        f"compensator {getattr(fn, '__name__', fn)!r} is async; "
                        "use async_rollback() / abort_async()"
                    )
            except Exception as exc:  # best effort: keep unwinding the rest
                errors.append(exc)
        self._finish_rollback(pending, errors)
        return errors

    async def _drain_async(self) -> List[BaseException]:
        pending = len(self._undo)
        errors: List[BaseException] = []
        while self._undo:
            fn, args, kwargs = self._undo.pop()
            try:
                result = fn(*args, **kwargs)
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                errors.append(exc)
        self._finish_rollback(pending, errors)
        return errors

    def _finish_rollback(self, pending: int, errors: List[BaseException]) -> None:
        self._state = "rolled_back"
        self.compensation_errors = errors
        policy = current_policy()
        policy.audit(AuditEvent(
            "rollback", "compensate",
            detail=f"{pending} action(s) compensated, {len(errors)} failed",
        ))
        for exc in errors:
            policy.audit(AuditEvent("rollback", "compensation_error", detail=repr(exc)))


@contextmanager
def rollback() -> Iterator[Transaction]:
    """Scope a compensating transaction to a ``with`` block.

    Commits on a clean exit; on any exception, runs the registered compensations
    LIFO and re-raises the original exception (compensation failures are recorded
    on the transaction and audited, never masking the cause).
    """
    tx = Transaction()
    current_policy().audit(AuditEvent("rollback", "begin"))
    try:
        yield tx
    except BaseException:
        if tx.state == "open":
            tx._drain_sync()
        raise
    else:
        if tx.state == "open":
            tx.commit()


@asynccontextmanager
async def async_rollback() -> AsyncIterator[Transaction]:
    """Async counterpart of :func:`rollback`; awaits coroutine compensators."""
    tx = Transaction()
    current_policy().audit(AuditEvent("rollback", "begin"))
    try:
        yield tx
    except BaseException:
        if tx.state == "open":
            await tx._drain_async()
        raise
    else:
        if tx.state == "open":
            tx.commit()
