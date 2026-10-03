"""The four bounds on real Postgres, with no model call."""

import asyncio
import json
import sys

import aiohttp
import psycopg
import pytest

from core import broker, db, ledger, runs, spending, tasks
from core.gateway import Gateway
from tests.performers import OutboxAppend, WorkspaceWrite

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


def test_owner_cannot_edit_the_ledger_either(owner_dsn, dsn):
    task = run(new_task(dsn))
    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            conn.execute("UPDATE events SET type = 'x' WHERE task_id = %s", (task,))
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            conn.execute("DELETE FROM events WHERE task_id = %s", (task,))


# -- spending is metered; only a stop refuses a call ------------------------------


def test_a_stopped_tasks_call_is_refused_with_a_ledger_row_before_any_provider_call(dsn):
    async def go():
        task = await new_task(dsn)
        gateway = Gateway(dsn, upstream="http://127.0.0.1:9")  # never reached
        await gateway.start()
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


def test_an_unknown_task_opens_no_call_and_writes_nothing(dsn):
    async def go():
        async with await db.connect(dsn) as conn:
            with pytest.raises(KeyError):
                await spending.open_call(conn, "no-such-task", {"call_id": "no-such-call"})
            row = await (
                await conn.execute("SELECT count(*) FROM events WHERE task_id = 'no-such-task'")
            ).fetchone()
            return row[0]

    assert run(go()) == 0


def test_prices_round_up_and_match_dated_ids():
    price = spending.prices("claude-haiku-4-5-20251001")
    assert price["output"] == 5_000_000
    assert spending.cost({"output_tokens": 1}, price) == 5
    assert spending.cost({"input_tokens": 1}, price) == 1  # 1 micro-dollar, rounded up
    assert spending.prices("some-unpriced-model") is None


# -- effects: classes, ceiling, and Tom's tap ------------------------------------


def test_act_is_held_until_tom_approves_and_the_approval_is_used_once(dsn, tmp_path):
    perf = broker.Performers(WorkspaceWrite(tmp_path), OutboxAppend(tmp_path / "outbox.jsonl"))

    async def go():
        task = await new_task(dsn, max_effect_class="act")
        async with await db.connect(dsn) as conn:
            wrote = await broker.request(
                conn, perf, task, broker.Action("workspace_write", "a.txt", {"text": "a"})
            )
            held = await broker.request(conn, perf, task, broker.Action("outbox_send", "tom", {"text": "hi"}))
            again = await broker.request(
                conn, perf, task, broker.Action("outbox_send", "tom", {"text": "hi"})
            )
            with pytest.raises(broker.NotApproved):
                await broker.release(conn, perf, held.effect_id)
            lines_before = _lines(tmp_path / "outbox.jsonl")
            await broker.approve(conn, held.effect_id, note="send it")
            sent = await broker.release(conn, perf, held.effect_id)
            repeat = await broker.release(conn, perf, held.effect_id)
            state = await tasks.status(conn, task)
        return wrote, held, again, lines_before, sent, repeat, state

    wrote, held, again, lines_before, sent, repeat, state = run(go())
    assert wrote.kind == "done" and (tmp_path / "a.txt").read_text() == "a"
    assert held.kind == "pending" and again.effect_id == held.effect_id
    assert lines_before == 0
    assert sent.kind == "done" and repeat.kind == "done"
    assert _lines(tmp_path / "outbox.jsonl") == 1
    assert tasks.audit(state) == []


def test_ceiling_and_stop_refuse_effects(dsn, tmp_path):
    perf = broker.Performers(WorkspaceWrite(tmp_path), OutboxAppend(tmp_path / "outbox.jsonl"))

    async def go():
        low = await new_task(dsn, max_effect_class="propose")
        async with await db.connect(dsn) as conn:
            above = await broker.request(conn, perf, low, broker.Action("outbox_send", "tom", {"text": "x"}))
            await tasks.stop(conn, low, reason="test")
            after_stop = await broker.request(
                conn, perf, low, broker.Action("workspace_write", "b.txt", {"text": "b"})
            )
        return above, after_stop

    above, after_stop = run(go())
    assert above.kind == "refused" and "ceiling" in above.error
    assert after_stop.kind == "refused" and after_stop.error == "task stopped"
    assert not (tmp_path / "b.txt").exists()


def test_the_requester_cannot_say_whether_an_action_adds_governance(dsn, tmp_path):
    """The flag is the broker's to compute from the review and docs verdicts
    (tests/test_pipeline.py has the merge cases); no requester can set it,
    and what the broker computed is what the ledger records."""
    perf = broker.Performers(OutboxAppend(tmp_path / "outbox.jsonl"))
    with pytest.raises(TypeError):
        broker.Action("outbox_send", "tom", {"text": "x"}, adds_governance=True)

    async def go():
        task = await new_task(dsn, max_effect_class="act")
        async with await db.connect(dsn) as conn:
            held = await broker.request(conn, perf, task, broker.Action("outbox_send", "tom", {"text": "x"}))
            rows = await ledger.read(conn, task)
        return held, rows

    held, rows = run(go())
    assert held.kind == "pending"
    assert next(r for r in rows if r["type"] == "effect.held")["payload"]["adds_governance"] is False


# -- stop is immediate and lossless ---------------------------------------------


def test_stop_from_another_connection_kills_the_turn_and_leaves_a_consistent_ledger(dsn, tmp_path):
    async def go():
        task = await new_task(dsn)
        gateway = Gateway(dsn)
        await gateway.start()
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


def test_revoke_cuts_a_call_still_waiting_on_the_provider_and_still_charges_it(dsn):
    async def go():
        task = await new_task(dsn)
        gateway = Gateway(dsn, upstream="http://10.255.255.1")  # a route that never answers
        await gateway.start()
        base = gateway.issue(task, "turn-1")
        body = {
            "model": "claude-haiku-4-5",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "hi"}],
        }

        async def client():
            async with aiohttp.ClientSession() as http:
                try:
                    async with http.post(base + "/v1/messages", json=body) as r:
                        await r.read()
                except aiohttp.ClientError:
                    pass

        pending = asyncio.create_task(client())
        async with await db.connect(dsn) as conn:
            while (await tasks.status(conn, task))["open_calls"] == {}:
                await asyncio.sleep(0.05)
        started = asyncio.get_running_loop().time()
        gateway.revoke(task)
        await gateway.drain(task)
        elapsed = asyncio.get_running_loop().time() - started
        await pending
        await gateway.close()
        async with await db.connect(dsn) as conn:
            return elapsed, await tasks.status(conn, task)

    elapsed, state = run(go())
    assert elapsed < 1
    assert not state["open_calls"]
    assert state["spent_usd_micros"] > 0  # sent or not is unknown: charged its worst case


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
            return rows, intent, settled, await broker.held_task(conn, intent["effect_id"]), task

    rows, intent, settled, owner, task = run(go())
    assert not [r for r in rows if r["type"] in ("effect.held", "effect.outcome")]
    assert intent["action_type"] == "workspace_write" and intent["target"] == "note.txt"
    assert intent["payload"] == {"text": "hello"} and intent["effect_class"] == "propose"
    assert intent["payload_sha256"] == ledger.digest({"text": "hello"}) and intent["approval_id"] is None
    assert settled.kind == "done" and owner == task


def test_an_intent_without_the_action_reads_it_from_the_held_row_and_without_one_concludes_nothing(
    dsn, tmp_path
):
    outbox = tmp_path / "outbox.jsonl"
    action = broker.Action("outbox_send", "tom", {"text": "hi"})
    described = action.describe("act", False)
    outbox.write_text(json.dumps({"key": described["idempotency_key"], "to": "tom", "text": "hi"}) + "\n")
    perf = broker.Performers(OutboxAppend(outbox), WorkspaceWrite(tmp_path))

    async def go():
        async with await db.connect(dsn) as conn:
            task = await tasks.start(conn, tasks.Brief(instruction="t", max_effect_class="act"))
            held, bare = ledger.new_id(), ledger.new_id()
            await ledger.append(conn, task, "effect.held", {"effect_id": held, **described})
            for effect_id in (held, bare):
                await ledger.append(conn, task, "effect.intent", {
                    "effect_id": effect_id, "idempotency_key": described["idempotency_key"], "approval_id": None,
                })  # fmt: skip
            return (
                await broker.reconcile(conn, perf, held),
                await broker.reconcile(conn, perf, bare),
                await broker.reconcile(conn, perf, ledger.new_id()),
            )

    from_held, without, unknown = run(go())
    assert from_held.kind == "done" and from_held.result["key"] == described["idempotency_key"]
    assert without is None and unknown is None
