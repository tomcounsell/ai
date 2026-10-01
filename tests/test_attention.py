"""Tom's taps and raises on real Postgres: approvals carry provenance and
count apart from questions and feedback, old rows read what they recorded
and nothing more, and a raise of the budget is folded by the one
computation of remaining money that reservations also use.

No model call. Live spend: none.
"""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest
from psycopg.types.json import Jsonb

from core import broker, budget, db, ledger, session, tasks
from core.gateway import Gateway
from tests.conftest import TEST_DB
from tests.test_session import turn_for
from tools.workspace import OutboxAppend

pytestmark = pytest.mark.spend(usd=0)

ROOT = Path(__file__).resolve().parent.parent


def run(coro):
    return asyncio.run(coro)


async def new_task(dsn, **kw) -> str:
    async with await db.connect(dsn) as conn:
        return await tasks.start(conn, tasks.Brief(instruction="test", **kw))


def cli(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "core", *args],
        cwd=ROOT,
        env={**os.environ, "VALOR_DB": TEST_DB},
        capture_output=True,
        text=True,
        check=False,
    )


async def _held(dsn, task, tmp_path, text) -> str:
    broker.register(OutboxAppend(tmp_path / "outbox.jsonl"))
    async with await db.connect(dsn) as conn:
        return (
            await broker.request(conn, task, broker.Action("outbox_send", "tom", {"text": text}))
        ).effect_id


# -- approvals ---------------------------------------------------------------------


def test_an_approval_carries_provenance_and_counts_apart_from_questions_and_feedback(dsn, tmp_path):
    async def go():
        task = await new_task(dsn, budget_usd_micros=0, max_effect_class="act")
        played = await _held(dsn, task, tmp_path, "one")
        live = await _held(dsn, task, tmp_path, "two")
        return task, played, live

    task, played, live = run(go())
    out = cli("approve", played, "--note", "standing permission", "--by", "stand-in", "--role-played")
    assert out.returncode == 0, out.stderr
    assert cli("approve", live, "--note", "yes, send it").returncode == 0

    async def after():
        async with await db.connect(dsn) as conn:
            released = await broker.release(conn, played)
            return released, await tasks.status(conn, task)

    released, state = run(after())
    assert released.kind == "done"  # still bound to the digest
    approvals = [a for a in state["attention"] if a["kind"] == "approval"]
    assert [a["effect_id"] for a in approvals] == [played, live]
    first, second = (a["provenance"] for a in approvals)
    assert first["by"] == "stand-in" and first["role_played"] is True and first["via"] == "the command line"
    assert second["by"] == "tom" and second["role_played"] is False and second["at"]
    assert approvals[0]["note"] == "standing permission"
    assert state["attention_counts"] == {
        "question": {"total": 0, "role_played": 0, "unknown": 0},
        "feedback": {"total": 0, "role_played": 0, "unknown": 0},
        "approval": {"total": 2, "role_played": 1, "unknown": 0},
        "budget_raise": {"total": 0, "role_played": 0, "unknown": 0},
    }


def test_rows_from_before_provenance_read_what_they_recorded_and_no_more(dsn):
    """An approval with only a top-level `by`, and an answer and a feedback
    without `role_played`, as the ledger holds them from before this shape:
    each reads `role_played` None and counts as unknown."""

    async def go():
        task = await new_task(dsn, budget_usd_micros=0)
        async with await db.connect(dsn) as conn, conn.transaction():
            await ledger.append(
                conn, task, "question.asked", {"question_id": "q1", "turn_id": "t", "text": "?"}
            )
            await ledger.append(
                conn,
                task,
                "question.answered",
                {
                    "question_id": "q1",
                    "text": "yes",
                    "provenance": {"by": "tom", "via": "cli", "at": "2026-09-30"},
                },
            )
            await ledger.append(conn, task, "task.delivered", {"turn_id": "t", "summary": "done"})
            await ledger.append(
                conn,
                task,
                "feedback.given",
                {"feedback_id": "f1", "on_delivery": "done", "text": "again", "provenance": {"by": "tom"}},
            )
            await ledger.append(
                conn,
                task,
                "approval.granted",
                {"approval_id": "a1", "effect_id": "e1", "payload_sha256": "x", "note": "ok", "by": "tom"},
            )
        async with await db.connect(dsn) as conn:
            rows = await ledger.read(conn, task)
            return rows, await tasks.status(conn, task)

    rows, state = run(go())
    question, feedback, approval = state["attention"]
    assert question["provenance"] == {"by": "tom", "via": "cli", "at": "2026-09-30", "role_played": None}
    assert feedback["provenance"]["role_played"] is None and feedback["provenance"]["via"] is None
    approval_row = next(r for r in rows if r["type"] == "approval.granted")
    assert approval["provenance"] == {
        "by": "tom",
        "via": None,
        "at": approval_row["at"].isoformat(),
        "role_played": None,
    }
    assert {k: v["unknown"] for k, v in state["attention_counts"].items()} == {
        "question": 1,
        "feedback": 1,
        "approval": 1,
        "budget_raise": 0,
    }


# -- budget raise ------------------------------------------------------------------


def _call(task, i, usd_micros) -> dict:
    return {"call_id": f"{task}-{i}", "turn_id": "t", "model": "m", "usd_micros": usd_micros}


def test_a_raise_reopens_an_exhausted_budget_and_one_fold_answers_remaining(dsn):
    async def go():
        task = await new_task(dsn, budget_usd_micros=1_000)
        seen = []
        async with await db.connect(dsn) as conn:

            async def agree():
                state = await tasks.status(conn, task)
                seen.append((state["remaining_usd_micros"], await budget.remaining(conn, task)))
                return state

            await budget.reserve(conn, task, _call(task, 1, 900))
            await budget.charge(conn, task, f"{task}-1", 950, {})  # over the reservation, under the budget
            await agree()
            with pytest.raises(budget.BudgetRefused):
                await budget.reserve(conn, task, _call(task, 2, 100))
            await budget.raise_budget(conn, task, 2_000, note="keep going", by="tom")
            await agree()
            await budget.reserve(conn, task, _call(task, 3, 1_500))
            await budget.charge(conn, task, f"{task}-3", 1_400, {})
            state = await agree()
        return seen, state

    seen, state = run(go())
    assert all(a == b for a, b in seen)
    assert [a for a, _ in seen] == [50, 2_050, 650]
    assert state["committed_usd_micros"] == 3_000 and state["charged_usd_micros"] == 2_350
    assert tasks.audit(state) == []  # past the first budget, within the raised one
    (raised,) = [a for a in state["attention"] if a["kind"] == "budget_raise"]
    assert raised["usd_micros"] == 2_000 and raised["note"] == "keep going"
    assert raised["provenance"]["by"] == "tom" and raised["provenance"]["role_played"] is False


def test_a_stopped_task_takes_no_raise_and_nothing_is_written(dsn):
    async def go():
        task = await new_task(dsn, budget_usd_micros=10)
        async with await db.connect(dsn) as conn:
            await tasks.stop(conn, task, reason="test")
            before = len(await ledger.read(conn, task))
            with pytest.raises(tasks.TaskStopped):
                await budget.raise_budget(conn, task, 1_000)
            for amount in (0, -5):
                with pytest.raises(ValueError):
                    await budget.raise_budget(conn, task, amount)
            return task, before, len(await ledger.read(conn, task))

    task, before, after = run(go())
    assert before == after
    out = cli("budget", "raise", task, "5")
    assert out.returncode != 0 and "stopped" in out.stderr


def test_the_cli_raise_is_folded_and_shown_in_the_next_brief(dsn, tmp_path):
    async def go():
        return await new_task(dsn, budget_usd_micros=1_000_000, workspace=str(tmp_path))

    task = run(go())
    out = cli("budget", "raise", task, "2.5", "--note", "more", "--by", "stand-in", "--role-played")
    assert out.returncode == 0, out.stderr
    assert "committed $3.5000" in out.stdout

    async def after():
        async with await db.connect(dsn) as conn:
            return await tasks.status(conn, task), await tasks.dispatch(conn, task)

    state, dispatched = run(after())
    assert state["committed_usd_micros"] == 3_500_000
    assert state["attention_counts"]["budget_raise"] == {"total": 1, "role_played": 1, "unknown": 0}
    assert "Budget: $3.5000" in dispatched["text"]


def test_a_run_stopped_by_an_empty_budget_runs_again_after_a_raise(dsn, tmp_path):
    async def go():
        task = await new_task(dsn, budget_usd_micros=0, workspace=str(tmp_path), max_effect_class="act")
        gateway = Gateway(dsn)
        await gateway.start()
        first = await session.run(gateway, task, turn_for, dsn=dsn)
        async with await db.connect(dsn) as conn:
            await budget.raise_budget(conn, task, 1_000)
        second = await session.run(gateway, task, turn_for, dsn=dsn)
        await gateway.close()
        return first, second

    first, second = run(go())
    assert first["status"] == "budget exhausted"
    assert second["status"] == "waiting"  # a turn ran and asked its question


def test_racing_raises_and_reservations_never_exceed_the_committed_total(dsn):
    async def go():
        task = await new_task(dsn, budget_usd_micros=1_000)

        async def reserve(i):
            async with await db.connect(dsn) as conn:
                try:
                    await budget.reserve(conn, task, _call(task, i, 300))
                    return 300
                except budget.BudgetRefused:
                    return 0

        async def give(_):
            async with await db.connect(dsn) as conn:
                await budget.raise_budget(conn, task, 500)
            return 0

        jobs = [reserve(i) for i in range(30)] + [give(i) for i in range(4)]
        reserved = sum(await asyncio.gather(*jobs))
        async with await db.connect(dsn) as conn:
            return reserved, await tasks.status(conn, task)

    reserved, state = run(go())
    assert state["committed_usd_micros"] == 3_000
    assert reserved <= 3_000 and reserved == sum(state["open_reservations"].values())
    assert state["remaining_usd_micros"] == 3_000 - reserved >= 0


def test_a_raise_written_without_provenance_folds_and_reads_unknown(dsn):

    async def go():
        task = await new_task(dsn, budget_usd_micros=100)
        async with await db.connect(dsn) as conn:
            await conn.execute(
                "INSERT INTO events (task_id, type, payload) VALUES (%s, 'budget.raised', %s)",
                (task, Jsonb({"raise_id": "r", "usd_micros": 50})),
            )
            return await tasks.status(conn, task)

    state = run(go())
    assert state["committed_usd_micros"] == 150
    p = state["attention"][0]["provenance"]
    assert p["by"] is None and p["via"] is None and p["role_played"] is None and p["at"]
    assert state["attention_counts"]["budget_raise"] == {"total": 1, "role_played": 0, "unknown": 1}
