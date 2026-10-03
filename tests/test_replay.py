"""The replay driver's push rule, on real Postgres and real git: a held
`push_branch` is approved and released when the workspace pushes to the
run's own bare origin, and left held for Tom when its push URL points
anywhere else. The URL is the one in the kernel's record of the task. Every merge effect is skipped: the driver never answers a
merge, and a held merge is not an effect left for Tom.

The driver reaches the kernel through `python -m core`, pointed here at the
test database with `VALOR_DB`.

Live spend: none.
"""

import asyncio
import subprocess
import sys
from pathlib import Path

import pytest
from tests.conftest import TEST_DB

from core import broker, db, tasks
from tests.emulator import common as replay_common
from tests.emulator import replay
from tests.emulator import workspace as replay_workspace

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
    return {"workdir": str(workdir), "origin": str(origin), "task_dir": str(run)}


async def _held_push(dsn: str, ws: dict) -> str:
    from tools.push_branch import PushBranch

    async with await db.connect(dsn) as conn:
        task = await tasks.start(
            conn,
            tasks.Brief(
                instruction="push", max_effect_class="act", workspace=ws["workdir"], push_url=ws["origin"]
            ),
        )
        held = await broker.request(
            conn,
            broker.Performers(PushBranch(ws["workdir"], url=ws["origin"])),
            task,
            broker.Action("push_branch", "valor/work", {"head_sha": git(ws["workdir"], "rev-parse", "HEAD")}),
        )
        assert held.kind == "pending"
        return task


def test_the_driver_releases_local_pushes_and_leaves_any_other_held(dsn, tmp_path, monkeypatch):
    """The rule reads the kernel's record of the task, never the workdir's
    git config, which a turn can rewrite."""
    monkeypatch.setenv("VALOR_DB", TEST_DB)
    stranger = tmp_path / "stranger.git"
    subprocess.run(["git", "init", "-q", "--bare", str(stranger)], check=True)
    local, elsewhere = _run(tmp_path, "local"), _run(tmp_path, "elsewhere")
    # The record pushes somewhere else; the workdir's own config still names
    # the run's origin, and is not what the rule reads.
    elsewhere["origin"] = str(stranger)
    local_task = asyncio.run(_held_push(dsn, local))
    other_task = asyncio.run(_held_push(dsn, elsewhere))

    log: list = []
    assert replay.release_pushes(local_task, local, log) == []
    assert git(local["origin"], "rev-parse", "valor/work") == git(local["workdir"], "rev-parse", "HEAD")
    assert [entry["step"] for entry in log] == ["push released"]

    left = replay.release_pushes(other_task, elsewhere, log)
    assert len(left) == 1 and log[-1]["push_url"] == str(stranger)
    assert subprocess.run(
        ["git", "-C", str(stranger), "rev-parse", "valor/work"], capture_output=True, check=False
    ).returncode


def test_replays_share_the_machine_in_slots(tmp_path, monkeypatch):
    """Up to SLOTS drivers run at once; the next waits for a free slot."""
    import threading
    import time

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


async def _held_merge(conn, task: str, url: str, head: str = "x") -> str:
    from core import ledger

    effect_id = ledger.new_id()
    payload = {
        "url": url,
        "target_branch": "main",
        "head_sha": head,
        "candidate": {"sha": head, "turn_id": "t"},
    }
    await ledger.append(
        conn,
        task,
        "effect.held",
        {"effect_id": effect_id, "action_type": "merge", "effect_class": "act", "target": "main",
         "payload": payload, "payload_sha256": ledger.digest(payload), "idempotency_key": ledger.new_id(),
         "adds_governance": False},
    )  # fmt: skip
    return effect_id


def test_the_driver_skips_every_merge_and_leaves_any_other_held_effect(dsn, tmp_path, monkeypatch):
    """A held merge, whatever URL it carries, is neither answered nor an
    effect left for Tom; neither is an earlier one a second held merge
    superseded. A held effect of another action is left, and ends the run."""
    monkeypatch.setenv("VALOR_DB", TEST_DB)
    ws = _run(tmp_path, "merge")

    async def held() -> tuple[str, str]:
        async with await db.connect(dsn) as conn:
            task = await tasks.start(
                conn, tasks.Brief(instruction="merge", max_effect_class="act", workspace=ws["workdir"])
            )
            await _held_merge(conn, task, str(tmp_path / "stranger.git"), head="a" * 40)
            await _held_merge(conn, task, ws["origin"], head="b" * 40)
            return task

    task = asyncio.run(held())
    log: list = []
    assert replay.release_pushes(task, ws, log) == [] and log == []
    pending = [line for line in replay.core("pending").splitlines() if task in line]
    assert len(pending) == 2  # both still held: nothing answered them

    async def other() -> str:
        from core import ledger

        async with await db.connect(dsn) as conn:
            payload = {"text": "hi"}
            await ledger.append(
                conn,
                task,
                "effect.held",
                {"effect_id": ledger.new_id(), "action_type": "send_message", "effect_class": "act",
                 "target": "tom", "payload": payload, "payload_sha256": ledger.digest(payload),
                 "idempotency_key": ledger.new_id(), "adds_governance": False},
            )  # fmt: skip

    asyncio.run(other())
    left = replay.release_pushes(task, ws, log)
    assert (
        len(left) == 1
        and log[-1]["step"] == "held effect left for Tom"
        and "send_message" in log[-1]["effect"]
    )


def test_a_replay_workspace_is_provisioned_by_the_kernel_and_never_touches_the_shared_cluster(
    dsn, tmp_path, monkeypatch
):
    import json

    from tests import scripted

    monkeypatch.setattr(replay_workspace, "DEMO", tmp_path / "demo")
    monkeypatch.setenv("VALOR_DB", TEST_DB)
    monkeypatch.setenv("VALOR_WORK", str(tmp_path / "work"))
    src = scripted.toy_repo(tmp_path)
    base = git(src, "rev-parse", "HEAD")
    info = replay_workspace.build(str(src), base, "toy-1-bare", ["postgres", "redis"], max_output_tokens=2048)
    spec = (tmp_path / "demo" / "runs" / "toy-1-bare" / "project.toml").read_text()
    assert 'target_branch = "main"' in spec and "max_output_tokens = 2048" in spec
    root = Path(__file__).resolve().parent.parent

    def core(*args):
        done = subprocess.run([sys.executable, "-m", "core", *args], cwd=root, capture_output=True, text=True,
                              check=False)  # fmt: skip
        assert done.returncode == 0, done.stderr
        return done.stdout.strip()

    task = core("start", "go", "--ceiling", "act", "--project", info["spec"], "--base", base)
    ws = replay_workspace.attach({**info, "task_id": task}, json.loads(core("workspace", "show", task)))
    assert Path(ws["workdir"]).is_dir() and ws["origin"].endswith("origin.git")
    harness = json.loads(Path(ws["harness_config"]).read_text())
    assert harness["max_output_tokens"] == 2048
    env = json.dumps(harness["env"])
    assert "5439" not in env and "test:test" not in env and "PGPASSWORD" not in env
    ports = ws["project"]["ports"]
    assert ports["postgres"] != 5439 and ports["redis"] not in range(6390, 6400)
    replay_workspace.teardown("toy-1-bare")
    assert not Path(ws["task_dir"]).exists() and not (tmp_path / "demo" / "runs" / "toy-1-bare").exists()

    async def removed():
        async with await db.connect(dsn) as conn:
            rows = [r["type"] for r in await __import__("core.ledger", fromlist=["read"]).read(conn, task)]
        return rows

    rows = asyncio.run(removed())
    assert "task.stopped" in rows and "workspace.removed" in rows


def test_a_replay_spec_carries_the_items_setup_suite_and_env(tmp_path, monkeypatch):
    import tomllib

    from core import workspace
    from tests import scripted

    monkeypatch.setattr(replay_workspace, "DEMO", tmp_path / "demo")
    src = scripted.toy_repo(tmp_path)
    base = git(src, "rev-parse", "HEAD")
    spec_of = lambda run: workspace.Spec.from_dict(
        tomllib.loads((tmp_path / "demo" / "runs" / run / "project.toml").read_text())
    )
    replay_workspace.build(str(src), base, "bare", [])
    bare = spec_of("bare")
    assert (bare.kind, bare.suite, bare.setup, bare.env) == ("plain", "true", (), {})
    suite = "uv run pytest -q --junitxml={junit} tests"
    replay_workspace.build(str(src), base, "full", [], kind="python-uv", setup=["uv sync --frozen --extra dev"],
                           suite=suite, env={"UV_PYTHON": "3.12"})  # fmt: skip
    full = spec_of("full")
    assert (full.kind, full.suite, full.setup, full.env) == (
        "python-uv",
        suite,
        ("uv sync --frozen --extra dev",),
        {"UV_PYTHON": "3.12"},
    )
