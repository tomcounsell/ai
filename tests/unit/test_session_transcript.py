"""Tests for bridge.session_transcript.complete_transcript (issue #1156).

Covers the transcript-boundary skip branch that prevents the
`waiting_for_children → completed/failed` bypass. The skip is scoped to the
two `finalize_session` call sites (the transcript-end path here and the Stop
hook in `.claude/hooks/stop.py`); non-terminal transitions and sessions in
other statuses are unaffected.

Fixtures use the autouse ``redis_test_db`` fixture for Popoto isolation.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest

from bridge.session_transcript import SESSION_LOGS_DIR, complete_transcript
from models.agent_session import AgentSession


@pytest.fixture
def waiting_pm_session(redis_test_db):
    """A PM session currently in waiting_for_children."""
    return AgentSession.create(
        session_id="pm-wfc-transcript-001",
        session_type="pm",
        project_key="test",
        status="waiting_for_children",
        chat_id="999",
        sender_name="TestUser",
        message_text="Run the pipeline",
        created_at=datetime.now(tz=UTC),
        started_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
        turn_count=0,
        tool_call_count=0,
    )


@pytest.fixture
def running_session(redis_test_db):
    """A regular running session (no children waiting)."""
    return AgentSession.create(
        session_id="sess-running-transcript-001",
        session_type="dev",
        project_key="test",
        status="running",
        chat_id="999",
        sender_name="TestUser",
        message_text="A task",
        created_at=datetime.now(tz=UTC),
        started_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
        turn_count=0,
        tool_call_count=0,
    )


def _reload(session_id: str) -> AgentSession:
    """Re-read a session from Redis by session_id."""
    matches = list(AgentSession.query.filter(session_id=session_id))
    assert matches, f"session {session_id} not found"
    return matches[0]


def _session_end_marker_count(session_id: str) -> int:
    """Count SESSION_END markers in the transcript file for this session."""
    log_path = SESSION_LOGS_DIR / session_id / "transcript.txt"
    if not log_path.exists():
        return 0
    return sum(1 for line in log_path.read_text().splitlines() if "SESSION_END" in line)


class TestCompleteTranscriptSkipWhenWaitingForChildren:
    """The transcript-end path must NOT collapse a waiting_for_children PM."""

    def test_complete_transcript_skips_finalize_when_waiting_for_children(
        self, waiting_pm_session, caplog
    ):
        """PM in waiting_for_children + status="completed" → skip, stay wfc."""
        caplog.set_level(logging.INFO, logger="bridge.session_transcript")

        complete_transcript(waiting_pm_session.session_id, status="completed")

        # (a) SESSION_END marker still written
        assert _session_end_marker_count(waiting_pm_session.session_id) >= 1

        # (b) status unchanged
        reloaded = _reload(waiting_pm_session.session_id)
        assert reloaded.status == "waiting_for_children"

        # (c) skip log line emitted
        skip_messages = [
            r.getMessage()
            for r in caplog.records
            if "complete_transcript skipping terminal transition" in r.getMessage()
        ]
        assert skip_messages, "expected skip INFO log line"
        assert "issue #1156" in skip_messages[0]
        assert waiting_pm_session.session_id in skip_messages[0]

    def test_complete_transcript_skips_finalize_when_waiting_for_children_with_failed_status(
        self, waiting_pm_session, caplog
    ):
        """PM in waiting_for_children + status="failed" → also skipped."""
        caplog.set_level(logging.INFO, logger="bridge.session_transcript")

        complete_transcript(waiting_pm_session.session_id, status="failed")

        assert _session_end_marker_count(waiting_pm_session.session_id) >= 1

        reloaded = _reload(waiting_pm_session.session_id)
        assert reloaded.status == "waiting_for_children"

        skip_messages = [
            r.getMessage()
            for r in caplog.records
            if "complete_transcript skipping terminal transition" in r.getMessage()
        ]
        assert skip_messages, "expected skip INFO log line on failed target too"


class TestCompleteTranscriptRunningUnaffected:
    """Regression: the skip only applies to waiting_for_children."""

    def test_complete_transcript_finalizes_when_running(self, running_session):
        """A running session still transitions normally via complete_transcript."""
        complete_transcript(running_session.session_id, status="completed")

        reloaded = _reload(running_session.session_id)
        assert reloaded.status == "completed"

    def test_complete_transcript_passes_through_dormant_transition(self, waiting_pm_session):
        """Non-terminal `dormant` target is unaffected by the skip.

        The skip branch only triggers when ``status in ("completed", "failed")``.
        A ``dormant`` target routes through ``transition_status`` unchanged.
        """
        complete_transcript(waiting_pm_session.session_id, status="dormant")

        reloaded = _reload(waiting_pm_session.session_id)
        assert reloaded.status == "dormant"
