"""A check's directory lasts as long as its run: its checkout and venv go
when the run ends, its `<name>.<step>.out` logs stay (the Captain's disk
filled three times on 2026-11-10 with every check run of every task kept
until the task's reclaim)."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import binaries, checks, db, fresh, ledger, router, workspace
from core.machine import State
from core.settings import settings
from tests import bridges, scripted

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


# -- the fresh runners: critique, review, docs -------------------------------------------------

# A fresh session's turn: prints its verdict as Claude Code's result; with
# `commit`, first commits a doc in its checkout and names that head.
VERDICT_TURN = r"""
import json, subprocess, sys
verdict, commit = json.loads(sys.argv[1]), sys.argv[2] == "1"
if commit:
    open("docs.md", "w").write("documented\n")
    git = [sys.argv[3], "-c", "user.name=d", "-c", "user.email=d@example.com"]
    subprocess.run([*git, "add", "docs.md"], check=True)
    subprocess.run([*git, "commit", "-qm", "docs"], check=True)
    verdict["head"] = subprocess.run([*git, "rev-parse", "HEAD"], check=True, capture_output=True,
                                     text=True).stdout.strip()
print(json.dumps({"result": json.dumps(verdict) if verdict else "no verdict here", "is_error": False,
                  "session_id": "00000000-0000-4000-8000-0000000000bb"}))
"""


def fresh_for(verdict: dict, commit: bool = False):
    from harnesses import claude_code

    def for_(prompt, checkout, model, harness, harness_name):
        def build(url, brief, turn_id):
            return claude_code.TurnCommand(
                argv=[sys.executable, "-c", VERDICT_TURN, json.dumps(verdict), "1" if commit else "0",
                      settings.git_bin],
                env={**harness["env"], "PATH": os.environ["PATH"], "HOME": os.environ["HOME"]},
                cwd=checkout, harness="script", parse=claude_code.parse, tmpdir=harness["tmpdir"],
            )  # fmt: skip

        return build

    return for_


@pytest.fixture
def provisioned(dsn, tmp_path, monkeypatch):
    """A task in a provisioned workspace with a plan commit and a candidate
    after it, both in its mirror; `to(state)` writes its rows to there."""
    from tests.test_kernel import new_task
    from tests.test_machine import Ledger

    monkeypatch.setattr(workspace, "_valor_key", lambda: None)
    # A turn cannot nest sandbox-exec: the stand-in runs what follows `-f <profile>`.
    shim = tmp_path / "sandbox-exec"
    shim.write_text('#!/bin/sh\nwhile [ "$1" != "-f" ]; do shift; done\nshift 2\nexec "$@"\n')
    shim.chmod(0o755)
    require = binaries.require
    monkeypatch.setattr(
        binaries, "require", lambda p: str(shim) if p == binaries.SANDBOX_EXEC else require(p)
    )
    _, origin = scripted.workspace(tmp_path)
    spec = workspace.Spec(name="app", repo=str(origin), kind="plain", suite="true")
    task = asyncio.run(new_task(dsn))
    made = workspace.provision(task, spec, {}, work=tmp_path / "work")
    lay = workspace.Layout(Path(made.mirror).parent)
    repo = Path(made.workspace)
    plan = scripted.commit(repo, "docs/plans/p.md", "# the plan\n", "plan")
    candidate = scripted.commit(repo, "app.txt", "built\n", "candidate")
    git(lay.mirror, "fetch", "-q", str(repo), "HEAD:refs/valor/test-head")

    def to(state: State) -> None:
        led = Ledger().plan(commit=plan, path="docs/plans/p.md")
        if state is State.CHECKS:
            led.critique("sound").build(sha=candidate)

        async def write():
            async with await db.connect(dsn) as conn:
                await ledger.append(conn, task, "workspace.provisioned", {"fields": made.brief_fields()})
                for row in led.rows[1:]:
                    p = {k: f"{task}-{v}" if k == "turn_id" else v for k, v in row["payload"].items()}
                    if isinstance(p.get("candidate"), dict):
                        p["candidate"] = {**p["candidate"], "turn_id": f"{task}-{p['candidate']['turn_id']}"}
                    await ledger.append(conn, task, row["type"], p)

        asyncio.run(write())

    return SimpleNamespace(task=task, lay=lay, plan=plan, candidate=candidate, to=to)


def step(dsn, task, runner):
    async def go():
        gateway = await bridges.gateway(dsn)
        try:
            out = await runner(router.Context(gateway, task, dsn, scripted._always))
            async with await db.connect(dsn) as conn:
                return out, await ledger.read(conn, task)
        finally:
            await gateway.close()

    return asyncio.run(go())


def left(lay) -> list[str]:
    """What stays in `checks/` beside the `.out` logs."""
    return (
        sorted(p.name for p in lay.checks.iterdir() if not p.name.endswith(".out"))
        if lay.checks.exists()
        else []
    )


def test_a_critiques_verdict_is_recorded_and_its_folder_goes(dsn, provisioned):
    t = provisioned
    t.to(State.CRITIQUE)
    out, rows = step(dsn, t.task, fresh.critique_runner(fresh_for({"verdict": "sound", "findings": []})))
    assert out == {"status": "moved"}, out
    assert [r["payload"]["verdict"] for r in rows if r["type"] == "critique.decided"] == ["sound"]
    assert left(t.lay) == []


@pytest.mark.parametrize("verdict", [{"verdict": "pass", "findings": []}, {}])
def test_a_reviews_folder_goes_with_a_verdict_and_without_one(dsn, provisioned, verdict):
    t = provisioned
    t.to(State.CHECKS)
    out, rows = step(dsn, t.task, fresh.review_runner(fresh_for(verdict)))
    decided = [r["payload"]["verdict"] for r in rows if r["type"] == "review.decided"]
    if verdict:
        assert out == {"status": "moved"} and decided == ["pass"], out
    else:
        assert out["status"] == "failed" and "no verdict" in out["turn"]["result"] and decided == []
    assert left(t.lay) == []


def test_the_docs_commit_reaches_the_mirror_before_its_folder_goes(dsn, provisioned):
    t = provisioned
    t.to(State.CHECKS)
    out, rows = step(
        dsn, t.task, fresh.docs_runner(fresh_for({"verdict": "updated", "findings": []}, commit=True))
    )
    kept = next(r["payload"] for r in rows if r["type"] == fresh.DOCS_KEPT)
    assert out == {"status": "moved"} and kept["refused"] is None and kept["kept"] != t.candidate, (out, kept)
    assert git(t.lay.mirror, "rev-parse", f"refs/valor/docs/{kept['turn_id']}") == kept["kept"]
    assert git(t.lay.mirror, "show", f"{kept['kept']}:docs.md") == "documented"
    assert left(t.lay) == []
