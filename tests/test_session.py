"""A task's turns on real Postgres: a question waits for Tom, his answer
resumes the same session, a delivery settles the task, and a push it
requested reaches a real bare repository only after his tap.

No model call: each turn is a real Python subprocess that plays Valor's
part by writing `.valor/` files, the way a `claude -p` turn would.
"""

import asyncio
import json
import os
import subprocess
import sys

import pytest

from core import broker, budget, db, ledger, session, signals, tasks
from core.gateway import Gateway
from harnesses import claude_code
from tools.push_branch import PushBranch

pytestmark = pytest.mark.spend(usd=0)

# Asks on its first turn; on the turn that opens with Tom's answer, commits
# the answer, requests a push of that commit, and delivers.
VALOR = r"""
import json, pathlib, subprocess, sys
prompt, resume, brief = sys.argv[1], sys.argv[2], sys.argv[3]
v = pathlib.Path(".valor")
(v / "effects").mkdir(parents=True, exist_ok=True)
log = v / "turns.jsonl"
with log.open("a") as f:
    f.write(json.dumps({"prompt": prompt, "resume": resume, "brief": brief}) + "\n")
if prompt.startswith("Tom answered"):
    pathlib.Path("greeting.txt").write_text(prompt.splitlines()[-1] + "\n")
    git = ["git", "-c", "user.name=Valor", "-c", "user.email=valor@example.com"]
    subprocess.run(git + ["add", "greeting.txt"], check=True)
    subprocess.run(git + ["commit", "-qm", "Greet Tom"], check=True)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    (v / "effects" / "push.json").write_text(json.dumps(
        {"action_type": "push_branch", "target": "valor/greeting", "payload": {"head_sha": sha}}))
    (v / "done.md").write_text("greeting.txt says what Tom asked for, committed and offered for push.")
else:
    (v / "question.md").write_text("Which greeting do you want?")
print(json.dumps({"result": "ok", "session_id": resume or "session-1", "is_error": False}))
"""


def run(coro):
    return asyncio.run(coro)


def git(cwd, *args) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout


def turn_for(prompt, resume, b):
    def build(url, brief, turn_id):
        return claude_code.TurnCommand(
            argv=[sys.executable, "-c", VALOR, prompt, resume or "", brief],
            env={"PATH": os.environ["PATH"], "HOME": os.environ["HOME"]},
            cwd=b.workspace,
            harness="script",
            parse=claude_code.parse,
        )

    return build


@pytest.fixture
def workspace(tmp_path):
    origin = tmp_path / "origin.git"
    ws = tmp_path / "ws"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    subprocess.run(["git", "init", "-q", str(ws)], check=True)
    git(
        ws,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@example.com",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "base",
    )
    git(ws, "remote", "add", "origin", str(origin))
    return ws, origin


async def new_task(dsn, ws, **kw) -> str:
    async with await db.connect(dsn) as conn:
        return await tasks.start(
            conn,
            tasks.Brief(
                instruction="Write Tom a greeting.",
                budget_usd_micros=kw.pop("budget_usd_micros", 1_000),
                max_effect_class="act",
                workspace=str(ws),
                **kw,
            ),
        )


def test_question_answer_delivery_and_a_push_held_for_tom(dsn, workspace):
    ws, origin = workspace
    broker.register(PushBranch(ws))

    async def go():
        task = await new_task(dsn, ws)
        gateway = Gateway(dsn)
        await gateway.start()
        first = await session.run(gateway, task, turn_for, dsn=dsn)
        async with await db.connect(dsn) as conn:
            with pytest.raises(LookupError):
                await session.answer(conn, "no-such-task", "x")
            waiting = await session.run(gateway, task, turn_for, dsn=dsn)  # runs no turn
            await session.answer(conn, task, "Morning, Tom.")
            with pytest.raises(LookupError):
                await session.answer(conn, task, "a second answer to nothing")
        second = await session.run(gateway, task, turn_for, dsn=dsn)
        again = await session.run(gateway, task, turn_for, dsn=dsn)  # runs no turn
        await gateway.close()
        async with await db.connect(dsn) as conn:
            held = next(e for e, s in second["state"]["effects"].items() if s == "pending")
            with pytest.raises(broker.NotApproved):
                await broker.release(conn, held)
            before = git(origin, "branch", "--list")
            await broker.approve(conn, held, note="push it")
            pushed = await broker.release(conn, held)
            return task, first, waiting, second, again, before, pushed, await ledger.read(conn, task)

    _, first, waiting, second, again, before, pushed, rows = run(go())
    assert first["status"] == "waiting" and first["question"]["question"] == "Which greeting do you want?"
    assert waiting["status"] == "waiting"
    assert second["status"] == "delivered" and "greeting.txt" in second["state"]["delivered"]
    assert again["status"] == "delivered"
    assert before == ""
    assert pushed.kind == "done"
    assert git(origin, "rev-parse", "valor/greeting").strip() == git(ws, "rev-parse", "HEAD").strip()

    attention = second["state"]["attention"]
    assert attention == [
        {
            "kind": "question",
            "question_id": attention[0]["question_id"],
            "question": "Which greeting do you want?",
            "answer": "Morning, Tom.",
            "provenance": attention[0]["provenance"],
        }
    ]
    assert attention[0]["provenance"]["by"] == "tom" and attention[0]["provenance"]["role_played"] is False
    answered = next(r["payload"] for r in rows if r["type"] == "question.answered")
    assert answered["provenance"]["by"] == "tom"

    turns = [json.loads(line) for line in (ws / ".valor" / "turns.jsonl").read_text().splitlines()]
    assert len(turns) == 2  # the waiting and delivered runs ran no turn
    assert turns[0]["prompt"] == "Write Tom a greeting." and turns[0]["resume"] == ""
    assert turns[1]["prompt"] == "Tom answered your question:\n\nMorning, Tom."
    assert turns[1]["resume"] == "session-1"
    for t in turns:
        assert "# Corrections from Tom" in t["brief"] and signals.PROTOCOL in t["brief"]
    started = [r["payload"] for r in rows if r["type"] == "turn.started"]
    assert [s["brief"] for s in started] == [t["brief"] for t in turns]
    assert [
        r["type"] for r in rows if r["type"] in ("question.asked", "question.answered", "task.delivered")
    ] == [
        "question.asked",
        "question.answered",
        "task.delivered",
    ]
    assert not (ws / ".valor" / "question.md").exists() and not (ws / ".valor" / "done.md").exists()


# Delivers on its first turn with one effect request. A turn opening with
# Tom's feedback delivers again only when the feedback says "revise", and
# otherwise leaves nothing, so a stale done.md or effect file read again
# would show as a delivery or an effect it did not make.
DELIVERER = r"""
import json, pathlib, sys
prompt, resume = sys.argv[1], sys.argv[2]
v = pathlib.Path(".valor")
(v / "effects").mkdir(parents=True, exist_ok=True)
with (v / "turns.jsonl").open("a") as f:
    f.write(json.dumps({"prompt": prompt, "resume": resume}) + "\n")
if not resume:
    (v / "effects" / "send.json").write_text(
        json.dumps({"action_type": "no_such_action", "target": "tom", "payload": {}}))
    (v / "done.md").write_text("First delivery.")
elif "revise" in prompt:
    (v / "done.md").write_text("Second delivery, revised per Tom's feedback.")
print(json.dumps({"result": "ok", "session_id": resume or "session-1", "is_error": False}))
"""


def deliverer_for(prompt, resume, b):
    def build(url, brief, turn_id):
        return claude_code.TurnCommand(
            argv=[sys.executable, "-c", DELIVERER, prompt, resume or ""],
            env={"PATH": os.environ["PATH"], "HOME": os.environ["HOME"]},
            cwd=b.workspace,
            harness="script",
            parse=claude_code.parse,
        )

    return build


def test_feedback_reopens_a_delivery_and_the_next_run_resumes_the_same_session(dsn, tmp_path):
    async def go():
        task = await new_task(dsn, tmp_path)
        gateway = Gateway(dsn)
        await gateway.start()
        first = await session.run(gateway, task, deliverer_for, dsn=dsn)
        async with await db.connect(dsn) as conn:
            await session.feedback(conn, task, "Please revise the wording.")
            reopened = await tasks.status(conn, task)
            with pytest.raises(LookupError):
                await session.feedback(conn, task, "feedback on an undelivered task")
        second = await session.run(gateway, task, deliverer_for, dsn=dsn)
        async with await db.connect(dsn) as conn:
            await session.feedback(conn, task, "Looks fine, nothing to change.")
        third = await session.run(gateway, task, deliverer_for, dsn=dsn)
        await gateway.close()
        async with await db.connect(dsn) as conn:
            return first, reopened, second, third, await ledger.read(conn, task)

    first, reopened, second, third, rows = run(go())
    assert first["status"] == "delivered" and first["state"]["delivered"] == "First delivery."
    assert reopened["state"] == "live" and reopened["delivered"] == "First delivery."
    assert second["status"] == "delivered"
    assert second["state"]["delivered"] == "Second delivery, revised per Tom's feedback."
    # The resumed turn after the third feedback wrote nothing: the consumed
    # done.md and effect file under .valor/handled/ were not read again.
    assert third["status"] == "idle"
    assert third["state"]["delivered"] == "Second delivery, revised per Tom's feedback."

    turns = [json.loads(line) for line in (tmp_path / ".valor" / "turns.jsonl").read_text().splitlines()]
    assert [t["resume"] for t in turns] == ["", "session-1", "session-1", "session-1"]
    assert turns[1]["prompt"].startswith("Tom reviewed your delivery and, as project manager, sends it back")
    assert "Please revise the wording." in turns[1]["prompt"]
    assert "no_such_action -> tom" in turns[1]["prompt"]
    assert turns[3]["prompt"].startswith("Continue.")

    collected = [r["payload"] for r in rows if r["type"] == "turn.collected"]
    assert [len(c["effects"]) for c in collected] == [1, 0, 0, 0]
    assert [c["done"] for c in collected[2:]] == [None, None]
    assert [r["payload"]["summary"] for r in rows if r["type"] == "task.delivered"] == [
        "First delivery.",
        "Second delivery, revised per Tom's feedback.",
    ]
    given = [r["payload"] for r in rows if r["type"] == "feedback.given"]
    assert given[0]["provenance"]["by"] == "tom" and given[0]["provenance"]["at"]
    assert given[0]["on_delivery"] == "First delivery."

    attention = third["state"]["attention"]
    assert [(a["kind"], a["feedback"]) for a in attention] == [
        ("feedback", "Please revise the wording."),
        ("feedback", "Looks fine, nothing to change."),
    ]


# Asks on its first turn and delivers on every turn that opens with an answer
# or feedback; exits 1 without a result when `.valor/fail_next` exists (and
# removes it), the way a turn the sandbox or the network broke would end.
FLAKY = r"""
import json, pathlib, sys
prompt, resume = sys.argv[1], sys.argv[2]
v = pathlib.Path(".valor")
v.mkdir(exist_ok=True)
with (v / "turns.jsonl").open("a") as f:
    f.write(json.dumps({"prompt": prompt, "resume": resume}) + "\n")
if (v / "fail_next").exists():
    (v / "fail_next").unlink()
    sys.exit(1)
if not resume:
    (v / "question.md").write_text("Which greeting?")
elif prompt.startswith(("Tom answered", "Tom reviewed")):
    (v / "done.md").write_text("Delivered: " + prompt.splitlines()[2])
print(json.dumps({"result": "ok", "session_id": resume or "session-1", "is_error": False}))
"""


def flaky_for(prompt, resume, b):
    def build(url, brief, turn_id):
        return claude_code.TurnCommand(
            argv=[sys.executable, "-c", FLAKY, prompt, resume or ""],
            env={"PATH": os.environ["PATH"], "HOME": os.environ["HOME"]},
            cwd=b.workspace,
            harness="script",
            parse=claude_code.parse,
        )

    return build


def test_a_failed_turn_leaves_the_answer_and_the_feedback_for_the_next_turn(dsn, tmp_path):
    fail_next = tmp_path / ".valor" / "fail_next"

    async def go():
        task = await new_task(dsn, tmp_path)
        gateway = Gateway(dsn)
        await gateway.start()
        outs = [await session.run(gateway, task, flaky_for, dsn=dsn)]
        async with await db.connect(dsn) as conn:
            await session.answer(conn, task, "Morning, Tom.", by="stand-in", role_played=True)
        fail_next.touch()
        outs.append(await session.run(gateway, task, flaky_for, dsn=dsn))
        outs.append(await session.run(gateway, task, flaky_for, dsn=dsn))
        async with await db.connect(dsn) as conn:
            await session.feedback(conn, task, "Shorter, please.")
        fail_next.touch()
        outs.append(await session.run(gateway, task, flaky_for, dsn=dsn))
        outs.append(await session.run(gateway, task, flaky_for, dsn=dsn))
        await gateway.close()
        return outs

    outs = run(go())
    assert [o["status"] for o in outs] == ["waiting", "failed", "delivered", "failed", "delivered"]
    assert outs[2]["state"]["delivered"] == "Delivered: Morning, Tom."
    assert outs[4]["state"]["delivered"] == "Delivered: Shorter, please."

    prompts = [
        json.loads(line)["prompt"] for line in (tmp_path / ".valor" / "turns.jsonl").read_text().splitlines()
    ]
    answered = "Tom answered your question:\n\nMorning, Tom."
    assert prompts[1:3] == [answered, answered]
    assert prompts[3] == prompts[4] and "Shorter, please." in prompts[3]

    question, feedback = outs[4]["state"]["attention"]
    assert question["provenance"]["by"] == "stand-in" and question["provenance"]["role_played"] is True
    assert feedback["provenance"]["by"] == "tom" and feedback["provenance"]["role_played"] is False


def test_the_clarify_mode_is_recorded_and_carried_in_the_brief_and_bare_is_unchanged(dsn, tmp_path):
    async def go():
        async with await db.connect(dsn) as conn:
            bare = await new_task(dsn, tmp_path)
            clarify = await new_task(dsn, tmp_path, mode="clarify")
            return (
                await tasks.dispatch(conn, bare),
                await tasks.dispatch(conn, clarify),
                await ledger.read(conn, clarify),
            )

    bare, clarify, rows = run(go())
    assert signals.CLARIFY not in bare["text"] and bare["text"].endswith(signals.PROTOCOL)
    assert clarify["text"].endswith(signals.PROTOCOL + "\n\n" + signals.CLARIFY)
    assert rows[0]["type"] == "task.started" and rows[0]["payload"]["mode"] == "clarify"
    with pytest.raises(ValueError, match="mode"):
        tasks.Brief(instruction="x", budget_usd_micros=0, mode="interview")


def test_a_stopped_task_takes_no_feedback_and_an_open_question_takes_an_answer(dsn, workspace):
    ws, _ = workspace

    async def go():
        task = await new_task(dsn, ws)
        gateway = Gateway(dsn)
        await gateway.start()
        waiting = await session.run(gateway, task, turn_for, dsn=dsn)
        await gateway.close()
        async with await db.connect(dsn) as conn:
            with pytest.raises(LookupError, match="open question"):
                await session.feedback(conn, task, "feedback while a question is open")
            await ledger.append(conn, task, "task.delivered", {"turn_id": "t", "summary": "delivered"})
            await tasks.stop(conn, task, reason="test")
            with pytest.raises(LookupError, match="stopped"):
                await session.feedback(conn, task, "feedback on a stopped task")
            return waiting, await ledger.read(conn, task)

    waiting, rows = run(go())
    assert waiting["status"] == "waiting"
    assert not [r for r in rows if r["type"] == "feedback.given"]


def test_an_unread_effect_request_and_continue_carry_the_outcome_into_the_next_prompt(dsn, tmp_path):
    (tmp_path / ".valor" / "effects").mkdir(parents=True)
    (tmp_path / ".valor" / "effects" / "bad.json").write_text("not json")
    (tmp_path / ".valor" / "effects" / "send.json").write_text(
        json.dumps({"action_type": "no_such_action", "target": "tom", "payload": {}})
    )

    async def go():
        task = await new_task(dsn, tmp_path)
        found = signals.collect(tmp_path, "turn-1")
        async with await db.connect(dsn) as conn:
            await ledger.append(
                conn,
                task,
                "turn.ended",
                {"turn_id": "turn-1", "outcome": "done", "result": {"session_id": "s"}},
            )
            await session.record(conn, task, "turn-1", found)
            return await session.next_prompt(conn, task), await tasks.status(conn, task)

    (prompt, resume), state = run(go())
    assert resume == "s" and prompt.startswith("Continue.")
    assert "bad.json: unreadable request" in prompt
    assert "no_such_action -> tom" in prompt and "refused: no performer" in prompt
    assert state["state"] == "live"
    assert list((tmp_path / ".valor" / "handled" / "turn-1" / "effects").iterdir())


def test_a_spent_budget_runs_no_turn(dsn, tmp_path):
    async def go():
        task = await new_task(dsn, tmp_path, budget_usd_micros=0)
        gateway = Gateway(dsn)
        await gateway.start()
        out = await session.run(gateway, task, turn_for, dsn=dsn)
        await gateway.close()
        return out

    assert run(go())["status"] == "budget exhausted"
    assert not (tmp_path / ".valor").exists()


def test_opus_5_5_has_its_own_price_and_one_hour_cache_writes_cost_double_input():
    price = budget.prices("claude-opus-5-5")
    assert price["input"] == 4_000_000 and price["output"] == 20_000_000
    assert budget.prices("claude-opus-5-20260101")["input"] == 5_000_000
    usage = {
        "cache_creation_input_tokens": 1_000_000,
        "cache_creation": {"ephemeral_5m_input_tokens": 250_000, "ephemeral_1h_input_tokens": 750_000},
    }
    assert budget.cost(usage, price) == round(0.25 * 5_000_000 + 0.75 * 8_000_000)
    assert budget.cost({"cache_creation_input_tokens": 1_000_000}, price) == 8_000_000


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
    assert argv[:7] == ["sandbox-exec", "-D", "GATEWAY_PORT=4321", "-D", "VALOR_TURN=turn1", "-f", "/p.sb"]
    assert argv[argv.index("--resume") + 1] == "abc"
    assert argv[argv.index("--system-prompt-snapshot") + 1] == "off"
    assert argv[argv.index("--append-system-prompt") + 1] == "You are Valor.\n\n# Brief"
    assert "GH_TOKEN" not in command.env and "SSH_AUTH_SOCK" not in command.env
    assert command.env["GH_CONFIG_DIR"] == "/gh" and command.env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert command.env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:4321/t/token"
    assert command.env["TEST_DB_PORT"] == "5439"
    assert command.env["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] == "1"


def test_a_push_runs_nothing_the_workspace_config_or_hooks_name(workspace, tmp_path):
    ws, origin = workspace
    marker = tmp_path / "ran"
    hook = f"#!/bin/sh\necho $0 >> {marker}\n"
    for path in (ws / ".git" / "hooks" / "pre-push", tmp_path / "hooks" / "pre-push"):
        path.parent.mkdir(exist_ok=True)
        path.write_text(hook)
        path.chmod(0o755)
    receive = tmp_path / "receive"
    receive.write_text(f'#!/bin/sh\necho receive >> {marker}\nexec git-receive-pack "$@"\n')
    receive.chmod(0o755)
    git(ws, "config", "core.hooksPath", str(tmp_path / "hooks"))
    git(ws, "config", "remote.origin.receivepack", str(receive))
    head = git(ws, "rev-parse", "HEAD").strip()

    pushed = PushBranch(ws).perform(broker.Action("push_branch", "valor/x", {"head_sha": head}), "key")
    assert pushed["sha"] == head and git(origin, "rev-parse", "valor/x").strip() == head
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
        claude_code.workspace_turn(request, cwd=str(tmp_path), resume="abc"),
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
