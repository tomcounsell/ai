"""Tests for ``subagent_in_flight`` — the Layer 2 liveness helper (#2420).

The helper reads the newest sidechain subagent transcript and returns ``True``
ONLY when it can positively determine the subagent is mid-flight (a pending
tool exchange). Every other case — a completed final response, a missing file,
a parse error — fails safe to ``False`` (do not downgrade): a false downgrade
that marks a genuinely-complete session ``failed`` is worse than a rare miss,
and Layer 1 already prevents the trigger in normal operation.

HARD-GATE ground truth (established against real on-disk transcripts under
``~/.claude/projects/*/*/subagents/agent-*.jsonl`` at build time — see the
build report on #2420): a sidechain JSONL has NO ``result``/``Stop`` record
type. Records are ``user``/``assistant``/``attachment``. The terminal signal
is the NEWEST record's shape:

- ``assistant`` whose ``stop_reason == "tool_use"`` (or whose final content
  block is ``tool_use``) → the model requested a tool and is awaiting its
  result → IN FLIGHT (``True``).
- ``user`` carrying a ``tool_result`` block → the tool returned but the
  assistant has not produced its next reply → IN FLIGHT (``True``).
- ``assistant`` text/thinking as the newest record → the final response.
  ``stop_reason`` is ``end_turn`` / ``stop_sequence`` / **or ``None``** (the
  streaming SDK-CLI flush frequently writes the closing text record with a
  ``null`` stop_reason). ALL of these are COMPLETE (``False``).

The ``stop_reason is None`` completed case is the load-bearing regression guard:
the original plan asserted completion required ``end_turn``/``stop_sequence``.
Real transcripts show ~24% of completed transcripts close on a ``null``
stop_reason. Keying "in flight" on "not end_turn/stop_sequence" would
false-downgrade every one of them. The helper instead keys "in flight" on a
POSITIVE pending-tool-exchange signal.
"""

from __future__ import annotations

import json
import os

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


CWD = "/tmp/sdlc2420-inflight-cwd"
SID = "11111111-1111-1111-1111-111111111111"


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

    def test_final_assistant_thinking_only_is_not_in_flight(self, tmp_path):
        """A trailing thinking-only assistant record is ambiguous → fail-safe False."""
        _write_transcript(
            str(tmp_path),
            CWD,
            SID,
            "agent-aaaa",
            [_assistant(thinking="hmm", stop_reason=None)],
        )
        assert subagent_in_flight(CWD, SID, projects_root=str(tmp_path)) is False

    def test_trailing_user_text_is_not_in_flight(self, tmp_path):
        """A trailing plain user text (not a tool_result) → fail-safe False."""
        _write_transcript(
            str(tmp_path),
            CWD,
            SID,
            "agent-aaaa",
            [_user_text("please continue")],
        )
        assert subagent_in_flight(CWD, SID, projects_root=str(tmp_path)) is False


class TestNewestFileSelection:
    def test_uses_newest_agent_transcript(self, tmp_path):
        """With multiple agent files, the helper reads the newest (by mtime)."""
        # Older agent: complete.
        p_old = _write_transcript(
            str(tmp_path), CWD, SID, "agent-old", [_assistant(text="done", stop_reason="end_turn")]
        )
        # Newer agent: in flight.
        p_new = _write_transcript(
            str(tmp_path),
            CWD,
            SID,
            "agent-new",
            [_assistant(stop_reason="tool_use", tool_use=True)],
        )
        # Force mtime ordering: old strictly older than new.
        os.utime(p_old, (1000, 1000))
        os.utime(p_new, (2000, 2000))
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
