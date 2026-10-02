"""A schema change applies to a ledger that already holds history without
rewriting any row.

`db.migrate` takes the schema file as an argument; here it applies
`core/schema.sql` plus an additive change of the kind later milestones make
(a nullable column, a plain index, a partial unique index) to:

- a copy of the kernel database `valor_rebuild`, which holds the
  demonstration's and the baseline's ledger (dumped read-only as the owner
  and restored into a scratch database; the original is never modified),
  skipped where this machine has no such database; and
- a database holding a row of every type the kernel writes, both approval
  shapes included, always run.

Each is checked row for row (`id`, `xmin`, and a digest of the rest),
table by table (`pg_relation_filenode`), and fold by fold: every task's
committed, charged, and remaining money through `tasks.status` against the
computation the kernel used before (`MONEY_SQL`, kept here as the oracle).

Live spend: none.
"""

import asyncio
import os
import subprocess
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

from core import db, guards, ledger, machine, tasks
from core.settings import settings

pytestmark = pytest.mark.spend(usd=0)

ADDITIVE = """
ALTER TABLE events ADD COLUMN IF NOT EXISTS annotation text;
CREATE INDEX IF NOT EXISTS events_type_idx ON events (type);
CREATE UNIQUE INDEX IF NOT EXISTS events_one_raise_row
    ON events ((payload->>'raise_id')) WHERE type = 'budget.raised';
"""

# Money as the kernel computed it before `tasks.money`: committed is the
# budget from the task's document, charged the sum of charges, and remaining
# committed minus charges minus open reservations (`REMAINING_SQL`).
MONEY_SQL = """
SELECT c, ch, c - ch - r FROM (SELECT
  (SELECT (body->>'budget_usd_micros')::bigint FROM documents
    WHERE kind = 'task' AND id = %(t)s) AS c,
  COALESCE((SELECT sum((payload->>'usd_micros')::bigint) FROM events
    WHERE task_id = %(t)s AND type = 'gateway.charged'), 0) AS ch,
  COALESCE((SELECT sum((r.payload->>'usd_micros')::bigint) FROM events r
    WHERE r.task_id = %(t)s AND r.type = 'gateway.reserved'
      AND NOT EXISTS (SELECT 1 FROM events c
        WHERE c.task_id = %(t)s AND c.type = 'gateway.charged'
          AND c.payload->>'call_id' = r.payload->>'call_id')), 0) AS r) AS money
"""


def _owner(database: str) -> psycopg.Connection:
    return psycopg.connect(settings.dsn(owner=True, database=database), autocommit=True)


def _exists(database: str) -> bool:
    with _owner("postgres") as conn:
        return (
            conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database,)).fetchone() is not None
        )


def _drop(database: str) -> None:
    with _owner("postgres") as conn:
        conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(database)))


def _snapshot(database: str) -> dict:
    with _owner(database) as conn:
        conn.execute("SET TIME ZONE 'UTC'")
        return {
            "events": conn.execute(
                "SELECT id, xmin::text, encode(sha256(convert_to(task_id || '|' || type || '|' || "
                "payload::text || '|' || at::text, 'UTF8')), 'hex') FROM events ORDER BY id"
            ).fetchall(),
            "documents": conn.execute(
                "SELECT kind, id, xmin::text, encode(sha256(convert_to(body::text || '|' || "
                "created_at::text, 'UTF8')), 'hex') FROM documents ORDER BY kind, id"
            ).fetchall(),
            "filenodes": conn.execute(
                "SELECT pg_relation_filenode('events'), pg_relation_filenode('documents')"
            ).fetchone(),
            "money": {
                t: tuple(int(v) for v in conn.execute(MONEY_SQL, {"t": t}).fetchone())
                for (t,) in conn.execute(
                    "SELECT id FROM documents WHERE kind = 'task' ORDER BY id"
                ).fetchall()
            },
        }


def _migrate_with_change(database: str, tmp_path: Path) -> None:
    schema = tmp_path / "schema.sql"
    schema.write_text(db.SCHEMA.read_text() + ADDITIVE)
    db.migrate(database, schema=schema)


def _check(database: str, before: dict) -> None:
    after = _snapshot(database)
    # No row rewritten: same xmin, same content. The only rows added are the
    # seeded guards, once each, on their own stream.
    assert after["events"][: len(before["events"])] == before["events"]
    added = [r[0] for r in after["events"][len(before["events"]) :]]
    with _owner(database) as conn:
        kinds = conn.execute("SELECT task_id, type FROM events WHERE id = ANY(%s)", (added,)).fetchall()
        guards_held = conn.execute(
            "SELECT payload->>'guard_id' FROM events WHERE task_id = 'guards' AND type = 'guard.granted'"
        ).fetchall()
    assert set(kinds) <= {("guards", "guard.granted")}
    assert sorted(g for (g,) in guards_held) == sorted(g["guard_id"] for g in guards.SEEDED)
    assert after["documents"] == before["documents"]
    assert after["filenodes"] == before["filenodes"]  # no table rewritten
    with _owner(database) as conn:
        assert conn.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_name = 'events' AND column_name = 'annotation'"
        ).fetchone()
        indexes = {r[0] for r in conn.execute("SELECT indexname FROM pg_indexes WHERE tablename = 'events'")}
        assert {
            "events_type_idx",
            "events_one_raise_row",
            "events_one_correction_number",
            "events_one_judge",
            "events_one_turn_row",
            "events_one_guard",
            "events_one_instance_grant",
        } <= indexes
        constraints = {
            r[0]
            for r in conn.execute(
                "SELECT conname FROM pg_constraint WHERE conrelid = 'events'::regclass AND contype = 'c'"
            )
        }
        assert machine.constraint_name() in constraints
        triggers = {
            r[0] for r in conn.execute("SELECT tgname FROM pg_trigger WHERE tgrelid = 'events'::regclass")
        }
        assert {"events_append_only", "events_no_truncate"} <= triggers
        grants = conn.execute(
            "SELECT has_table_privilege(%s, 'events', 'INSERT'), has_table_privilege(%s, 'events', 'UPDATE')",
            (settings.kernel_role, settings.kernel_role),
        ).fetchone()
        assert grants == (True, False)
        ones = conn.execute(
            "SELECT count(*) FROM events WHERE type = 'correction.recorded' AND payload->>'number' = '1'"
        ).fetchone()[0]
        assert ones == 1

    async def folds():
        async with await db.connect(settings.dsn(database=database)) as conn:
            out = {}
            for t in before["money"]:
                s = await tasks.status(conn, t)
                out[t] = (s["committed_usd_micros"], s["charged_usd_micros"], s["remaining_usd_micros"])
            return out

    assert asyncio.run(folds()) == before["money"]


@pytest.mark.skipif(not _exists(settings.database), reason=f"no {settings.database} database on this machine")
def test_a_schema_change_applies_to_a_copy_of_the_kernel_ledger_without_rewriting_it(tmp_path):
    copy = f"{settings.database}_copy_{os.getpid()}"
    dump = tmp_path / "kernel.dump"
    pg_bin = Path(settings.pg_bin)
    subprocess.run(
        [
            str(pg_bin / "pg_dump"),
            "--format=custom",
            f"--file={dump}",
            f"--dbname={settings.dsn(owner=True, database=settings.database)}",
        ],
        check=True,
        capture_output=True,
    )
    _drop(copy)
    with _owner("postgres") as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(copy)))
    try:
        subprocess.run(
            [
                str(pg_bin / "pg_restore"),
                "--exit-on-error",
                "--no-owner",
                f"--dbname={settings.dsn(owner=True, database=copy)}",
                str(dump),
            ],
            check=True,
            capture_output=True,
        )
        before = _snapshot(copy)
        assert len(before["events"]) > 1000 and before["money"]  # it holds history
        _migrate_with_change(copy, tmp_path)
        _check(copy, before)
        _legacy_folds(copy)
    finally:
        _drop(copy)


def old_state(rows: list[dict]) -> str:
    """The four-state fold the kernel used before the state machine, kept
    here as the oracle the legacy mapping is checked against: stopped, then
    a delivery not reopened by feedback, then an unanswered question, then
    live."""
    asked: dict[str, bool] = {}
    delivered = reopened = stopped = False
    for row in rows:
        kind, p = row["type"], row["payload"]
        if kind == "question.asked":
            asked[p["question_id"]] = False
        elif kind == "question.answered":
            asked[p["question_id"]] = True
        elif kind == "feedback.given":
            reopened = True
        elif kind == "task.delivered":
            delivered, reopened = True, False
        elif kind == "task.stopped":
            stopped = True
    if stopped:
        return "stopped"
    if delivered and not reopened:
        return "delivered"
    if not all(asked.values()):
        return "waiting for Tom"
    return "live"


ORACLE = {
    "stopped": {machine.State.STOPPED},
    "delivered": {machine.State.MERGE},
    "waiting for Tom": {machine.State.WAITING},
    "live": {machine.State.PATCH, machine.State.BUILD, machine.State.JUDGE},
}


def _legacy_folds(database: str) -> None:
    """Every task of a ledger written before the state machine folds,
    read-only, as legacy, to the state the old fold's precedence gives."""

    async def go():
        async with await db.connect(settings.dsn(database=database)) as conn:
            ids = [
                r[0]
                for r in await (
                    await conn.execute("SELECT id FROM documents WHERE kind = 'task' ORDER BY id")
                ).fetchall()
            ]
            return {t: await ledger.read(conn, t) for t in ids}

    streams = asyncio.run(go())
    assert streams
    for task, rows in streams.items():
        f = machine.fold(rows)
        if f.calibration:  # a calibration run's task, written by `core calibrate`
            continue
        assert f.legacy, task
        assert f.state in ORACLE[old_state(rows)], (task, f.state, old_state(rows))


EVERY_TYPE = [
    ("gateway.reserved", {"call_id": "c1", "turn_id": "t1", "model": "m", "usd_micros": 300}),
    ("gateway.reserved", {"call_id": "c2", "turn_id": "t1", "model": "m", "usd_micros": 200}),
    ("gateway.charged", {"call_id": "c1", "usd_micros": 120, "turn_id": "t1", "model": "m"}),
    ("gateway.refused", {"call_id": "c3", "usd_micros": 9_999, "reason": "exceeds remaining"}),
    ("turn.started", {"turn_id": "t1", "harness": "x", "argv": [], "brief": "b", "corrections": [1]}),
    ("turn.collected", {"turn_id": "t1", "question": "?", "done": None, "effects": []}),
    ("turn.reaped", {"turn_id": "t1", "processes": [{"pid": 1, "command": "x", "signal": "SIGTERM"}]}),
    ("turn.ended", {"turn_id": "t1", "outcome": "done", "returncode": 0, "result": {}}),
    ("question.asked", {"question_id": "q1", "turn_id": "t1", "text": "?"}),
    ("question.answered", {"question_id": "q1", "text": "yes", "provenance": {"by": "tom", "via": "cli"}}),
    ("task.delivered", {"turn_id": "t1", "summary": "done"}),
    (
        "feedback.given",
        {"feedback_id": "f1", "on_delivery": "done", "text": "again", "provenance": {"by": "tom"}},
    ),
    ("effect.held", {"effect_id": "e1", "idempotency_key": "k1", "payload_sha256": "s"}),
    ("effect.refused", {"effect_id": "e2", "idempotency_key": "k2", "reason": "ceiling"}),
    (
        "approval.granted",
        {"approval_id": "a1", "effect_id": "e1", "payload_sha256": "s", "note": "ok", "by": "tom"},
    ),
    (
        "approval.granted",
        {
            "approval_id": "a2",
            "effect_id": "e1",
            "payload_sha256": "s",
            "note": "ok",
            "provenance": ledger.provenance("stand-in", "test", True),
        },
    ),
    ("effect.intent", {"effect_id": "e1", "idempotency_key": "k1", "approval_id": "a1"}),
    (
        "effect.outcome",
        {"effect_id": "e1", "idempotency_key": "k1", "kind": "done", "result": {}, "error": None},
    ),
    (
        "budget.raised",
        {"raise_id": "r1", "usd_micros": 500, "provenance": ledger.provenance("tom", "cli", False)},
    ),
    ("judge.decided", {"verdict": "precise", "leg": "manual", "judgement_id": None}),
    (
        "plan.written",
        {"turn_id": "t1", "path": "p.md", "sha256": "d", "critique_rounds": 1, "review_rounds": 2},
    ),
    ("critique.decided", {"plan_sha256": "d", "verdict": "sound", "raised": {"review_rounds": 2}}),
    ("test.decided", {"candidate": {"sha": "c", "turn_id": "t1"}, "verdict": "gaps"}),
    ("review.decided", {"candidate": {"sha": "c", "turn_id": "t1"}, "verdict": "governance_refused"}),
    ("docs.decided", {"candidate": {"sha": "c", "turn_id": "t1"}, "verdict": "no_change", "head": "c"}),
    ("guard.granted", {"guard_id": "grant-x", "instance_id": "i1", "incident": "i", "mission_items": [1]}),
    ("task.stopped", {"reason": "test", "by": "tom"}),
]


def test_a_schema_change_applies_over_a_row_of_every_type_without_rewriting_it(tmp_path):
    database = f"{settings.test_database}_history_{os.getpid()}"
    dsn = db.migrate(database, fresh=True)

    async def populate():
        async with await db.connect(dsn) as conn:
            task = await tasks.start(conn, tasks.Brief(instruction="every type", budget_usd_micros=1_000))
            async with conn.transaction():
                for kind, payload in EVERY_TYPE:
                    await ledger.append(conn, task, kind, payload)
            return task

    try:
        task = asyncio.run(populate())
        before = _snapshot(database)
        with _owner(database) as conn:
            types = {r[0] for r in conn.execute("SELECT DISTINCT type FROM events").fetchall()}
        assert {k for k, _ in EVERY_TYPE} | {"task.started", "correction.recorded", "guard.granted"} == types
        # The old computation never saw raises; the new fold adds them to
        # committed and remaining, and differs from the oracle by exactly
        # the raise.
        committed, charged, remaining = before["money"][task]
        before["money"][task] = (committed + 500, charged, remaining + 500)
        _migrate_with_change(database, tmp_path)
        _check(database, before)
    finally:
        _drop(database)
