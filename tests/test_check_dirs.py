"""A check's directory lasts as long as its run: its checkout and venv go
when the run ends, its `<name>.<step>.out` logs stay (the Captain's disk
filled three times on 2026-11-10 with every check run of every task kept
until the task's reclaim)."""

import asyncio
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import checks, workspace
from core.settings import settings
from tests import scripted

pytestmark = pytest.mark.spend(usd=0)

TASK = "a1b2c3d4e5f6"
JUNIT = '<testsuite><testcase classname="t" name="a"/></testsuite>'


def git(repo: Path, *args: str) -> str:
    return subprocess.run([settings.git_bin, "-C", str(repo), *args], check=True, capture_output=True,
                          text=True).stdout.strip()  # fmt: skip


def test_a_fresh_directory_goes_when_its_run_ends_and_its_logs_stay(tmp_path):
    lay = workspace.Layout(tmp_path / TASK)

    async def go(fail: bool):
        async with workspace.fresh(lay.checks / "review-x") as check_dir:
            assert (check_dir / "tmp").is_dir() and (check_dir / "claude").is_dir()
            (check_dir / "repo" / ".venv").mkdir(parents=True)
            (lay.checks / "review-x.setup-0.out").write_text("log")
            if fail:
                raise ValueError("the run failed")
            return "verdict"

    assert asyncio.run(go(False)) == "verdict"
    assert not (lay.checks / "review-x").exists()
    with pytest.raises(ValueError):
        asyncio.run(go(True))
    assert not (lay.checks / "review-x").exists()
    assert (lay.checks / "review-x.setup-0.out").read_text() == "log"


def test_a_removal_that_fails_leaves_the_runs_result(tmp_path, monkeypatch, capsys):
    lay = workspace.Layout(tmp_path / TASK)
    real = workspace.rmtree

    async def go():
        async with workspace.fresh(lay.checks / "critique-x"):
            monkeypatch.setattr(workspace, "rmtree", lambda p: (_ for _ in ()).throw(OSError(66, "busy")))
            return "recorded"

    assert asyncio.run(go()) == "recorded"
    assert "critique-x: not removed after its run" in capsys.readouterr().err
    monkeypatch.setattr(workspace, "rmtree", real)

    async def again():  # the next run's fresh directory clears what was left
        async with workspace.fresh(lay.checks / "critique-x") as check_dir:
            return sorted(p.name for p in check_dir.iterdir())

    assert asyncio.run(again()) == ["claude", "codex", "pi", "tmp"]


@pytest.fixture
def made(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "_valor_key", lambda: None)
    # A turn cannot nest sandbox-exec, so the suite runs unsandboxed here.
    monkeypatch.setattr(workspace, "sandboxed", lambda profile, mark, *argv: list(argv))
    ws, origin = scripted.workspace(tmp_path)
    scripted.commit(ws, "a.txt", "a\n", "a commit")
    git(ws, "push", "-q", "origin", "HEAD:refs/heads/main")
    work = tmp_path / "work"
    spec = workspace.Spec(name="app", repo=str(origin), kind="plain", suite="true")
    workspace.provision(TASK, spec, {}, work=work)
    lay = workspace.layout(TASK, work)
    return lay, git(ws, "rev-parse", "HEAD")


def suite(lay, sha, command, *, stop_after=None):
    b = SimpleNamespace(mirror=str(lay.mirror), project={"suite": command, "setup": ["true"]})

    async def go():
        stop = asyncio.create_task(asyncio.sleep(3600 if stop_after is None else stop_after))
        try:
            return await checks.suite(SimpleNamespace(task_id=TASK), lay, b, sha, "head", stop, digest="d")
        finally:
            stop.cancel()

    return asyncio.run(go())


def test_a_suite_runs_checkout_goes_after_its_junit_is_read(made):
    lay, sha = made
    command = f"printf '{JUNIT}' > {{junit}}"
    for _ in range(2):  # a second run at the same commit checks out again
        ran = suite(lay, sha, command)
        assert ran["exit"] == 0 and ran["tests"] is not None and ran["cause"] is None, ran
        assert not (lay.checks / f"test-head-{sha[:12]}").exists()
    names = {p.name for p in lay.checks.iterdir()}
    assert {f"test-head-{sha[:12]}.suite.out", f"test-head-{sha[:12]}.setup-0.out"} <= names


def test_a_failed_setup_and_a_stop_leave_no_checkout(made):
    lay, sha = made
    b = SimpleNamespace(mirror=str(lay.mirror), project={"suite": "true", "setup": ["exit 4"]})

    async def failed_setup():
        stop = asyncio.create_task(asyncio.sleep(3600))
        try:
            return await checks.suite(SimpleNamespace(task_id=TASK), lay, b, sha, "head", stop, digest="d")
        finally:
            stop.cancel()

    ran = asyncio.run(failed_setup())
    assert ran["setup"]["ok"] is False and ran["exit"] == 4
    assert not (lay.checks / f"test-head-{sha[:12]}").exists()
    with pytest.raises(checks._Stopped):
        suite(lay, sha, "sleep 30", stop_after=0.5)
    assert not (lay.checks / f"test-head-{sha[:12]}").exists()
