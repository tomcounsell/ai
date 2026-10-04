"""Routines on real Postgres: the toml in the kernel's checkout, the standing
objective and its registration, a stop that holds, period spending as a
report, the launchd job, and an unfinished run continued. No model call."""

import asyncio
import dataclasses
import plistlib
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from psycopg.types.json import Jsonb

from core import db, ledger, routines, serve, spending, tasks
from core.settings import settings
from tests.test_objective_tree import call, child, run, stop_rows

pytestmark = pytest.mark.spend(usd=0)

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2027, 3, 1, 12, 0, tzinfo=UTC)


def unique() -> str:
    return "t" + uuid.uuid4().hex[:10]


def write_toml(
    directory: Path, name: str, *, runner: str = "noop", ceiling: str = "propose", extra=""
) -> None:
    (directory / name).mkdir(parents=True)
    (directory / name / "routine.toml").write_text(
        f'name = "{name}"\nrunner = "{runner}"\nceiling = "{ceiling}"\nmodel = "frontier"\n'
        'mission_item = 5\nneed = ["a thing Tom needs"]\ncreated = 2026-10-04\n'
        'instruction = "Do the thing."\n[schedule]\nhour = 3\nminute = 5\n' + extra
    )


async def noop(ctx: routines.Context) -> routines.Ran:
    return routines.Ran(None, "nothing_due", "nothing")


@pytest.fixture
def where(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr(routines, "settings", dataclasses.replace(settings, routines_dir=str(tmp_path)))
    return tmp_path


async def charged(owner, task_id: str, micros: int, at: datetime, call_id: str | None = None) -> None:
    async with await psycopg.AsyncConnection.connect(owner, autocommit=True) as conn:
        await conn.execute(
            "INSERT INTO events (task_id, type, payload, at) VALUES (%s, 'gateway.charged', %s, %s)",
            (task_id, Jsonb({"call_id": call_id or ledger.new_id(), "usd_micros": micros}), at),
        )


def test_the_toml_loads_from_the_kernels_checkout_and_a_workspace_toml_is_never_read(where, tmp_path):
    name = unique()
    write_toml(where, name)
    r = routines.load(name)
    assert (r.runner, r.ceiling, r.schedule, r.need) == (
        "noop",
        "propose",
        {"hour": 3, "minute": 5},
        ("a thing Tom needs",),
    )
    assert routines.names() == [name]
    other = tmp_path / "workspace"
    write_toml(other, "elsewhere")
    with pytest.raises(routines.Refused):
        routines.load("elsewhere")  # the kernel's directory does not hold it
    with pytest.raises(routines.Refused):
        routines.load("../workspace/elsewhere")
    assert routines.names() == [name]


def test_the_kernels_own_routines_load():
    assert set(routines.names(ROOT / "routines")) >= {"expiry", "emulator"}
    for n in routines.names(ROOT / "routines"):
        routines.load(n, ROOT / "routines")


def test_registration_is_reused_a_ceiling_change_sums_both_and_a_duplicate_is_refused(dsn, where):
    name = unique()
    write_toml(where, name)

    async def go():
        async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
            r = routines.load(name)
            first, stopped = await routines.ensure(conn, r)
            again, _ = await routines.ensure(conn, r)
            assert (first, stopped) == (again, None)
            assert len(await routines.registrations(conn, name)) == 1
            call_id = await spending.open_call(conn, first, call())
            await spending.charge(conn, first, call_id, 700, {})
            higher = dataclasses.replace(r, ceiling="act")
            second, _ = await routines.ensure(conn, higher)
            assert second != first
            call_id = await spending.open_call(conn, second, call())
            await spending.charge(conn, second, call_id, 300, {})
            rep = await routines.report(conn, name, datetime.now(UTC) + timedelta(seconds=5))
            assert rep["objectives"] == [first, second]
            assert rep["period_spent_usd_micros"] == 1000
            with pytest.raises(psycopg.errors.UniqueViolation):
                await ledger.append(
                    conn,
                    "routines",
                    "routine.registered",
                    {"name": name, "ceiling": "act", "objective": "x", "replaces": None},
                )

    run(go())


def test_a_stopped_objective_writes_nothing_and_restart_registers_with_replaces(dsn, where):
    name = unique()
    write_toml(where, name)
    seen = []

    async def runner(ctx):
        seen.append(ctx.objective)
        return routines.Ran(None, "nothing_due", "nothing")

    async def go():
        async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
            await routines.run(conn, name, {"noop": runner})
            objective = seen[0]
            await tasks.stop(conn, objective, reason="enough")
            before = await conn.execute("SELECT count(*) FROM events")
            before = (await before.fetchone())[0]
            said = await routines.run(conn, name, {"noop": runner})
            after = (await (await conn.execute("SELECT count(*) FROM events")).fetchone())[0]
            assert "is stopped" in said and "--restart" in said
            assert (before, len(seen)) == (after, 1)
            await routines.run(conn, name, {"noop": runner}, restart=True)
            fresh = seen[1]
            assert fresh != objective
            reg = await routines.registrations(conn, name)
            assert reg[-1]["replaces"] == objective and reg[-1]["objective"] == fresh
            assert len(await stop_rows(conn, objective)) == 1
            assert await stop_rows(conn, fresh) == []

    run(go())


def test_a_racing_second_restart_registers_one_objective(dsn, where):
    name = unique()
    write_toml(where, name)

    async def go():
        async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as a:
            objective, _ = await routines.ensure(a, routines.load(name))
            await tasks.stop(a, objective, reason="enough")

            async def restart():
                async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as c:
                    return await routines.ensure(c, routines.load(name), restart=True)

            got = await asyncio.gather(restart(), restart())
            assert got[0][0] == got[1][0]
            live = [r for r in await routines.registrations(a, name) if r.get("replaces") == objective]
            assert len(live) == 1

    run(go())


def test_a_second_emulator_process_says_already_running(dsn, tmp_path, monkeypatch):
    from routines.emulator import runner

    demo = tmp_path / "demo"
    (demo / "items").mkdir(parents=True)
    monkeypatch.setattr(runner, "settings", dataclasses.replace(settings, demo_dir=str(demo)))

    async def go():
        async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
            r = routines.load("emulator", ROOT / "routines")
            objective, _ = await routines.ensure(conn, r)
            run_id = await tasks.start_child(
                conn, objective, instruction="held run", marker={"objective": "emulator"}
            )
            first = await psycopg.AsyncConnection.connect(dsn, autocommit=True)
            try:
                await first.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (f"run:{run_id}",))
                ctx = routines.Context(conn, r, objective, NOW, None, dsn=dsn)
                got = await runner.run(ctx)
                assert (got.run, got.outcome, got.summary) == (run_id, "running", "already running")
            finally:
                await first.close()

    run(go())


def test_period_spending_covers_thirty_days_and_lists_open_calls_apart(dsn, owner_dsn, where):
    name = unique()
    write_toml(where, name)

    async def go():
        async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
            r = routines.load(name)
            objective, _ = await routines.ensure(conn, r)
            run_id = await tasks.start_child(conn, objective, instruction="run", marker={})
            grand = await child(conn, run_id, instruction="grand")
            calibration = await tasks.start_calibration(
                conn, "emulator", detail={"emulator": {"run": "r", "item_task": grand}}
            )
            await charged(owner_dsn, objective, 100, NOW - timedelta(days=31))
            await charged(owner_dsn, objective, 200, NOW - timedelta(days=29))
            await charged(owner_dsn, run_id, 300, NOW - timedelta(days=1))
            await charged(owner_dsn, grand, 400, NOW - timedelta(days=2))
            await charged(owner_dsn, calibration, 500, NOW - timedelta(days=3))
            await charged(owner_dsn, grand, 999, NOW + timedelta(days=1))
            open_id = ledger.new_id()
            async with await psycopg.AsyncConnection.connect(owner_dsn, autocommit=True) as o:
                await o.execute(
                    "INSERT INTO events (task_id, type, payload, at) VALUES (%s, 'gateway.reserved', %s, %s)",
                    (grand, Jsonb({"call_id": open_id, "usd_micros": 50}), NOW - timedelta(days=1)),
                )
            rep = await routines.report(conn, name, NOW)
            assert rep["period_spent_usd_micros"] == 200 + 300 + 400 + 500
            assert open_id in rep["period_open_calls"]

    run(go())


def test_a_million_dollars_of_prior_charges_does_not_stop_the_run(dsn, owner_dsn, where):
    name = unique()
    write_toml(where, name)
    ran = []

    async def runner(ctx):
        ran.append(ctx.objective)
        return routines.Ran(None, "nothing_due", "nothing")

    async def go():
        async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
            objective, _ = await routines.ensure(conn, routines.load(name))
            await charged(owner_dsn, objective, 1_000_000 * 1_000_000, datetime.now(UTC) - timedelta(days=1))
            said = await routines.run(conn, name, {"noop": runner})
            assert ran == [objective]
            assert "$1000000.00" in said or "1,000,000" in said or "1000000" in said

    run(go())


def test_the_plist_names_the_kernels_interpreter_the_schedule_and_the_settings_set(where, monkeypatch):
    name = unique()
    write_toml(where, name)
    monkeypatch.setenv("VALOR_DEMO", "/some/demo")
    monkeypatch.setenv("VALOR_PGHOST", "db.example")
    monkeypatch.setenv("VALOR_MACHINE", "pink")
    monkeypatch.setenv("VALOR_WORK", "/some/work")
    monkeypatch.setenv("VALOR_PROJECTS", "/some/projects")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-appear")
    got = plistlib.loads(routines.plist(routines.load(name), python="/py/bin/python", root=ROOT))
    assert got["Label"] == f"com.valor.routine.{name}"
    assert got["ProgramArguments"] == ["/py/bin/python", "-m", "core", "routine", name]
    assert got["StartCalendarInterval"] == {"Hour": 3, "Minute": 5}
    assert got["WorkingDirectory"] == str(ROOT)
    env = got["EnvironmentVariables"]
    assert env["VALOR_DEMO"] == "/some/demo" and env["VALOR_PGHOST"] == "db.example"
    assert env["VALOR_MACHINE"] == "pink" and env["VALOR_WORK"] == "/some/work"
    assert env["VALOR_PROJECTS"] == "/some/projects"
    assert set(env) <= {"HOME", "PATH", *routines.PLIST_ENV}
    assert "must-not-appear" not in routines.plist(routines.load(name)).decode()


def test_the_command_prints_a_plist_for_the_expiry_routine():
    out = subprocess.run(
        [sys.executable, "-m", "core", "routine", "expiry", "--plist"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    ).stdout
    assert plistlib.loads(out)["Label"] == "com.valor.routine.expiry"


def test_an_unfinished_run_is_continued_not_started_again(dsn, where):
    name = unique()
    write_toml(where, name)

    async def runner(ctx):
        open_ = await routines.open_run(ctx)
        if open_:
            return routines.Ran(open_, "continued", "open")
        run_id = await tasks.start_child(ctx.conn, ctx.objective, instruction="run")
        return routines.Ran(run_id, "started", "new")

    async def go():
        async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
            first = await routines.run(conn, name, {"noop": runner})
            second = await routines.run(conn, name, {"noop": runner})
            assert "started" in first and "continued" in second
            reg = (await routines.registrations(conn, name))[0]
            ran = await routines.runs(conn, [reg["objective"]])
            assert ran[0]["run"] == ran[1]["run"]
            rep = await routines.report(conn, name)
            assert [e["firings"] for e in rep["runs"]] == [2]

    run(go())


def test_the_kernel_passes_an_objective_node_by(dsn, where):
    name = unique()
    write_toml(where, name)

    async def go():
        async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
            objective, _ = await routines.ensure(conn, routines.load(name))
            kernel = serve.Kernel(None, {}, None, dsn)
            assert await kernel._ready(conn, objective) is None
            assert objective not in kernel.jobs

    run(go())


def test_the_printed_line_counts_the_run_it_just_made(dsn, owner_dsn, where):
    name = unique()
    write_toml(where, name)

    async def runner(ctx):
        kid = await tasks.start_child(ctx.conn, ctx.objective, instruction="k", routine=name)
        await charged(owner_dsn, kid, 5000, datetime.now(UTC))  # charged while the run goes
        return routines.Ran(kid, "started", "k")

    async def go():
        async with await db.connect(dsn) as conn:
            said = await routines.run(conn, name, {"noop": runner})
            return said, routines.listing(await routines.report(conn, name))

    said, listed = run(go())
    assert "$0.005000 in 1 runs" in said and "$0.005000" in listed


def test_a_paused_replay_is_resumed_by_the_next_firing_and_not_recorded_finished(
    dsn, where, tmp_path, monkeypatch
):
    import json

    from routines.emulator import runner

    name = unique()
    write_toml(where, name, runner="emulator")
    demo = tmp_path / "demo"
    (demo / "items").mkdir(parents=True)
    (demo / "results").mkdir()
    (demo / "items" / "it.json").write_text(json.dumps({"name": "it"}))
    monkeypatch.setattr(runner, "settings", dataclasses.replace(settings, demo_dir=str(demo)))
    calls = []

    async def drive(item, arm, run_, nm):
        calls.append((arm, run_))
        paused = len(calls) <= 3  # the first firing pauses every arm; the second finishes them
        result = {"run": nm, "outcome": None if paused else "passed", "judge": {}}
        (demo / "results" / f"{nm}.json").write_text(json.dumps(result))
        return 0, ""

    monkeypatch.setattr(runner, "_drive", drive)

    async def go():
        async with await db.connect(dsn) as conn:
            first = await routines.run(conn, name, {"emulator": runner.run}, dsn=dsn)
            second = await routines.run(conn, name, {"emulator": runner.run}, dsn=dsn)
            return first, second

    first, second = run(go())
    assert "running" in first and "finished" in second
    assert len(calls) == 6 and len({r for _, r in calls}) == 1  # the same run, resumed
