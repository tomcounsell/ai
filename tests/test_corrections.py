"""Corrections on real Postgres: correction 1 recorded by a fresh `migrate`,
the rest recorded in the ledger, numbered in order, never edited, and
rendered into every turn's prompt and dispatched Brief.

No model call: the turn runs the argv the Claude Code harness builds, with
the `claude` binary swapped for a Python process that exits at once.
"""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest

from core import corrections, db, ledger, runs, tasks
from core.gateway import Gateway
from core.settings import settings
from harnesses import claude_code
from tests.conftest import TEST_DB

pytestmark = pytest.mark.spend(usd=0)

ROOT = Path(__file__).resolve().parent.parent
RESTRAINT = next(
    line for line in (ROOT / "CLAUDE.md").read_text().splitlines() if line.startswith("**Governance")
)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(scope="module")
def first(dsn):
    """Correction 1, as the session's fresh `migrate` recorded it."""

    async def go():
        async with await db.connect(dsn) as conn:
            return (await corrections.in_force(conn))[0]

    return run(go())


def test_a_fresh_migrate_holds_correction_one_the_restraint_paragraph(first):
    assert first["number"] == 1
    assert first["scope"] == "global" and first["source_class"] == "direct"
    assert first["text"] == RESTRAINT
    p = first["provenance"]
    assert p["by"] == "tom" and p["via"] == "CLAUDE.md, seeded by migrate" and p["role_played"] is False


def test_migrating_again_adds_no_second_correction_one_and_the_next_is_two(dsn, first):
    db.migrate(TEST_DB)
    db.migrate(TEST_DB)

    async def go():
        async with await db.connect(dsn) as conn:
            ones = await (
                await conn.execute(
                    "SELECT count(*) FROM events WHERE type = 'correction.recorded' "
                    "AND payload->>'number' = '1'"
                )
            ).fetchone()
            before = len(await corrections.in_force(conn))
            nxt = await corrections.record(conn, "After the seed.", by="tom", via="test")
            return ones[0], before, nxt

    ones, before, nxt = run(go())
    assert ones == 1
    assert nxt["number"] == before + 1


def test_corrections_are_numbered_in_the_order_given_even_when_racing(dsn, first):
    async def go():
        async def one(i):
            async with await db.connect(dsn) as conn:
                return await corrections.record(conn, f"racing {i}", by="tom", via="test")

        recorded = await asyncio.gather(*(one(i) for i in range(10)))
        async with await db.connect(dsn) as conn:
            return recorded, await corrections.in_force(conn)

    recorded, standing = run(go())
    numbers = [c["number"] for c in standing]
    assert numbers == list(range(1, len(numbers) + 1))
    assert sorted(c["number"] for c in recorded) == numbers[-10:]
    assert standing[0]["text"] == RESTRAINT


def test_scope_and_source_class_are_closed_sets(dsn):
    async def go():
        async with await db.connect(dsn) as conn:
            for kw in ({"scope": "project"}, {"source_class": "rumor"}):
                with pytest.raises(ValueError):
                    await corrections.record(conn, "x", by="tom", via="test", **kw)

    run(go())


def test_a_correction_cannot_be_edited_deleted_or_renumbered(dsn, owner_dsn, first):
    with psycopg.connect(dsn, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE events SET payload = '{}' WHERE id = %s", (first["event_id"],))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("DELETE FROM events WHERE id = %s", (first["event_id"],))
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                "INSERT INTO events (task_id, type, payload) VALUES (%s, 'correction.recorded', %s)",
                (corrections.STREAM, '{"number": 1, "text": "a second number one"}'),
            )
    with (
        psycopg.connect(owner_dsn, autocommit=True) as conn,
        pytest.raises(psycopg.errors.RaiseException, match="append-only"),
    ):
        conn.execute("UPDATE events SET payload = '{}' WHERE id = %s", (first["event_id"],))


def test_every_turn_renders_the_corrections_in_force_when_it_starts(dsn, first, tmp_path):
    """A task started before a correction still gets it on its next turn:
    the Brief is rendered from the ledger at dispatch, not copied at start."""

    def build(url, brief, turn_id):
        command = claude_code.turn("Reply with one word.", cwd=str(tmp_path))(url, brief, turn_id)
        command.argv = [sys.executable, "-c", "pass", *command.argv[1:]]
        return command

    async def go():
        async with await db.connect(dsn) as conn:
            task = await tasks.start(conn, tasks.Brief(instruction="test"))
            later = await corrections.record(conn, "Prefer the smaller diff.", by="tom", via="test")
        gateway = Gateway(dsn)
        await gateway.start()
        ended = await runs.run_turn(gateway, task, build, dsn=dsn)
        await gateway.close()
        async with await db.connect(dsn) as conn:
            rows = await ledger.read(conn, task)
        return later, ended, next(r["payload"] for r in rows if r["type"] == "turn.started")

    later, ended, started = run(go())
    assert ended["outcome"] == "done"
    assert started["corrections"][0] == 1 and later["number"] in started["corrections"]
    assert started["brief_sha256"] == ledger.digest(started["brief"])
    assert "# Brief" in started["brief"] and "Governance grant: none" in started["brief"]
    for text in (RESTRAINT, later["text"]):
        assert text in started["brief"]
    system_prompt = started["argv"][started["argv"].index("--system-prompt") + 1]
    assert system_prompt.startswith("You are Valor.") and started["brief"] in system_prompt


def test_the_cli_records_and_lists_corrections(dsn, first):
    env = {**os.environ, "VALOR_DB": TEST_DB}

    def cli(*args):
        return subprocess.run(
            [sys.executable, "-m", "core", *args],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        ).stdout

    recorded = cli("correct", "Ask before widening scope.", "--via", "test")
    listed = cli("corrections")
    assert recorded.startswith("correction ") and "recorded, ledger row" in recorded
    assert listed.index(RESTRAINT) < listed.index("Ask before widening scope.")


def test_migrate_names_the_file_when_the_governance_paragraph_is_missing(tmp_path, monkeypatch):
    source = tmp_path / "CLAUDE.md"
    source.write_text("# CLAUDE.md\n\nNo paragraph here.\n")
    monkeypatch.setattr(corrections, "GOVERNANCE_SOURCE", source)
    with pytest.raises(ValueError, match=f"{source} has no line starting"):
        corrections.governance_paragraph()
    database = f"{TEST_DB}_nogov_{os.getpid()}"
    try:
        with pytest.raises(ValueError, match="correction 1 is that paragraph"):
            db.migrate(database, fresh=True)
    finally:
        with psycopg.connect(settings.dsn(owner=True, database="postgres"), autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
