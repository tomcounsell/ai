"""The ledger, effects, and stop on real Postgres, with no model call."""

import asyncio
import sys

import aiohttp
import psycopg
import pytest

from core import broker, db, ledger, runs, tasks
from core.gateway import Gateway
from tests import scripted
from tests.performers import OutboxAppend, WorkspaceWrite
from tests.ports import listen

pytestmark = pytest.mark.spend(usd=0)


def run(coro):
    return asyncio.run(coro)


async def new_task(dsn, **kw) -> str:
    async with await db.connect(dsn) as conn:
        return await tasks.start(conn, tasks.Brief(instruction="test", **kw))


# -- the ledger cannot be edited ------------------------------------------------


def test_kernel_role_cannot_edit_the_ledger(dsn):
    task = run(new_task(dsn))
    with psycopg.connect(dsn, autocommit=True) as conn:
        for statement in (
            "UPDATE events SET type = 'x' WHERE task_id = %s",
            "DELETE FROM events WHERE task_id = %s",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(statement, (task,))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("TRUNCATE events")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE documents SET body = '{}' WHERE id = %s", (task,))


# -- spending is metered; only a stop refuses a call ------------------------------


def test_a_stopped_tasks_call_is_refused_with_a_ledger_row_before_any_provider_call(dsn):
    async def go():
        task = await new_task(dsn)
        gateway = Gateway(dsn, upstream="http://127.0.0.1:9")  # never reached
        await gateway.start(port=listen())
        base = gateway.issue(task, "turn-1")
        body = {
            "model": "claude-haiku-4-5",
            "max_tokens": 1000,
            "messages": [{"role": "user", "content": "hi"}],
        }
        async with await db.connect(dsn) as conn:
            await tasks.stop(conn, task, reason="test")
        async with aiohttp.ClientSession() as http, http.post(base + "/v1/messages", json=body) as r:
            refused = (r.status, await r.json())
        await gateway.close()
        async with await db.connect(dsn) as conn:
            return refused, await ledger.read(conn, task), await tasks.status(conn, task)

    (status, error), rows, state = run(go())
    assert status == 400 and "stopped" in error["error"]["message"]
    (row,) = [r for r in rows if r["type"].startswith("gateway.")]
    assert row["type"] == "gateway.refused" and row["payload"]["reason"] == "stopped"
    assert row["payload"]["turn_id"] == "turn-1" and row["payload"]["model"] == "claude-haiku-4-5"
    assert state["spent_usd_micros"] == 0 and not state["open_calls"]


# -- effects: one request, one effect -------------------------------------------


def test_act_is_performed_at_request_and_one_request_id_is_one_effect(dsn, tmp_path):
    perf = broker.Performers(WorkspaceWrite(tmp_path), OutboxAppend(tmp_path / "outbox.jsonl"))

    async def go():
        task = await new_task(dsn, max_effect_class="act")
        async with await db.connect(dsn) as conn:
            wrote = await broker.request(
                conn, perf, task, broker.Action("workspace_write", "a.txt", {"text": "a"})
            )
            held = await broker.request(conn, perf, task, broker.Action("outbox_send", "tom", {"text": "hi"}))
            twin = await broker.request(conn, perf, task, broker.Action("outbox_send", "tom", {"text": "hi"}))
            once = await broker.request(
                conn, perf, task, broker.Action("outbox_send", "tom", {"text": "hi"}), request_id="t1/a.json"
            )
            again = await broker.request(
                conn, perf, task, broker.Action("outbox_send", "tom", {"text": "hi"}), request_id="t1/a.json"
            )
            state = await tasks.status(conn, task)
        return wrote, held, twin, once, again, state

    wrote, held, twin, once, again, state = run(go())
    assert wrote.kind == "done" and (tmp_path / "a.txt").read_text() == "a"
    # Two identical requests are two effects; one request_id is one.
    assert held.kind == "done" and twin.kind == "done" and twin.effect_id != held.effect_id
    assert again.effect_id == once.effect_id and again.kind == "done"
    assert _lines(tmp_path / "outbox.jsonl") == 3
    assert tasks.audit(state) == []


# -- stop is immediate and lossless ---------------------------------------------


def test_stop_from_another_connection_kills_the_turn_and_leaves_a_consistent_ledger(dsn, tmp_path):
    async def go():
        task = await new_task(dsn)
        gateway = Gateway(dsn)
        await gateway.start(port=listen())
        # A real subprocess that would run for a minute, with a child of its own.
        build = lambda url, brief, turn_id: runs.TurnCommand(
            argv=[
                sys.executable,
                "-c",
                "import subprocess,time; subprocess.Popen(['sleep','60']); time.sleep(60)",
            ],
            env={},
            cwd=str(tmp_path),
            harness="sleeper",
        )
        turn = asyncio.create_task(runs.run_turn(gateway, task, build, dsn=dsn))
        await asyncio.sleep(1.0)
        started = asyncio.get_running_loop().time()
        async with await db.connect(dsn) as conn:
            assert await tasks.stop(conn, task, reason="test") is True
            assert await tasks.stop(conn, task, reason="twice") is False
        ended = await turn
        elapsed = asyncio.get_running_loop().time() - started
        await gateway.close()
        async with await db.connect(dsn) as conn:
            state = await tasks.status(conn, task)
            with pytest.raises(tasks.TaskStopped):
                await runs.run_turn(gateway, task, build, dsn=dsn)
        return ended, elapsed, state

    ended, elapsed, state = run(go())
    assert ended["outcome"] == "stopped" and ended["returncode"] == -9
    assert elapsed < 2
    assert state["state"] == "stopped" and tasks.audit(state) == []


def _lines(path) -> int:
    return len(path.read_text().splitlines()) if path.exists() else 0


# -- reconcile from the intent row ----------------------------------------------------------


class StuckWrite(WorkspaceWrite):
    """Writes the file, then never returns: the process dies mid-perform."""

    def __init__(self, root, wrote: asyncio.Event):
        super().__init__(root)
        self.wrote = wrote

    async def perform(self, action, key):
        out = await super().perform(action, key)
        self.wrote.set()
        await asyncio.Event().wait()
        return out


def test_the_intent_row_carries_the_action_and_reconcile_rebuilds_it_from_that_alone(dsn, tmp_path):
    """A propose-class effect has no `effect.held` row; a perform cut off
    after its intent leaves only the intent, and reconcile settles it."""
    action = broker.Action("workspace_write", "note.txt", {"text": "hello"})

    async def go():
        async with await db.connect(dsn) as conn:
            task = await tasks.start(conn, tasks.Brief(instruction="t"))
        wrote = asyncio.Event()
        stuck = broker.Performers(StuckWrite(tmp_path, wrote))
        conn = await db.connect(dsn)
        requesting = asyncio.create_task(broker.request(conn, stuck, task, action))
        await wrote.wait()
        requesting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await requesting
        await conn.close()
        async with await db.connect(dsn) as conn:
            rows = await ledger.read(conn, task)
            (intent,) = [r["payload"] for r in rows if r["type"] == "effect.intent"]
            settled = await broker.reconcile(
                conn, broker.Performers(WorkspaceWrite(tmp_path)), intent["effect_id"]
            )
            return rows, intent, settled, await scripted.task_of(conn, intent["effect_id"]), task

    rows, intent, settled, owner, task = run(go())
    assert not [r for r in rows if r["type"] in ("effect.held", "effect.outcome")]
    assert intent["action_type"] == "workspace_write" and intent["target"] == "note.txt"
    assert intent["payload"] == {"text": "hello"} and intent["effect_class"] == "propose"
    assert intent["payload_sha256"] == ledger.digest({"text": "hello"}) and "approval_id" not in intent
    assert settled.kind == "done" and owner == task


# -- turns run at once up to the machine's limit ---------------------------------


def test_max_turns_holders_run_at_once_and_the_next_waits_for_one(dsn, monkeypatch):
    import dataclasses
    import uuid

    from core import slot

    monkeypatch.setattr(
        slot, "settings", dataclasses.replace(slot.settings, max_turns=2, machine=uuid.uuid4().hex)
    )

    async def go():
        inside: list[str] = []
        release = asyncio.Event()

        async def hold(name: str) -> None:
            async with slot.held(name, dsn):
                inside.append(name)
                await release.wait()

        first = [asyncio.create_task(hold(n)) for n in ("a", "b")]
        while len(inside) < 2:
            await asyncio.sleep(0.05)
        third = asyncio.create_task(hold("c"))
        await asyncio.sleep(3 * slot.POLL_S)
        assert sorted(inside) == ["a", "b"]
        release.set()
        await asyncio.wait_for(asyncio.gather(*first, third), timeout=10)
        assert sorted(inside) == ["a", "b", "c"]

    run(asyncio.wait_for(go(), timeout=15))


# -- the turn's environment ------------------------------------------------------


def test_a_turn_keeps_the_operators_path_and_knows_its_github_repo(tmp_path, monkeypatch):
    from core import workspace

    monkeypatch.setenv("PATH", "/opt/homebrew/bin:/usr/bin")
    env = workspace.environment({"PATH": "/task/bin:/usr/bin", "REDIS_PORT": "6400"})
    assert env["PATH"] == "/task/bin:/usr/bin:/opt/homebrew/bin" and env["REDIS_PORT"] == "6400"
    spec = workspace.Spec(
        name="p", repo="https://github.com/tomcounsell/popoto.git", kind="python-uv", suite="true"
    )
    lay = workspace.Layout(tmp_path)
    found = workspace.harness_env(lay, spec, {}, {}, bin_dir=tmp_path / "bin", passfile=tmp_path / "pass")
    assert found["GH_REPO"] == "tomcounsell/popoto"
