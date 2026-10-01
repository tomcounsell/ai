"""The replay driver's push rule, on real Postgres and real git: a held
`push_branch` is approved and released when the workspace pushes to the
run's own bare origin, and left held for Tom when its push URL points
anywhere else.

The driver reaches the kernel through `python -m core`, pointed here at the
test database with `VALOR_DB`.

Live spend: none.
"""

import asyncio
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import TEST_DB

from core import broker, db, tasks

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import replay

pytestmark = pytest.mark.spend(usd=0)


def git(cwd, *args) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def _run(tmp_path: Path, name: str) -> dict:
    run = tmp_path / name
    workdir, origin = run / "repo", run / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "valor/work", str(workdir)], check=True)
    git(
        workdir,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@example.com",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "x",
    )
    git(workdir, "remote", "add", "origin", str(origin))
    return {"workdir": str(workdir), "origin": str(origin)}


async def _held_push(dsn: str, ws: dict) -> str:
    async with await db.connect(dsn) as conn:
        task = await tasks.start(
            conn,
            tasks.Brief(
                instruction="push", budget_usd_micros=0, max_effect_class="act", workspace=ws["workdir"]
            ),
        )
        held = await broker.request(
            conn,
            task,
            broker.Action("push_branch", "valor/work", {"head_sha": git(ws["workdir"], "rev-parse", "HEAD")}),
        )
        assert held.kind == "pending"
        return task


def test_the_driver_releases_local_pushes_and_leaves_any_other_held(dsn, tmp_path, monkeypatch):
    from tools.push_branch import PushBranch

    monkeypatch.setenv("VALOR_DB", TEST_DB)
    local, elsewhere = _run(tmp_path, "local"), _run(tmp_path, "elsewhere")
    broker.register(PushBranch(local["workdir"]))
    local_task = asyncio.run(_held_push(dsn, local))
    stranger = tmp_path / "stranger.git"
    subprocess.run(["git", "init", "-q", "--bare", str(stranger)], check=True)
    git(elsewhere["workdir"], "remote", "set-url", "--push", "origin", str(stranger))
    broker.register(PushBranch(elsewhere["workdir"]))
    other_task = asyncio.run(_held_push(dsn, elsewhere))

    log: list = []
    assert replay.release_pushes(local_task, local, log) == []
    assert git(local["origin"], "rev-parse", "valor/work") == git(local["workdir"], "rev-parse", "HEAD")
    assert [entry["step"] for entry in log] == ["push released"]

    left = replay.release_pushes(other_task, elsewhere, log)
    assert len(left) == 1 and log[-1]["push_urls"] == [str(stranger)]
    assert subprocess.run(
        ["git", "-C", str(stranger), "rev-parse", "valor/work"], capture_output=True, check=False
    ).returncode
