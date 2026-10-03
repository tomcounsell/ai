"""The four bounds on real Postgres, with no model call."""

import asyncio
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
    broker.register(WorkspaceWrite(tmp_path))
    broker.register(OutboxAppend(tmp_path / "outbox.jsonl"))

    async def go():
        task = await new_task(dsn, max_effect_class="act")
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


def test_ceiling_and_stop_refuse_effects(dsn, tmp_path):
    broker.register(WorkspaceWrite(tmp_path))
    broker.register(OutboxAppend(tmp_path / "outbox.jsonl"))

    async def go():
        low = await new_task(dsn, max_effect_class="propose")
        async with await db.connect(dsn) as conn:
            above = await broker.request(conn, low, broker.Action("outbox_send", "tom", {"text": "x"}))
            await tasks.stop(conn, low, reason="test")
            after_stop = await broker.request(
                conn, low, broker.Action("workspace_write", "b.txt", {"text": "b"})
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
    broker.register(OutboxAppend(tmp_path / "outbox.jsonl"))
    with pytest.raises(TypeError):
        broker.Action("outbox_send", "tom", {"text": "x"}, adds_governance=True)

    async def go():
        task = await new_task(dsn, max_effect_class="act")
        async with await db.connect(dsn) as conn:
            held = await broker.request(conn, task, broker.Action("outbox_send", "tom", {"text": "x"}))
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
            charged = [r for r in await ledger.read(conn, task) if r["type"] == "gateway.charged"]
            return elapsed, await tasks.status(conn, task), charged

    elapsed, state, charged = run(go())
    assert elapsed < 1
    assert not state["open_calls"] and len(charged) == 1
    assert state["spent_usd_micros"] > 0  # sent or not is unknown: charged its worst case


def test_a_turn_that_exits_cuts_its_silent_calls(dsn, tmp_path):
    """A turn leaves a child holding a call to an upstream that never
    answers, then exits: the run still ends, the call is charged its worst
    case, and the run's lock is free."""
    from core import router
    from core.machine import State

    go_on = tmp_path / "go-on"
    child = tmp_path / "child.py"
    child.write_text(
        "import json, os, urllib.request\n"
        "body = json.dumps({'model': 'claude-haiku-4-5', 'max_tokens': 50, 'messages': []}).encode()\n"
        "url = os.environ['ANTHROPIC_BASE_URL'] + '/v1/messages'\n"
        "urllib.request.urlopen(urllib.request.Request(url, data=body))\n"
    )
    turn = (
        "import os, subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, {str(child)!r}], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        f"while not os.path.exists({str(go_on)!r}): time.sleep(0.05)\n"
    )

    async def go():
        seen = asyncio.Event()

        async def on(reader, writer):
            await reader.readuntil(b"\r\n\r\n")
            seen.set()
            while await reader.read(65536):
                pass
            writer.close()

        server = await asyncio.start_server(on, "127.0.0.1", 0)
        task = await new_task(dsn)
        gateway = Gateway(dsn, upstream=f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}")
        await gateway.start()
        build = lambda url, brief, turn_id: runs.TurnCommand(
            argv=[sys.executable, "-c", turn], env={"ANTHROPIC_BASE_URL": url}, cwd=str(tmp_path), harness="t"
        )

        async def one_turn(ctx):
            return {
                "status": "failed",
                "turn": await runs.run_turn(ctx.gateway, ctx.task_id, build, dsn=ctx.dsn),
            }

        running = asyncio.create_task(router.run(gateway, task, {State.JUDGE: one_turn}, dsn=dsn))
        await asyncio.wait_for(seen.wait(), 20)
        go_on.touch()
        out = await asyncio.wait_for(running, 20)
        await gateway.close()
        server.close()
        async with await db.connect(dsn) as conn:
            rows = await ledger.read(conn, task)
            free = await (
                await conn.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (f"run:{task}",))
            ).fetchone()
        return out, rows, free[0], gateway

    out, rows, free, gateway = run(go())
    assert out["status"] == "failed" and out["turn"]["outcome"] == "done"
    opened = [r["payload"] for r in rows if r["type"] == "gateway.opened"]
    charged = [r["payload"] for r in rows if r["type"] == "gateway.charged"]
    assert [r["type"] for r in rows].count("turn.ended") == 1 and free is True
    assert len(opened) == 1 and len(charged) == 1 and charged[0]["cut"] is True
    assert charged[0]["usd_micros"] == opened[0]["estimate_usd_micros"]
    assert not any(gateway.calls.values())


# -- no invented output or file limit ----------------------------------------------------


def test_workspace_turn_sets_no_output_limit_by_default(tmp_path):
    from harnesses import claude_code

    built = claude_code.workspace_turn("hi", cwd=str(tmp_path), harness={"sandbox_profile": "/p.sb"})
    assert "CLAUDE_CODE_MAX_OUTPUT_TOKENS" not in built("http://127.0.0.1:1/t/x", "brief", "t1").env


def test_a_spec_output_limit_reaches_the_turn(tmp_path):
    from harnesses import claude_code

    harness = {"sandbox_profile": "/p.sb", "max_output_tokens": 4096}
    built = claude_code.workspace_turn("hi", cwd=str(tmp_path), harness=harness)
    assert built("http://127.0.0.1:1/t/x", "brief", "t1").env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "4096"


@pytest.mark.parametrize("soft", [256, None])
def test_start_raises_the_open_file_limit_and_never_lowers_it(soft):
    """From a lowered soft limit it rises to the hard limit (or
    `min(OPEN_MAX, hard)` where the hard limit is refused); from the soft
    limit the process already has, it never falls."""
    import json
    import subprocess

    script = (
        "import json, resource\n"
        "from core.__main__ import OPEN_MAX, _raise_open_files\n"
        "soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)\n"
        f"if {soft!r} is not None: resource.setrlimit(resource.RLIMIT_NOFILE, ({soft!r}, hard))\n"
        "before = resource.getrlimit(resource.RLIMIT_NOFILE)[0]\n"
        "_raise_open_files()\n"
        "after, hard = resource.getrlimit(resource.RLIMIT_NOFILE)\n"
        "print(json.dumps([before, after, hard, OPEN_MAX]))\n"
    )
    done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True)
    before, after, hard, open_max = json.loads(done.stdout)
    assert after >= before
    assert after == hard or after == min(open_max, hard) or after == before


def test_a_term_or_hangup_of_the_kernel_interrupts_provisioning(monkeypatch):
    """`_provision` turns SIGTERM and SIGHUP into the interrupt: the thread's
    work sees it, and the start is cancelled."""
    import os
    import signal
    import threading
    import time

    from core import __main__ as kernel
    from core import git

    for sig in (signal.SIGTERM, signal.SIGHUP):
        seen = {}

        entered = threading.Event()

        def provision(task_id, spec, ports, *, base=None, seen=seen, entered=entered):
            held = git.watch()
            entered.set()  # the handlers are in place before the thread starts
            while not held.interrupted:
                time.sleep(0.01)
            seen["interrupted"] = True
            raise git.Interrupted()

        monkeypatch.setattr(kernel.workspace, "provision", provision)

        async def go(sig=sig, entered=entered):
            job = asyncio.create_task(kernel._provision("t", None, {}, None))
            assert await asyncio.to_thread(entered.wait, 20)
            os.kill(os.getpid(), sig)
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(job, 20)

        run(go())
        assert seen == {"interrupted": True}
