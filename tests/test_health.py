"""A Mac's database heals itself (docs/plans/a4601299-db-self-recovery.md):
what a restart cures, how one is done and spaced, the kernel restarting a
failing database and carrying on without killing live work, a Python move
draining, the bridges leaving for launchd on a lost database, and the
`watch` routine."""

import asyncio
import dataclasses
import json
import os
import plistlib
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

import core.bridge
import core.gateway
from core import db, health, ledger, machine, rollout, router, runs, serve, tasks
from core.machine import State
from core.settings import settings
from tests.bridges import FakeBridge, new_task, rows, typed

pytestmark = pytest.mark.spend(usd=0)

run = asyncio.run

# The error every new backend raised on 2026-11-13, word for word but the paths.
PLPGSQL = (
    'could not load library "/opt/homebrew/lib/postgresql@18/plpgsql.dylib": '
    "dlopen(/opt/homebrew/lib/postgresql@18/plpgsql.dylib, 0x000A): Symbol not found: _DatumGetHeapTupleHeader"
)


# -- what a restart cures -------------------------------------------------------------


def test_a_library_that_will_not_load_or_a_lost_connection_is_curable_and_starting_up_is_not():
    assert health.curable(psycopg.errors.UndefinedFile(PLPGSQL))
    assert health.curable(psycopg.errors.InternalError_(PLPGSQL))
    assert health.curable(psycopg.OperationalError('connection to server on socket "/tmp/.s.PGSQL.5432" failed: '
                                                   "Connection refused"))  # fmt: skip
    assert health.curable(psycopg.OperationalError("consuming input failed: server closed the connection"))
    assert health.curable(
        psycopg.errors.AdminShutdown("terminating connection due to unexpected postmaster exit")
    )
    assert not health.curable(psycopg.errors.CannotConnectNow("the database system is starting up"))
    assert not health.curable(psycopg.OperationalError("FATAL:  the database system is starting up"))
    assert not health.curable(psycopg.errors.InternalError_("some other internal error"))
    assert not health.curable(psycopg.errors.UniqueViolation("duplicate key"))
    assert not health.curable(psycopg.errors.SyntaxError("syntax error"))
    assert not health.curable(RuntimeError(PLPGSQL))


# -- how a restart is done and spaced ---------------------------------------------------


@pytest.fixture
def cluster(tmp_path, monkeypatch):
    """A data directory, its launchd job, and every command recorded: `fails`
    names the commands that exit 1."""
    data = tmp_path / "var" / "postgresql@18"
    data.mkdir(parents=True)
    (data / "postmaster.pid").write_text("123\n")
    agents = tmp_path / "LaunchAgents"
    agents.mkdir()
    (agents / "sh.brew.postgresql@18.plist").write_bytes(plistlib.dumps({
        "Label": "sh.brew.postgresql@18", "KeepAlive": {"Crashed": True},
        "ProgramArguments": ["/opt/homebrew/opt/postgresql@18/bin/postgres", "-D", str(data)],
    }))  # fmt: skip
    (agents / "other.plist").write_bytes(
        plistlib.dumps({"Label": "other", "ProgramArguments": ["x", "-D", "/y"]})
    )
    calls, fails = [], set()
    lock = threading.Lock()

    def fake(argv, **kw):
        with lock:
            calls.append(argv)
        time.sleep(0.05)
        word = argv[-1] if "pg_ctl" in argv[0] else argv[1]
        mode = argv[argv.index("-m") + 1] if "-m" in argv else None
        failed = word in fails or mode in fails
        return subprocess.CompletedProcess(argv, 1 if failed else 0, "", "refused" if failed else "")

    monkeypatch.setattr(health.subprocess, "run", fake)
    monkeypatch.setattr(health.binaries, "require", str)
    return SimpleNamespace(data=data, agents=agents, calls=calls, fails=fails, bin="/pg/bin")


def restart(c) -> str:
    return health.restart_postgres(str(c.data), c.bin, c.agents)


def words(c) -> list[str]:
    out = []
    for argv in c.calls:
        out.append(f"pg_ctl {argv[argv.index('-m') + 1]}" if "pg_ctl" in argv[0] else f"launchctl {argv[1]}")
    return out


def test_a_restart_fast_stops_the_cluster_then_starts_its_launchd_job(cluster):
    assert restart(cluster) == "restarted"
    assert words(cluster) == ["pg_ctl fast", "launchctl print", "launchctl kickstart"]
    assert cluster.calls[0][:4] == ["/pg/bin/pg_ctl", "-D", str(cluster.data), "-m"]
    assert cluster.calls[-1][-1].endswith("/sh.brew.postgresql@18")


def test_a_fast_stop_that_fails_is_made_immediate_and_an_unloaded_job_is_bootstrapped(cluster):
    cluster.fails |= {"fast", "print"}
    assert restart(cluster) == "restarted"
    assert words(cluster) == ["pg_ctl fast", "pg_ctl immediate", "launchctl print", "launchctl bootstrap"]
    assert cluster.calls[-1][-1] == str(cluster.agents / "sh.brew.postgresql@18.plist")


def test_a_failed_start_says_why_and_a_cluster_with_no_job_is_not_touched(cluster, tmp_path):
    cluster.fails.add("kickstart")
    assert restart(cluster).startswith("launchctl exited 1")
    cluster.calls.clear()
    assert health.restart_postgres(str(tmp_path / "elsewhere"), cluster.bin, cluster.agents) == "no job"
    assert cluster.calls == []


def test_restarts_are_spaced_ten_minutes_even_while_the_server_is_down(cluster):
    assert restart(cluster) == "restarted"
    (cluster.data / "postmaster.pid").unlink()  # it never came back up
    cluster.calls.clear()
    assert restart(cluster) == "spaced" and cluster.calls == []
    stamp = cluster.data.with_name("postgresql@18.valor-restart")
    old = time.time() - health.SPACING_S - 1
    os.utime(stamp, (old, old))
    assert restart(cluster) == "restarted"
    assert words(cluster) == ["launchctl print", "launchctl kickstart"]  # nothing to stop


def test_two_restarters_at_once_restart_the_server_once(cluster):
    done = []
    threads = [threading.Thread(target=lambda: done.append(restart(cluster))) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(done) == ["restarted", "spaced"]
    assert words(cluster).count("pg_ctl fast") == 1


def test_health_fix_starts_only_the_services_with_no_pid(monkeypatch, tmp_path):
    kicked, restarted = [], []
    gone = psycopg.errors.UndefinedFile(PLPGSQL)

    async def probe(dsn=None):
        return gone

    monkeypatch.setattr(health, "probe", probe)
    monkeypatch.setattr(
        health, "services", lambda agents: {"com.valor.kernel": 10, "com.valor.kernel.slack": None}
    )
    monkeypatch.setattr(health, "kick", lambda label: kicked.append(label))
    monkeypatch.setattr(health, "restart_postgres", lambda *a: restarted.append(a) or "restarted")
    out = run(health.report(fix=True, agents=tmp_path))
    assert kicked == ["com.valor.kernel.slack"] and len(restarted) == 1
    assert out["fixed"] == {"postgres": "restarted", "com.valor.kernel.slack": "started"}
    assert out["healthy"] is False and out["curable"] and out["probe"].startswith("could not load library")
    kicked.clear()
    assert run(health.report(agents=tmp_path)).get("fixed") is None and kicked == []


def test_the_services_are_the_loaded_valor_jobs_kept_alive(monkeypatch, tmp_path):
    for label, keep in (
        ("com.valor.kernel", True),
        ("com.valor.voice", True),
        ("com.valor.routine.update", False),
    ):
        (tmp_path / f"{label}.plist").write_bytes(plistlib.dumps({"Label": label, "KeepAlive": keep}))
    listed = (
        "PID\tStatus\tLabel\n10\t0\tcom.valor.kernel\n-\t1\tcom.valor.voice\n-\t0\tcom.valor.routine.update\n"
    )
    monkeypatch.setattr(health, "_launchctl", lambda *a: subprocess.CompletedProcess(a, 0, listed, ""))
    assert health.services(tmp_path) == {"com.valor.kernel": 10, "com.valor.voice": None}


# -- the kernel restarts a failing database and carries on ----------------------------------


class Stand:
    def __init__(self, *args, **kw):
        pass

    async def start(self):
        pass

    async def close(self):
        pass


@pytest.fixture
def kernel_alone(monkeypatch):
    """`serve` with no gateway and a fast clock."""
    for where, name in ((serve, "Gateway"), (core.gateway, "ClaudeLogin"), (core.gateway, "OpenAIKey")):
        monkeypatch.setattr(where, name, Stand)
    monkeypatch.setattr(serve, "settings", dataclasses.replace(serve.settings, serve_tick_s=0.05))
    monkeypatch.setattr(health, "DOWN_S", 0.3)


def owner(dsn: str) -> str:
    return settings.dsn(owner=True, database=conninfo_to_dict(dsn)["dbname"])


def terminate_all(dsn: str) -> None:
    """What a restart of the server does to every session of this database."""
    with psycopg.connect(owner(dsn), autocommit=True) as c:
        c.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                  "WHERE datname = current_database() AND pid <> pg_backend_pid()")  # fmt: skip


@pytest.fixture
def plpgsql_broken(fresh, monkeypatch):
    """The 2026-11-13 failure on this database: every ledger write, and the
    probe, fail with the error the upgrade caused, until the stand-in for
    `restart_postgres` mends it and ends every session as a restart does."""
    with psycopg.connect(owner(fresh), autocommit=True) as c:
        c.execute("CREATE TABLE IF NOT EXISTS broken (on_ boolean)")
        c.execute("DELETE FROM broken")
        c.execute("INSERT INTO broken VALUES (false)")
        c.execute(f"GRANT SELECT ON broken TO {settings.kernel_role}")
        c.execute(
            "CREATE OR REPLACE FUNCTION broken_check() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
            f"IF (SELECT on_ FROM broken) THEN RAISE EXCEPTION USING ERRCODE = '58P01', MESSAGE = '{PLPGSQL}'; "
            "END IF; RETURN NEW; END $$"
        )
        c.execute("CREATE OR REPLACE FUNCTION broken_probe() RETURNS void LANGUAGE plpgsql AS $$ BEGIN "
                  f"IF (SELECT on_ FROM broken) THEN RAISE EXCEPTION USING ERRCODE = '58P01', MESSAGE = '{PLPGSQL}'; "
                  "END IF; END $$")  # fmt: skip
        c.execute("DROP TRIGGER IF EXISTS broken_writes ON events")
        c.execute(
            "CREATE TRIGGER broken_writes BEFORE INSERT ON events FOR EACH ROW EXECUTE FUNCTION broken_check()"
        )
    monkeypatch.setattr(health, "PROBE", "SELECT broken_probe()")
    restarts = []

    def restart_postgres(*a):
        restarts.append(time.monotonic())
        with psycopg.connect(owner(fresh), autocommit=True) as c:
            c.execute("UPDATE broken SET on_ = false")
        terminate_all(fresh)
        return "restarted"

    monkeypatch.setattr(health, "restart_postgres", restart_postgres)
    yield SimpleNamespace(dsn=fresh, restarts=restarts)
    with psycopg.connect(owner(fresh), autocommit=True) as c:
        c.execute("DROP TRIGGER IF EXISTS broken_writes ON events")


async def until(check, timeout: float = 30):
    end = time.monotonic() + timeout
    while not (got := await check()):
        if time.monotonic() > end:
            raise AssertionError(f"timed out waiting on {check.__name__}")
        await asyncio.sleep(0.05)
    return got


def test_a_ledger_write_failing_like_the_upgrade_restarts_the_database_once_and_the_kernel_carries_on(
    plpgsql_broken, kernel_alone, monkeypatch
):
    dsn = plpgsql_broken.dsn
    task = run(new_task(dsn))
    with psycopg.connect(owner(dsn), autocommit=True) as c:
        c.execute("UPDATE broken SET on_ = true")
    ticks, after = [], []
    real = serve.Kernel.tick

    async def tick(self, conn):
        ticks.append(time.monotonic())
        await real(self, conn)
        if plpgsql_broken.restarts:
            # The work continues: a write after the restart lands.
            after.append(await ledger.append(conn, task, "note.after", {}))
            if len(after) >= 3:
                raise rollout.Restart("t" * 40)

    monkeypatch.setattr(serve.Kernel, "tick", tick)

    async def go():
        started = time.monotonic()
        # The work in flight fails as it did: a write raises the upgrade's error.
        async with await db.connect(dsn) as conn:
            with pytest.raises(psycopg.errors.UndefinedFile):
                await ledger.append(conn, task, "note.before", {})
        with pytest.raises(rollout.Restart):
            await serve.serve({}, dsn=dsn, checkout=None, kick=None)
        return started

    started = run(go())
    assert len(plpgsql_broken.restarts) == 1
    assert plpgsql_broken.restarts[0] - started >= health.DOWN_S
    (restarted,) = typed(run(rows(dsn, serve.KERNEL_STREAM)), "db.restarted")
    assert restarted["cause"] == PLPGSQL and restarted["restarts"] == ["restarted"]
    assert len(typed(run(rows(dsn, task)), "note.after")) == 3


def test_a_quiet_mac_whose_old_connection_still_works_is_restarted_by_the_probe(
    plpgsql_broken, kernel_alone, monkeypatch
):
    """Nothing writes: only the wake's new connection sees the failure."""
    with psycopg.connect(owner(plpgsql_broken.dsn), autocommit=True) as c:
        c.execute("UPDATE broken SET on_ = true")
    ticks = []

    async def tick(self, conn):
        await conn.execute("SELECT 1")  # the kernel's own connection answers throughout
        ticks.append(len(plpgsql_broken.restarts))
        if plpgsql_broken.restarts and len(ticks) > ticks.index(1) + 2:
            raise rollout.Restart("t" * 40)

    monkeypatch.setattr(serve.Kernel, "tick", tick)
    with pytest.raises(rollout.Restart):
        run(serve.serve({}, dsn=plpgsql_broken.dsn, checkout=None, kick=None))
    assert len(plpgsql_broken.restarts) == 1


def test_a_healthy_database_and_one_starting_up_are_never_restarted(fresh, kernel_alone, monkeypatch):
    restarts, ticks = [], []
    monkeypatch.setattr(health, "restart_postgres", lambda *a: restarts.append(a) or "restarted")

    async def tick(self, conn):
        ticks.append(1)
        if len(ticks) > 12:
            raise rollout.Restart("t" * 40)

    monkeypatch.setattr(serve.Kernel, "tick", tick)
    with pytest.raises(rollout.Restart):
        run(serve.serve({}, dsn=fresh, checkout=None, kick=None))

    async def starting(dsn=None):
        return psycopg.errors.CannotConnectNow("the database system is starting up")

    monkeypatch.setattr(health, "probe", starting)
    ticks.clear()
    with pytest.raises(rollout.Restart):
        run(serve.serve({}, dsn=fresh, checkout=None, kick=None))
    assert restarts == []


def test_a_kernel_started_on_a_refusing_database_restarts_it_and_comes_up(fresh, kernel_alone, monkeypatch):
    refusing = [True]
    restarts = []
    real = db.connect

    async def connect(dsn=None, **kw):
        if refusing[0]:
            raise psycopg.OperationalError('connection to server on socket "/tmp/.s.PGSQL.5432" failed: '
                                           "Connection refused")  # fmt: skip
        return await real(dsn, **kw)

    def restart_postgres(*a):
        restarts.append(1)
        refusing[0] = False
        return "restarted"

    monkeypatch.setattr(db, "connect", connect)
    monkeypatch.setattr(health, "restart_postgres", restart_postgres)

    async def tick(self, conn):
        raise rollout.Restart("t" * 40)

    monkeypatch.setattr(serve.Kernel, "tick", tick)
    with pytest.raises(rollout.Restart):
        run(serve.serve({}, dsn=fresh, checkout=None, kick=None))
    assert restarts == [1]
    (restarted,) = typed(run(rows(fresh, serve.KERNEL_STREAM)), "db.restarted")
    assert "Connection refused" in restarted["cause"]


# -- live work survives the restart -----------------------------------------------------------


def test_a_running_step_takes_its_run_lock_again_after_the_server_dropped_every_session(fresh):
    seen = {}

    async def judge(ctx):
        terminate_all(ctx.dsn)  # the restart, mid-step
        seen["alive"] = await ctx.alive()
        async with await db.connect(ctx.dsn) as other:
            got = await (await other.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0))",
                                             (f"run:{ctx.task_id}",))).fetchone()  # fmt: skip
        seen["free to another"] = got[0]
        return {"status": "idle"}

    async def go():
        task = await new_task(fresh)
        gateway = core.gateway.Gateway(fresh)
        return await router.step(gateway, task, {State.JUDGE: judge}, fresh)

    assert run(go())["status"] == "idle"
    assert seen == {"alive": True, "free to another": False}


def test_after_a_restart_a_dead_turn_ends_interrupted_and_a_live_one_is_left_alone(fresh, tmp_path):
    async def go():
        tasks_ = [await new_task(fresh) for _ in range(3)]
        async with await db.connect(fresh) as conn:
            for t, turn in zip(tasks_, ("dead", "elsewhere", "kernels"), strict=True):
                await ledger.append(
                    conn, t, "turn.started", {"turn_id": f"turn-{turn}-{t}", "state": "build"}
                )
                await ledger.append(conn, t, "gateway.opened", {"call_id": f"call-{t}", "holder": f"run:{t}",
                                                                "estimate_usd_micros": 5})  # fmt: skip
        # A turn of another process (`python -m core run`), still running.
        alive = subprocess.Popen(  # noqa: ASYNC220  started and killed at once
            [sys.executable, "-c", "import time; time.sleep(60)"],
            env={**os.environ, runs.TURN_ENV: f"turn-elsewhere-{tasks_[1]}"},
        )
        try:
            await asyncio.sleep(0.3)
            async with await db.connect(fresh) as conn:
                done = await serve.recover(conn, live={tasks_[2]})
            assert alive.poll() is None
        finally:
            alive.kill()
        return tasks_, done

    tasks_, done = run(go())
    assert done["interrupted"] == [f"turn-dead-{tasks_[0]}"] and done["charged"] == [] and done["swept"] == []
    (ended,) = typed(run(rows(fresh, tasks_[0])), "turn.ended")
    assert ended["outcome"] == "interrupted" and ended["reason"] == "database restarted"
    for t in tasks_[1:]:
        written = run(rows(fresh, t))
        assert typed(written, "turn.ended") == [] and typed(written, "gateway.charged") == []
    assert machine.fold(run(rows(fresh, tasks_[0]))).state is not None


def test_a_services_handle_takes_its_lock_again(fresh):
    async def go():
        task = await new_task(fresh)
        handle = router._Services(task, fresh, [], {}, None)
        assert await handle.claim()
        terminate_all(fresh)
        assert await handle.claim()
        async with await db.connect(fresh) as other:
            got = await (await other.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0))",
                                             (f"services:{task}",))).fetchone()  # fmt: skip
        await handle.close()
        return got[0]

    assert run(go()) is False


def test_a_bridge_whose_database_went_away_leaves_for_launchd(fresh):
    bridge = FakeBridge("local")

    async def forever(outbox):
        async for _ in outbox:
            pass

    bridge.run = forever

    async def go():
        async def cut():
            await asyncio.sleep(0.5)
            terminate_all(fresh)

        cutting = asyncio.create_task(cut())
        try:
            from tests.bridges import configure

            # Either way the process exits nonzero, and launchd starts it again.
            with configure(serve_tick_s=0.05), pytest.raises((SystemExit, psycopg.OperationalError)):
                await core.bridge.serve(bridge, fresh)
        finally:
            await cutting

    run(go())


# -- a Python move drains -----------------------------------------------------------------


def test_a_python_move_waits_for_running_work_then_restarts_and_the_next_start_restarts_the_services(
    fresh, monkeypatch
):
    kicked = []
    monkeypatch.setattr(health, "services", lambda: {serve.LABEL: 1, "com.valor.kernel.slack": 2,
                                                     "com.valor.voice": None})  # fmt: skip

    async def go():
        kernel = serve.Kernel(Stand(), {}, None, fresh)
        kernel.python = "/opt/homebrew/Cellar/python@3.14/3.14.7/bin/python3.14"
        release = asyncio.Event()
        kernel.jobs["t1"] = asyncio.create_task(release.wait())
        async with await db.connect(fresh) as conn:
            await kernel.drain(conn)  # a foreground job runs: held
            assert kernel.python_moved
            await kernel.schedule(conn)
            assert set(kernel.jobs) == {"t1"}
            release.set()
            await kernel.jobs.pop("t1")
            with pytest.raises(rollout.Restart):
                await kernel.drain(conn)
            first = await serve.kick_services(conn, lambda label: kicked.append(label))
            again = await serve.kick_services(conn, lambda label: kicked.append(label))
        return first, again

    first, again = run(go())
    assert first == ["com.valor.kernel.slack", "com.valor.voice"] and again == []
    assert kicked == ["com.valor.kernel.slack", "com.valor.voice"]
    (restarting,) = typed(run(rows(fresh, serve.KERNEL_STREAM)), "kernel.restarting")
    assert restarting["reason"] == "python"


# -- the watch ----------------------------------------------------------------------------


class Peers:
    """Stand-in ssh: each host's health in turn; None is unreachable."""

    def __init__(self, **states):
        self.states = states
        self.commands: list[tuple[str, str]] = []

    async def __call__(self, host, command):
        self.commands.append((host, command))
        if "core health" not in command:
            return 0, ""
        state = self.states[host]
        if state is None:
            return 255, "ssh: connect to host: Operation timed out"
        if "--fix" in command:
            self.states[host] = state = state.get("after_fix", state)
            return 0, json.dumps({**state, "fixed": {"postgres": "restarted"}})
        return 0, "Sentry noise\n" + json.dumps(state)


GOOD = {"healthy": True, "probe": None, "services": {"com.valor.kernel": 1}, "free_gb": 80}
BAD = {
    "healthy": False,
    "probe": PLPGSQL,
    "curable": True,
    "services": {"com.valor.kernel": 1},
    "free_gb": 80,
}


def watch_once(dsn, peers, holds, objective, now=None):
    from routines.watch import runner as watch

    async def go():
        async with await db.connect(dsn) as conn:
            ctx = SimpleNamespace(conn=conn, objective=objective, now=now or datetime.now(UTC))
            return await watch.runner(remote=peers, holds=holds, targets=("pirate",))(ctx)

    return run(go())


def test_the_watch_holds_a_broken_mac_fixes_it_a_watch_later_and_reports_only_what_stays_broken(
    fresh, tmp_path, monkeypatch
):
    told = []
    from routines.watch import runner as watch

    async def tell(conn, key, text):
        told.append(key)

    monkeypatch.setattr(watch, "_tell", tell)
    objective = run(new_task(fresh))
    holds = tmp_path / "holds"

    def states():
        return [r["type"] for r in run(rows(fresh, objective)) if r["type"].startswith("watch.")]

    peers = Peers(pirate=GOOD)
    assert watch_once(fresh, peers, holds, objective).summary == "pirate: healthy"
    assert states() == [] and not (holds / "hold-pirate").exists()
    peers.states["pirate"] = {**BAD, "after_fix": GOOD}
    watch_once(fresh, peers, holds, objective)  # broken once: held, not fixed
    assert states()[-1] == "watch.held" and (holds / "hold-pirate").exists()
    assert (
        "pirate",
        "mkdir -p ~/.config/brew-nightly && touch ~/.config/brew-nightly/hold-pirate",
    ) in peers.commands
    assert not any("--fix" in c for _, c in peers.commands)
    watch_once(fresh, peers, holds, objective)  # broken again: fixed, recorded, not told
    assert states()[-1] == "watch.fixed" and not (holds / "hold-pirate").exists() and told == []
    watch_once(fresh, peers, holds, objective)
    assert states()[-1] == "watch.healthy"
    peers.states["pirate"] = BAD
    watch_once(fresh, peers, holds, objective)
    watch_once(fresh, peers, holds, objective)
    watch_once(fresh, peers, holds, objective)  # still broken: no second row, no second report
    assert states()[-2:] == ["watch.held", "watch.failed"] and len(told) == 1
    assert (holds / "hold-pirate").exists()


def test_an_unreachable_mac_holds_nothing_and_is_reported_once_after_a_day(fresh, tmp_path, monkeypatch):
    told = []
    from routines.watch import runner as watch

    async def tell(conn, key, text):
        told.append(key)

    monkeypatch.setattr(watch, "_tell", tell)
    objective = run(new_task(fresh))
    holds = tmp_path / "holds"
    peers = Peers(pirate=None)
    watch_once(fresh, peers, holds, objective)
    watch_once(fresh, peers, holds, objective)
    assert [r["type"] for r in run(rows(fresh, objective)) if r["type"].startswith("watch.")] == [
        "watch.unreachable"
    ]
    assert told == [] and not holds.exists() and len(peers.commands) == 2
    later = datetime.now(UTC) + timedelta(hours=25)
    watch_once(fresh, peers, holds, objective, now=later)
    watch_once(fresh, peers, holds, objective, now=later)
    assert len(set(told)) == 1


def test_a_watch_report_with_no_session_to_hear_it_is_a_sentry_event_once(fresh, monkeypatch):
    from routines.watch import runner as watch

    events = []
    monkeypatch.setattr(watch.sentry_sdk, "capture_message", lambda text, level: events.append(level))

    async def none(conn):
        return None

    monkeypatch.setattr(watch.intake, "operator_session", none)

    async def go():
        async with await db.connect(fresh) as conn:
            await watch._tell(conn, "watch:cowboy:failed:1", "the Cowboy is broken")
            await watch._tell(conn, "watch:cowboy:failed:1", "the Cowboy is broken")

    run(go())
    assert events == ["fatal"]


def test_the_watch_routine_loads_and_its_job_carries_the_watched_macs(monkeypatch):
    from core import routines

    r = routines.load("watch")
    assert r.runner == "watch" and r.interval == 1800
    monkeypatch.setenv("VALOR_WATCH", "valor-the-pirate,valor-the-bald")
    job = plistlib.loads(routines.plist(r))
    assert job["EnvironmentVariables"]["VALOR_WATCH"] == "valor-the-pirate,valor-the-bald"
    assert job["StartInterval"] == 1800
    assert Path(job["ProgramArguments"][-1]) == Path("watch")


def test_a_running_turn_survives_the_database_dropping_every_session_and_a_stop_after_still_stops_it(
    fresh, tmp_path
):
    """2026-11-13, the Cowboy's controlled restart: the turn's stop listener
    raised on the dropped session and its turn was killed."""
    from tests import bridges

    async def go():
        task = await new_task(fresh)
        gateway = await bridges.gateway(fresh)
        marker = tmp_path / "finished"
        script = f"import time, pathlib; time.sleep(3); pathlib.Path({str(marker)!r}).write_text('ok')"

        def build(url, brief, turn_id, script=script):
            return runs.TurnCommand(
                argv=[sys.executable, "-c", script], env={}, cwd=str(tmp_path), harness="sleeper"
            )

        turn = asyncio.create_task(runs.run_turn(gateway, task, build, dsn=fresh))
        await asyncio.sleep(1.0)
        terminate_all(fresh)  # what a restart of the server does
        ended = await asyncio.wait_for(turn, 30)
        # A second turn, stopped after a drop: the stop is still heard.
        sleeper = "import time; time.sleep(60)"
        second = asyncio.create_task(runs.run_turn(gateway, task, lambda u, b, t: runs.TurnCommand(
            argv=[sys.executable, "-c", sleeper], env={}, cwd=str(tmp_path), harness="sleeper"), dsn=fresh))  # fmt: skip
        await asyncio.sleep(1.0)
        terminate_all(fresh)
        await asyncio.sleep(1.5)
        async with await db.connect(fresh) as conn:
            await tasks.stop(conn, task, reason="test")
        stopped = await asyncio.wait_for(second, 20)
        await gateway.close()
        return ended, marker.exists(), stopped

    ended, finished, stopped = run(go())
    assert ended["outcome"] == "done" and finished
    assert stopped["outcome"] == "stopped"
