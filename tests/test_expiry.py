"""The expiry sweep on real Postgres: what `routines.due` folds from rows the
kernel wrote, and the sweep task the routine starts when something is due.
Each test has a database of its own, since `due` reads every row. Old rows
are inserted with an explicit `at`. No model call."""

import dataclasses
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from psycopg.types.json import Jsonb

from core import broker, db, guards, ledger, machine, routines, targets, tasks, workspace
from core.machine import State
from core.settings import settings
from tests import scripted
from tests.conftest import TEST_DB
from tests.test_docs_runner import docs_runners
from tests.test_objective_tree import merge, run
from tests.test_routines import write_toml
from tests.test_workspace import _spec_file

pytestmark = pytest.mark.spend(usd=0)

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2027, 2, 1, 12, 0, tzinfo=UTC)  # after the seeded guards' 2026-12-30 expiry


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A database of this test's own and a routines directory holding the
    kernel's two routines."""
    name = f"{TEST_DB}_expiry"
    dsn = db.migrate(name, fresh=True)
    owner = settings.dsn(owner=True, database=name)
    for n in ("expiry", "emulator"):
        shutil.copytree(ROOT / "routines" / n, tmp_path / "routines" / n)
    monkeypatch.setattr(
        routines, "settings", dataclasses.replace(settings, routines_dir=str(tmp_path / "routines"))
    )
    return dsn, owner, tmp_path / "routines"


async def put(owner: str, task_id: str, type_: str, payload: dict, at: datetime) -> None:
    async with await psycopg.AsyncConnection.connect(owner, autocommit=True) as conn:
        await conn.execute(
            "INSERT INTO events (task_id, type, payload, at) VALUES (%s, %s, %s, %s)",
            (task_id, type_, Jsonb(payload), at),
        )


async def due(dsn: str, now: datetime = NOW) -> dict:
    async with await db.connect(dsn) as conn:
        return await routines.due(conn, now)


def listed(found: dict, kind: str | None = None) -> set[str]:
    return {i["id"] for i in found["items"] if kind in (None, i["kind"])}


SEEDED = {g["guard_id"] for g in guards.SEEDED}


def test_nothing_is_due_before_the_expiry(world):
    dsn, _, _ = world
    assert run(due(dsn, datetime(2026, 12, 29, tzinfo=UTC))) == {"items": [], "outside": []}


def test_an_unfired_guard_is_due_at_its_expiry(world):
    dsn, _, _ = world
    found = run(due(dsn))
    assert listed(found, "guard") == SEEDED
    item = next(i for i in found["items"] if i["kind"] == "guard")
    assert item["last_firing"] is None and item["firing_record"] is True and item["expires"] == "2026-12-30"
    assert "never fired" in routines.render(found, routines.load("expiry"))


def test_a_guard_that_fired_is_due_ninety_days_after_its_last_firing(world):
    dsn, owner, _ = world
    guard = machine.GUARD_JUDGE

    async def go():
        async with await db.connect(dsn) as conn:
            t = await tasks.start(conn, tasks.Brief(instruction="tom's work"))
        await put(
            owner,
            t,
            "judge.decided",
            {"guard_id": guard, "verdict": "precise"},
            datetime(2026, 12, 1, tzinfo=UTC),
        )
        return (
            await due(dsn, datetime(2027, 2, 28, tzinfo=UTC)),
            await due(dsn, datetime(2027, 3, 2, tzinfo=UTC)),
        )

    before, after = run(go())
    assert guard not in listed(before) and listed(before, "guard") == SEEDED - {guard}
    fired = next(i for i in after["items"] if i["id"] == guard)
    assert fired["last_firing"] == "2026-12-01"


def test_a_guard_that_fired_only_on_a_background_task_is_due_at_expiry(world):
    dsn, owner, _ = world
    guard = machine.GUARD_CRITIQUE

    async def go():
        async with await db.connect(dsn) as conn:
            replay = await tasks.start(conn, tasks.Brief(instruction="replay", replay=True))
            under = await tasks.start_child(conn, replay, instruction="under")
        await put(
            owner,
            under,
            "critique.decided",
            {"guard_id": guard, "verdict": "revise"},
            NOW - timedelta(days=5),
        )
        return await due(dsn)

    found = run(go())
    item = next(i for i in found["items"] if i["id"] == guard)
    assert item["last_firing"] is None


def test_a_guard_id_in_a_row_that_is_no_verdict_is_not_a_firing(world):
    dsn, owner, _ = world
    guard = machine.GUARD_REVIEW

    async def go():
        async with await db.connect(dsn) as conn:
            t = await tasks.start(conn, tasks.Brief(instruction="x"))
        await put(owner, t, "task.noted", {"guard_id": guard}, NOW - timedelta(days=5))
        return await due(dsn)

    assert next(i for i in run(go())["items"] if i["id"] == guard)["last_firing"] is None


def test_an_item_a_sweep_listed_is_not_listed_again_until_ninety_days_on(world):
    dsn, owner, _ = world
    guard = machine.GUARD_BREADTH

    async def go():
        async with await db.connect(dsn) as conn:
            t = await tasks.start(conn, tasks.Brief(instruction="sweep"))
        await put(
            owner,
            t,
            "task.started",
            {"due": [{"kind": "guard", "id": guard}], "sweep": "expiry", "sdlc": 1},
            datetime(2027, 1, 20, tzinfo=UTC),
        )
        return await due(dsn), await due(dsn, datetime(2027, 4, 21, tzinfo=UTC))

    soon, later = run(go())
    assert guard not in listed(soon) and listed(soon, "guard") == SEEDED - {guard}
    assert guard in listed(later)


def test_an_instance_grant_a_merged_sweep_listed_is_gone_and_never_due_again(world):
    dsn, owner, _ = world

    async def go():
        async with await db.connect(dsn) as conn:
            ours = await tasks.start(conn, tasks.Brief(instruction="ours", project={"name": "valor"}))
            sweep = await tasks.start(conn, tasks.Brief(instruction="sweep"))
        await put(
            owner,
            ours,
            "guard.granted",
            {"guard_id": "g-x", "instance_id": "x", "name": "x", "incident": "i", "mission_items": [2],
             "granted_at": "2026-09-01", "expires": "2026-09-30"},
            datetime(2026, 9, 1, tzinfo=UTC),
        )  # fmt: skip
        listing = {"due": [{"kind": "grant", "id": "g-x"}], "sweep": "expiry", "sdlc": 1}
        await put(owner, sweep, "task.started", listing, datetime(2026, 10, 1, tzinfo=UTC))
        before = await due(dsn, datetime(2026, 12, 15, tzinfo=UTC))  # sweep open: held off 90 days
        async with await db.connect(dsn) as conn:
            await merge(conn, sweep)
        return (
            before,
            await due(dsn, datetime(2027, 1, 1, tzinfo=UTC)),
            await due(dsn, datetime(2028, 1, 1, tzinfo=UTC)),
        )

    before, after, much_later = run(go())
    assert "g-x" not in listed(before)
    assert "g-x" not in listed(after) and "g-x" not in listed(much_later)


def test_the_rendered_sweep_prompt_removes_only_what_the_list_names(world):
    _, _, _ = world
    text = routines.render({"items": [], "outside": []}, routines.load("expiry"))
    assert "Remove nothing the list does not name" in text
    assert "Keep nothing" not in text and "Tom" not in text


def test_a_seeded_guard_the_checkout_no_longer_holds_is_not_due(world):
    dsn, owner, _ = world

    async def go():
        await put(
            owner,
            "guards",
            "guard.granted",
            {"guard_id": "removed-long-ago", "name": "n", "incident": "i", "mission_items": [1],
             "granted_at": "2026-10-01", "expires": "2026-12-30"},
            datetime(2026, 10, 1, tzinfo=UTC),
        )  # fmt: skip
        return await due(dsn)

    assert "removed-long-ago" not in listed(run(go()))


def test_an_instance_grant_past_its_expiry_is_due_with_no_firing_record_and_another_projects_is_outside(
    world,
):
    dsn, owner, _ = world

    async def go():
        async with await db.connect(dsn) as conn:
            ours = await tasks.start(conn, tasks.Brief(instruction="ours", project={"name": "valor"}))
            theirs = await tasks.start(conn, tasks.Brief(instruction="theirs", project={"name": "popoto"}))
        for task, instance in ((ours, "inst-ours"), (theirs, "inst-theirs")):
            await put(
                owner,
                task,
                "guard.granted",
                {"guard_id": f"g-{instance}", "instance_id": instance, "name": instance, "incident": "i",
                 "mission_items": [2], "granted_at": "2026-10-10", "expires": "2027-01-08"},
                datetime(2026, 10, 10, tzinfo=UTC),
            )  # fmt: skip
        return await due(dsn), await due(dsn, datetime(2027, 1, 8, tzinfo=UTC))

    found, on_the_day = run(go())
    grant = next(i for i in found["items"] if i["kind"] == "grant")
    assert grant["id"] == "g-inst-ours" and grant["firing_record"] is False
    assert [i["id"] for i in found["outside"]] == ["g-inst-theirs"]
    assert "no firing record" in routines.render(found, routines.load("expiry"))
    assert listed(on_the_day, "grant") == set()  # due when today is past `expires`


async def register(dsn: str, owner: str, name: str, at: datetime) -> str:
    async with await db.connect(dsn) as conn:
        objective = await tasks.start(
            conn,
            tasks.Brief(instruction=f"routine {name}", routine=name),
            marker={routines.OBJECTIVE: name},
        )
    await put(
        owner,
        routines.STREAM,
        "routine.registered",
        {"name": name, "ceiling": "propose", "digest": "d", "need": ["a need"], "mission_item": 5,
         "objective": objective, "replaces": None},
        at,
    )  # fmt: skip
    return objective


def test_a_routine_is_due_when_unused_and_not_when_a_child_delivered_or_it_is_the_expiry_routine(world):
    dsn, owner, where = world
    write_toml(where, "stale")
    write_toml(where, "busy")
    write_toml(where, "recent")

    async def go():
        old = NOW - timedelta(days=120)
        stale = await register(dsn, owner, "stale", old)
        busy = await register(dsn, owner, "busy", old)
        await register(dsn, owner, "expiry", old)
        emulator = await register(dsn, owner, "emulator", old)
        await register(dsn, owner, "recent", NOW - timedelta(days=10))
        async with await db.connect(dsn) as conn:
            runs = {}
            for objective in (stale, busy, emulator):
                runs[objective] = await tasks.start_child(conn, objective, instruction="a run")
        await put(
            owner,
            stale,
            "routine.ran",
            {"run": runs[stale], "outcome": "nothing_due"},
            NOW - timedelta(days=30),
        )
        for objective, delivered in ((busy, True), (emulator, True)):
            await put(
                owner,
                objective,
                "routine.ran",
                {"run": runs[objective], "outcome": "finished"},
                NOW - timedelta(days=30),
            )
            if delivered:
                async with await db.connect(dsn) as conn:
                    replay = await tasks.start_child(conn, runs[objective], instruction="a replay")
                await put(
                    owner,
                    replay,
                    "task.delivered",
                    {"summary": "done", "outcome": "passed"},
                    NOW - timedelta(days=30),
                )
        return await due(dsn)

    found = run(go())
    assert listed(found, "routine") == {"stale"}
    item = next(i for i in found["items"] if i["kind"] == "routine")
    assert item["incident"] == "a thing Tom needs" and item["expires"] == "2027-01-02"


def test_nothing_due_starts_no_task_and_runs_no_turn(world):
    dsn, _, _ = world

    async def go():
        async with await db.connect(dsn) as conn:
            r = routines.load("expiry")
            ctx = routines.Context(
                conn, r, "none", datetime(2026, 11, 1, tzinfo=UTC), None, start_project=None
            )
            before = (await (await conn.execute("SELECT count(*) FROM events")).fetchone())[0]
            ran = await routines.expiry_runner(ctx)
            after = (await (await conn.execute("SELECT count(*) FROM events")).fetchone())[0]
            return ran, before, after

    ran, before, after = run(go())
    assert (ran.run, ran.outcome) == (None, "nothing_due") and before == after


def cli(tmp_path: Path, name: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "core", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "VALOR_DB": name,
            "VALOR_WORK": str(tmp_path / "work"),
            "VALOR_ROUTINES": str(tmp_path / "routines"),
        },
    )


def test_items_due_start_one_project_task_the_kernel_carries_to_a_held_merge(tmp_path):
    name = f"{TEST_DB}_expiry"
    dsn = db.migrate(name, fresh=True)
    owner = settings.dsn(owner=True, database=name)
    spec = _spec_file(tmp_path)
    expiry = tmp_path / "routines" / "expiry"
    expiry.mkdir(parents=True)
    text = (ROOT / "routines" / "expiry" / "routine.toml").read_text()
    lines = [
        f'project = "{spec}"' if ln.startswith("project") else ln
        for ln in text.splitlines()
        if not ln.startswith("branch")
    ]
    (expiry / "routine.toml").write_text("\n".join(lines) + "\n")

    async def seed():
        async with await db.connect(dsn) as conn:
            ours = await tasks.start(conn, tasks.Brief(instruction="ours", project={"name": "valor"}))
        await put(
            owner,
            ours,
            "guard.granted",
            {"guard_id": "g-old", "instance_id": "inst-old", "name": "an old hunk", "incident": "an incident",
             "mission_items": [2], "granted_at": "2026-08-01", "expires": "2026-09-30"},
            datetime(2026, 8, 1, tzinfo=UTC),
        )  # fmt: skip

    run(seed())
    first = cli(tmp_path, name, "routine", "expiry")
    assert first.returncode == 0, first.stderr
    assert "started" in first.stdout
    second = cli(tmp_path, name, "routine", "expiry")
    assert second.returncode == 0, second.stderr
    assert "continued" in second.stdout

    async def inspect():
        async with await db.connect(dsn) as conn:
            (reg,) = await routines.registrations(conn, "expiry")
            kids = await tasks.children(conn, reg["objective"])
            assert len(kids) == 1
            b = await tasks.brief(conn, kids[0])
            started = (await ledger.read(conn, kids[0]))[0]["payload"]
            return kids[0], b, started, await tasks.background(conn, kids[0])

    task, b, started, background = run(inspect())
    assert b.max_effect_class == "act" and b.routine == "expiry" and background
    assert "inst-old" not in b.instruction and "g-old" in b.instruction and "an incident" in b.instruction
    assert started["sweep"] == "expiry" and started["due"] == [{"kind": "grant", "id": "g-old"}]
    ws = Path(b.workspace)
    assert ws.is_dir()

    async def drive():
        scripted.steer(ws, fresh_acts=["sound", "docs"], request_merge=True)
        await scripted_drive(dsn, task, scripted.fresh_runners(ws))
        await scripted.check(dsn, task, "test", "pass")
        await scripted.check(dsn, task, "review", "pass")
        await scripted_drive(dsn, task, docs_runners(ws))
        async with await db.connect(dsn) as conn:
            return machine.fold(await ledger.read(conn, task)), await ledger.read(conn, task)

    f, written = run(drive())
    assert f.state is State.MERGE and f.merge_effect["state"] == "held"
    assert not [r for r in written if r["type"] in ("effect.intent", "effect.outcome")]
    # The merge is released like any merge: the lead's approval, no other.
    effect = f.merge_effect["effect_id"]

    async def release():
        async with await db.connect(dsn) as conn:
            await broker.approve(conn, effect, note="released", by="build lead")
            done = await scripted.release(conn, effect)
            return done, machine.fold(await ledger.read(conn, task))

    done, merged = run(release())
    assert done.kind == "done" and merged.state is State.MERGED


def test_the_real_valor_spec_loads_through_the_routines_path_and_needs_the_granted_merge_target(world):
    dsn, _, _ = world
    r = routines.load("expiry")
    assert r.project == "valor"
    spec = workspace.Spec.load(r.project)  # projects/valor.toml in the checkout, no clone, no push
    spec = dataclasses.replace(spec, branch=r.branch, target_branch=r.branch)
    assert spec.name == "valor" and spec.merge_url

    async def go():
        async with await db.connect(dsn) as conn:
            before = await targets.check(conn, spec.merge_url, spec.target_branch)
            await targets.grant(conn, spec.merge_url, spec.target_branch, "the granted merge target")
            return before, await targets.check(conn, spec.merge_url, spec.target_branch)

    before, after = run(go())
    assert before and after is None


async def scripted_drive(dsn, task, runners_):
    from core.gateway import Gateway

    gateway = Gateway(dsn)
    await gateway.start()
    try:
        return await scripted.route(gateway, task, runners_, dsn=dsn)
    finally:
        await gateway.close()
