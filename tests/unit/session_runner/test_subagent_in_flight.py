"""Tests for ``subagent_in_flight`` — the Layer 2 liveness helper (#2420).

A subagent is in flight when its sidechain transcript is **both** still being
written to (mtime within the window) and **not** closed by a finished
assistant answer. Both halves are required: recency alone flags a subagent
that finished a second ago, and shape alone flags every stranded transcript
left behind by an earlier turn of the same claude session.

Ground truth, measured by replaying 1573 real on-disk sidechains under
``~/.claude/projects/*/*/subagents/agent-*.jsonl``:

- A sidechain JSONL has NO ``result``/``Stop`` record type. Records are
  ``user`` / ``assistant`` / ``attachment``.
- 1571 of 1573 open with a ``user`` record whose ``message.content`` is a
  plain **string** — the task prompt, written the instant the subagent is
  spawned and before it has produced anything.
- 1454 (92.5%) close on an ``assistant`` record carrying a ``text`` block.
  The other 118 end mid-exchange: killed, interrupted, or stranded.
- Of those closing records, 1224 carry ``stop_reason == "end_turn"``, 207
  carry ``None`` (the streaming SDK-CLI flush), and 23 ``stop_sequence``.
  Keying completion on ``end_turn`` alone would false-downgrade 15% of
  finished subagents, so ``stop_reason`` is only consulted to rule OUT
  completion on ``"tool_use"``.

The just-spawned case is the load-bearing regression guard. It is the
fire-and-forget window the whole feature exists to catch — PM spawns a
background dev, the transcript holds only the prompt record, PM acks and exits
clean. The shipped predicate keyed "in flight" on a positive pending-tool-
exchange signal and read ``message.content`` as a list, so a string-content
prompt record yielded no blocks and fell through to ``False`` on 100% of
just-spawned subagents (PR #2455 review). Detection is now 100% there.
"""

from __future__ import annotations

import json
import os
import time

import pytest

from agent.session_runner.adapter import subagent_in_flight


def _write_transcript(projects_root, cwd, claude_session_id, agent_id, records):
    """Write a synthetic sidechain transcript and return its path."""
    slug = os.path.realpath(cwd).replace("/", "-").replace(".", "-")
    base = os.path.join(projects_root, slug, claude_session_id, "subagents")
    os.makedirs(base, exist_ok=True)
    path = os.path.join(base, f"{agent_id}.jsonl")
    with open(path, "w") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")
    return path


def _assistant(*, text=None, thinking=None, stop_reason=None, tool_use=False):
    content = []
    if thinking is not None:
        content.append({"type": "thinking", "thinking": thinking})
    if text is not None:
        content.append({"type": "text", "text": text})
    if tool_use:
        content.append({"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {}})
    return {
        "type": "assistant",
        "message": {"role": "assistant", "stop_reason": stop_reason, "content": content},
    }


def _user_tool_result():
    return {
        "type": "user",
        "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]},
    }


def _user_text(text):
    return {
        "type": "user",
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }


def _task_prompt(text="Do the thing."):
    """The real just-spawned first record: a user record whose content is a
    plain STRING, not a block list (1571 of 1573 real sidechains)."""
    return {"type": "user", "isSidechain": True, "message": {"role": "user", "content": text}}


CWD = "/tmp/sdlc2420-inflight-cwd"
SID = "11111111-1111-1111-1111-111111111111"


class TestJustSpawned:
    """The fire-and-forget window: the transcript holds only the task prompt.

    This is the case the shipped predicate missed 100% of the time.
    """

    def test_task_prompt_only_is_in_flight(self, tmp_path):
        """A lone string-content prompt record → the subagent has not spoken."""
        _write_transcript(str(tmp_path), CWD, SID, "agent-aaaa", [_task_prompt()])
        assert subagent_in_flight(CWD, SID, projects_root=str(tmp_path)) is True

    def test_string_content_is_not_discarded(self, tmp_path):
        """Regression guard for `blocks = content if isinstance(content, list)`.

        That expression dropped every string-content record on the floor, which
        is exactly the shape of the record that proves a subagent is live.
        """
        _write_transcript(
            str(tmp_path), CWD, SID, "agent-aaaa", [_task_prompt("Ship the feature.")]
        )
        assert subagent_in_flight(CWD, SID, projects_root=str(tmp_path)) is True

    def test_prompt_plus_attachment_is_in_flight(self, tmp_path):
        """Claude Code writes `attachment` records (tool listings, skill
        listings) right after the prompt, before the model produces anything."""
        _write_transcript(
            str(tmp_path),
            CWD,
            SID,
            "agent-aaaa",
            [_task_prompt(), {"type": "attachment", "attachment": {"type": "skill_listing"}}],
        )
        assert subagent_in_flight(CWD, SID, projects_root=str(tmp_path)) is True

    def test_empty_transcript_is_in_flight(self, tmp_path):
        """A zero-byte agent-*.jsonl is created at spawn, before the first
        record lands. Recently created and holding no answer → live."""
        slug = os.path.realpath(CWD).replace("/", "-").replace(".", "-")
        base = os.path.join(str(tmp_path), slug, SID, "subagents")
        os.makedirs(base, exist_ok=True)
        open(os.path.join(base, "agent-aaaa.jsonl"), "w").close()
        assert subagent_in_flight(CWD, SID, projects_root=str(tmp_path)) is True


class TestInFlightPositive:
    def test_assistant_tool_use_is_in_flight(self, tmp_path):
        """Newest record is an assistant tool_use awaiting a result → True."""
        _write_transcript(
            str(tmp_path),
            CWD,
            SID,
            "agent-aaaa",
            [
                _assistant(text="Working on it", stop_reason=None),
                _assistant(stop_reason="tool_use", tool_use=True),
            ],
        )
        assert subagent_in_flight(CWD, SID, projects_root=str(tmp_path)) is True

    def test_user_tool_result_is_in_flight(self, tmp_path):
        """Newest record is a user tool_result the assistant hasn't answered → True."""
        _write_transcript(
            str(tmp_path),
            CWD,
            SID,
            "agent-aaaa",
            [
                _assistant(stop_reason="tool_use", tool_use=True),
                _user_tool_result(),
            ],
        )
        assert subagent_in_flight(CWD, SID, projects_root=str(tmp_path)) is True


class TestCompleteIsNotInFlight:
    @pytest.mark.parametrize("stop_reason", ["end_turn", "stop_sequence", None])
    def test_final_assistant_text_is_complete(self, tmp_path, stop_reason):
        """Newest record is a final assistant text — complete for EVERY closing
        stop_reason including the streaming ``None`` case (regression guard
        against the plan's end_turn-only assumption)."""
        _write_transcript(
            str(tmp_path),
            CWD,
            SID,
            "agent-aaaa",
            [
                _assistant(stop_reason="tool_use", tool_use=True),
                _user_tool_result(),
                _assistant(thinking="done thinking", stop_reason=None),
                _assistant(text="All finished.", stop_reason=stop_reason),
            ],
        )
        assert subagent_in_flight(CWD, SID, projects_root=str(tmp_path)) is False

    def test_trailing_thinking_only_is_in_flight(self, tmp_path):
        """A trailing thinking-only assistant record means the model is mid-turn.

        Corrects an assertion that read this shape as "ambiguous → fail-safe
        False". Thinking is not an answer: the record is written while the
        model is still working, and one real transcript in the corpus ends
        here because its subagent was killed mid-thought.
        """
        _write_transcript(
            str(tmp_path),
            CWD,
            SID,
            "agent-aaaa",
            [_assistant(thinking="hmm", stop_reason=None)],
        )
        assert subagent_in_flight(CWD, SID, projects_root=str(tmp_path)) is True

    def test_trailing_user_text_is_in_flight(self, tmp_path):
        """A trailing plain user text means the assistant has not replied yet.

        Corrects an assertion that read this shape as "fail-safe False". 77
        real transcripts end here — every one a subagent that was interrupted
        rather than one that answered.
        """
        _write_transcript(
            str(tmp_path),
            CWD,
            SID,
            "agent-aaaa",
            [_user_text("please continue")],
        )
        assert subagent_in_flight(CWD, SID, projects_root=str(tmp_path)) is True


class TestEveryAgentIsChecked:
    """Every subagent is inspected, not only the most recently modified one."""

    def test_live_agent_found_behind_a_more_recent_finished_one(self, tmp_path):
        """dev-A is live, dev-B finished more recently → still in flight.

        Reading only ``ids[-1]`` (the newest by mtime) reports "not in flight"
        here while A is still running.
        """
        _write_transcript(
            str(tmp_path),
            CWD,
            SID,
            "agent-live",
            [_assistant(stop_reason="tool_use", tool_use=True)],
        )
        p_finished = _write_transcript(
            str(tmp_path),
            CWD,
            SID,
            "agent-finished",
            [_assistant(text="done", stop_reason="end_turn")],
        )
        # Make the FINISHED agent the newest by mtime, still inside the window.
        os.utime(p_finished, None)
        assert subagent_in_flight(CWD, SID, projects_root=str(tmp_path)) is True

    def test_all_agents_finished_is_not_in_flight(self, tmp_path):
        _write_transcript(
            str(tmp_path), CWD, SID, "agent-a", [_assistant(text="done", stop_reason="end_turn")]
        )
        _write_transcript(
            str(tmp_path), CWD, SID, "agent-b", [_assistant(text="done", stop_reason=None)]
        )
        assert subagent_in_flight(CWD, SID, projects_root=str(tmp_path)) is False


class TestMtimeWindow:
    """Recency scopes the shape check to the CURRENT turn.

    Sidechain files accumulate under the claude session id, not the turn, so a
    subagent stranded three turns ago would otherwise read as in flight
    forever and downgrade every later clean exit.
    """

    def test_stale_in_flight_shape_is_not_in_flight(self, tmp_path):
        """An unmistakably mid-exchange transcript, untouched for hours → False."""
        path = _write_transcript(
            str(tmp_path),
            CWD,
            SID,
            "agent-stranded",
            [_assistant(stop_reason="tool_use", tool_use=True)],
        )
        os.utime(path, (1000, 1000))
        assert subagent_in_flight(CWD, SID, projects_root=str(tmp_path)) is False

    def test_stale_task_prompt_is_not_in_flight(self, tmp_path):
        """The just-spawned shape from an earlier turn is equally stale."""
        path = _write_transcript(str(tmp_path), CWD, SID, "agent-old", [_task_prompt()])
        os.utime(path, (1000, 1000))
        assert subagent_in_flight(CWD, SID, projects_root=str(tmp_path)) is False

    def test_window_boundary_is_honored(self, tmp_path):
        """A transcript just inside the caller's window counts, just outside does not."""
        path = _write_transcript(str(tmp_path), CWD, SID, "agent-edge", [_task_prompt()])
        os.utime(path, (time.time() - 30, time.time() - 30))
        assert subagent_in_flight(CWD, SID, projects_root=str(tmp_path), window_s=60) is True
        assert subagent_in_flight(CWD, SID, projects_root=str(tmp_path), window_s=10) is False

    def test_future_mtime_counts_as_recent(self, tmp_path):
        """Clock skew must not read as "finished" — a future mtime is fresh."""
        path = _write_transcript(str(tmp_path), CWD, SID, "agent-skewed", [_task_prompt()])
        os.utime(path, (time.time() + 300, time.time() + 300))
        assert subagent_in_flight(CWD, SID, projects_root=str(tmp_path)) is True


class TestFailSafe:
    def test_missing_projects_root_is_false(self, tmp_path):
        assert subagent_in_flight(CWD, SID, projects_root=str(tmp_path / "nope")) is False

    def test_no_subagents_dir_is_false(self, tmp_path):
        # cwd slug dir exists but no subagents transcripts.
        slug = os.path.realpath(CWD).replace("/", "-").replace(".", "-")
        os.makedirs(os.path.join(str(tmp_path), slug, SID), exist_ok=True)
        assert subagent_in_flight(CWD, SID, projects_root=str(tmp_path)) is False

    def test_empty_cwd_is_false(self, tmp_path):
        assert subagent_in_flight("", SID, projects_root=str(tmp_path)) is False

    def test_empty_session_id_is_false(self, tmp_path):
        assert subagent_in_flight(CWD, "", projects_root=str(tmp_path)) is False

    def test_partial_trailing_line_ignored(self, tmp_path):
        """A non-newline-terminated trailing line is mid-write → excluded. The
        last COMPLETE record (a tool_use) drives the decision."""
        slug = os.path.realpath(CWD).replace("/", "-").replace(".", "-")
        base = os.path.join(str(tmp_path), slug, SID, "subagents")
        os.makedirs(base, exist_ok=True)
        path = os.path.join(base, "agent-aaaa.jsonl")
        with open(path, "w") as f:
            f.write(json.dumps(_assistant(stop_reason="tool_use", tool_use=True)) + "\n")
            f.write('{"type": "user", "message": {"role": "user", "content')  # partial
        assert subagent_in_flight(CWD, SID, projects_root=str(tmp_path)) is True

    def test_garbage_lines_are_skipped(self, tmp_path):
        slug = os.path.realpath(CWD).replace("/", "-").replace(".", "-")
        base = os.path.join(str(tmp_path), slug, SID, "subagents")
        os.makedirs(base, exist_ok=True)
        path = os.path.join(base, "agent-aaaa.jsonl")
        with open(path, "w") as f:
            f.write("not json at all\n")
            f.write(json.dumps(_assistant(text="final", stop_reason="end_turn")) + "\n")
        assert subagent_in_flight(CWD, SID, projects_root=str(tmp_path)) is False


# --------------------------------------------------------------------------
# AC2 — runner finalization-chokepoint downgrade wiring (#2420)
#
# The is_clean-scoped chokepoint must downgrade EVERY wrap-up-eligible clean
# reason (PM_COMPLETE / PM_USER / PM_NEEDS_HUMAN / PM_FLOOR_DELIVERED) to the
# non-clean PM_USER_SUBAGENT_LIVE anomaly when a spawned subagent is still in
# flight at finalization, so _runner_final_status returns "failed" — closing
# the two-literals bypass hole flagged in critique. A clean exit with NO live
# subagent must stay clean and finalize "completed".
# --------------------------------------------------------------------------
from unittest.mock import MagicMock, patch  # noqa: E402

from agent.session_executor import _runner_final_status  # noqa: E402
from agent.session_runner.adapter import SessionRunnerAdapter  # noqa: E402
from agent.session_runner.role_driver import HeadlessTurnOutcome  # noqa: E402
from agent.session_runner.router import ExitReason  # noqa: E402
from agent.session_runner.runner import SessionRunner, _RouteDecision  # noqa: E402

_CLEAN_WRAPUP_REASONS = [
    ExitReason.PM_COMPLETE,
    ExitReason.PM_USER,
    ExitReason.PM_NEEDS_HUMAN,
    ExitReason.PM_FLOOR_DELIVERED,
]


class _FakeSession:
    def __init__(self):
        self.session_id = "sess-2420-downgrade"
        self.chat_id = 111
        self.telegram_message_id = 222
        self.session_events = None
        self.last_stdout_at = None

    def save(self, update_fields=None):
        pass


class _ScriptedDriver:
    """Returns one turn-ended outcome, then carries a claude_session_id so the
    chokepoint's sidechain probe is reached."""

    def __init__(self):
        self.claude_session_id = SID

    async def run_turn(self, message):
        return HeadlessTurnOutcome(reply_text="ok", turn_ended=True, turn_end_source="result")


def _make_downgrade_runner(exit_reason):
    session = _FakeSession()
    adapter = SessionRunnerAdapter(
        session, "test-proj", "telegram", resolve_callbacks=lambda pk, t: (lambda *a: None, None)
    )
    driver = _ScriptedDriver()
    runner = SessionRunner(
        agent_session=session,
        adapter=adapter,
        working_dir=CWD,
        driver=driver,
        steering_pop_fn=lambda: [],
        session_type="eng",
    )
    # Force the route decision to break with the parametrized clean reason.
    runner._route_turn = lambda outcome: _RouteDecision(True, exit_reason=exit_reason)

    # Neutralize the wrap-up guard (fires because user_facing_routed is False)
    # so the injected clean reason survives unchanged to the chokepoint — this
    # test exercises the is_clean downgrade, not wrap-up reassignment.
    async def _noop_wrapup(summary):
        return None

    runner._run_wrapup_guard = _noop_wrapup
    return runner, session


@pytest.mark.asyncio
@pytest.mark.parametrize("clean_reason", _CLEAN_WRAPUP_REASONS)
async def test_clean_exit_with_live_subagent_downgrades(clean_reason):
    """Each wrap-up-eligible clean reason downgrades to PM_USER_SUBAGENT_LIVE
    when a subagent is in flight → _runner_final_status returns 'failed'."""
    runner, _ = _make_downgrade_runner(clean_reason)
    with patch("agent.session_runner.runner.subagent_in_flight", return_value=True):
        summary = await runner.run("go")

    assert summary.exit_reason is ExitReason.PM_USER_SUBAGENT_LIVE, (
        f"clean reason {clean_reason} with a live subagent must downgrade"
    )
    session = MagicMock()
    session.exit_reason = summary.exit_reason.value
    assert _runner_final_status(None, session) == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("clean_reason", _CLEAN_WRAPUP_REASONS)
async def test_clean_exit_without_live_subagent_stays_completed(clean_reason):
    """With no live subagent, a clean reason is untouched → 'completed'."""
    runner, _ = _make_downgrade_runner(clean_reason)
    with patch("agent.session_runner.runner.subagent_in_flight", return_value=False):
        summary = await runner.run("go")

    assert summary.exit_reason is clean_reason
    session = MagicMock()
    session.exit_reason = summary.exit_reason.value
    assert _runner_final_status(None, session) == "completed"
