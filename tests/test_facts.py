"""Truth over time (docs/memory.md, Facts; issue #3684): dated facts that
supersede one another, entities under every name they have had, typed
links within a bound, sources, each project's and client's apart, rebuilt
from the ledger; written and read through `core/memory.py`, through a turn's
`valor-record` on a live gateway, and through the kernel command line, on
real Postgres, with made-up projects."""

import asyncio
import contextlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest

from core import db, memory, record, tasks, workspace
from core.gateway import Gateway
from core.settings import settings
from tests import bridges
from tests.ports import listen
from tools import valor_record

pytestmark = pytest.mark.spend(usd=0)

ROOT = Path(__file__).resolve().parent.parent
# Three projects: a and b for acme, c for bolt; d shares a's repo under bolt.
PROJECTS = {"a": ("acme", "a"), "b": ("acme", "b"), "c": ("bolt", "c"), "d": ("bolt", "a")}


def repo(name: str) -> str:
    return f"https://example.test/{name}.git"


def spec(name: str) -> workspace.Spec:
    client, r = PROJECTS[name]
    return workspace.Spec.from_dict(
        {"name": name, "client": client, "repo": repo(r), "kind": "plain", "suite": "true"}
    )


def fact(claim: str, since: str, **more) -> dict:
    return {
        "op": "fact",
        "kind": "pricing",
        "claim": claim,
        "source": "commit:abc1234",
        "since": since,
        **more,
    }


@pytest.fixture
def world(fresh, tmp_path):
    memory.reset()
    with bridges.operator(tmp_path) as s:
        for i, name in enumerate(PROJECTS):
            client, r = PROJECTS[name]
            (Path(s.projects_dir) / f"{name}.toml").write_text(
                f'name = "{name}"\nclient = "{client}"\nrepo = "{repo(r)}"\nkind = "plain"\n'
                f'suite = "true"\nchats = ["telegram:-20{i}"]\n'
            )
        yield fresh
    memory.reset()


def run(dsn: str, go):
    async def wrapped():
        async with await db.connect(dsn) as conn:
            return await go(conn)

    return asyncio.run(wrapped())


def write(dsn: str, name: str, *writes: dict) -> dict[str, str]:
    return run(dsn, lambda conn: memory.fact_write(conn, spec(name), list(writes)))


def read(dsn: str, name: str, **query) -> dict:
    return run(dsn, lambda conn: memory.facts_read(conn, spec(name), query))


def claims(found: dict) -> list[str]:
    return [f["claim"] for f in found["facts"]]


def rows(dsn: str) -> int:
    return run(dsn, lambda conn: _count(conn))


async def _count(conn) -> int:
    return (await (await conn.execute("SELECT count(*) FROM events WHERE task_id = 'memory'")).fetchone())[0]


def test_supersede_as_of_history_and_confirm(world):
    dsn = world
    n = write(
        dsn, "a",
        fact("the plan costs 10", "2025-01-01", **{"as": "p1"}),
        {"op": "supersede", "old": "@p1", "claim": "the plan costs 20", "kind": "pricing", "since": "2025-03-01",
         "source": "telegram:-200/7", "reason": "raised in March", "as": "p2"},
        {"op": "supersede", "old": "@p2", "claim": "the plan costs 30", "kind": "pricing", "since": "2025-06-01",
         "source": "telegram:-200/9", "reason": "raised again", "as": "p3"},
        fact("the app is a Django site", "2024-12-01", kind="stack", **{"as": "s"}),
    )  # fmt: skip
    assert claims(read(dsn, "a")) == ["the app is a Django site", "the plan costs 30"]
    assert claims(read(dsn, "a", as_of="2024-12-15")) == ["the app is a Django site"]
    assert claims(read(dsn, "a", as_of="2025-02-01")) == ["the app is a Django site", "the plan costs 10"]
    # Half-open: at the supersede instant the successor holds, not the old.
    assert claims(read(dsn, "a", as_of="2025-03-01")) == ["the app is a Django site", "the plan costs 20"]
    assert claims(read(dsn, "a", as_of="2026-01-01", kind="Pricing")) == ["the plan costs 30"]
    # A superseded fact is absent from the current set, present in history.
    history = read(dsn, "a", history=n["p2"])["facts"]
    assert [f["claim"] for f in history] == ["the plan costs 10", "the plan costs 20", "the plan costs 30"]
    old, mid, _ = history
    assert (old["until"], old["superseded_by"], mid["supersedes"], mid["reason"]) == (
        "2025-03-01", n["p2"], n["p1"], "raised in March",
    )  # fmt: skip
    assert (
        old["status"] == "inferred" and "inferred" in old["entry"] and "> the plan costs 10" in old["entry"]
    )
    # Superseding a fact that is not the head is refused, naming the head;
    # nothing is written.
    before = rows(dsn)
    with pytest.raises(memory.Refused, match=f"superseded by {n['p3']}; supersede that"):
        write(dsn, "a", {**fact("forked", "2025-07-01"), "op": "supersede", "old": n["p1"], "reason": "x"})
    # A supersede dated before the old fact's start is refused.
    with pytest.raises(memory.Refused, match="precedes the start"):
        write(dsn, "a", {**fact("too early", "2025-05-01"), "op": "supersede", "old": n["p3"], "reason": "x"})
    assert rows(dsn) == before
    # Confirm records the answer, here a row of another ledger; a superseded
    # fact can be confirmed and keeps its interval.
    write(dsn, "a", {"op": "confirm", "fact": n["p1"], "answer": "Another Mac/valor/42"})
    (old, *_) = read(dsn, "a", history=n["p1"])["facts"]
    assert (old["status"], old["confirmed_by"], old["since"], old["until"]) == (
        "confirmed", "Another Mac/valor/42", "2025-01-01", "2025-03-01",
    )  # fmt: skip
    assert claims(read(dsn, "a", as_of="2025-02-01")) == ["the app is a Django site", "the plan costs 10"]


def test_entities_aliases_and_links_within_the_bound(world):
    dsn = world
    n = write(
        dsn, "a",
        {"op": "entity", "name": "Old Name", "kind": "product", "aliases": ["oldname"], "as": "e"},
        {"op": "entity", "name": "Dana", "kind": "person", "as": "dana"},
        fact("the product renamed", "2025-02-01", kind="naming", about=["OLD   name"], **{"as": "f"}),
        fact("Dana owns pricing", "2025-02-02", kind="people", about=["Dana"], **{"as": "g"}),
        {"op": "link", "a": "@dana", "b": "@e", "kind": "decided", "source": "telegram:-200/3"},
        {"op": "entity", "name": "Far", "kind": "concept", "as": "far"},
        {"op": "link", "a": "@far", "b": "@g", "kind": "mentions"},
    )  # fmt: skip
    # Adding an entity under a name or alias it already has returns it and
    # adds the new alias; an alias another entity holds is refused.
    again = write(dsn, "a", {"op": "entity", "name": "New Name", "kind": "product", "aliases": ["oldname"]})
    assert again["1"] == n["e"]
    assert write(dsn, "a", {"op": "alias", "entity": "new name", "alias": "Old Name"})["1"] == n["e"]
    with pytest.raises(memory.Refused, match=f"already names {n['dana']}"):
        write(dsn, "a", {"op": "alias", "entity": "Old Name", "alias": "dana"})
    with pytest.raises(memory.Refused, match="different entities"):
        write(dsn, "a", {"op": "entity", "name": "Dana", "kind": "person", "aliases": ["Old Name"]})
    one = read(dsn, "a", related="NEW NAME", depth=1)
    assert one["seed"] == n["e"]
    assert {(r.get("fact") or r["entity"], r["hops"]) for r in one["related"]} == {
        (n["f"], 1),
        (n["dana"], 1),
    }
    assert {(l["kind"], l["a"]) for l in one["links"]} == {("about", n["f"]), ("decided", "Dana")}
    (e,) = [r for r in read(dsn, "a", related=n["dana"], depth=1)["related"] if r.get("entity")]
    assert set(e["aliases"]) == {"Old Name", "oldname", "New Name"}
    two = {
        r.get("fact") or r["entity"]: r["hops"]
        for r in read(dsn, "a", related="New Name", depth=2)["related"]
    }
    assert two == {n["f"]: 1, n["dana"]: 1, n["g"]: 2}
    three = {r.get("fact") or r["entity"] for r in read(dsn, "a", related="New Name", depth=3)["related"]}
    assert n["far"] in three
    (f,) = [x for x in read(dsn, "a")["facts"] if x["fact"] == n["f"]]
    assert f["about"] == ["Old Name"]


def test_another_projects_and_clients_facts_never_return(world):
    dsn = world
    n = write(
        dsn, "a", fact("acme a's secret", "2025-01-01"), {"op": "entity", "name": "Zed", "kind": "person"}
    )
    write(dsn, "b", fact("acme b's fact", "2025-01-01"))
    # d shares a's repo under another client.
    write(dsn, "d", fact("bolt d's fact", "2025-01-01"))
    assert claims(read(dsn, "a")) == ["acme a's secret"]
    assert claims(read(dsn, "b")) == ["acme b's fact"]
    assert claims(read(dsn, "d")) == ["bolt d's fact"]
    assert claims(read(dsn, "c")) == []
    for other in ("b", "d"):
        with pytest.raises(ValueError, match="no fact"):
            read(dsn, other, history=n["1"])
        with pytest.raises(ValueError, match="no fact or entity"):
            read(dsn, other, related="Zed")
        with pytest.raises(memory.Refused, match="no fact"):
            write(dsn, other, {"op": "confirm", "fact": n["1"], "answer": "9"})
        with pytest.raises(memory.Refused, match="no entity"):
            write(dsn, other, fact("about zed", "2025-01-01", about=["Zed"]))
    # d may name an entity Zed of its own.
    assert write(dsn, "d", {"op": "entity", "name": "Zed", "kind": "person"})["1"] != n["2"]


def test_writes_are_keyed_a_rebuild_restores_them_and_they_make_no_record(world, tmp_path):
    dsn = world
    batch = [
        fact("the plan costs 10", "2025-01-01", **{"as": "p1"}),
        {"op": "entity", "name": "Dana", "kind": "person", "as": "dana"},
        {"op": "link", "a": "@dana", "b": "@p1", "kind": "set"},
        {"op": "supersede", "old": "@p1", "claim": "the plan costs 20", "kind": "pricing", "since": "2025-03-01",
         "source": "telegram:-200/7", "reason": "raised", "as": "p2"},
        {"op": "source", "channel": "git", "upto": "abc1234", "at": "2025-04-01"},
        {"op": "source", "channel": "telegram:-200", "ok": False, "at": "2025-04-02"},
    ]  # fmt: skip
    run(dsn, memory.ingest)  # migrate's correction 1
    first = write(dsn, "a", *batch[:2])
    count = rows(dsn)
    # The same batch again, whole: what was written resolves to the same
    # names, and only the rest is appended.
    names = write(dsn, "a", *batch)
    assert {k: names[k] for k in first} == first and rows(dsn) == count + 4
    assert write(dsn, "a", *batch) == names and rows(dsn) == count + 4
    # Fact rows make no record, and the kernel's ingest takes nothing again.
    assert run(dsn, memory.ingest) == {"rows": 0, "records": 0}
    with psycopg.connect(run(dsn, lambda conn: _mdsn(conn))) as m:
        assert m.execute("SELECT count(*) FROM memory.record WHERE origin <> 'correction'").fetchone()[0] == 0
    before = (
        read(dsn, "a", history=names["p2"]),
        read(dsn, "a", related="Dana"),
        read(dsn, "a", sources=True),
    )
    assert before[2]["sources"] == [
        {"channel": "git", "last_tried": "2025-04-01", "last_ok": "2025-04-01", "upto": "abc1234"},
        {"channel": "telegram:-200", "last_tried": "2025-04-02", "last_ok": "never", "upto": ""},
    ]
    # A rebuild: the store dropped and the ledger ingested again.
    with psycopg.connect(run(dsn, lambda conn: _mdsn(conn)), autocommit=True) as m:
        for (table,) in m.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'memory'").fetchall():
            m.execute(f'DROP TABLE IF EXISTS memory."{table}" CASCADE')
    memory.reset()
    assert run(dsn, memory.ingest)["rows"] == rows(dsn) + 1
    after = (
        read(dsn, "a", history=names["p2"]),
        read(dsn, "a", related="Dana"),
        read(dsn, "a", sources=True),
    )
    assert after == before
    assert claims(read(dsn, "a", as_of="2025-02-01")) == ["the plan costs 10"]


async def _mdsn(conn) -> str:
    return memory.dsn_for(conn)


def valor(dsn: str, task: str, *argvs: list[str]) -> list[tuple[int, str]]:
    """Each argv of `valor-record` as a turn of `task` runs it, through a
    live gateway, and what it printed."""

    async def go():
        gateway = Gateway(dsn, upstream="http://127.0.0.1:9")
        await gateway.start(port=listen())
        os.environ["VALOR_RECORD_URL"] = gateway.issue(task, "turn-1") + "/record"
        out = []
        for argv in argvs:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                rc = await asyncio.to_thread(valor_record.main, argv)
            out.append((rc, buf.getvalue()))
        gateway.revoke(task)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rc = await asyncio.to_thread(valor_record.main, ["facts"])
        out.append((rc, buf.getvalue()))
        await gateway.close()
        return out

    return asyncio.run(go())


def started(dsn: str, name: str) -> str:
    client, r = PROJECTS[name]
    brief = tasks.Brief(instruction="work", project={"name": name, "repo": repo(r), "client": client})
    return run(dsn, lambda conn: tasks.start(conn, brief))


def test_a_turn_writes_and_reads_its_projects_facts_and_the_command_line_writes_them_too(
    world, monkeypatch, tmp_path
):
    dsn = world
    monkeypatch.setenv("VALOR_RECORD_URL", "")  # restored after the gateway's
    task = started(dsn, "a")
    run(dsn, memory.ingest)
    out = valor(
        dsn, task,
        ["entity", "add", "Dana", "--kind", "person", "--alias", "D."],
        ["fact", "add", "Dana sets prices", "--kind", "people", "--source", "telegram:-200/5",
         "--since", "2025-01-02", "--about", "d."],
        ["fact", "add", "Dana sets prices", "--kind", "people", "--source", "telegram:-200/5",
         "--since", "2025-01-02", "--about", "d."],
        ["facts", "--as-of", "2025-02-01"],
        ["facts", "--related", "Dana", "--json"],
        ["fact", "supersede", "999999", "x", "--kind", "k", "--source", "s", "--since", "2025-01-01", "--reason", "r"],
        ["source", "git", "--upto", "abc"],
        ["sources"],
    )  # fmt: skip
    (
        (rc, _),
        (_, added),
        (_, again),
        (_, listed),
        (_, related),
        (bad, said),
        _,
        (_, sources),
        (gone, revoked),
    ) = out
    assert rc == 0 and added == again and added.startswith(f"{settings.machine}/{db_name(dsn)}/")
    assert (
        listed.startswith(f"- fact {added.strip()} [people], since 2025-01-02, inferred")
        and "  > Dana sets prices" in listed
    )
    assert json.loads(related)["related"][0]["fact"] == added.strip()
    assert bad == 1 and "no fact '999999'" in said
    assert "git" in sources and "abc" in sources
    # A write on a revoked token is refused as every read is.
    assert gone == 1 and "revoked" in revoked
    # The kernel command line, with a spec file outside the projects folder,
    # writes the same project's facts under the kernel's ledger name.
    copied = tmp_path / "copied.toml"
    copied.write_text((Path(settings.projects_dir) / "a.toml").read_text())
    batch = tmp_path / "batch.jsonl"
    batch.write_text("\n".join(json.dumps(w) for w in [
        fact("the plan costs 10", "2025-01-01", **{"as": "p1"}),
        {"op": "link", "a": "Dana", "b": "@p1", "kind": "set"},
    ]))  # fmt: skip
    env = {
        **os.environ,
        "VALOR_PROJECTS": str(tmp_path / "nowhere"),
        "VALOR_DB": db_name(dsn),
        "VALOR_MACHINE": settings.machine,
    }
    cli = [sys.executable, "-m", "core", "facts", "--project", str(copied)]
    applied = subprocess.run(
        [*cli, "apply", str(batch)], cwd=ROOT, env=env, capture_output=True, text=True, check=False
    )
    assert applied.returncode == 0, applied.stderr
    names = json.loads(applied.stdout)
    assert names["p1"].startswith(f"{settings.machine}/{db_name(dsn)}/")
    again = subprocess.run(
        [*cli, "apply", str(batch)], cwd=ROOT, env=env, capture_output=True, text=True, check=False
    )
    assert json.loads(again.stdout) == names
    shown = subprocess.run(
        [*cli, "read", "--history", names["p1"]],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert "> the plan costs 10" in shown.stdout
    # The kernel's next ingest takes nothing again, and the turn reads them.
    assert run(dsn, memory.ingest)["rows"] == 0
    ((rc, listed), _) = valor(dsn, task, ["facts"])
    assert rc == 0 and "the plan costs 10" in listed
    with pytest.raises(record.NotFound, match="no fact"):
        asyncio.run(record.answer(dsn, started(dsn, "b"), "facts", {"history": names["p1"]}))


def db_name(dsn: str) -> str:
    return psycopg.conninfo.conninfo_to_dict(dsn)["dbname"]
