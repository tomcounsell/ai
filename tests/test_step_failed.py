"""A step that fails leaves `step.failed` (`router.step`): the state, the
check, the reason as text, and the turn, written as a runner writes its
rows, and shown by `tasks.status`, `core run`'s line, and the task page,
while it is the task's latest row. On a real Postgres ledger.

Live spend: none.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from core import db, ledger, notices, router, serve, session, tasks
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


# -- the kernel tries a failed step again, then ends the task ------------------------------


def test_the_same_step_failing_step_tries_times_in_a_row_ends_the_task_with_a_report(dsn):
    """No wait on Tom's steer: at `STEP_TRIES` failures in a row of the same
    state the task ends with the last reason, and Tom gets one report
    saying what failed."""

    async def failing(ctx):
        return {"status": "failed", "turn": {"result": "the harness exited 1"}}

    async def go():
        task = await new_task(dsn)
        for _ in range(router.STEP_TRIES):
            await step(dsn, task, failing)
        async with await db.connect(dsn) as conn:
            await notices.owe(conn, task)
            state = await tasks.status(conn, task)
        return state, await rows(dsn, task)

    state, got = run(go())
    assert len(failed_rows(got)) == router.STEP_TRIES and state["state"] == "ended"
    (ended,) = [r["payload"] for r in got if r["type"] == "task.ended"]
    assert ended["kind"] == "step" and ended["tries"] == router.STEP_TRIES and ended["state"] == "judge"
    (report,) = [r["payload"] for r in got if r["type"] == "notice.requested"]
    assert report["kind"] == "ended" and "the harness exited 1" in report["text"]
    assert f"failed {router.STEP_TRIES} times in a row" in report["text"]


def test_failures_count_in_a_row_and_progress_or_another_state_starts_the_count_again():
    def failed(i, state="build", check=None):
        return {"id": i, "type": "step.failed", "payload": {"state": state, "check": check}}

    assert router.failures([]) == 0
    assert router.failures([failed(1), failed(2)]) == 2
    collected = {"id": 3, "type": "turn.collected", "payload": {"verdict": "failed"}}
    assert router.failures([failed(1), collected, failed(4)]) == 2  # a failed turn is no progress
    idle = {"id": 3, "type": "turn.collected", "payload": {"verdict": "idle"}}
    assert router.failures([failed(1), idle, failed(4)]) == 1
    assert router.failures([failed(1, "checks", "review"), failed(2, "checks", "test")]) == 1
    steered = {"id": 3, "type": "message.steered", "payload": {}}
    assert router.failures([failed(1), steered, failed(4)]) == 1


def test_a_failed_step_is_due_again_after_a_doubling_delay():
    t0 = datetime(2026, 10, 10, tzinfo=UTC)
    one = [{"id": 1, "type": "step.failed", "payload": {"state": "build"}, "at": t0}]
    two = [*one, {"id": 2, "type": "step.failed", "payload": {"state": "build"}, "at": t0}]
    base = timedelta(seconds=router.STEP_BACKOFF_S)
    assert not router.retry_due(one, t0 + base - timedelta(seconds=1)) and router.retry_due(one, t0 + base)
    assert not router.retry_due(two, t0 + 2 * base - timedelta(seconds=1)) and router.retry_due(
        two, t0 + 2 * base
    )
    quiet = [*one, {"id": 3, "type": "notice.requested", "payload": {}, "at": t0}]
    assert router.retry_due(quiet, t0 + base)  # a notice is no newer row
    moved = [*one, {"id": 3, "type": "judge.decided", "payload": {}, "at": t0}]
    assert not router.retry_due(moved, t0 + base)  # the latest step did not fail


def test_the_resident_kernel_steps_a_failed_task_again_without_a_new_row(dsn, monkeypatch):
    """The kernel schedules the failed task again once the delay passed
    (none here), until the task ends."""
    monkeypatch.setattr(router, "STEP_BACKOFF_S", 0.0)

    async def failing(ctx):
        return {"status": "failed", "turn": {"result": "boom"}}

    async def go():
        task = await new_task(dsn)
        kernel = serve.Kernel(None, {State.JUDGE: failing}, None, dsn)

        async def active(conn):
            return [task]

        kernel.active = active
        async with await db.connect(dsn) as conn:
            for _ in range(router.STEP_TRIES + 2):
                await kernel.schedule(conn)
                await asyncio.gather(*kernel.jobs.values())
        return await rows(dsn, task)

    got = run(go())
    assert len(failed_rows(got)) == router.STEP_TRIES
    assert [r["type"] for r in got].count("task.ended") == 1


def test_the_next_working_turn_opens_with_why_the_last_try_failed(dsn):
    async def go():
        task = await new_task(dsn)
        async with await db.connect(dsn) as conn:
            await ledger.append(conn, task, "step.failed", {"state": "judge", "reason": "rate limited"})
            failed = await session.next_prompt(conn, task)
            await ledger.append(
                conn, task, "turn.collected", {"turn_id": "t", "state": None, "verdict": None}
            )
            later = await session.next_prompt(conn, task)
        return failed[0], later[0]

    failed, later = run(go())
    assert "rate limited" in failed and "rate limited" not in later
