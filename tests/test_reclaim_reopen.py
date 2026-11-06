"""Feedback on a task whose workspace the kernel reclaimed: the kernel
provisions it a new workspace from the target branch and its next turn is
a new session there (VALOR-KZ: every step had spawned the harness in the
removed directory)."""

import asyncio
import pathlib
import uuid

import pytest

from core import db, ledger, machine, runs, serve, session, tasks, workspace
from core.machine import State
from core.settings import settings
from tests import scripted
from tests.test_kernel import SESSION, new_task
from tests.test_machine import _reach

pytestmark = pytest.mark.spend(usd=0)
run = asyncio.run


async def finished(dsn, tmp_path, where: State) -> tuple[str, pathlib.Path]:
    """A provisioned task with a commit of its own in its workspace, played to
    `where`; the origin's main gains `merged.txt` (a merge landing)."""
    ws, origin = scripted.workspace(tmp_path)
    name = f"app{uuid.uuid4().hex[:6]}"
    (pathlib.Path(settings.projects_dir) / f"{name}.toml").write_text(
        f'name = "{name}"\nrepo = "{origin}"\nkind = "plain"\nsuite = "true"\n'
    )
    task = await new_task(dsn, project={"name": name})
    made = await asyncio.to_thread(workspace.provision, task, workspace.Spec.load(name), {})
    scripted.commit(pathlib.Path(made.workspace), "unmerged.txt", "x\n", "unmerged work")
    if where is State.MERGED:
        scripted.commit(ws, "merged.txt", "x\n", "merged work")
        scripted.git(ws, "push", "-q", "origin", "HEAD:refs/heads/main")
    async with await db.connect(dsn) as conn:
        await ledger.append(conn, task, "workspace.provisioned", {"fields": made.brief_fields()})
        for row in _reach(where).rows[1:]:
            p = {k: f"{task}-{v}" if k in ("turn_id", "effect_id") else v for k, v in row["payload"].items()}
            if row["type"] == "turn.ended":
                p["result"] = {**p["result"], "session_id": SESSION}
            await ledger.append(conn, task, row["type"], p)
    return task, pathlib.Path(made.workspace)


async def reclaimable(conn) -> set[str]:
    return {r[0] for r in await (await conn.execute(serve.RECLAIMABLE)).fetchall()}


@pytest.mark.parametrize("where", [State.MERGED, State.ENDED])
def test_feedback_after_reclaim_provisions_a_new_workspace_and_a_new_session(dsn, tmp_path, where):
    async def go():
        task, old = await finished(dsn, tmp_path, where)
        async with await db.connect(dsn) as conn:
            assert await workspace.reclaim(conn, task, by="t", via="t")
            assert not old.exists() and task not in await reclaimable(conn)
            await session.feedback(conn, task, "make it shorter")
            assert (await tasks.brief(conn, task)).workspace is None
            kernel = serve.Kernel(None, {}, None, dsn)
            assert await kernel._ready(conn, task) is None
            await kernel.jobs[task]
            b = await tasks.brief(conn, task)
            prompt, resume, _ = await session.next_prompt(conn, task)
            return (
                task,
                b,
                prompt,
                resume,
                await reclaimable(conn),
                machine.fold(await ledger.read(conn, task)),
            )

    task, b, prompt, resume, again, f = run(go())
    new = pathlib.Path(b.workspace)
    assert new.is_dir() and (new / "README.md").exists() and not (new / "unmerged.txt").exists()
    assert (new / "merged.txt").exists() is (where is State.MERGED)
    assert f.state is State.PATCH and resume is None
    assert "Your workspace was rebuilt" in prompt and "work that was not is gone" in prompt
    assert b.base_sha in prompt and "make it shorter" in prompt
    assert task in again  # reclaimed again once it finishes


def test_a_task_never_reclaimed_resumes_its_session(dsn, tmp_path):
    async def go():
        task, old = await finished(dsn, tmp_path, State.MERGED)
        async with await db.connect(dsn) as conn:
            await session.feedback(conn, task, "make it shorter")
            prompt, resume, _ = await session.next_prompt(conn, task)
            return old, await tasks.brief(conn, task), prompt, resume

    old, b, prompt, resume = run(go())
    assert resume == SESSION and b.workspace == str(old) and "rebuilt" not in prompt


def test_a_reprovisioned_tasks_ports_are_taken_and_its_services_swept(dsn, tmp_path, monkeypatch):
    """The latest workspace row decides (`workspace.removed`): a removal
    followed by a provisioning is a live workspace again."""
    port = 40000 + uuid.uuid4().int % 9000
    mirror = tmp_path / "t" / "kernel.git"
    project = {"name": "x", "ports": {"postgres": port}, "services": ["postgres"]}
    task = run(new_task(dsn, project=project, mirror=str(mirror)))
    monkeypatch.setattr(runs, "marked_services", lambda ids: [i for i in ids if i == task])
    monkeypatch.setattr(workspace, "stop_services", lambda t, lay: [{"service": "postgres"}])

    async def go():
        async with await db.connect(dsn) as conn:
            await ledger.append(conn, task, "workspace.removed", {"path": str(mirror.parent)})
            gone = (port in await workspace.taken_ports(conn), await workspace.sweep(conn, "nothing"))
            await ledger.append(conn, task, "workspace.provisioned", {"fields": {"mirror": str(mirror)}})
            back = (port in await workspace.taken_ports(conn), await workspace.sweep(conn, "nothing"))
            return gone, back

    (gone_taken, gone_swept), (back_taken, back_swept) = run(go())
    assert not gone_taken and gone_swept == []
    assert back_taken and [s["task"] for s in back_swept] == [task]
