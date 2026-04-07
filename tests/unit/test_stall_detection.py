"""Unit tests for stall detection in monitoring/session_watchdog.py.

Tests check_stalled_sessions (detection) and fix_unhealthy_session (abandon).
The old stall retry mechanisms (_recover_stalled_pending, _kill_stalled_worker,
_enqueue_stall_retry) were deleted in the bridge-resilience refactor.
Recovery is now handled by the unified _agent_session_health_check in agent/agent_session_queue.py.
"""

import time
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from monitoring.session_watchdog import (
    STALL_THRESHOLD_ACTIVE,
    STALL_THRESHOLD_PENDING,
    STALL_THRESHOLD_RUNNING,
    STALL_THRESHOLDS,
    _to_timestamp,
    check_stalled_sessions,
    fix_unhealthy_session,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_agent_session(
    session_id="test-stall-001",
    status="active",
    started_at="DEFAULT",
    created_at="DEFAULT",
    updated_at="DEFAULT",
    project_key="test",
    chat_id="12345",
    agent_session_id="session-001",
    history=None,
):
    now = time.time()
    ns = SimpleNamespace(
        session_id=session_id,
        agent_session_id=agent_session_id,
        status=status,
        started_at=now - 60 if started_at == "DEFAULT" else started_at,
        created_at=now - 120 if created_at == "DEFAULT" else created_at,
        updated_at=now if updated_at == "DEFAULT" else updated_at,
        project_key=project_key,
        chat_id=chat_id,
    )
    _history = history or []
    ns._get_history_list = lambda: _history
    ns.log_lifecycle_transition = MagicMock()
    ns.save = MagicMock()
    ns.delete = MagicMock()
    return ns


def _mock_query_for_sessions(sessions_by_status):
    def filter_fn(**kwargs):
        status = kwargs.get("status", "")
        return sessions_by_status.get(status, [])

    return SimpleNamespace(filter=filter_fn)


def _stalled_session_ids(result):
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


# ===================================================================
# check_stalled_sessions
# ===================================================================


class TestCheckStalledSessions:
    def test_no_sessions_returns_empty(self):
        mock_query = _mock_query_for_sessions({})
        with patch("monitoring.session_watchdog.AgentSession.query", mock_query):
            result = check_stalled_sessions()
            assert result == []

    def test_healthy_pending_not_stalled(self):
        now = time.time()
        session = _make_agent_session(
            status="pending",
            created_at=now - 60,
            started_at=None,
        )
        mock_query = _mock_query_for_sessions({"pending": [session]})
        with patch("monitoring.session_watchdog.AgentSession.query", mock_query):
            result = check_stalled_sessions()
            assert result == []

    def test_stalled_pending_detected(self):
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

    def test_stalled_running_detected(self):
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
        now = time.time()
        session = _make_agent_session(
            session_id="stalled-active",
            status="active",
            updated_at=now - (STALL_THRESHOLD_ACTIVE + 60),
            started_at=now - 3600,
        )
        mock_query = _mock_query_for_sessions({"active": [session]})
        with patch("monitoring.session_watchdog.AgentSession.query", mock_query):
            result = check_stalled_sessions()
            assert "stalled-active" in _stalled_session_ids(result)

    def test_active_with_recent_activity_not_stalled(self):
        now = time.time()
        session = _make_agent_session(
            status="active",
            updated_at=now - 30,
            started_at=now - 3600,
        )
        mock_query = _mock_query_for_sessions({"active": [session]})
        with patch("monitoring.session_watchdog.AgentSession.query", mock_query):
            result = check_stalled_sessions()
            assert result == []

    def test_query_exception_returns_empty(self):
        mock_query = MagicMock()
        mock_query.filter.side_effect = Exception("Redis down")
        with patch("monitoring.session_watchdog.AgentSession.query", mock_query):
            result = check_stalled_sessions()
            assert result == []


# ===================================================================
# fix_unhealthy_session (simplified — no retry, just abandon)
# ===================================================================


class TestFixUnhealthySession:
    @pytest.mark.asyncio
    async def test_silent_session_abandoned(self):
        """Silent sessions are abandoned directly (no retry mechanism)."""
        now = time.time()
        session = _make_agent_session(
            session_id="abandon-test",
            status="active",
            updated_at=now - 2000,
            started_at=now - 3000,
        )
        assessment = {
            "healthy": False,
            "issues": ["Silent for 33 minutes"],
            "severity": "warning",
        }

        with patch(
            "monitoring.session_watchdog._safe_abandon_session",
            return_value=True,
        ) as mock_abandon:
            result = await fix_unhealthy_session(session, assessment)
            assert result is True
            mock_abandon.assert_called_once()

    @pytest.mark.asyncio
    async def test_critical_issues_abandoned_with_issue(self):
        """Critical issues are abandoned and a GitHub issue is created."""
        now = time.time()
        session = _make_agent_session(
            session_id="critical-test",
            status="active",
            updated_at=now - 100,
            started_at=now - 500,
        )
        assessment = {
            "healthy": False,
            "issues": [
                "Looping: Bash called 5 times",
                "Error cascade: 5 errors",
            ],
            "severity": "critical",
        }

        with (
            patch("monitoring.session_watchdog._safe_abandon_session") as mock_abandon,
            patch(
                "monitoring.session_watchdog.create_session_issue",
                new_callable=AsyncMock,
            ) as mock_issue,
        ):
            result = await fix_unhealthy_session(session, assessment)
            assert result is True
            mock_abandon.assert_called_once()
            mock_issue.assert_called_once()

    @pytest.mark.asyncio
    async def test_long_running_session_abandoned(self):
        """Long-running sessions (>2h) are abandoned."""
        now = time.time()
        session = _make_agent_session(
            session_id="long-test",
            status="active",
            updated_at=now - 100,  # Recent activity
            started_at=now - 8000,  # >2 hours
        )
        assessment = {
            "healthy": False,
            "issues": ["Running for 2 hours"],
            "severity": "warning",
        }

        with patch(
            "monitoring.session_watchdog._safe_abandon_session",
            return_value=True,
        ) as mock_abandon:
            result = await fix_unhealthy_session(session, assessment)
            assert result is True
            mock_abandon.assert_called_once()


# ===================================================================
# _to_timestamp — UTC fix for naive datetimes (issue #777)
# ===================================================================


class TestToTimestamp:
    def test_none_returns_none(self):
        assert _to_timestamp(None) is None

    def test_float_passthrough(self):
        ts = time.time()
        assert _to_timestamp(ts) == ts

    def test_int_passthrough(self):
        assert _to_timestamp(1234567890) == 1234567890.0

    def test_aware_datetime_returns_correct_timestamp(self):
        aware = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        assert _to_timestamp(aware) == aware.timestamp()

    def test_naive_datetime_treated_as_utc(self):
        """Naive datetime (as returned by Popoto SortedField) must be
        treated as UTC, not local time.  On a UTC+7 machine, the old code
        would inflate the timestamp by 25200 seconds; after the fix both
        forms must agree within 1 second of each other."""
        naive = datetime.utcnow()
        aware = datetime.now(tz=UTC)
        assert abs(_to_timestamp(naive) - _to_timestamp(aware)) < 1.0

    def test_naive_matches_aware_explicit_value(self):
        """Verify with a fixed timestamp to rule out timing jitter."""
        naive = datetime(2026, 4, 7, 10, 0, 0)
        aware = datetime(2026, 4, 7, 10, 0, 0, tzinfo=UTC)
        assert _to_timestamp(naive) == _to_timestamp(aware)

    def test_unrecognized_type_returns_none(self):
        assert _to_timestamp("not-a-datetime") is None
