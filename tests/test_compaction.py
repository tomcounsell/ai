"""A chat session compacts the way Hermes Agent does (docs/plans/chat-compaction.md):
at half its model's window within 150k to 600k, with instructions the PreCompact hook
of Valor's plugin gives Claude Code's own compaction."""

import json
import subprocess
from pathlib import Path

from core import __main__, tasks
from core.settings import VALOR_PLUGIN
from harnesses import claude_code

PROFILE = {"sandbox_profile": "turn"}
COMPACTION = {"CLAUDE_CODE_AUTO_COMPACT_WINDOW", "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"}


def test_a_chat_session_compacts_at_half_its_window_within_150k_to_600k():
    at = claude_code.compact_at
    assert [at(w) for w in (200_000, 300_000, 1_000_000, 1_200_000, 1_500_000)] == [
        150_000, 150_000, 500_000, 600_000, 600_000,
    ]  # fmt: skip


def env(channel: str | None, model: str = "claude-opus-5-5") -> dict:
    harness = {**PROFILE, "chat_channel": channel} if channel else PROFILE
    return claude_code.workspace_turn("p", cwd=".", model=model, harness=harness)("http://h:1", "b", "t").env


def test_only_a_chat_session_is_given_the_threshold_and_the_instructions():
    chat = env("telegram")
    # Claude Code compacts at 80% of the window less 20,000: (645,000 - 20,000) * 0.8 = 500,000.
    assert {k: chat[k] for k in COMPACTION} == {
        "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "645000",
        "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": "80",
    }
    assert chat["VALOR_COMPACT"] == claude_code.compact_text("telegram")
    task = env(None)
    assert not (COMPACTION | {"VALOR_COMPACT"}) & set(task)
    # A model with no listed window keeps Claude Code's own threshold.
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
