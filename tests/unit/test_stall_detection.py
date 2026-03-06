"""Unit tests for stall detection in monitoring/session_watchdog.py."""

import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from monitoring.session_watchdog import (
    STALL_THRESHOLD_ACTIVE,
    STALL_THRESHOLD_PENDING,
    STALL_THRESHOLD_RUNNING,
    STALL_THRESHOLDS,
    check_stalled_sessions,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_agent_session(
    session_id="test-stall-001",
    status="active",
    started_at="DEFAULT",
    created_at="DEFAULT",
    last_activity="DEFAULT",
    project_key="test",
    chat_id="12345",
    job_id="job-001",
    history=None,
):
    """Create a fake AgentSession for testing stall detection.

    Use explicit None for fields that should be None (e.g., started_at=None
    for a pending session that hasn't started). The "DEFAULT" sentinel provides
    reasonable defaults.
    """
    now = time.time()
    ns = SimpleNamespace(
        session_id=session_id,
        job_id=job_id,
        status=status,
        started_at=now - 60 if started_at == "DEFAULT" else started_at,
        created_at=now - 120 if created_at == "DEFAULT" else created_at,
        last_activity=now if last_activity == "DEFAULT" else last_activity,
        project_key=project_key,
        chat_id=chat_id,
        last_transition_at=None,  # Not on model yet, always None
    )
    _history = history or []
    ns._get_history_list = lambda: _history
    return ns


def _mock_query_for_sessions(sessions_by_status):
    """Create a mock query that returns sessions filtered by status.

    Args:
        sessions_by_status: dict mapping status string to list of sessions
    """

    def filter_fn(**kwargs):
        status = kwargs.get("status", "")
        return sessions_by_status.get(status, [])

    return SimpleNamespace(filter=filter_fn)


def _stalled_session_ids(result):
    """Extract session_ids from a list of stalled session dicts."""
    return [s["session_id"] for s in result]


# ===================================================================
# Constants
# ===================================================================


class TestStallConstants:
    def test_pending_threshold(self):
        assert STALL_THRESHOLD_PENDING == 300

    def test_running_threshold(self):
        assert STALL_THRESHOLD_RUNNING == 2700

    def test_active_threshold(self):
        assert STALL_THRESHOLD_ACTIVE == 600

    def test_stall_thresholds_dict(self):
        assert STALL_THRESHOLDS == {
            "pending": 300,
            "running": 2700,
            "active": 600,
        }

    def test_dict_matches_individual_constants(self):
        assert STALL_THRESHOLDS["pending"] == STALL_THRESHOLD_PENDING
        assert STALL_THRESHOLDS["running"] == STALL_THRESHOLD_RUNNING
        assert STALL_THRESHOLDS["active"] == STALL_THRESHOLD_ACTIVE


# ===================================================================
# check_stalled_sessions
# ===================================================================


class TestCheckStalledSessions:
    """Tests for the check_stalled_sessions function."""

    def test_no_sessions_returns_empty(self):
        """When no sessions match any filter, return empty list."""
        mock_query = _mock_query_for_sessions({})
        with patch("monitoring.session_watchdog.AgentSession.query", mock_query):
            result = check_stalled_sessions()
            assert result == []

    def test_healthy_pending_not_stalled(self):
        """A pending session within threshold is not stalled."""
        now = time.time()
        session = _make_agent_session(
            status="pending",
            created_at=now - 60,  # 1 minute ago, well within 5 min threshold
            started_at=None,
        )
        mock_query = _mock_query_for_sessions({"pending": [session]})
        with patch("monitoring.session_watchdog.AgentSession.query", mock_query):
            result = check_stalled_sessions()
            assert result == []

    def test_stalled_pending_detected(self):
        """A pending session exceeding threshold is detected as stalled."""
        now = time.time()
        session = _make_agent_session(
            session_id="stalled-pending",
            status="pending",
            created_at=now - (STALL_THRESHOLD_PENDING + 60),
            started_at=None,
        )
        mock_query = _mock_query_for_sessions({"pending": [session]})
        with patch("monitoring.session_watchdog.AgentSession.query", mock_query):
            result = check_stalled_sessions()
            assert "stalled-pending" in _stalled_session_ids(result)

    def test_stalled_pending_returns_dict(self):
        """Stalled session result contains expected dict keys."""
        now = time.time()
        session = _make_agent_session(
            session_id="dict-check",
            status="pending",
            created_at=now - (STALL_THRESHOLD_PENDING + 60),
            started_at=None,
            history=["[lifecycle] pending: job enqueued"],
        )
        mock_query = _mock_query_for_sessions({"pending": [session]})
        with patch("monitoring.session_watchdog.AgentSession.query", mock_query):
            result = check_stalled_sessions()
            assert len(result) == 1
            info = result[0]
            assert info["session_id"] == "dict-check"
            assert info["status"] == "pending"
            assert info["duration"] > STALL_THRESHOLD_PENDING
            assert info["threshold"] == STALL_THRESHOLD_PENDING
            assert info["project_key"] == "test"
            assert "pending" in info["last_history"]

    def test_stalled_running_detected(self):
        """A running session exceeding threshold is detected as stalled."""
        now = time.time()
        session = _make_agent_session(
            session_id="stalled-running",
            status="running",
            started_at=now - (STALL_THRESHOLD_RUNNING + 60),
            created_at=now - (STALL_THRESHOLD_RUNNING + 120),
        )
        mock_query = _mock_query_for_sessions({"running": [session]})
        with patch("monitoring.session_watchdog.AgentSession.query", mock_query):
            result = check_stalled_sessions()
            assert "stalled-running" in _stalled_session_ids(result)

    def test_stalled_active_no_recent_activity(self):
        """An active session with no recent activity is detected as stalled."""
        now = time.time()
        session = _make_agent_session(
            session_id="stalled-active",
            status="active",
            last_activity=now - (STALL_THRESHOLD_ACTIVE + 60),
            started_at=now - 3600,
        )
        mock_query = _mock_query_for_sessions({"active": [session]})
        with patch("monitoring.session_watchdog.AgentSession.query", mock_query):
            result = check_stalled_sessions()
            assert "stalled-active" in _stalled_session_ids(result)

    def test_active_with_recent_activity_not_stalled(self):
        """An active session with recent activity is healthy."""
        now = time.time()
        session = _make_agent_session(
            status="active",
            last_activity=now - 30,  # 30 seconds ago
            started_at=now - 3600,
        )
        mock_query = _mock_query_for_sessions({"active": [session]})
        with patch("monitoring.session_watchdog.AgentSession.query", mock_query):
            result = check_stalled_sessions()
            assert result == []

    def test_multiple_sessions_mixed(self):
        """Mixed healthy and stalled sessions: only stalled ones returned."""
        now = time.time()
        healthy = _make_agent_session(
            session_id="healthy-one",
            status="active",
            last_activity=now - 30,
        )
        stalled = _make_agent_session(
            session_id="stalled-one",
            status="pending",
            created_at=now - (STALL_THRESHOLD_PENDING + 120),
            started_at=None,
        )
        mock_query = _mock_query_for_sessions(
            {"pending": [stalled], "active": [healthy]}
        )
        with patch("monitoring.session_watchdog.AgentSession.query", mock_query):
            result = check_stalled_sessions()
            ids = _stalled_session_ids(result)
            assert "stalled-one" in ids
            assert "healthy-one" not in ids

    def test_boundary_pending_just_under_threshold(self):
        """A pending session just under the threshold is NOT stalled."""
        now = time.time()
        session = _make_agent_session(
            session_id="boundary",
            status="pending",
            # 10 seconds under threshold to avoid timing flakes
            created_at=now - (STALL_THRESHOLD_PENDING - 10),
            started_at=None,
        )
        mock_query = _mock_query_for_sessions({"pending": [session]})
        with patch("monitoring.session_watchdog.AgentSession.query", mock_query):
            result = check_stalled_sessions()
            assert result == []

    def test_uses_started_at_fallback_for_running(self):
        """For running sessions, started_at is used as timestamp reference."""
        now = time.time()
        session = _make_agent_session(
            session_id="running-fallback",
            status="running",
            started_at=now - (STALL_THRESHOLD_RUNNING + 10),
            created_at=now - (STALL_THRESHOLD_RUNNING + 500),
        )
        # last_transition_at is None, so should use started_at
        mock_query = _mock_query_for_sessions({"running": [session]})
        with patch("monitoring.session_watchdog.AgentSession.query", mock_query):
            result = check_stalled_sessions()
            assert "running-fallback" in _stalled_session_ids(result)

    def test_query_exception_returns_empty(self):
        """If querying sessions raises, return empty list gracefully."""
        mock_query = MagicMock()
        mock_query.filter.side_effect = Exception("Redis down")
        with patch("monitoring.session_watchdog.AgentSession.query", mock_query):
            result = check_stalled_sessions()
            assert result == []

    def test_returns_list_of_dicts(self):
        """Return value is a list of dicts with session_id strings."""
        now = time.time()
        session = _make_agent_session(
            session_id="id-check",
            status="pending",
            created_at=now - (STALL_THRESHOLD_PENDING + 60),
            started_at=None,
        )
        mock_query = _mock_query_for_sessions({"pending": [session]})
        with patch("monitoring.session_watchdog.AgentSession.query", mock_query):
            result = check_stalled_sessions()
            assert isinstance(result, list)
            assert all(isinstance(s, dict) for s in result)
            assert all("session_id" in s for s in result)
            assert all(isinstance(s["session_id"], str) for s in result)

    def test_no_history_shows_placeholder(self):
        """Sessions without history show 'no history' as last_history."""
        now = time.time()
        session = _make_agent_session(
            session_id="no-hist",
            status="pending",
            created_at=now - (STALL_THRESHOLD_PENDING + 60),
            started_at=None,
            history=[],
        )
        mock_query = _mock_query_for_sessions({"pending": [session]})
        with patch("monitoring.session_watchdog.AgentSession.query", mock_query):
            result = check_stalled_sessions()
            assert len(result) == 1
            assert result[0]["last_history"] == "no history"

    def test_lifecycle_stall_log_message(self, caplog):
        """Stalled sessions produce LIFECYCLE_STALL log messages."""
        import logging

        now = time.time()
        session = _make_agent_session(
            session_id="log-check",
            status="pending",
            created_at=now - (STALL_THRESHOLD_PENDING + 60),
            started_at=None,
        )
        mock_query = _mock_query_for_sessions({"pending": [session]})
        with patch("monitoring.session_watchdog.AgentSession.query", mock_query):
            with caplog.at_level(logging.WARNING, logger="monitoring.session_watchdog"):
                check_stalled_sessions()

        lifecycle_stall_logs = [
            r for r in caplog.records if "LIFECYCLE_STALL" in r.message
        ]
        assert len(lifecycle_stall_logs) >= 1
        msg = lifecycle_stall_logs[0].message
        assert "session=log-check" in msg
        assert "status=pending" in msg
