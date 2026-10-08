"""A step that fails leaves `step.failed` (`router.step`): the state, the
check, the reason as text, and the turn, written as a runner writes its
rows, and shown by `tasks.status`, `core run`'s line, and the task page,
while it is the task's latest row. On a real Postgres ledger.

Live spend: none.
"""

import asyncio

import psycopg
import pytest

from core import db, ledger, notices, router, tasks
from core.__main__ import _status_line
from core.machine import State
from tests.bridges import new_task, rows
from ui import app

pytestmark = [pytest.mark.spend(usd=0)]


def run(coro):
    return asyncio.run(coro)


def failed_rows(got) -> list[dict]:
    return [r["payload"] for r in got if r["type"] == "step.failed"]


async def step(dsn, task, runner) -> dict:
    return await router.step(None, task, {State.JUDGE: runner}, dsn)


def test_a_returned_failure_is_a_row_and_the_returned_state_carries_it(dsn):
    """A turn that did not finish: its outcome and what the harness said are
    the reason, as text, with the turn's id; the step's state, the status
    line, `tasks.status`, and the task page all carry it."""

    async def failing(ctx):
        return {
            "status": "failed",
            "turn": {"turn_id": "t1", "outcome": "failed", "result": {"text": "boom"}},
        }

    async def go():
        task = await new_task(dsn)
        out = await step(dsn, task, failing)
        async with await db.connect(dsn) as conn:
            state = await tasks.status(conn, task)
            page = (await app.task_page(conn, task)).text
        return task, out, state, page, await rows(dsn, task)

    task, out, state, page, got = run(go())
    (row,) = failed_rows(got)
    assert row == {"state": "judge", "check": None, "reason": "the turn ended failed: boom", "turn_id": "t1"}
    assert out["status"] == "failed" and out["state"]["failed_step"]["reason"] == row["reason"]
    assert state["failed_step"] == out["state"]["failed_step"]
    assert f"recorded as step.failed, row {state['failed_step']['row']}" in _status_line(task, out)
    assert "failed_step" in page and "the turn ended failed: boom" in page


@pytest.mark.parametrize(
    ("turn", "reason"),
    [
        (
            {"result": "no verdict: the final message is not a JSON object"},
            "no verdict: the final message is not a JSON object",
        ),
        ({"turn_id": "t2", "outcome": "stopped", "result": {}}, "the turn ended stopped"),
        (
            {"turn_id": "t3", "outcome": "done", "result": {"error": "rate limited", "is_error": True}},
            "the turn ended done: rate limited",
        ),
        (None, "the turn ended None"),
    ],
)
def test_the_reason_is_always_text(dsn, turn, reason):
    async def failing(ctx):
        return {"status": "failed", **({"turn": turn} if turn is not None else {})}

    async def go():
        task = await new_task(dsn)
        await step(dsn, task, failing)
        return await rows(dsn, task)

    (row,) = failed_rows(run(go()))
    assert row["reason"] == reason and row["turn_id"] == (turn or {}).get("turn_id")


def test_a_raised_step_is_a_row_and_the_exception_goes_on(dsn):
    async def raising(ctx):
        raise RuntimeError("the runner broke")

    async def go():
        task = await new_task(dsn)
        with pytest.raises(RuntimeError, match="the runner broke"):
            await step(dsn, task, raising)
        return await rows(dsn, task)

    (row,) = failed_rows(run(go()))
    assert row == {
        "state": "judge",
        "check": None,
        "reason": "RuntimeError: the runner broke",
        "turn_id": None,
    }


def test_a_failed_write_never_replaces_the_steps_own_exception(dsn, monkeypatch, capsys):
    real = ledger.append

    async def append(conn, task_id, type_, payload):
        if type_ == "step.failed":
            raise psycopg.OperationalError("the database went away")
        return await real(conn, task_id, type_, payload)

    async def raising(ctx):
        monkeypatch.setattr(ledger, "append", append)
        raise RuntimeError("the runner broke")

    async def go():
        task = await new_task(dsn)
        with pytest.raises(RuntimeError, match="the runner broke"):
            await step(dsn, task, raising)
        monkeypatch.setattr(ledger, "append", real)
        return await rows(dsn, task)

    assert failed_rows(run(go())) == []
    assert "step.failed not written" in capsys.readouterr().err


def test_a_lost_lock_writes_nothing(dsn, owner_dsn):
    async def cut(ctx):
        with psycopg.connect(owner_dsn, autocommit=True) as owner:
            owner.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_locks WHERE locktype = 'advisory' "
                "AND granted AND pid <> pg_backend_pid() AND database = "
                "(SELECT oid FROM pg_database WHERE datname = current_database())"
            )
        return {"status": "failed", "turn": {"result": "x"}}

    async def go():
        task = await new_task(dsn)
        out = await step(dsn, task, cut)
        return out, await rows(dsn, task)

    out, got = run(go())
    assert out == {"status": "lock lost"} and failed_rows(got) == []


def test_a_task_stopped_while_its_step_fails_gets_no_row(dsn):
    async def stopping(ctx):
        async with await db.connect(ctx.dsn) as conn:
            await tasks.stop(conn, ctx.task_id, reason="test")
        return {"status": "failed", "turn": {"result": "x"}}

    async def go():
        task = await new_task(dsn)
        out = await step(dsn, task, stopping)
        async with await db.connect(dsn) as conn:
            state = await tasks.status(conn, task)
        return out, state, await rows(dsn, task)

    out, state, got = run(go())
    assert out["status"] == "failed" and failed_rows(got) == [] and state["failed_step"] is None


@pytest.mark.parametrize("status", ["moved", "stopped", "lock lost", "idle"])
def test_no_row_for_a_step_that_did_not_fail(dsn, status):
    async def ending(ctx):
        return {"status": status}

    async def go():
        task = await new_task(dsn)
        await step(dsn, task, ending)
        return await rows(dsn, task)

    assert failed_rows(run(go())) == []


def test_failed_step_holds_across_quiet_rows_and_clears_on_any_other(dsn):
    async def failing(ctx):
        return {"status": "failed", "turn": {"result": "x"}}

    async def go():
        task = await new_task(dsn)
        await step(dsn, task, failing)
        seen = []
        async with await db.connect(dsn) as conn:
            await notices.request(conn, task, kind="test", about_key="k", text="t")
            await ledger.append(conn, task, "notice.sent", {"notice_id": "n", "sent": []})
            seen.append((await tasks.status(conn, task))["failed_step"])
            await ledger.append(conn, task, "message.steered", {"text": "go on"})
            seen.append((await tasks.status(conn, task))["failed_step"])
        return seen

    held, cleared = run(go())
    assert held is not None and held["reason"] == "x"
    assert cleared is None
