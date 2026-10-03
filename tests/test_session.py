"""The working session on real Postgres and real git: a thin request asks,
Tom's answer lands in the session that asked, the plan is recorded from a
committed file, the build's candidate goes through the checks to a merge
held for Tom, and feedback after the merge patches in the same session.

No model call: each turn is a scripted subprocess (`tests/scripted.py`)
that plays its stage by writing `.valor/` files and committing, the way a
`claude -p` turn would. Verdicts for stages with no runner yet are recorded
by hand, as `python -m core verdict` records them.
"""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from core import broker, db, ledger, router, session, signals, spending, tasks
from core.gateway import Gateway
from harnesses import claude_code
from tests import judgement_upstream, scripted
from tests.conftest import TEST_DB
from tests.scripted import git
from tools.push_branch import PushBranch

pytestmark = pytest.mark.spend(usd=0)

ROOT = Path(__file__).resolve().parent.parent


def run(coro):
    return asyncio.run(coro)


async def drive(dsn, task) -> dict:
    gateway = Gateway(dsn)
    await gateway.start()
    try:
        return await router.run(gateway, task, scripted.RUNNERS, dsn=dsn)
    finally:
        await gateway.close()


def test_a_thin_request_asks_and_the_answer_resumes_the_session_that_asked(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)

    async def go():
        task = await scripted.start(dsn, ws, judge="thin")
        first = await drive(dsn, task)
        again = await drive(dsn, task)  # runs no turn
        async with await db.connect(dsn) as conn:
            with pytest.raises(LookupError):
                await session.answer(conn, "no-such-task", "x")
            await session.answer(conn, task, "Morning, Tom.")
            with pytest.raises(LookupError):
                await session.answer(conn, task, "a second answer to nothing")
        second = await drive(dsn, task)
        return task, first, again, second

    _task, first, again, second = run(go())
    assert first["status"] == "waiting" and again["status"] == "waiting"
    assert second["status"] == "no runner" and second["missing"] == ["critique"]
    st = second["state"]
    assert st["state"] == "critique" and st["plan"]["path"] == "docs/plan.md"
    assert st["plan"]["review_rounds"] == 1 and st["plan"]["commit"] == git(ws, "rev-parse", "HEAD")
    t = scripted.turns(ws)
    assert [x["stage"] for x in t] == ["clarify", "clarify", "plan"]
    assert t[0]["prompt"] == "Write Tom a greeting." and t[0]["resume"] == ""
    assert t[1]["prompt"] == "# Tom's answer\n\nMorning, Tom." and t[1]["resume"] == "session-1"
    assert t[2]["prompt"].startswith("# No material question") and t[2]["resume"] == "session-1"
    for x in t:
        assert "# Corrections from Tom" in x["brief"] and "# How this task reaches Tom" in x["brief"]
    assert "push_branch" in t[0]["brief"] and "`merge`" not in t[0]["brief"]
    # the judge's verdict is the kernel's reading of a judgement row: no attention spent
    assert (st["attention_counts"]["question"]["total"], st["attention_counts"]["verdict"]["total"]) == (1, 0)


def test_a_candidate_reaches_a_held_merge_and_feedback_after_the_merge_patches(dsn, tmp_path):
    ws, origin = scripted.workspace(tmp_path)
    scripted.steer(ws, push="valor/greeting")

    async def go():
        task = await scripted.start(dsn, ws)
        planned = await drive(dsn, task)
        await scripted.critique(dsn, task)
        built = await drive(dsn, task)
        await scripted.checks(dsn, task)
        delivered = await drive(dsn, task)
        st = delivered["state"]
        merge = next(
            e for e, s in st["effects"].items() if s == "pending" and e == st["merge_effect"]["effect_id"]
        )
        push = next(e for e, s in st["effects"].items() if s == "pending" and e != merge)
        async with await db.connect(dsn) as conn:
            await broker.approve(conn, push, note="push it")
            await broker.release(conn, push)
            await broker.approve(conn, merge, note="merge it")
            merged = await broker.release(conn, merge)
            await session.feedback(conn, task, "Greet him by name.")
        patched = await drive(dsn, task)
        return task, planned, built, delivered, merged, patched

    _task, planned, built, delivered, merged, patched = run(go())
    assert planned["status"] == "no runner" and built["missing"] == ["test", "review", "docs"]
    cand = built["state"]["candidate"]
    assert cand["sha"] == git(ws, "rev-parse", "HEAD~1")  # the patch committed on top since
    assert delivered["status"] == "delivered" and delivered["state"]["delivery"]["outcome"] == "passed"
    assert "greeting.txt" in delivered["state"]["delivered"]
    assert merged.kind == "done" and git(origin, "rev-parse", "main") == cand["sha"]
    assert git(origin, "rev-parse", "valor/greeting") == cand["sha"]
    assert patched["status"] == "no runner" and patched["state"]["candidate"]["sha"] != cand["sha"]
    t = scripted.turns(ws)
    assert [x["stage"] for x in t] == ["plan", "build", "patch"]
    assert t[1]["prompt"].startswith("# Critique: sound")
    assert t[2]["prompt"].startswith("# Tom's feedback on the delivery\n\nGreet him by name.")
    assert all(x["resume"] == "session-1" for x in t[1:])
    assert "Plan: docs/plan.md" in t[1]["brief"] and "# Stage: build" in t[1]["brief"]


def test_a_failed_turn_leaves_the_answer_for_the_next_turn(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)

    async def go():
        task = await scripted.start(dsn, ws, judge="thin")
        outs = [await drive(dsn, task)]
        async with await db.connect(dsn) as conn:
            await session.answer(conn, task, "Morning, Tom.", by="stand-in", role_played=True)
        scripted.steer(ws, fail_next=True)
        outs.append(await drive(dsn, task))
        outs.append(await drive(dsn, task))
        return outs

    outs = run(go())
    assert [o["status"] for o in outs] == ["waiting", "failed", "no runner"]
    prompts = [x["prompt"] for x in scripted.turns(ws)]
    answered = "# Tom's answer\n\nMorning, Tom."
    assert prompts[1] == answered and prompts[2] == answered  # sent again after the failed turn
    question = outs[2]["state"]["attention"][0]
    assert question["provenance"]["by"] == "stand-in" and question["provenance"]["role_played"] is True


def test_two_turns_with_no_signal_return_idle_and_the_next_prompt_says_continue(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)

    async def go():
        task = await scripted.start(dsn, ws)
        await drive(dsn, task)
        await scripted.critique(dsn, task)
        scripted.steer(ws, build="nothing")
        return await drive(dsn, task)

    out = run(go())
    assert out["status"] == "idle" and out["state"]["state"] == "build"
    t = scripted.turns(ws)
    assert t[-2]["prompt"].startswith("# Critique: sound") and t[-1]["prompt"] == "Continue."


def test_a_stopped_task_takes_no_feedback_and_an_open_question_takes_an_answer(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)

    async def go():
        task = await scripted.start(dsn, ws, judge="thin")
        waiting = await drive(dsn, task)
        async with await db.connect(dsn) as conn:
            with pytest.raises(LookupError, match="open question"):
                await session.feedback(conn, task, "feedback while a question is open")
            await tasks.stop(conn, task, reason="test")
            with pytest.raises(LookupError, match="stopped"):
                await session.feedback(conn, task, "feedback on a stopped task")
            with pytest.raises(LookupError):
                await session.answer(conn, task, "an answer to a stopped task")
            return waiting, await ledger.read(conn, task)

    waiting, rows = run(go())
    assert waiting["status"] == "waiting"
    assert not [r for r in rows if r["type"] in ("feedback.given", "question.answered")]


def test_an_unread_effect_request_and_continue_carry_the_outcome_into_the_next_prompt(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)
    (ws / ".valor" / "effects").mkdir(parents=True)
    (ws / ".valor" / "effects" / "bad.json").write_text("not json")
    (ws / ".valor" / "effects" / "send.json").write_text(
        json.dumps({"action_type": "no_such_action", "target": "tom", "payload": {}})
    )
    (ws / ".valor" / "effects" / "merge.json").write_text(
        json.dumps({"action_type": "merge", "target": "main", "payload": {"head_sha": "x"}})
    )

    async def go():
        task = await scripted.start(dsn, ws)
        found = signals.collect(ws, "turn-1")
        async with await db.connect(dsn) as conn:
            await ledger.append(conn, task, "turn.started", {"turn_id": "turn-1", "state": "plan"})
            await ledger.append(
                conn,
                task,
                "turn.ended",
                {"turn_id": "turn-1", "outcome": "done", "result": {"session_id": "s"}},
            )
            verdict = await session.record(conn, task, "turn-1", found, state=tasks.machine.State.PLAN,
                                           workspace=str(ws))  # fmt: skip
            return verdict, await session.next_prompt(conn, task), await tasks.status(conn, task)

    verdict, (prompt, resume), state = run(go())
    assert verdict == "idle" and resume == "s" and prompt.startswith("Continue.")
    assert "bad.json: unreadable request" in prompt
    assert "no_such_action -> tom" in prompt and "refused: no performer" in prompt
    assert "merge.json: the merge is the kernel's to request" in prompt
    assert state["state"] == "plan" and list(state["effects"].values()) == ["refused"]
    assert list((ws / ".valor" / "handled" / "turn-1" / "effects").iterdir())


def test_opus_5_5_has_its_own_price_and_one_hour_cache_writes_cost_double_input():
    price = spending.prices("claude-opus-5-5")
    assert price["input"] == 4_000_000 and price["output"] == 20_000_000
    assert spending.prices("claude-opus-5-20260101")["input"] == 5_000_000
    usage = {
        "cache_creation_input_tokens": 1_000_000,
        "cache_creation": {"ephemeral_5m_input_tokens": 250_000, "ephemeral_1h_input_tokens": 750_000},
    }
    assert spending.cost(usage, price) == round(0.25 * 5_000_000 + 0.75 * 8_000_000)
    assert spending.cost({"cache_creation_input_tokens": 1_000_000}, price) == 8_000_000


def test_a_workspace_turn_resumes_runs_sandboxed_and_carries_no_credentials(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "secret")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/agent")
    build = claude_code.workspace_turn(
        "Continue.",
        cwd="/w",
        resume="abc",
        model="opus",
        harness={
            "sandbox_profile": "/p.sb",
            "gitconfig": "/g",
            "gh_config_dir": "/gh",
            "env": {"TEST_DB_PORT": "5439"},
        },
    )
    command = build("http://127.0.0.1:4321/t/token", "# Brief", "turn1")
    argv = command.argv
    assert argv[:7] == [
        "/usr/bin/sandbox-exec",
        "-D",
        "GATEWAY_PORT=4321",
        "-D",
        "VALOR_TURN=turn1",
        "-f",
        "/p.sb",
    ]
    assert argv[argv.index("--resume") + 1] == "abc"
    assert argv[argv.index("--system-prompt-snapshot") + 1] == "off"
    assert argv[argv.index("--append-system-prompt") + 1] == "# Brief"
    assert "GH_TOKEN" not in command.env and "SSH_AUTH_SOCK" not in command.env
    assert command.env["GH_CONFIG_DIR"] == "/gh" and command.env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert command.env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:4321/t/token"
    assert command.env["TEST_DB_PORT"] == "5439"
    assert command.env["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] == "1"


def test_a_workspace_turn_without_a_sandbox_profile_is_refused_before_anything_runs(dsn, tmp_path):
    for harness in (None, {}, {"gitconfig": "/g"}, {"sandbox_profile": ""}):
        with pytest.raises(claude_code.Unsandboxed):
            claude_code.workspace_turn("hi", cwd=str(tmp_path), harness=harness)

    async def start():
        async with await db.connect(dsn) as conn:
            return await tasks.start(
                conn,
                tasks.Brief(instruction="x", workspace=str(tmp_path)),
            )

    task = run(start())
    jev, ow = judgement_upstream.shared().urls(fixed="precise")
    out = subprocess.run(
        [sys.executable, "-m", "core", "run", task],
        cwd=ROOT,
        env={**os.environ, "VALOR_DB": TEST_DB, "VALOR_JEV_URL": jev, "VALOR_OPEN_WEIGHT_URL": ow},
        capture_output=True,
        text=True,
        check=False,
    )
    assert out.returncode != 0 and "sandbox profile" in out.stderr

    async def rows():
        async with await db.connect(dsn) as conn:
            return [r["type"] for r in await ledger.read(conn, task)]

    got = run(rows())  # the judge ran through the local upstream; no turn started
    assert got[0] == "task.started" and "judge.decided" in got and "turn.started" not in got
    assert not (tmp_path / ".valor").exists()


def test_a_push_runs_nothing_the_workspace_config_or_hooks_name(tmp_path):
    ws, origin = scripted.workspace(tmp_path)
    marker = tmp_path / "ran"
    hook = f"#!/bin/sh\necho $0 >> {marker}\n"
    for path in (ws / ".git" / "hooks" / "pre-push", tmp_path / "hooks" / "pre-push"):
        path.parent.mkdir(exist_ok=True)
        path.write_text(hook)
        path.chmod(0o755)
    receive = tmp_path / "receive"
    receive.write_text(f'#!/bin/sh\necho receive >> {marker}\nexec git-receive-pack "$@"\n')
    receive.chmod(0o755)
    head = git(ws, "rev-parse", "HEAD").strip()

    # A hook in the default directory is pinned off; a hooks path or a
    # receive program in the workspace's config refuses the push outright.
    pushed = PushBranch(ws).perform(broker.Action("push_branch", "valor/x", {"head_sha": head}), "key")
    assert pushed["sha"] == head and git(origin, "rev-parse", "valor/x").strip() == head
    git(ws, "config", "core.hooksPath", str(tmp_path / "hooks"))
    git(ws, "config", "remote.origin.receivepack", str(receive))
    with pytest.raises(ValueError, match="core.hookspath"):
        PushBranch(ws).perform(broker.Action("push_branch", "valor/y", {"head_sha": head}), "key")
    assert not marker.exists()


def test_a_request_that_starts_with_a_dash_reaches_claude_as_the_prompt(tmp_path):
    """Tom's request for pso-a began "- Create new flag ..."; as a bare argv
    element after `-p`, claude rejected it ("unknown option") and the turn
    failed before any model call. The prompt now follows `--`. The real CLI
    parses it: pointed at a dead gateway, it is still retrying after two
    seconds, not exiting on an option error."""
    request = "- Create new flag, separate from the old one"
    for build in (
        claude_code.turn(request, cwd=str(tmp_path)),
        claude_code.workspace_turn(
            request, cwd=str(tmp_path), resume="abc", harness={"sandbox_profile": "/p.sb"}
        ),
    ):
        argv = build("http://127.0.0.1:9/t/x", "# Brief", "turn1").argv
        assert argv[-2:] == ["--", request]
    argv = claude_code.turn(request, cwd=str(tmp_path))("http://127.0.0.1:9/t/x", "# Brief", "turn1").argv
    env = {"PATH": os.environ["PATH"], "HOME": os.environ["HOME"], "ANTHROPIC_BASE_URL": "http://127.0.0.1:9"}
    proc = subprocess.Popen(
        argv, cwd=tmp_path, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    try:
        out, _ = proc.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate()
    assert "unknown option" not in out
