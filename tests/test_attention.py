"""Tom's taps on real Postgres: approvals carry provenance and count apart
from questions and feedback, old rows read what they recorded and nothing
more, and a legacy ledger's money rows fold into metered spending and
never into the attention log.

No model call. Live spend: none.
"""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest
from psycopg.types.json import Jsonb

from core import broker, db, ledger, tasks
from tests.conftest import TEST_DB
from tests.performers import OutboxAppend

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
        task = await new_task(dsn, max_effect_class="act")
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
        "verdict": {"total": 0, "role_played": 0, "unknown": 0},
        "grant": {"total": 0, "role_played": 0, "unknown": 0},
    }


def test_rows_from_before_provenance_read_what_they_recorded_and_no_more(dsn):
    """An approval with only a top-level `by`, and an answer and a feedback
    without `role_played`, as the ledger holds them from before this shape:
    each reads `role_played` None and counts as unknown."""

    async def go():
        task = await new_task(dsn)
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
        "verdict": 0,
        "grant": 0,
    }


# -- a legacy ledger -----------------------------------------------------------------


def test_a_legacy_ledger_with_reservations_and_raises_folds_to_its_spending(dsn):
    """Ledgers written before 2026-10-03 open calls with `gateway.reserved`,
    carry `budget.raised` rows (some without provenance), and start tasks
    with `budget_usd_micros` in the document and the `task.started` row.
    Nothing is rewritten: a reservation folds as an opened call, and the
    raise and the old field are ignored."""

    async def go():
        b = tasks.Brief(instruction="legacy")
        async with await db.connect(dsn) as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO documents (kind, id, body) VALUES ('task', %s, %s)",
                (b.id, Jsonb({"id": b.id, "instruction": "legacy", "budget_usd_micros": 1_000})),
            )
            for kind, payload in (
                ("task.started", {"sdlc": 1, "instruction": "legacy", "budget_usd_micros": 1_000}),
                ("gateway.reserved", {"call_id": f"{b.id}-1", "turn_id": "t", "usd_micros": 900}),
                ("gateway.charged", {"call_id": f"{b.id}-1", "usd_micros": 950}),
                ("budget.raised", {"raise_id": "r1", "usd_micros": 2_000, "note": "keep going"}),
                ("gateway.reserved", {"call_id": f"{b.id}-2", "turn_id": "t", "usd_micros": 1_500}),
            ):
                await conn.execute(
                    "INSERT INTO events (task_id, type, payload) VALUES (%s, %s, %s)",
                    (b.id, kind, Jsonb(payload)),
                )
        async with await db.connect(dsn) as conn:
            return await tasks.status(conn, b.id), await tasks.brief(conn, b.id)

    state, brief = run(go())
    assert brief.instruction == "legacy"  # the old document still loads
    assert state["spent_usd_micros"] == 950
    assert state["open_calls"] == {f"{brief.id}-2": 1_500}
    assert tasks.audit(state) == [f"gateway call {brief.id}-2 opened and never charged"]
    assert state["attention"] == [] and set(state["attention_counts"]) == set(tasks.ATTENTION_KINDS)
    assert not {"committed_usd_micros", "remaining_usd_micros"} & set(state)


# -- unknown tasks, open questions, one start per task -------------------------------


def test_an_open_question_is_listed_and_not_counted(dsn):
    async def go():
        task = await new_task(dsn)
        async with await db.connect(dsn) as conn:
            async with conn.transaction():
                await ledger.append(conn, task, "judge.decided", {"verdict": "precise", "leg": "judgement"})
                await ledger.append(
                    conn,
                    task,
                    "question.asked",
                    {"question_id": "q1", "turn_id": "t", "text": "?", "state": "plan"},
                )
            return await tasks.status(conn, task)

    state = run(go())
    assert state["state"] == "waiting" and state["return_to"] == "plan"
    assert [(a["kind"], a.get("answer")) for a in state["attention"]] == [("question", None)]
    assert state["attention_counts"]["question"] == {"total": 0, "role_played": 0, "unknown": 0}


def test_a_task_is_started_once(dsn):
    """`start` writes `task.started` with the task's document, and the
    document's primary key refuses a second start of the same id, so no
    task has two."""

    async def go():
        brief = tasks.Brief(instruction="once")
        async with await db.connect(dsn) as conn:
            await tasks.start(conn, brief)
            with pytest.raises(psycopg.errors.UniqueViolation):
                await tasks.start(conn, brief)
            return brief.id, await tasks.status(conn, brief.id)

    task, _ = run(go())
    rows = run(_types(dsn, task))
    assert rows.count("task.started") == 1


async def _types(dsn, task):
    async with await db.connect(dsn) as conn:
        return [r["type"] for r in await ledger.read(conn, task)]
