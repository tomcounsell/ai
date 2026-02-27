"""Unit tests for monitoring/session_status.py -- CLI session status report."""

import time
from unittest.mock import MagicMock, patch

from monitoring.session_status import format_duration, get_session_report

# ===================================================================
# format_duration
# ===================================================================


class TestFormatDuration:
    def test_seconds(self):
        assert format_duration(30) == "30s"

    def test_zero(self):
        assert format_duration(0) == "0s"

    def test_minutes(self):
        assert format_duration(120) == "2m"

    def test_boundary_59s(self):
        assert format_duration(59) == "59s"

    def test_boundary_60s(self):
        assert format_duration(60) == "1m"

    def test_hours(self):
        assert format_duration(7200) == "2.0h"

    def test_boundary_3600(self):
        assert format_duration(3600) == "1.0h"


# ===================================================================
# get_session_report
# ===================================================================


def _make_fake_session(
    session_id="sess-001",
    job_id="job-001",
    status="active",
    created_at=None,
    started_at=None,
    last_activity=None,
    project_key="test",
    last_transition_at=None,
    history=None,
):
    """Create a mock AgentSession for report testing."""
    now = time.time()
    mock = MagicMock()
    mock.session_id = session_id
    mock.job_id = job_id
    mock.status = status
    mock.created_at = created_at or now - 300
    mock.started_at = started_at or now - 200
    mock.last_activity = last_activity or now - 10
    mock.project_key = project_key
    mock.last_transition_at = last_transition_at
    mock._get_history_list.return_value = history or []
    return mock


# Patch path: AgentSession is imported inside get_session_report() from
# models.agent_session, so we patch at the source module.
_AGENT_SESSION_PATCH = "models.agent_session.AgentSession"


class TestGetSessionReport:
    def test_no_active_sessions(self):
        """When no sessions exist, report says so."""
        mock_query = MagicMock()
        mock_query.all.return_value = []
        with patch(_AGENT_SESSION_PATCH) as mock_cls:
            mock_cls.query = mock_query
            report = get_session_report()
            assert "No active sessions" in report

    def test_active_session_shown(self):
        """Active sessions appear in the report."""
        session = _make_fake_session(session_id="active-123", status="active")
        mock_query = MagicMock()
        mock_query.all.return_value = [session]
        with patch(_AGENT_SESSION_PATCH) as mock_cls:
            mock_cls.query = mock_query
            report = get_session_report()
            assert "active-123" in report
            assert "active" in report
            assert "SESSION STATUS REPORT" in report

    def test_completed_excluded_by_default(self):
        """Completed sessions are excluded unless --all is used."""
        completed = _make_fake_session(session_id="done-456", status="completed")
        active = _make_fake_session(session_id="live-789", status="active")
        mock_query = MagicMock()
        mock_query.all.return_value = [completed, active]
        with patch(_AGENT_SESSION_PATCH) as mock_cls:
            mock_cls.query = mock_query
            report = get_session_report(include_completed=False)
            assert "done-456" not in report
            assert "live-789" in report

    def test_completed_included_with_flag(self):
        """Completed sessions included when include_completed=True."""
        completed = _make_fake_session(session_id="done-456", status="completed")
        mock_query = MagicMock()
        mock_query.all.return_value = [completed]
        with patch(_AGENT_SESSION_PATCH) as mock_cls:
            mock_cls.query = mock_query
            report = get_session_report(include_completed=True)
            assert "done-456" in report

    def test_stalled_only_filter(self):
        """Only stalled sessions shown when stalled_only=True."""
        now = time.time()
        stalled = _make_fake_session(
            session_id="stalled-001",
            status="pending",
            created_at=now - 600,  # 10 min, over 5 min threshold
            started_at=None,
            last_transition_at=None,
        )
        stalled.started_at = None
        healthy = _make_fake_session(
            session_id="healthy-001",
            status="active",
            last_activity=now - 10,
            started_at=now - 60,
            last_transition_at=None,
        )
        mock_query = MagicMock()
        mock_query.all.return_value = [stalled, healthy]
        with patch(_AGENT_SESSION_PATCH) as mock_cls:
            mock_cls.query = mock_query
            report = get_session_report(stalled_only=True)
            assert "stalled-001" in report
            assert "healthy-001" not in report

    def test_no_stalled_sessions_message(self):
        """When filtering for stalled but none exist, get clear message."""
        now = time.time()
        healthy = _make_fake_session(
            session_id="healthy-001",
            status="active",
            last_activity=now - 10,
        )
        mock_query = MagicMock()
        mock_query.all.return_value = [healthy]
        with patch(_AGENT_SESSION_PATCH) as mock_cls:
            mock_cls.query = mock_query
            report = get_session_report(stalled_only=True)
            assert "No stalled sessions" in report

    def test_stall_marker_in_report(self):
        """Stalled sessions have a STALLED marker in output."""
        now = time.time()
        stalled = _make_fake_session(
            session_id="stalled-002",
            status="pending",
            created_at=now - 600,
            started_at=None,
            last_transition_at=None,
        )
        stalled.started_at = None
        mock_query = MagicMock()
        mock_query.all.return_value = [stalled]
        with patch(_AGENT_SESSION_PATCH) as mock_cls:
            mock_cls.query = mock_query
            report = get_session_report()
            assert "STALLED" in report

    def test_report_includes_total_count(self):
        """Report footer shows total session count."""
        sessions = [
            _make_fake_session(session_id=f"sess-{i}", status="active")
            for i in range(3)
        ]
        mock_query = MagicMock()
        mock_query.all.return_value = sessions
        with patch(_AGENT_SESSION_PATCH) as mock_cls:
            mock_cls.query = mock_query
            report = get_session_report()
            assert "Total: 3 sessions" in report
