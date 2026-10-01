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
    broker.register(PushBranch(elsewhere["workdir"]))
    other_task = asyncio.run(_held_push(dsn, elsewhere))
    # The workspace's config is rewritten after the push was held, as a turn
    # could rewrite it.
    stranger = tmp_path / "stranger.git"
    subprocess.run(["git", "init", "-q", "--bare", str(stranger)], check=True)
    git(elsewhere["workdir"], "remote", "set-url", "--push", "origin", str(stranger))

    log: list = []
    assert replay.release_pushes(local_task, local, log) == []
    assert git(local["origin"], "rev-parse", "valor/work") == git(local["workdir"], "rev-parse", "HEAD")
    assert [entry["step"] for entry in log] == ["push released"]

    left = replay.release_pushes(other_task, elsewhere, log)
    assert len(left) == 1 and log[-1]["push_urls"] == [str(stranger)]
    assert subprocess.run(
        ["git", "-C", str(stranger), "rev-parse", "valor/work"], capture_output=True, check=False
    ).returncode


def test_replays_share_the_machine_in_slots(tmp_path, monkeypatch):
    """Up to SLOTS drivers run at once; the next waits for a free slot."""
    import threading
    import time

    import replay_common

    monkeypatch.setattr(replay_common, "DEMO", tmp_path)
    monkeypatch.setattr(replay_common, "LOCK", tmp_path / "claude-turn.lock")
    monkeypatch.setattr(replay_common, "SLOTS", 2)
    entered = []

    def third():
        with replay_common.machine_lock("third"):
            entered.append(time.monotonic())

    with replay_common.machine_lock("one"), replay_common.machine_lock("two"):
        t = threading.Thread(target=third)
        t.start()
        time.sleep(1)
        assert entered == []
        released = time.monotonic()
    t.join(timeout=15)
    assert entered and entered[0] >= released


def test_each_redis_run_gets_a_port_no_other_run_holds(tmp_path, monkeypatch):
    import json

    import replay_workspace

    monkeypatch.setattr(replay_workspace, "DEMO", tmp_path)
    for name, info in {"old": {}, "a": {"redis_port": 6391}, "b": {"redis_port": 6393}}.items():
        (tmp_path / "runs" / name).mkdir(parents=True)
        (tmp_path / "runs" / name / "replay.json").write_text(json.dumps(info))
    assert replay_workspace._redis_port(tmp_path / "runs" / "new") == 6392
    assert replay_workspace._redis_port(tmp_path / "runs" / "b") == 6393
    assert replay_workspace._redis_port(tmp_path / "runs" / "old") == 6390


def test_the_driver_checks_a_merge_by_the_url_its_payload_carries(dsn, tmp_path, monkeypatch):
    """A held merge is released when its payload names the run's own origin
    (the URL the kernel recorded at start, which the approval binds), and
    left held when it names any other, whatever the workspace's config
    says now."""
    from core import ledger

    monkeypatch.setenv("VALOR_DB", TEST_DB)
    ws = _run(tmp_path, "merge")

    async def held(url: str) -> str:
        async with await db.connect(dsn) as conn:
            task = await tasks.start(
                conn,
                tasks.Brief(
                    instruction="merge", budget_usd_micros=0, max_effect_class="act", workspace=ws["workdir"]
                ),
            )
            payload = {
                "url": url,
                "target_branch": "main",
                "head_sha": "x",
                "candidate": {"sha": "x", "turn_id": "t"},
            }
            await ledger.append(
                conn,
                task,
                "effect.held",
                {"effect_id": ledger.new_id(), "action_type": "merge", "effect_class": "act", "target": "main",
                 "payload": payload, "payload_sha256": ledger.digest(payload), "idempotency_key": ledger.new_id(),
                 "adds_governance": False},
            )  # fmt: skip
            return task

    elsewhere = asyncio.run(held(str(tmp_path / "stranger.git")))
    log: list = []
    left = replay.release_pushes(elsewhere, ws, log)
    assert len(left) == 1 and log[-1]["step"] == "held effect left for Tom"
    assert log[-1]["push_urls"] == [str(tmp_path / "stranger.git")]
