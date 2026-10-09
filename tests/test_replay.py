"""The replay driver's wiring, on real Postgres and real git: a replay
task's push leaves when requested and a send it asks for has no performer;
the replay workspace and spec.

The driver reaches the kernel through `python -m core`, pointed here at the
test database with `VALOR_DB`.

Live spend: none.
"""

import asyncio
import subprocess
import sys
from pathlib import Path

import pytest

from core import broker, db, tasks
from tests.conftest import TEST_DB
from tests.emulator import common as replay_common
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


def test_a_replay_task_pushes_at_request_and_has_no_send_performer(dsn, tmp_path):
    """A replay task is built with the kernel performers only: its push
    leaves when requested, and a send it asks for meets `no performer`."""
    from core.__main__ import _performers

    ws = _run(tmp_path, "local")

    async def go():
        async with await db.connect(dsn) as conn:
            brief = tasks.Brief(
                instruction="push",
                max_effect_class="act",
                workspace=ws["workdir"],
                push_url=ws["origin"],
                replay=True,
            )
            task = await tasks.start(conn, brief)
            performers = _performers(brief)
            head = git(ws["workdir"], "rev-parse", "HEAD")
            pushed = await broker.request(
                conn, performers, task, broker.Action("push_branch", "valor/work", {"head_sha": head})
            )
            sent = await broker.request(
                conn, performers, task, broker.Action("telegram.send_message", "1", {"text": "hi"})
            )
            return pushed, sent, head

    pushed, sent, head = asyncio.run(go())
    assert pushed.kind == "done" and git(ws["origin"], "rev-parse", "valor/work") == head
    assert sent.kind == "refused" and sent.error == broker.NO_PERFORMER


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


@pytest.mark.macos
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


@pytest.mark.parametrize("fails", ["status", "stand_in"])
def test_a_failed_status_read_or_stand_in_is_logged_and_pauses_the_driver(monkeypatch, fails):
    """`step` records a status read or a stand-in that raises in the run's
    log and pauses, as it does a failed `core run`; the next invocation
    resumes (`docs/emulator.md`)."""
    from tests.emulator import replay

    def boom(*a, **kw):
        raise RuntimeError("claude -p returned no JSON")

    monkeypatch.setattr(replay, "status", boom if fails == "status" else lambda t: {"state": "merged"})
    monkeypatch.setattr(replay, "stand_in", boom)
    monkeypatch.setattr(replay, "core", lambda *a: pytest.fail("no run after a failed step"))
    result = {"task_id": "t1", "log": [], "run": "r", "outcome": None}
    item, ws = {"answer_key": "k"}, {"mirror": "m", "base": "b", "run_dir": "d"}
    args = type("A", (), {"stand_in_model": "x", "max_feedback": 1})()
    replay.step(result, item, ws, args, meter=None)
    step = "status failed" if fails == "status" else "stand-in failed"
    assert result["log"][-1]["step"] == step
    assert result["log"][-1]["error"] == "RuntimeError: claude -p returned no JSON"
    assert result["paused"] == f"{step}: RuntimeError: claude -p returned no JSON"
    assert result["outcome"] is None
