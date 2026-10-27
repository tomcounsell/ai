"""A turn searches its project's memory (`valor-record memory`,
`core/record.py`'s `memory` verb, `core/memory.py` `search`): its own
project's records and standards of working from any client, a confined
record only for its own client, never a correction, read-only, on real
Postgres through a live gateway."""

import asyncio
import base64
import contextlib
import io
import json
import os

import psycopg
import pytest

from core import corrections, db, ledger, memory, record, tasks
from core.gateway import Gateway
from tests import bridges
from tests.ports import listen
from tools import valor_record

pytestmark = pytest.mark.spend(usd=0)

# Three projects: a for acme, b and c for bolt.
PROJECTS = {"a": "acme", "b": "bolt", "c": "bolt"}


def repo(name: str) -> str:
    return f"https://example.test/{name}.git"


async def worked(conn, task: str, prompt: str, reply: str) -> None:
    """A collected turn of `task` whose transcript holds its prompt and a
    reply."""
    turn_id = ledger.new_id()
    lines = [
        json.dumps({"type": t, "message": {"content": x}})
        for t, x in (("user", prompt), ("assistant", reply))
    ]
    body = {"turn_id": turn_id, "name": "s.jsonl", "offset": 0, "chunk": 0,
            "base64": base64.b64encode("\n".join(lines).encode()).decode()}  # fmt: skip
    await ledger.append(conn, task, "turn.started", {"turn_id": turn_id, "state": "build"})
    await conn.execute(
        "INSERT INTO documents (kind, id, body) VALUES ('transcript', %s, %s::jsonb)",
        (f"{turn_id}/s.jsonl/0", json.dumps(body)),
    )
    await ledger.append(conn, task, "turn.ended", {"turn_id": turn_id, "outcome": "done", "result": {},
                                                   "transcript": {"files": [{"name": "s.jsonl"}]}})  # fmt: skip


async def started(conn, project: str, instruction: str, chat: str | None = None) -> str:
    brief = tasks.Brief(
        instruction=instruction,
        project={"name": project, "repo": repo(project), "client": PROJECTS[project]},
        chat={"channel": "telegram", "chat_id": chat} if chat else None,
    )
    task = await tasks.start(conn, brief)
    await ledger.append(conn, task, "turn.started", {"turn_id": ledger.new_id(), "state": "plan"})
    return task


async def name_of(conn, task: str) -> str:
    """The record name of `task`'s instruction."""
    row = await (
        await conn.execute("SELECT id FROM events WHERE task_id = %s AND type = 'task.started'", (task,))
    ).fetchone()
    return f"{memory.ledger_name(conn)}/{row[0]}.0"


@pytest.fixture
def world(fresh, tmp_path):
    """Memory's popoto state is forgotten for each fresh database."""
    memory.reset()
    with bridges.operator(tmp_path):
        for i, (name, client) in enumerate(PROJECTS.items()):
            (tmp_path / "projects" / f"{name}.toml").write_text(
                f'name = "{name}"\nclient = "{client}"\nrepo = "{repo(name)}"\nkind = "plain"\n'
                f'suite = "true"\nchats = ["telegram:-10{i}"]\n'
            )

        async def seed():
            async with await db.connect(fresh) as conn:
                t = {"session": await started(conn, "a", "the a chat", chat="-100")}
                t["a"] = await started(conn, "a", "the florist's zebra invoices go out as PDF")
                t["b"] = await started(conn, "b", "bolt's zebra deploys wait for Friday")
                t["std"] = await started(conn, "b", "zebra commit messages say why, not what")
                t["conf"] = await started(conn, "b", "bolt pays a zebra discount of nine percent")
                t["c"] = await started(conn, "c", "the c task")
                await memory.mark(conn, await name_of(conn, t["std"]))
                await memory.mark(conn, await name_of(conn, t["conf"]))
                await corrections.record(
                    conn,
                    "zebra discounts are bolt's alone",
                    by="tom",
                    via="test",
                    record=await name_of(conn, t["conf"]),
                )
                # A turn of the session after it began: its prompt is not a
                # record, its reply is.
                await worked(conn, t["session"], "what zebra colour did we pick", "We picked the blue zebra.")
                # Two replies, each past half of a page's token budget.
                for n in (1, 2):
                    await worked(conn, t["a"], "go on", f"okapi report {n}: " + "the okapi grazes. " * 900)
                t["name"] = await name_of(conn, t["a"])
                await memory.ingest(conn)
                return t

        yield fresh, asyncio.run(seed())
    memory.reset()


def search(dsn: str, task: str, *argvs: list[str]) -> list[tuple[int, dict]]:
    """Each argv of `valor-record memory --json` as a turn of `task` runs
    it, through a live gateway, and what it printed."""

    async def go():
        gateway = Gateway(dsn, upstream="http://127.0.0.1:9")
        await gateway.start(port=listen())
        base = gateway.issue(task, "turn-1") + "/record"
        out = []
        os.environ["VALOR_RECORD_URL"] = base
        for argv in argvs:
            buf, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
                rc = await asyncio.to_thread(valor_record.main, ["memory", *argv])
            out.append((rc, buf.getvalue() + err.getvalue()))
        await gateway.close()
        return out

    return asyncio.run(go())


def found(printed: str) -> list[str]:
    return [r["text"] for r in json.loads(printed)["records"]]


def stored(dsn: str) -> dict[str, int]:
    """The row count of every ledger and memory table."""

    async def go():
        async with await db.connect(dsn) as conn:
            mdsn = memory.dsn_for(conn)
            out = {}
            for table in ("events", "documents"):
                out[table] = (await (await conn.execute(f"SELECT count(*) FROM {table}")).fetchone())[0]
        with psycopg.connect(mdsn) as m:
            for (table,) in m.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'memory'"
            ).fetchall():
                out[table] = m.execute(f'SELECT count(*) FROM memory."{table}"').fetchone()[0]
        return out

    return asyncio.run(go())


def test_a_turn_finds_its_projects_memory_and_standards_and_nothing_of_another(world, monkeypatch):
    dsn, t = world
    monkeypatch.setenv("VALOR_RECORD_URL", "")  # restored after the gateway's
    before = stored(dsn)
    (rc, a), (_, c), (_, prompt) = (
        search(dsn, t["session"], ["zebra", "--json", "-n", "50"])
        + search(dsn, t["c"], ["zebra", "--json", "-n", "50"])
        + search(dsn, t["session"], ["colour", "--json"])
    )
    assert rc == 0
    # Project a: its own records (the instruction, and a reply ingested
    # after the session began) and bolt's standard; not b's unmarked
    # record, nor the standard a correction confined to bolt, nor the
    # correction itself.
    assert set(found(a)) == {
        "the florist's zebra invoices go out as PDF",
        "We picked the blue zebra.",
        "zebra commit messages say why, not what",
    }
    # Project c, also bolt's: both standards, the confined one included; none
    # of a's, nor b's unmarked record.
    assert set(found(c)) == {
        "zebra commit messages say why, not what",
        "bolt pays a zebra discount of nine percent",
    }
    # The session's prompt was not a record.
    assert found(prompt) == []
    (hit,) = [r for r in json.loads(a)["records"] if r["text"].startswith("the florist")]
    assert hit["record"] == t["name"]
    assert (hit["origin"], hit["project"]) == ("instruction", repo("a"))
    assert hit["at"].startswith("20") and f"task {t['a']}" in hit["label"]
    assert stored(dsn) == before


def test_paging_quoting_and_what_cannot_be_read(world, monkeypatch):
    dsn, t = world
    monkeypatch.setenv("VALOR_RECORD_URL", "")  # restored after the gateway's
    (_, first), (_, second), (_, both), (_, long), (_, printed), (_, nothing), (empty, said) = search(
        dsn, t["session"],
        ["zebra", "--json", "-n", "1"], ["zebra", "--json", "-n", "1", "--offset", "1"], ["zebra", "--json", "-n", "2"],
        ["okapi", "--json"], ["zebra", "-n", "1"], ["quagga"], [" "],
    )  # fmt: skip
    one, two = json.loads(first), json.loads(second)
    assert len(one["records"]) == 1 and one["next"] == 1 and two["next"] == 2
    assert found(both) == found(first) + found(second)
    # Two long records do not fit one page's budget: the page holds one, and
    # the next page starts at the other.
    long = json.loads(long)
    assert len(long["records"]) == 1 and long["next"] == 1
    # Printed, each record is its label and its text quoted as data.
    assert printed.splitlines()[0].startswith("- task ") and "\n  > " in printed
    assert "(more: --offset 1)" in printed
    assert nothing.strip() == "nothing found"
    with pytest.raises(record.NotFound, match="offset 'x'"):
        asyncio.run(record.answer(dsn, t["session"], "memory", {"q": "zebra", "offset": "x"}))
    assert record._limit({"limit": "500"}, record.MEMORY_LIMIT, record.MEMORY_MOST) == 50
    assert empty == 1 and "some words" in said
    with bridges.configure(memory="off"):
        ((off, said),) = search(dsn, t["session"], ["zebra"])
    assert off == 1 and "memory is off" in said
