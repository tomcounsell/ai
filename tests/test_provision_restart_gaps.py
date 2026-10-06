"""Behaviors of the 2.4b change (a killed kernel's unfinished provisioning is
redone) that the 2.4b tests leave unexercised."""

import os
import subprocess
import sys
import time
from dataclasses import asdict, replace

import psycopg
import pytest
from psycopg.types.json import Jsonb

from core import db, git, ledger, runs, serve, tasks
from core import workspace as kws
from tests import scripted
from tests.ports import service as ports_service
from tests.test_serve import _message_task, _toy_project, only, rows, run, settled, typed
from tests.test_serve import fresh as _fresh
from tests.test_serve import op as _op
from tests.test_workspace import _cli, _rows

fresh, op = _fresh, _op  # fixtures, shared with test_serve

pytestmark = pytest.mark.spend(usd=0)


def test_the_mark_ends_with_the_block_and_yields_to_extra_env(tmp_path):
    """`git.marked` marks the calls inside it, an explicit `extra_env`
    wins over it, and a call after the block carries no mark."""
    seen = []
    real = git.start

    def recording(argv, **kwargs):
        seen.append(kwargs["env"].get(runs.TURN_ENV))
        return real(argv, **kwargs)

    src = scripted.toy_repo(tmp_path)
    git.start = recording
    try:
        with git.marked("provision-x"):
            git._git(src, "status")
            git._git(src, "status", extra_env={runs.TURN_ENV: "explicit"})
        git._git(src, "status")
    finally:
        git.start = real
    assert seen == ["provision-x", "explicit", os.environ.get(runs.TURN_ENV)]


def test_remove_clears_a_dangling_link_at_the_root(tmp_path):
    """A link at the task's root pointing nowhere is cleared, and what it
    pointed at is not made."""
    work = tmp_path / "work"
    work.mkdir()
    task = ledger.new_id()
    lay = kws.layout(task, work)
    lay.root.symlink_to(tmp_path / "nowhere")
    assert os.path.lexists(lay.root) and not lay.root.exists()
    kws.remove(task, lay)
    assert not os.path.lexists(lay.root) and not (tmp_path / "nowhere").exists()


def test_remove_of_a_missing_root_is_nothing_to_clear(tmp_path):
    task = ledger.new_id()
    kws.remove(task, kws.layout(task, tmp_path / "work"))


def test_remove_reaps_what_a_setup_command_left(tmp_path):
    """The process marked `setup-<task>-<n>` for each `<n>.log` under
    `setup/` is stopped; one marked for a log that is absent is not."""
    work = tmp_path / "work"
    task = ledger.new_id()
    lay = kws.reserve(task, {}, work)
    lay.setup.mkdir(parents=True)
    (lay.setup / "0.log").write_text("")
    (lay.setup / "notes.log").write_text("")  # not a command's log

    def sleeper(mark):
        return subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import time; time.sleep(600)",
            ],  # `sleep`, a platform binary, hides its environment from ps -E
            env={**os.environ, runs.TURN_ENV: mark},
            start_new_session=True,
        )

    marked = sleeper(f"setup-{task}-0")
    other = sleeper(f"setup-{task}-1")
    try:
        time.sleep(0.5)
        kws.remove(task, lay)
        assert marked.wait(10) is not None
        assert other.poll() is None
        assert not lay.root.exists()
    finally:
        for p in (marked, other):
            p.kill()
            p.wait()


async def _stop(dsn, task):
    async with await db.connect(dsn) as conn:
        await tasks.stop(conn, task, reason="test")


def test_workspace_remove_refuses_while_the_task_is_being_provisioned(dsn, tmp_path):
    """With `provision:<task>` held, a stopped task's unfinished directory
    stays and no row is written."""
    work = tmp_path / "work"
    task = ledger.new_id()
    lay = kws.reserve(task, {}, work)

    async def start():
        async with await db.connect(dsn) as conn:
            await tasks.start(conn, tasks.Brief(id=task, instruction="x", project={"name": "toy"}))

    run(start())
    run(_stop(dsn, task))
    holder = psycopg.connect(dsn, autocommit=True)
    try:
        holder.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (f"provision:{task}",))
        got = _cli(tmp_path, "workspace", "remove", task)
        assert got.returncode == 1 and "being provisioned now" in got.stderr
        assert lay.root.is_dir()
        assert not [r for r in run(_rows(dsn, task)) if r["type"] == "workspace.removed"]
    finally:
        holder.close()
        kws.rmtree(lay.root)


def test_workspace_remove_of_a_task_with_no_directory_is_still_refused(dsn, tmp_path):
    """The old refusal stands when there is nothing unfinished to clear."""
    task = ledger.new_id()

    async def start():
        async with await db.connect(dsn) as conn:
            await tasks.start(conn, tasks.Brief(id=task, instruction="x", project={"name": "toy"}))

    run(start())
    run(_stop(dsn, task))
    got = _cli(tmp_path, "workspace", "remove", task)
    assert got.returncode == 1 and "no workspace the kernel provisioned" in got.stderr
    assert not [r for r in run(_rows(dsn, task)) if r["type"] == "workspace.removed"]


def test_a_provisioning_job_for_a_task_with_no_project_clears_nothing(fresh, op, tmp_path):
    """The re-read refuses a task with no project, as `_ready` does."""
    src = scripted.toy_repo(tmp_path)
    _toy_project(tmp_path, src, [])
    task = ledger.new_id()

    async def start():
        async with await db.connect(fresh) as conn:
            await tasks.start(conn, tasks.Brief(id=task, instruction="x"))

    run(start())
    root = tmp_path / "work" / task
    root.mkdir(parents=True)
    (root / "sentinel").write_text("here")
    before = run(rows(fresh, task))

    async def go():
        kernel = serve.Kernel(None, {}, None, fresh)
        try:
            await kernel._provision(task, "toy")
        finally:
            await kernel.close()

    run(go())
    assert (root / "sentinel").read_text() == "here" and run(rows(fresh, task)) == before


def test_a_dangling_link_at_the_task_root_is_redone(fresh, op, tmp_path):
    """A link where the directory should be (seen by `lexists`) is cleared
    and the task provisioned."""
    src = scripted.toy_repo(tmp_path)
    _toy_project(tmp_path, src, [])
    task = _message_task(fresh)
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    (work / task).symlink_to(tmp_path / "nowhere")

    async def go():
        kernel = only(serve.Kernel(None, {}, None, fresh), task)
        try:
            async with await db.connect(fresh) as conn:
                await kernel.schedule(conn)
            await settled(kernel)
        finally:
            await kernel.close()

    run(go())
    written = run(rows(fresh, task))
    types = [r["type"] for r in written]
    assert types.count("workspace.provisioned") == 1 and "workspace.failed" not in types, typed(
        written, "workspace.failed"
    )
    assert (work / task / "repo" / ".git").is_dir() and not (work / task).is_symlink()


def test_workspace_remove_takes_a_merged_tasks_unfinished_provisioning(dsn, tmp_path):
    """A merged task's leftover directory is removed as a stopped one's is."""
    from tests.test_objective_tree import merge

    work = tmp_path / "work"
    task = ledger.new_id()
    lay = kws.reserve(task, {}, work)

    async def start():
        async with await db.connect(dsn) as conn:
            await tasks.start(conn, tasks.Brief(id=task, instruction="x", project={"name": "toy"}))
            await merge(conn, task)

    run(start())
    got = _cli(tmp_path, "workspace", "remove", task)
    assert got.returncode == 0, got.stderr
    assert not os.path.lexists(lay.root)
    assert [r["type"] for r in run(_rows(dsn, task))].count("workspace.removed") == 1


@pytest.mark.parametrize("kind", ["legacy", "calibration"])
def test_a_provisioning_job_for_a_legacy_or_calibration_task_clears_nothing(fresh, op, tmp_path, kind):
    """The re-read refuses a legacy or a calibration task even when its
    document names a project, as `_ready` does."""
    src = scripted.toy_repo(tmp_path)
    _toy_project(tmp_path, src, [])

    async def start():
        async with await db.connect(fresh) as conn:
            if kind == "legacy":
                b = tasks.Brief(instruction="x", project={"name": "toy"})
                async with conn.transaction():
                    await tasks._write(conn, b, {}, ledger.provenance("tom", "test", False))
                return b.id
            # `start_calibration`'s rows, its document naming a project
            b = tasks.Brief(instruction="calibrate site", max_effect_class="read", project={"name": "toy"})
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO documents (kind, id, body) VALUES ('task', %s, %s)", (b.id, Jsonb(asdict(b)))
                )
                await ledger.append(conn, b.id, "task.started", {"calibration": "site", "instruction": "x"})
            return b.id

    task = run(start())
    root = tmp_path / "work" / task
    root.mkdir(parents=True)
    (root / "sentinel").write_text("here")
    before = run(rows(fresh, task))

    async def go():
        kernel = serve.Kernel(None, {}, None, fresh)
        try:
            await kernel._provision(task, "toy")
        finally:
            await kernel.close()

    run(go())
    assert (root / "sentinel").read_text() == "here" and run(rows(fresh, task)) == before


def test_the_redo_frees_the_dead_attempts_ports(fresh, op, tmp_path, monkeypatch):
    """The only Postgres port, recorded in the dead attempt's `ports.json`,
    is chosen again by the redo."""
    from core.settings import settings

    src = scripted.toy_repo(tmp_path)
    _toy_project(tmp_path, src, [])
    spec = tmp_path / "projects" / "toy.toml"
    spec.write_text(spec.read_text() + 'services = ["postgres"]\n')
    port = ports_service((6579, 6579))
    monkeypatch.setattr(serve, "settings", replace(settings, pg_ports=(port, port)))
    chosen = []

    class Made:
        def brief_fields(self):
            return {"workspace": str(root / "repo"), "mirror": str(root / "mirror")}

    def provision(task_id, spec, ports):
        chosen.append(ports)
        return Made()

    monkeypatch.setattr(kws, "provision", provision)
    task = _message_task(fresh)
    root = tmp_path / "work" / task
    root.mkdir(parents=True)
    (root / kws.PORTS_FILE).write_text(f'{{"postgres": {port}}}')

    async def go():
        kernel = serve.Kernel(None, {}, None, fresh)
        try:
            await kernel._provision(task, "toy")
        finally:
            await kernel.close()

    run(go())
    written = run(rows(fresh, task))
    types = [r["type"] for r in written]
    assert chosen == [{"postgres": port}], typed(written, "workspace.failed")
    assert types.count("workspace.provisioned") == 1 and "workspace.failed" not in types
