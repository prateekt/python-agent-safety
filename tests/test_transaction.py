import asyncio

import pytest

from agent_safety import (
    ListSink,
    PermissionSet,
    RollbackError,
    async_rollback,
    rollback,
    safety_context,
)


def test_commit_on_clean_exit_runs_no_compensation():
    undone = []
    with rollback() as tx:
        tx.on_undo(undone.append, "a")
        tx.on_undo(undone.append, "b")
    assert undone == []           # clean exit -> compensations discarded
    assert tx.committed
    assert tx.state == "committed"


def test_compensation_runs_lifo_on_exception():
    order = []
    with pytest.raises(RuntimeError, match="boom"):
        with rollback() as tx:
            tx.on_undo(order.append, "first")
            tx.on_undo(order.append, "second")
            raise RuntimeError("boom")
    assert order == ["second", "first"]   # last registered, first undone
    assert tx.state == "rolled_back"


def test_only_registered_actions_are_undone():
    # The failing action's own undo is registered *after* it, so it never runs.
    undone = []

    def step_two():
        raise ValueError("step two failed")

    with pytest.raises(ValueError):
        with rollback() as tx:
            tx.on_undo(undone.append, "undo step one")
            step_two()                       # raises before its undo is registered
            tx.on_undo(undone.append, "undo step two")  # never reached
    assert undone == ["undo step one"]


def test_explicit_commit_then_later_failure_keeps_work():
    undone = []
    with pytest.raises(RuntimeError):
        with rollback() as tx:
            tx.on_undo(undone.append, "a")
            tx.commit()                      # point of no return
            raise RuntimeError("after commit")
    assert undone == []                      # committed work is not rolled back
    assert tx.committed


def test_explicit_abort_runs_compensations():
    undone = []
    with rollback() as tx:
        tx.on_undo(undone.append, "a")
        tx.on_undo(undone.append, "b")
        tx.abort()
        assert tx.state == "rolled_back"
    assert undone == ["b", "a"]


def test_compensation_error_is_recorded_and_does_not_stop_unwind():
    undone = []

    def bad_undo():
        raise RuntimeError("compensator failed")

    with pytest.raises(ValueError, match="original"):
        with rollback() as tx:
            tx.on_undo(undone.append, "a")
            tx.on_undo(bad_undo)
            raise ValueError("original")
    # the failing compensator didn't prevent "a" from being undone
    assert undone == ["a"]
    assert len(tx.compensation_errors) == 1
    assert isinstance(tx.compensation_errors[0], RuntimeError)


def test_abort_raises_rollback_error_on_compensator_failure():
    def bad_undo():
        raise RuntimeError("nope")

    with pytest.raises(RollbackError) as ei:
        with rollback() as tx:
            tx.on_undo(bad_undo)
            tx.abort()
    assert len(ei.value.errors) == 1


def test_on_undo_after_finalize_raises():
    with rollback() as tx:
        tx.on_undo(lambda: None)
        tx.commit()
        with pytest.raises(RuntimeError):
            tx.on_undo(lambda: None)


def test_nested_rollbacks_are_independent():
    undone = []
    with rollback() as outer:
        outer.on_undo(undone.append, "outer")
        with pytest.raises(RuntimeError):
            with rollback() as inner:
                inner.on_undo(undone.append, "inner")
                raise RuntimeError("inner boom")
        assert undone == ["inner"]            # inner compensated, outer intact
    assert undone == ["inner"]                # outer committed cleanly


def test_rollback_emits_audit_events():
    audit = ListSink()
    with safety_context(PermissionSet.allow_all(), audit=[audit]):
        with pytest.raises(RuntimeError):
            with rollback() as tx:
                tx.on_undo(lambda: None)
                raise RuntimeError("x")
    actions = [(e.action, e.decision) for e in audit.events]
    assert ("rollback", "begin") in actions
    assert ("rollback", "compensate") in actions


# -- async ----------------------------------------------------------------

def test_async_rollback_compensates_with_async_undo():
    undone = []

    async def undo(label):
        await asyncio.sleep(0)
        undone.append(label)

    async def run():
        with pytest.raises(RuntimeError):
            async with async_rollback() as tx:
                tx.on_undo(undo, "a")
                tx.on_undo(undo, "b")
                raise RuntimeError("boom")
        return tx

    tx = asyncio.run(run())
    assert undone == ["b", "a"]
    assert tx.state == "rolled_back"


def test_async_rollback_commits_on_clean_exit():
    undone = []

    async def undo():
        undone.append("x")

    async def run():
        async with async_rollback() as tx:
            tx.on_undo(undo)
        return tx

    tx = asyncio.run(run())
    assert undone == []
    assert tx.committed


def test_sync_rollback_rejects_async_compensator():
    async def undo():
        pass

    with pytest.raises(ValueError):
        with rollback() as tx:
            tx.on_undo(undo)
            raise ValueError("trigger unwind")
    # the async compensator could not run in a sync rollback -> recorded
    assert len(tx.compensation_errors) == 1
    assert isinstance(tx.compensation_errors[0], RuntimeError)
