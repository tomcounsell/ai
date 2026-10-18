"""Mid-turn steering (docs/plans/mid-turn-steering.md): a message for a
running turn reaches it after its current step, through Valor's
PostToolUse hook, each agent once; the turn's main agent taking it spends
it once the turn finishes, so no later prompt shows it again; stop still
stops at once. Real Postgres; the harness is a Python stand-in that runs
the plugin's hook after each step, as Claude Code does."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from core import db, ledger, machine, reactions, runs, session, tasks
from core.settings import VALOR_PLUGIN
from harnesses import claude_code
from tests import bridges, test_kernel
from tests.test_kernel import audit, new_task
from tests.test_kernel import said as in_team
from tests.test_reactions import effect, requested, worked

pytestmark = pytest.mark.spend(usd=0)
team = test_kernel.team  # the kernel tests' fixture: a project's team chats

run = asyncio.run
HOOK = Path(VALOR_PLUGIN) / "hooks" / "steer.py"

# A harness turn: `steps` steps of `pause` seconds; after each, the hook runs
# for the main agent and for a subagent, and what it printed is logged.
STEPPER = r"""
import json, subprocess, sys, time
hook, log, steps, pause = sys.argv[1], sys.argv[2], int(sys.argv[3]), float(sys.argv[4])
for n in range(steps):
    time.sleep(pause)
    for agent in (None, "sub1"):
        event = {"hook_event_name": "PostToolUse", **({"agent_id": agent} if agent else {})}
        out = subprocess.run([sys.executable, "-I", hook], input=json.dumps(event),
                             capture_output=True, text=True).stdout
        with open(log, "a") as f:
            f.write(json.dumps({"step": n, "agent": agent or "main", "out": out}) + "\n")
"""


def stepper(tmp_path, steps: int, pause: float):
    log = tmp_path / "steps.jsonl"
    build = lambda url, brief, turn_id: runs.TurnCommand(
        argv=[sys.executable, "-c", STEPPER, str(HOOK), str(log), str(steps), str(pause)],
        env={}, cwd=str(tmp_path), harness="stepper", tmpdir=str(tmp_path / "tmp"), steerable=True,
    )  # fmt: skip
    return build, log


def given(log: Path) -> list[tuple[int, str, str]]:
    """(step, agent, context) for each hook run that gave something."""
    out = []
    for line in log.read_text().splitlines():
        r = json.loads(line)
        if r["out"]:
            out.append(
                (r["step"], r["agent"], json.loads(r["out"])["hookSpecificOutput"]["additionalContext"])
            )
    return out


async def steer(dsn, task, text) -> int:
    async with await db.connect(dsn) as conn:
        return await ledger.append(
            conn,
            task,
            "message.steered",
            {"text": text, "provenance": ledger.provenance("tom", "chat", False)},
        )


async def last_id(dsn, task) -> int:
    async with await db.connect(dsn) as conn:
        return max(r["id"] for r in await ledger.read(conn, task))


def test_a_steer_lands_after_the_current_step_once_per_agent_and_is_never_shown_again(dsn, tmp_path):
    async def go():
        with bridges.configure(work_dir=str(tmp_path / "work")):
            task = await new_task(dsn)
            upto = await last_id(dsn, task)
            # Recorded after the prompt was read, before the turn started (its slot wait).
            before = await steer(dsn, task, "use the blue one")
            gateway = await bridges.gateway(dsn)
            build, log = stepper(tmp_path, steps=6, pause=0.5)
            turn = asyncio.create_task(runs.run_turn(
                gateway, task, build, dsn=dsn, state="build", read_upto=upto,
                steering=lambda rows: session.mid_turn(rows, upto, False),
            ))  # fmt: skip
            await asyncio.sleep(1.3)
            mid = await steer(dsn, task, "and make it bigger")
            ended = await turn
            late = await steer(dsn, task, "thanks")
            await gateway.close()
            async with await db.connect(dsn) as conn:
                rows = await ledger.read(conn, task)
                prompt, _, _ = await session.next_prompt(conn, task)
            inbox = runs.inbox_path(task, ended["turn_id"])
            return before, mid, late, ended, rows, prompt, given(log), inbox

    before, mid, late, ended, rows, prompt, got, inbox = run(go())
    assert ended["outcome"] == "done"
    (start,) = [r for r in rows if r["type"] == "turn.started"]
    assert start["payload"]["read_upto"] < before < start["id"]
    # The steer from the slot wait reaches the first step; the one sent mid-turn,
    # a later step; each agent takes each once.
    main = [(step, text) for step, agent, text in got if agent == "main"]
    sub = [(step, text) for step, agent, text in got if agent == "sub1"]
    assert main[0][0] == 0 and "use the blue one" in main[0][1] and "bigger" not in main[0][1]
    assert len(main) == 2 and main[1][0] > 0 and "and make it bigger" in main[1][1]
    # The subagent takes the same messages, each once, whichever step its hook ran at.
    assert len(sub) == 2 and "use the blue one" in sub[0][1] and "and make it bigger" in sub[1][1]
    assert "bigger" not in sub[0][1] and all(t.startswith("Valor steering") for _, t in main + sub)
    delivered = [r["payload"] for r in rows if r["type"] == "message.delivered"]
    assert delivered == [{"steered_id": before, "turn_id": ended["turn_id"]},
                         {"steered_id": mid, "turn_id": ended["turn_id"]}]  # fmt: skip
    # Only the steer that missed the turn opens the next one.
    assert "thanks" in prompt and "blue" not in prompt and "bigger" not in prompt
    assert [p["text"] for p in machine.fold(rows).steering] == ["thanks"] and late > before
    assert not inbox.exists()


def test_a_turn_owed_no_steering_gets_no_inbox(dsn, tmp_path):
    """A fresh or compaction turn, or one whose harness cannot take a
    message mid-turn, is given nothing; its steer opens the next turn."""

    async def go():
        with bridges.configure(work_dir=str(tmp_path / "work")):
            task = await new_task(dsn)
            gateway = await bridges.gateway(dsn)
            build, log = stepper(tmp_path, steps=3, pause=0.4)
            turn = asyncio.create_task(runs.run_turn(gateway, task, build, dsn=dsn, fresh="review"))
            await asyncio.sleep(0.5)
            await steer(dsn, task, "too late for review")
            await turn
            await gateway.close()
            async with await db.connect(dsn) as conn:
                return await ledger.read(conn, task), given(log)

    rows, got = run(go())
    assert got == [] and not [r for r in rows if r["type"] == "message.delivered"]
    assert [p["text"] for p in machine.fold(rows).steering] == ["too late for review"]


def test_stop_kills_the_turn_at_once_while_steers_are_flowing(dsn, tmp_path):
    async def go():
        with bridges.configure(work_dir=str(tmp_path / "work")):
            task = await new_task(dsn)
            upto = await last_id(dsn, task)
            gateway = await bridges.gateway(dsn)
            build, _ = stepper(tmp_path, steps=600, pause=0.1)
            turn = asyncio.create_task(runs.run_turn(
                gateway, task, build, dsn=dsn, state="build", read_upto=upto,
                steering=lambda rows: session.mid_turn(rows, upto, False),
            ))  # fmt: skip

            async def flow():
                n = 0
                while True:
                    n += 1
                    await steer(dsn, task, f"steer {n}")
                    await asyncio.sleep(0.05)

            flowing = asyncio.create_task(flow())
            await asyncio.sleep(1.5)
            started = asyncio.get_running_loop().time()
            async with await db.connect(dsn) as conn:
                assert await tasks.stop(conn, task, reason="test") is True
            ended = await turn
            elapsed = asyncio.get_running_loop().time() - started
            flowing.cancel()
            await gateway.close()
            async with await db.connect(dsn) as conn:
                return ended, elapsed, await tasks.status(conn, task), await ledger.read(conn, task)

    ended, elapsed, state, rows = run(go())
    assert ended["outcome"] == "stopped" and elapsed < 20
    assert state["state"] == "stopped" and audit(state) == []
    # A stopped turn spends nothing it took: every steer stays.
    stopped = next(r["id"] for r in rows if r["type"] == "task.stopped")
    steered = [r["id"] for r in rows if r["type"] == "message.steered" and r["id"] < stopped]
    assert [r["payload"]["steered_id"] for r in rows if r["type"] == "message.delivered"]
    assert len(machine.fold(rows).steering) == len(steered)


# -- the hook --------------------------------------------------------------------


def hook(inbox: Path | None, event: dict) -> str:
    env = {"PATH": os.environ["PATH"], **({"VALOR_STEER": str(inbox)} if inbox else {})}
    return subprocess.run(
        [sys.executable, "-I", HOOK],
        input=json.dumps(event),
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def test_the_hook_gives_each_agent_each_message_once_and_names_the_failure_event(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "7.md").write_text("Tom wrote while you worked:\n- use the blue one")
    assert hook(None, {"hook_event_name": "PostToolUse"}) == ""
    first = json.loads(hook(inbox, {"hook_event_name": "PostToolUseFailure"}))["hookSpecificOutput"]
    assert first["hookEventName"] == "PostToolUseFailure" and first["additionalContext"].endswith("blue one")
    assert hook(inbox, {"hook_event_name": "PostToolUse"}) == ""
    (inbox / "12.md").write_text("Tom wrote while you worked:\n- and bigger")
    (inbox / ".13.part").write_text("half")
    again = json.loads(hook(inbox, {"hook_event_name": "PostToolUse"}))["hookSpecificOutput"][
        "additionalContext"
    ]
    assert "bigger" in again and "blue" not in again and "half" not in again
    sub = json.loads(hook(inbox, {"hook_event_name": "PostToolUse", "agent_id": "a1"}))["hookSpecificOutput"]
    assert sub["additionalContext"].index("blue") < sub["additionalContext"].index("bigger")
    assert runs._taken(inbox) == {7, 12}


def test_parallel_subagents_lose_and_tangle_no_receipt(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    for i in (3, 4, 5):
        (inbox / f"{i}.md").write_text(f"message {i}")
    env = {"PATH": os.environ["PATH"], "VALOR_STEER": str(inbox)}
    agents = [a for _ in range(2) for a in ("main", "a1", "a2", "a3", "a4", "a5")]
    procs = [
        subprocess.Popen(
            [sys.executable, "-I", HOOK], stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env, text=True
        )
        for _ in agents
    ]
    outs = []
    for p, agent in zip(procs, agents, strict=True):
        event = {"hook_event_name": "PostToolUse", **({"agent_id": agent} if agent != "main" else {})}
        outs.append((agent, p.communicate(json.dumps(event))[0]))
    lines = [json.loads(line) for line in (inbox / "taken.jsonl").read_text().splitlines()]
    assert sorted((r["agent"], r["id"]) for r in lines) == sorted(
        (a, i) for a in set(agents) for i in (3, 4, 5)
    )
    for agent in set(agents):
        given_to = [o for a, o in outs if a == agent and o]
        assert len(given_to) == 1 and all(f"message {i}" in given_to[0] for i in (3, 4, 5))


def test_valors_plugin_runs_the_hook_after_every_step_and_claude_code_turns_take_it():
    hooks = json.loads((Path(VALOR_PLUGIN) / "hooks" / "hooks.json").read_text())["hooks"]
    for event in ("PostToolUse", "PostToolUseFailure"):
        (entry,) = hooks[event]
        assert "matcher" not in entry and "steer.py" in entry["hooks"][0]["command"]
    # Without an inbox the hook does not start Python.
    command = hooks["PostToolUse"][0]["hooks"][0]["command"].replace("${CLAUDE_PLUGIN_ROOT}", "/nonexistent")
    done = subprocess.run(
        ["sh", "-c", command], input="{}", capture_output=True, text=True, env={}, check=False
    )
    assert (done.returncode, done.stdout) == (0, "")
    made = claude_code.workspace_turn("p", cwd=".", harness={"sandbox_profile": "turn"})(
        "http://h:1", "b", "t"
    )
    assert made.steerable


# -- the fold ----------------------------------------------------------------------


def rows_of(*specs) -> list[dict]:
    """A task's rows, from id 1, after its `task.started` (id 0)."""
    started = ("task.started", {"sdlc": 1, "instruction": "x"})
    return [{"id": i, "type": t, "payload": p} for i, (t, p) in enumerate((started, *specs))]


def turn(tid, state="build", read_upto=None, outcome="done", is_error=False):
    started = {"turn_id": tid, "state": state, **({"read_upto": read_upto} if read_upto is not None else {})}
    ended = {"turn_id": tid, "outcome": outcome, "result": {"session_id": "s", "is_error": is_error}}
    return ("turn.started", started), ("turn.ended", ended)


def steered(text):
    return ("message.steered", {"text": text})


def test_the_prompt_window_and_what_a_finished_turn_took_set_the_steering():
    s, e = turn("t1", read_upto=1)
    # Row 2 came after the prompt was read: it is not spent by the turn's start.
    rows = rows_of(("judge.decided", {"verdict": "precise", "leg": "manual"}), steered("slot wait"), s,
                   steered("mid"), steered("missed"), ("message.delivered", {"steered_id": 2, "turn_id": "t1"}),
                   ("message.delivered", {"steered_id": 4, "turn_id": "t1"}), e)  # fmt: skip
    f = machine.fold(rows)
    assert f.steer_since == 1 and [p["text"] for p in f.steering] == ["missed"]
    # With no `read_upto` (fresh, advisor, compaction, older rows) the turn's start is the window.
    s, e = turn("t1")
    assert machine.fold(rows_of(steered("a"), s, steered("b"), e)).steer_since == 2


@pytest.mark.parametrize("outcome,is_error", [("done", True), ("failed", False), ("stopped", False)])
def test_a_turn_that_did_not_finish_keeps_everything_it_took(outcome, is_error):
    """Kept, it may be shown twice (taken, then in the next prompt); lost
    would be worse."""
    s, e = turn("t1", read_upto=0, outcome=outcome, is_error=is_error)
    rows = rows_of(s, steered("mid"), ("message.delivered", {"steered_id": 2, "turn_id": "t1"}), e)
    assert [p["text"] for p in machine.fold(rows).steering] == ["mid"]


def test_a_steer_only_a_subagent_took_stays_for_the_main_agent():
    s, e = turn("t1", read_upto=0)
    assert [p["text"] for p in machine.fold(rows_of(s, steered("sub only"), e)).steering] == ["sub only"]


def test_steering_taken_mid_build_no_longer_holds_a_passing_join():
    from tests.test_machine import Ledger

    led = Ledger().plan().critique("sound")
    led.add("turn.started", {"turn_id": "b", "state": "build", "read_upto": len(led.rows)})
    led.add("message.steered", {"text": "bigger"})
    led.add("message.delivered", {"steered_id": len(led.rows), "turn_id": "b"})
    led.add("turn.ended", {"turn_id": "b", "outcome": "done", "result": {"session_id": "s"}})
    led.add("turn.collected", {"turn_id": "b", "state": "build", "verdict": "candidate",
                               "candidate": {"sha": "c1", "turn_id": "b"}})  # fmt: skip
    led.candidate = {"sha": "c1", "turn_id": "b"}
    led.checks("pass", "pass", "no_change")
    assert led.state is machine.State.MERGE


# -- a chat session ------------------------------------------------------------------


def said(kind, mid, text):
    return (kind, {"received_id": f"r{mid}", "message_id": mid, "sender_name": "Rain", "text": text})


def test_a_chat_turn_is_given_the_heard_lines_with_the_steer_and_its_next_prompt_skips_them():
    s, e = turn("t1", state="clarify", read_upto=1)
    mid = (said("message.steered", "1", "hi"), s, said("message.heard", "2", "aside"),
           said("message.steered", "3", "valor, bigger"), ("child.reported", {"child_id": "c", "text": "done"}),
           said("message.heard", "5", "later aside"))  # fmt: skip
    owed = session.mid_turn(rows_of(*mid), 1, True)
    assert [i for i, _ in owed] == [4]
    assert "aside" in owed[0][1] and "valor, bigger" in owed[0][1] and ": hi" not in owed[0][1]
    rows = rows_of(*mid, ("message.delivered", {"steered_id": 4, "turn_id": "t1"}), e)
    prompt = session.chat_prompt(rows)
    # The task's report arrived mid-turn and was not delivered: it wakes the next turn.
    assert session.chat_waiting(rows)
    assert "your task c reports" in prompt.lower() and "later aside" in prompt
    assert "valor, bigger" not in prompt and "(message 2)" not in prompt


def test_a_chat_turn_that_took_its_only_message_is_not_woken_again():
    s, e = turn("t1", state="clarify", read_upto=0)
    rows = rows_of(s, said("message.steered", "1", "valor, bigger"),
                   ("message.delivered", {"steered_id": 2, "turn_id": "t1"}), e)  # fmt: skip
    assert not session.chat_waiting(rows) and session.chat_prompt(rows) == ""


def test_a_message_taken_mid_turn_gets_that_turns_activity(dsn, team):
    async def go():
        first = await in_team(dsn, "valor, find the florist's supplier")
        task = first["task_id"]
        await worked(dsn, task)  # the turn that read the first message: it only replied
        upto = await last_id(dsn, task)
        turn_id = ledger.new_id()
        async with await db.connect(dsn) as conn:
            await ledger.append(
                conn, task, "turn.started", {"turn_id": turn_id, "state": "clarify", "read_upto": upto}
            )
        m = await in_team(dsn, "valor, and their prices")
        async with await db.connect(dsn) as conn:
            (row,) = [
                r for r in await ledger.read(conn, task) if r["type"] == "message.steered" and r["id"] > upto
            ]
            await ledger.append(
                conn, task, "message.delivered", {"steered_id": row["id"], "turn_id": turn_id}
            )
            await ledger.append(
                conn, task, "turn.ended", {"turn_id": turn_id, "outcome": "done", "result": {}}
            )
            label = await reactions.on_turn(conn, task, turn_id, [effect("task.start")])
        return label, await requested(dsn, m["received_id"])

    label, rows = run(go())
    assert label == "task" and [r["label"] for r in rows] == ["seen", "task"]
