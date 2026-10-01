"""The four bounds on real Postgres, with no model call."""

import asyncio
import sys

import aiohttp
import psycopg
import pytest

from core import broker, budget, db, runs, tasks
from core.gateway import Gateway
from tools.workspace import OutboxAppend, WorkspaceWrite

pytestmark = pytest.mark.spend(usd=0)


def run(coro):
    return asyncio.run(coro)


async def new_task(dsn, **kw) -> str:
    async with await db.connect(dsn) as conn:
        return await tasks.start(conn, tasks.Brief(instruction="test", **kw))


# -- the ledger cannot be edited ------------------------------------------------


def test_kernel_role_cannot_edit_the_ledger(dsn):
    task = run(new_task(dsn, budget_usd_micros=1))
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
    task = run(new_task(dsn, budget_usd_micros=1))
    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            conn.execute("UPDATE events SET type = 'x' WHERE task_id = %s", (task,))
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            conn.execute("DELETE FROM events WHERE task_id = %s", (task,))


# -- money is conserved ---------------------------------------------------------


def test_racing_reservations_never_exceed_the_budget(dsn):
    async def go():
        task = await new_task(dsn, budget_usd_micros=10_000)

        async def one(i):
            async with await db.connect(dsn) as conn:
                try:
                    await budget.reserve(
                        conn, task, {"call_id": f"{task}-{i}", "usd_micros": 700, "model": "m"}
                    )
                    return True
                except budget.BudgetRefused:
                    return False

        granted = await asyncio.gather(*(one(i) for i in range(40)))
        async with await db.connect(dsn) as conn:
            state = await tasks.status(conn, task)
        return sum(granted), state

    granted, state = run(go())
    assert granted == 14  # 14 * 700 = 9,800; a 15th would pass 10,000
    assert state["remaining_usd_micros"] == 10_000 - 14 * 700


def test_gateway_refuses_over_budget_before_any_provider_call(dsn):
    async def go():
        task = await new_task(dsn, budget_usd_micros=10)
        gateway = Gateway(dsn, upstream="http://127.0.0.1:9")  # never reached
        await gateway.start()
        base = gateway.issue(task, "turn-1")
        body = {
            "model": "claude-haiku-4-5",
            "max_tokens": 1000,
            "messages": [{"role": "user", "content": "hi"}],
        }
        async with aiohttp.ClientSession() as http, http.post(base + "/v1/messages", json=body) as r:
            refused = (r.status, await r.json())
        async with await db.connect(dsn) as conn:
            await tasks.stop(conn, task, reason="test")
        async with aiohttp.ClientSession() as http, http.post(base + "/v1/messages", json=body) as r:
            after_stop = r.status
        await gateway.close()
        async with await db.connect(dsn) as conn:
            return refused, after_stop, await tasks.status(conn, task)

    (status, error), after_stop, state = run(go())
    assert status == 400 and "budget refused" in error["error"]["message"]
    assert after_stop == 400  # the stop fence, read from the ledger
    assert state["charged_usd_micros"] == 0 and not state["open_reservations"]


def test_prices_round_up_and_match_dated_ids():
    price = budget.prices("claude-haiku-4-5-20251001")
    assert price["output"] == 5_000_000
    assert budget.cost({"output_tokens": 1}, price) == 5
    assert budget.cost({"input_tokens": 1}, price) == 1  # 1 micro-dollar, rounded up
    assert budget.prices("some-unpriced-model") is None


# -- effects: classes, ceiling, and Tom's tap ------------------------------------


def test_act_is_held_until_tom_approves_and_the_approval_is_used_once(dsn, tmp_path):
    broker.register(WorkspaceWrite(tmp_path))
    broker.register(OutboxAppend(tmp_path / "outbox.jsonl"))

    async def go():
        task = await new_task(dsn, budget_usd_micros=0, max_effect_class="act")
        async with await db.connect(dsn) as conn:
            wrote = await broker.request(conn, task, broker.Action("workspace_write", "a.txt", {"text": "a"}))
            held = await broker.request(conn, task, broker.Action("outbox_send", "tom", {"text": "hi"}))
            again = await broker.request(conn, task, broker.Action("outbox_send", "tom", {"text": "hi"}))
            with pytest.raises(broker.NotApproved):
                await broker.release(conn, held.effect_id)
            lines_before = _lines(tmp_path / "outbox.jsonl")
            await broker.approve(conn, held.effect_id, note="send it")
            sent = await broker.release(conn, held.effect_id)
            repeat = await broker.release(conn, held.effect_id)
            state = await tasks.status(conn, task)
        return wrote, held, again, lines_before, sent, repeat, state

    wrote, held, again, lines_before, sent, repeat, state = run(go())
    assert wrote.kind == "done" and (tmp_path / "a.txt").read_text() == "a"
    assert held.kind == "pending" and again.effect_id == held.effect_id
    assert lines_before == 0
    assert sent.kind == "done" and repeat.kind == "done"
    assert _lines(tmp_path / "outbox.jsonl") == 1
    assert tasks.audit(state) == []


def test_ceiling_governance_and_stop_refuse_effects(dsn, tmp_path):
    broker.register(WorkspaceWrite(tmp_path))
    broker.register(OutboxAppend(tmp_path / "outbox.jsonl"))

    async def go():
        low = await new_task(dsn, budget_usd_micros=0, max_effect_class="propose")
        granted = await new_task(
            dsn,
            budget_usd_micros=0,
            max_effect_class="act",
            governance_grant="tom: one gate for the merge path",
        )
        async with await db.connect(dsn) as conn:
            above = await broker.request(conn, low, broker.Action("outbox_send", "tom", {"text": "x"}))
            gate = broker.Action("workspace_write", "gate.py", {"text": "#"}, adds_governance=True)
            ungranted = await broker.request(conn, low, gate)
            with_grant = await broker.request(conn, granted, gate)
            await tasks.stop(conn, low, reason="test")
            after_stop = await broker.request(
                conn, low, broker.Action("workspace_write", "b.txt", {"text": "b"})
            )
        return above, ungranted, with_grant, after_stop

    above, ungranted, with_grant, after_stop = run(go())
    assert above.kind == "refused" and "ceiling" in above.error
    assert ungranted.kind == "refused"  # the ceiling or the missing grant, either refuses
    assert with_grant.kind == "pending"  # governance is act: it waits for Tom
    assert after_stop.kind == "refused" and after_stop.error == "task stopped"
    assert not (tmp_path / "gate.py").exists()


def test_governance_with_no_grant_is_refused_even_under_an_act_ceiling(dsn, tmp_path):
    broker.register(WorkspaceWrite(tmp_path))

    async def go():
        task = await new_task(dsn, budget_usd_micros=0, max_effect_class="act")
        async with await db.connect(dsn) as conn:
            return await broker.request(
                conn, task, broker.Action("workspace_write", "g.py", {"text": "#"}, adds_governance=True)
            )

    outcome = run(go())
    assert outcome.kind == "refused" and "governance_grant" in outcome.error


# -- stop is immediate and lossless ---------------------------------------------


def test_stop_from_another_connection_kills_the_turn_and_leaves_a_consistent_ledger(dsn, tmp_path):
    async def go():
        task = await new_task(dsn, budget_usd_micros=0)
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
        task = await new_task(dsn, budget_usd_micros=100_000)
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
            while (await tasks.status(conn, task))["open_reservations"] == {}:
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
    assert not state["open_reservations"]
    assert state["charged_usd_micros"] > 0  # sent or not is unknown: charged its worst case
