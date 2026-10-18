"""A chat session controls its own compaction (docs/plans/chat-compaction-control.md):
it is reminded of its context size, compacts itself between turns when it asks, and
is compacted for it only past 650k, each time by Claude Code's own compaction with
the instructions the PreCompact hook of Valor's plugin gives it."""

import asyncio
import json
import subprocess
import uuid
from pathlib import Path

import pytest

from core import __main__, db, ledger, machine, serve, session, signals, tasks
from core.machine import State
from core.settings import VALOR_PLUGIN, compaction
from harnesses import claude_code
from tests import bridges, scripted

PROFILE = {"sandbox_profile": "turn"}
COMPACTION = {"CLAUDE_CODE_AUTO_COMPACT_WINDOW", "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"}


def test_reminders_and_the_force_are_toms_on_a_1m_window_and_scaled_below_it():
    assert compaction("claude-opus-5-5") == ((150_000, 250_000, 350_000, 450_000, 550_000, 600_000), 650_000)
    with pytest.MonkeyPatch.context() as m:
        m.setitem(claude_code.compaction.__globals__["WINDOWS"], "small", 200_000)
        assert compaction("small") == ((30_000, 50_000, 70_000, 90_000, 110_000, 120_000), 130_000)
    assert compaction("claude-sonnet-5-5") is None


def env(channel: str | None, model: str = "claude-opus-5-5") -> dict:
    harness = {**PROFILE, "chat_channel": channel} if channel else PROFILE
    return claude_code.workspace_turn("p", cwd=".", model=model, harness=harness)("http://h:1", "b", "t").env


def test_only_a_chat_session_is_given_the_force_and_the_instructions():
    chat = env("telegram")
    # Claude Code compacts at 80% of the window less 20,000: (832,500 - 20,000) * 0.8 = 650,000.
    assert {k: chat[k] for k in COMPACTION} == {
        "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "832500",
        "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": "80",
    }
    assert chat["VALOR_COMPACT"] == claude_code.compact_text("telegram")
    task = env(None)
    assert not (COMPACTION | {"VALOR_COMPACT"}) & set(task)
    # A model with no listed window keeps Claude Code's own compaction.
    unlisted = env("telegram", model="claude-sonnet-5-5")
    assert not COMPACTION & set(unlisted) and unlisted["VALOR_COMPACT"]


def test_the_kernel_names_the_chat_sessions_channel_and_no_tasks():
    def harness(chat):
        b = tasks.Brief(instruction="x", harness=PROFILE, harness_name="claude_code", chat=chat)
        return __main__._turn_for("p", None, b)("http://h:1", "b", "t").env

    assert harness({"channel": "email", "chat_id": "t"})["VALOR_COMPACT"] == claude_code.compact_text("email")
    assert "VALOR_COMPACT" not in harness(None)


def test_only_an_email_thread_keeps_its_opening_and_every_channel_keeps_the_rest():
    for channel in ("telegram", "slack", "local", "email"):
        text = claude_code.compact_text(channel)
        assert "{opening}" not in text
        for clause in (
            "within about 12,000\ntokens",
            "about 2,000 tokens",
            "every ask not yet answered and who made it",
            "each task\n   the session started",
            "what the session promised the chat",
            "keep what is still true",
            "What the humans said, verbatim, newest first",
            "About 24,000 characters",
            "cut any one message at 4,000 characters",
            "Leave out messages that part 3 holds",
            "`valor-telegram read`",
            "last 20 messages",
            "within about 3,000 tokens",
        ):
            assert clause in text, (channel, clause)
        assert (claude_code.EMAIL_OPENING in text) == (channel == "email")
        assert (claude_code.CHAT_OPENING in text) == (channel != "email")
    assert "outside part 2's cap and never dropped" in claude_code.EMAIL_OPENING


def test_the_precompact_hook_prints_the_instructions_and_nothing_when_unset():
    hooks = json.loads((Path(VALOR_PLUGIN) / "hooks" / "hooks.json").read_text())["hooks"]
    (command,) = [h["command"] for entry in hooks["PreCompact"] for h in entry["hooks"]]

    def run(extra: dict) -> str:
        call = json.dumps({"hook_event_name": "PreCompact", "trigger": "auto"})
        return subprocess.run(
            ["/bin/sh", "-c", command], input=call, env=extra, capture_output=True, text=True, check=True
        ).stdout

    text = claude_code.compact_text("email") + '\n"quoted" $HOME `x` ü'
    assert run({"VALOR_COMPACT": text}) == text
    assert run({}) == ""


# -- the reminder: the context size as each mark is passed, once -----------------------

MODEL = "claude-opus-5-5"


def ledger_of(*turns) -> list[dict]:
    """Rows for chat turns in order: an int is a working turn ending at that
    context size, `"compact"` a compaction turn that finished, `"failed"` one
    that did not."""
    rows: list[dict] = []

    def row(kind, payload):
        rows.append({"id": len(rows) + 1, "type": kind, "payload": payload})

    row("task.started", {"sdlc": 1, "instruction": "x"})
    row("judge.decided", {"verdict": "precise", "leg": "manual"})

    for n, t in enumerate(turns):
        turn = f"t{n}"
        row(
            "turn.started",
            {"turn_id": turn, "state": "clarify", **({"compaction": True} if isinstance(t, str) else {})},
        )
        if isinstance(t, int):
            row("gateway.charged", charged(turn, t))
        failed = t == "failed"
        row(
            "turn.ended",
            {"turn_id": turn, "outcome": "failed" if failed else "done", "result": {"is_error": False}},
        )
    return rows


def charged(turn, size, model=MODEL, complete=True, unsent=False) -> dict:
    usage = {"input_tokens": 10, "cache_read_input_tokens": size - 1010, "cache_creation_input_tokens": 500,
             "output_tokens": 500}  # fmt: skip
    return {"turn_id": turn, "model": model, "complete": complete, "unsent": unsent, "usage": usage}


def reminded(*turns) -> bool:
    return session.context_report(ledger_of(*turns), MODEL).startswith("# Your context")


def test_the_session_is_reminded_once_as_it_passes_each_mark():
    assert reminded(140_000, 160_000)
    assert "about 160,000 tokens" in session.context_report(ledger_of(160_000), MODEL)
    assert "past 650,000 it is compacted for you" in session.context_report(ledger_of(160_000), MODEL)
    assert not reminded(160_000, 240_000)
    assert reminded(240_000, 400_000)  # two marks passed at once: one reminder
    assert reminded(590_000, 640_000)
    assert not reminded(600_000, 640_000)  # 600k reminded once; then only the force
    assert not reminded(100_000)
    # A compaction that finished since the last turn: no reminder.
    assert session.context_report(ledger_of(140_000, 160_000, "compact"), MODEL) == ""
    # After a compaction the context shrank, so a mark passed again reminds again.
    assert reminded(360_000, "compact", 160_000)
    assert not reminded(360_000, "compact", 140_000)
    # A model with no reminders never reminds.
    assert session.context_report(ledger_of(140_000, 160_000), "claude-sonnet-5-5") == ""


def test_the_size_is_the_turns_last_complete_sent_call_on_the_sessions_model():
    rows = ledger_of(140_000, 160_000)
    ended = rows.pop()
    rows += [{"id": 90, "type": "gateway.charged", "payload": p} for p in (
        charged("t1", 900_000, model="claude-haiku-5-5"),  # a side call on another model
        charged("t1", 900_000, complete=False),  # a call cut short
        charged("t1", 900_000, unsent=True),  # a call never sent
    )] + [ended]  # fmt: skip
    assert session.context_size(rows, "t1", MODEL) == 160_000
    rows.insert(
        3, {"id": 91, "type": "gateway.charged", "payload": charged("t1", 100_000)}
    )  # an earlier call
    assert session.context_size(rows, "t1", MODEL) == 160_000


def test_a_failed_compaction_is_told_to_the_session_not_reminded():
    text = session.context_report(ledger_of(140_000, 160_000, "failed"), MODEL)
    assert text.startswith("# Your compaction") and "did not finish (it ended failed)" in text


def test_a_compaction_turn_acts_on_no_message_and_keeps_the_session():
    rows = ledger_of(100_000)  # the working turn's turn.started is row 3
    rows.append({"id": 10, "type": "message.steered", "payload": {"text": "during", "message_id": "7"}})
    rows += [{"id": 11, "type": "turn.started", "payload": {"turn_id": "c", "state": "clarify", "compaction": True}},
             {"id": 12, "type": "turn.ended", "payload": {"turn_id": "c", "outcome": "done",
                                                         "result": {"session_id": SESSION}}}]  # fmt: skip
    f = machine.fold(rows)
    assert f.steer_since == 3 and f.session == SESSION and not f.entry_finished
    assert "during" in session.chat_prompt(rows)
    assert not session._working_turn_finished(rows[-2:], f.turn_states)


SESSION = "00000000-0000-4000-8000-000000000001"


# -- the request: compact.md, then a /compact turn in the same session -------------------


def test_a_task_turns_compact_md_counts_for_nothing():
    _, _, errors = session._stage_verdict(State.BUILD, signals.Signals(compact="x"), None, "t", True)
    assert errors == ["compact means nothing outside a chat session; ignored"]


def test_compact_prompt_is_claude_codes_command_with_the_sessions_text():
    assert session.compact_prompt("(empty compact.md)") == "/compact"
    assert session.compact_prompt("Keep the codename.") == "/compact Keep the codename."


async def chat_task(dsn, ws) -> str:
    chat = {"channel": "telegram", "chat_id": str(uuid.uuid4().int % 10**9)}
    return await scripted.start(dsn, ws, chat=chat)


async def say(dsn, task, text, n) -> None:
    async with await db.connect(dsn) as conn:
        await ledger.append(conn, task, "message.steered", {"message_id": str(n), "sender_name": "Tom",
                            "sent_at": "2026-10-17T09:00:00+00:00", "text": text})  # fmt: skip


async def wake(dsn, task) -> dict:
    gateway = await bridges.gateway(dsn)
    try:
        return await session.run(gateway, task, scripted.turn_for, dsn=dsn, performers=scripted.performers(
            await brief(dsn, task)))  # fmt: skip
    finally:
        await gateway.close()


async def brief(dsn, task) -> tasks.Brief:
    async with await db.connect(dsn) as conn:
        return await tasks.brief(conn, task)


def turns(ws) -> list[dict]:
    return [json.loads(x) for x in (ws / ".git" / "valor-turns.jsonl").read_text().splitlines()]


def test_a_chat_session_that_asks_is_compacted_in_place_before_its_next_turn(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)
    scripted.steer(ws, chat=[{"compact.md": "Keep the codename.", "question.md": "?"}, {}])

    async def go():
        task = await chat_task(dsn, ws)
        await say(dsn, task, "The codename is BLUE HERON.", 1)
        out = await wake(dsn, task)
        await say(dsn, task, "Still there?", 2)
        async with await db.connect(dsn) as conn:
            prompt, resume, _ = await session.next_prompt(conn, task)
            recollected = await serve.recollect(conn, task, scripted.performers)
        return out, prompt, resume, recollected, await bridges.rows(dsn, task)

    out, prompt, resume, recollected, rows = asyncio.run(go())
    chat, compact = turns(ws)
    assert "The codename is BLUE HERON." in chat["prompt"]
    assert out["status"] == "listening"
    assert compact["prompt"] == "/compact Keep the codename." and compact["resume"] == resume == SESSION
    (started,) = [
        r["payload"] for r in rows if r["type"] == "turn.started" and r["payload"].get("compaction")
    ]
    collected = [r["payload"]["turn_id"] for r in rows if r["type"] == "turn.collected"]
    assert started["turn_id"] not in collected and len(collected) == 1
    # The next prompt has the new message and the chat turn's own report, not the compaction's.
    assert "Still there?" in prompt and "The codename is BLUE HERON." not in prompt
    assert "question means nothing in a chat session" in prompt
    # A kernel restart never collects the compaction turn.
    assert recollected is None


def test_an_empty_request_a_failed_compaction_and_a_stop_beside_one(dsn, tmp_path):
    ws, _ = scripted.workspace(tmp_path)
    scripted.steer(
        ws, fail_compact=True, chat=[{"compact.md": ""}, {"compact.md": "x", "stop.md": "Done here."}]
    )

    async def go():
        task = await chat_task(dsn, ws)
        await say(dsn, task, "hello", 1)
        first = await wake(dsn, task)
        await say(dsn, task, "again", 2)
        async with await db.connect(dsn) as conn:
            prompt, _, _ = await session.next_prompt(conn, task)
        second = await wake(dsn, task)
        return first, prompt, second, await bridges.rows(dsn, task)

    first, prompt, second, rows = asyncio.run(go())
    log = turns(ws)
    assert [t["prompt"].split("\n")[0] for t in log[1:2]] == ["/compact"]
    # The failed compaction neither fails the chat nor reads as its turn failing.
    assert first["status"] == "listening" and not [r for r in rows if r["type"] == "step.failed"]
    assert "# Your compaction" in prompt and "# The last try failed" not in prompt
    # stop.md beside compact.md ends the session, uncompacted.
    assert second["status"] == "moved" and machine.fold(rows).state is State.ENDED
    assert len(log) == 3 and not log[2]["prompt"].startswith("/compact")
