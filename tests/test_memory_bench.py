"""The private memory measurement's scorer (`python -m core memory bench`,
`memory/bench.py`, docs/plans/memory-measure.md), on real Postgres: a
question sees only the records from before its `asked_on`, an undated
import only in the with-undated arm, another client's records never, a
standard of working always; the scratch store is its own database and the
installed backend is reset after."""

import asyncio
import json
import subprocess
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import make_conninfo

from core import db, memory
from core.settings import settings
from memory import records
from tests.test_memory_import import FLOWERS, WEEDS, specs  # noqa: F401  the fixture

pytestmark = pytest.mark.spend(usd=0)


def line(text, project=FLOWERS, client="flowers-co", occurred=None):
    row = {"text": text, "project": project, "client": client, "by": "tom",
           "memory_id": uuid.uuid4().hex, "merged_from": []}  # fmt: skip
    if occurred:
        row |= {"occurred_at": occurred, "dated_by": "telegram message"}
    return json.dumps(row)


def test_bench_replays_each_question_on_the_store_as_it_stood(dsn, specs, tmp_path):  # noqa: F811
    async def build():
        async with await db.connect(dsn) as conn:
            await memory.import_old(
                conn,
                [
                    line(
                        "June: the greenhouse ledger goes out as a PDF", occurred="2026-06-01T00:00:00+00:00"
                    ),
                    line(
                        "July: the greenhouse ledger now goes out as a link",
                        occurred="2026-07-01T00:00:00+00:00",
                    ),
                    line("Undated: the greenhouse ledger is due on the fifth"),
                    line(
                        "Weeds: the greenhouse ledger for the weeds client",
                        WEEDS,
                        "weeds-co",
                        "2026-05-01T00:00:00+00:00",
                    ),
                    line(
                        "Weeds standard: every greenhouse ledger names its due date",
                        WEEDS,
                        "weeds-co",
                        "2026-05-01T00:00:00+00:00",
                    ),
                ],
            )
            await memory.ingest(conn)
            records.use(memory.dsn_for(conn))
            refs = {r.text.split(":")[0]: records.ref(r.ledger, r.key) for r in records.Record.query.all()}
            await memory.mark(conn, refs["Weeds standard"])
            await memory.ingest(conn)
            return refs, memory.ledger_name(conn)

    refs, name = asyncio.run(build())
    records.use(memory_dsn(dsn))
    before = len(records.Record.query.all())
    records.reset()
    dump = tmp_path / "store.dump"
    subprocess.run(
        [str(Path(settings.pg_bin) / "pg_dump"), "-Fc", "-n", "memory", "-f", str(dump), memory_dsn(dsn)],
        check=True,
    )
    test_db = psycopg.conninfo.conninfo_to_dict(dsn)["dbname"]
    owner = settings.dsn(owner=True, database=test_db)
    scratch_name = test_db + "_bench"
    with psycopg.connect(owner, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{scratch_name}"')
        conn.execute(f'CREATE DATABASE "{scratch_name}"')
    scratch = make_conninfo(owner, dbname=scratch_name)
    ask = {"ledger": name, "project": FLOWERS, "client": "flowers-co", "query": "greenhouse ledger"}
    questions = tmp_path / "questions.json"
    questions.write_text(json.dumps({"version": "v-test", "questions": [
        {"id": "june", "type": "long-term", "asked_on": "2026-06-15", "gold": [refs["June"]],
         "forbidden": [refs["Weeds"]], **ask},
        {"id": "undated", "type": "episodic", "asked_on": "2026-06-15", "gold": [refs["Undated"]], **ask},
        {"id": "early", "type": "long-term", "asked_on": "2026-06-15", "gold": [refs["July"]], **ask},
        {"id": "july", "type": "long-term", "asked_on": "2026-07-15", "gold": [refs["July"]], **ask},
    ]}))  # fmt: skip
    out = tmp_path / "results"
    try:
        by_type = memory.bench(str(questions), [str(dump)], str(out), scratch)
    finally:
        with psycopg.connect(owner, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{scratch_name}"')
    (scores,) = out.glob("v-test-????-??-??.json")
    result = json.loads(scores.read_text())
    rows = {q["id"]: q for q in result["questions"]}
    assert rows["june"]["with_undated"]["at10"] and rows["june"]["dated_only"]["at10"]
    assert rows["june"]["with_undated"]["leaked"] == []
    assert rows["undated"]["with_undated"]["at10"] and not rows["undated"]["dated_only"]["at10"]
    assert rows["july"]["with_undated"]["brief"]
    assert not rows["early"]["with_undated"]["at10"] and not rows["early"]["dated_only"]["at10"]
    assert result["question_set"] == "v-test" and len(result["question_set_sha256"]) == 64
    assert by_type["long-term"]["dated_only"]["recall_at_10"] == round(2 / 3, 3)
    assert by_type["episodic"]["with_undated"]["undated_in_top10"] == 1
    fixture = json.loads(next(out.glob("v-test-*-popoto-fixture.json")).read_text())
    assert {q["id"] for q in fixture["questions"]} == {"june", "undated", "early", "july"}
    assert {r["id"]: r["at"] for r in fixture["records"]}[refs["Undated"]] is None
    assert all(r["project"] == f"{name}|{FLOWERS}" for r in fixture["records"])
    assert records._dsn is None
    records.use(memory_dsn(dsn))
    assert len(records.Record.query.all()) == before
    records.reset()


def memory_dsn(dsn: str) -> str:
    async def go():
        async with await db.connect(dsn) as conn:
            return memory.dsn_for(conn)

    return asyncio.run(go())
