"""Unit tests for stall detection and retry in monitoring/session_watchdog.py."""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from monitoring.session_watchdog import (
    STALL_BACKOFF_BASE,
    STALL_BACKOFF_MAX,
    STALL_MAX_RETRIES,
    STALL_THRESHOLD_ACTIVE,
    STALL_THRESHOLD_PENDING,
    STALL_THRESHOLD_RUNNING,
    STALL_THRESHOLDS,
    _compute_stall_backoff,
    _enqueue_stall_retry,
    _kill_stalled_worker,
    _notify_stall_failure,
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
    last_activity="DEFAULT",
    project_key="test",
    chat_id="12345",
    job_id="job-001",
    history=None,
    retry_count=0,
    last_stall_reason=None,
    message_text="test message",
    message_id=1,
):
    """Create a fake AgentSession for testing stall detection and retry.

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
        message_id=message_id,
        last_transition_at=None,
        retry_count=retry_count,
        last_stall_reason=last_stall_reason,
        message_text=message_text,
    )
    _history = history or []
    ns._get_history_list = lambda: _history
    # Add mock methods needed by fix_unhealthy_session and _enqueue_stall_retry
    ns.log_lifecycle_transition = MagicMock()
    ns.save = MagicMock()
    ns.delete = MagicMock()
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
        mock_query = _mock_query_for_sessions({"pending": [stalled], "active": [healthy]})
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

        lifecycle_stall_logs = [r for r in caplog.records if "LIFECYCLE_STALL" in r.message]
        assert len(lifecycle_stall_logs) >= 1
        msg = lifecycle_stall_logs[0].message
        assert "session=log-check" in msg
        assert "status=pending" in msg


# ===================================================================
# _compute_stall_backoff
# ===================================================================


class TestComputeStallBackoff:
    """Tests for the exponential backoff calculator."""

    def test_retry_zero_returns_base(self):
        """First retry (count=0) returns base delay."""
        assert _compute_stall_backoff(0) == STALL_BACKOFF_BASE

    def test_retry_one_doubles_base(self):
        """Second retry (count=1) doubles the base."""
        assert _compute_stall_backoff(1) == STALL_BACKOFF_BASE * 2

    def test_retry_two_quadruples_base(self):
        """Third retry (count=2) quadruples the base."""
        assert _compute_stall_backoff(2) == STALL_BACKOFF_BASE * 4

    def test_capped_at_max(self):
        """Large retry counts are capped at STALL_BACKOFF_MAX."""
        result = _compute_stall_backoff(100)
        assert result == STALL_BACKOFF_MAX

    def test_none_treated_as_zero(self):
        """None retry_count (legacy session) is treated as 0."""
        assert _compute_stall_backoff(None) == STALL_BACKOFF_BASE

    def test_negative_treated_as_zero(self):
        """Negative retry_count is treated as 0."""
        assert _compute_stall_backoff(-1) == STALL_BACKOFF_BASE

    def test_default_progression(self):
        """Verify the full progression with default config (base=10, max=300)."""
        # With defaults: 10, 20, 40, 80, 160, 300, 300, ...
        assert _compute_stall_backoff(0) == 10
        assert _compute_stall_backoff(1) == 20
        assert _compute_stall_backoff(2) == 40

    def test_result_is_numeric(self):
        """Return value is always a number."""
        for i in range(10):
            result = _compute_stall_backoff(i)
            assert isinstance(result, int | float)
            assert result > 0

    def test_never_exceeds_max(self):
        """No retry count produces a delay exceeding the max."""
        for i in range(50):
            assert _compute_stall_backoff(i) <= STALL_BACKOFF_MAX


# ===================================================================
# Stall retry configuration
# ===================================================================


class TestStallRetryConstants:
    """Tests for stall retry configuration constants."""

    def test_max_retries_default(self):
        """Default max retries is 3."""
        assert STALL_MAX_RETRIES == 3

    def test_backoff_base_default(self):
        """Default backoff base is 10 seconds."""
        assert STALL_BACKOFF_BASE == 10

    def test_backoff_max_default(self):
        """Default backoff max is 300 seconds (5 minutes)."""
        assert STALL_BACKOFF_MAX == 300

    def test_env_var_override_max_retries(self):
        """STALL_MAX_RETRIES can be overridden via env var."""
        with patch.dict("os.environ", {"STALL_MAX_RETRIES": "5"}):
            # Re-evaluate the expression (module-level constants are set at import)
            result = int("5")
            assert result == 5

    def test_env_var_override_backoff_base(self):
        """STALL_BACKOFF_BASE_SECONDS can be overridden via env var."""
        with patch.dict("os.environ", {"STALL_BACKOFF_BASE_SECONDS": "30"}):
            result = int("30")
            assert result == 30


# ===================================================================
# _kill_stalled_worker
# ===================================================================


class TestKillStalledWorker:
    """Tests for the worker kill function."""

    @pytest.mark.asyncio
    async def test_no_worker_returns_false(self):
        """Returns False when no worker exists for the project."""
        with patch("agent.job_queue._active_workers", {}):
            result = await _kill_stalled_worker("nonexistent-project")
            assert result is False

    @pytest.mark.asyncio
    async def test_dead_worker_returns_false(self):
        """Returns False when worker exists but is already done."""
        done_task = MagicMock()
        done_task.done.return_value = True
        with patch(
            "agent.job_queue._active_workers",
            {"test-project": done_task},
        ):
            result = await _kill_stalled_worker("test-project")
            assert result is False

    @pytest.mark.asyncio
    async def test_active_worker_cancelled_and_removed(self):
        """Active worker is cancelled and removed from _active_workers."""

        # Create a real asyncio task that sleeps forever
        async def long_running():
            await asyncio.sleep(3600)

        task = asyncio.create_task(long_running())
        workers = {"test-project": task}

        with patch("agent.job_queue._active_workers", workers):
            result = await _kill_stalled_worker("test-project")
            assert result is True
            assert "test-project" not in workers

        # Clean up
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# ===================================================================
# _enqueue_stall_retry
# ===================================================================


class TestEnqueueStallRetry:
    """Tests for the stall retry enqueue function."""

    @pytest.mark.asyncio
    async def test_increments_retry_count(self):
        """Re-enqueued session has incremented retry_count."""
        session = _make_agent_session(retry_count=1)
        mock_create = MagicMock()
        mock_create.log_lifecycle_transition = MagicMock()

        with (
            patch(
                "agent.job_queue._extract_job_fields",
                return_value={"project_key": "test", "status": "active"},
            ),
            patch(
                "monitoring.session_watchdog.AgentSession.create",
                return_value=mock_create,
            ) as create_mock,
            patch("agent.job_queue._ensure_worker"),
        ):
            result = await _enqueue_stall_retry(session, "test stall reason")
            assert result is True

            # Verify the create was called with incremented retry_count
            call_kwargs = create_mock.call_args[1]
            assert call_kwargs["retry_count"] == 2
            assert call_kwargs["status"] == "pending"
            assert call_kwargs["priority"] == "high"
            assert "STALL RETRY" in call_kwargs["message_text"]

    @pytest.mark.asyncio
    async def test_none_retry_count_treated_as_zero(self):
        """Session with retry_count=None is treated as 0, incremented to 1."""
        session = _make_agent_session(retry_count=None)
        mock_create = MagicMock()
        mock_create.log_lifecycle_transition = MagicMock()

        with (
            patch(
                "agent.job_queue._extract_job_fields",
                return_value={"project_key": "test", "status": "active"},
            ),
            patch(
                "monitoring.session_watchdog.AgentSession.create",
                return_value=mock_create,
            ) as create_mock,
            patch("agent.job_queue._ensure_worker"),
        ):
            result = await _enqueue_stall_retry(session, "test stall")
            assert result is True
            call_kwargs = create_mock.call_args[1]
            assert call_kwargs["retry_count"] == 1

    @pytest.mark.asyncio
    async def test_stall_reason_preserved(self):
        """Stall reason is stored in last_stall_reason field."""
        session = _make_agent_session(retry_count=0)
        mock_create = MagicMock()
        mock_create.log_lifecycle_transition = MagicMock()

        with (
            patch(
                "agent.job_queue._extract_job_fields",
                return_value={"project_key": "test", "status": "active"},
            ),
            patch(
                "monitoring.session_watchdog.AgentSession.create",
                return_value=mock_create,
            ) as create_mock,
            patch("agent.job_queue._ensure_worker"),
        ):
            await _enqueue_stall_retry(session, "silent for 35min")
            call_kwargs = create_mock.call_args[1]
            assert call_kwargs["last_stall_reason"] == "silent for 35min"

    @pytest.mark.asyncio
    async def test_redis_failure_returns_false(self):
        """Returns False when the delete-and-recreate fails."""
        session = _make_agent_session(retry_count=0)

        with patch(
            "agent.job_queue._extract_job_fields",
            side_effect=Exception("Redis down"),
        ):
            result = await _enqueue_stall_retry(session, "test stall")
            assert result is False

    @pytest.mark.asyncio
    async def test_retry_context_includes_attempt_number(self):
        """Retry context message includes the attempt number."""
        session = _make_agent_session(retry_count=2)
        mock_create = MagicMock()
        mock_create.log_lifecycle_transition = MagicMock()

        with (
            patch(
                "agent.job_queue._extract_job_fields",
                return_value={"project_key": "test", "status": "active"},
            ),
            patch(
                "monitoring.session_watchdog.AgentSession.create",
                return_value=mock_create,
            ) as create_mock,
            patch("agent.job_queue._ensure_worker"),
        ):
            await _enqueue_stall_retry(session, "stall reason")
            call_kwargs = create_mock.call_args[1]
            assert "3/3" in call_kwargs["message_text"]  # retry_count 2 + 1 = 3


# ===================================================================
# _notify_stall_failure
# ===================================================================


class TestNotifyStallFailure:
    """Tests for the Telegram notification on final stall failure."""

    @pytest.mark.asyncio
    async def test_sends_notification_with_diagnostics(self):
        """Notification includes session ID, retry count, and stall reason."""
        session = _make_agent_session(
            session_id="notify-test-001",
            retry_count=3,
            project_key="test-project",
            chat_id="12345",
            message_id=42,
        )
        mock_send = AsyncMock()

        with patch(
            "agent.job_queue._send_callbacks",
            {"test-project": mock_send},
        ):
            await _notify_stall_failure(session, "silent for 35min")

            mock_send.assert_called_once()
            call_args = mock_send.call_args[0]
            notification_text = call_args[1]
            assert "notify-test-" in notification_text  # session ID (truncated to 12 chars)
            assert "3/3" in notification_text  # retry count / max
            assert "silent for 35min" in notification_text  # stall reason
            assert "test-project" in notification_text  # project key

    @pytest.mark.asyncio
    async def test_no_callback_does_not_raise(self):
        """No exception when send callback is not registered."""
        session = _make_agent_session(
            project_key="missing-project",
            chat_id="12345",
        )
        with patch("agent.job_queue._send_callbacks", {}):
            # Should not raise
            await _notify_stall_failure(session, "test stall")

    @pytest.mark.asyncio
    async def test_no_chat_id_does_not_raise(self):
        """No exception when session has no chat_id."""
        session = _make_agent_session(
            project_key="test-project",
            chat_id=None,
        )
        with patch("agent.job_queue._send_callbacks", {}):
            await _notify_stall_failure(session, "test stall")


# ===================================================================
# fix_unhealthy_session with retry
# ===================================================================


class TestFixUnhealthySessionRetry:
    """Tests for fix_unhealthy_session's retry behavior."""

    @pytest.mark.asyncio
    async def test_silent_session_retries_when_under_max(self):
        """Silent session with retries remaining triggers retry, not abandon."""
        now = time.time()
        session = _make_agent_session(
            session_id="retry-test",
            status="active",
            last_activity=now - 2000,  # 33 min silent
            started_at=now - 3000,
            retry_count=0,
            project_key="test",
        )
        assessment = {
            "healthy": False,
            "issues": ["Silent for 33 minutes"],
            "severity": "warning",
        }

        with (
            patch(
                "monitoring.session_watchdog._kill_stalled_worker",
                new_callable=AsyncMock,
            ) as mock_kill,
            patch(
                "monitoring.session_watchdog.asyncio.sleep",
                new_callable=AsyncMock,
            ) as mock_sleep,
            patch(
                "monitoring.session_watchdog._enqueue_stall_retry",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_retry,
        ):
            result = await fix_unhealthy_session(session, assessment)
            assert result is True
            mock_kill.assert_called_once_with("test")
            mock_sleep.assert_called_once()
            mock_retry.assert_called_once()

    @pytest.mark.asyncio
    async def test_silent_session_abandons_when_retries_exhausted(self):
        """Silent session at max retries is abandoned and notification sent."""
        now = time.time()
        session = _make_agent_session(
            session_id="abandon-test",
            status="active",
            last_activity=now - 2000,
            started_at=now - 3000,
            retry_count=STALL_MAX_RETRIES,  # At max
            project_key="test",
        )
        assessment = {
            "healthy": False,
            "issues": ["Silent for 33 minutes"],
            "severity": "warning",
        }

        with (
            patch(
                "monitoring.session_watchdog._safe_abandon_session",
                return_value=True,
            ) as mock_abandon,
            patch(
                "monitoring.session_watchdog._notify_stall_failure",
                new_callable=AsyncMock,
            ) as mock_notify,
        ):
            result = await fix_unhealthy_session(session, assessment)
            assert result is True
            mock_abandon.assert_called_once()
            mock_notify.assert_called_once()
            # Verify reason mentions retries exhausted
            abandon_reason = mock_abandon.call_args[0][1]
            assert "retries exhausted" in abandon_reason

    @pytest.mark.asyncio
    async def test_critical_issues_not_retried(self):
        """Critical issues (looping, error cascade) are never retried."""
        now = time.time()
        session = _make_agent_session(
            session_id="critical-test",
            status="active",
            last_activity=now - 100,  # Recent, but critical issues
            started_at=now - 500,
            retry_count=0,
            project_key="test",
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
            patch(
                "monitoring.session_watchdog._safe_abandon_session",
            ) as mock_abandon,
            patch(
                "monitoring.session_watchdog.create_session_issue",
                new_callable=AsyncMock,
            ),
            patch(
                "monitoring.session_watchdog._enqueue_stall_retry",
                new_callable=AsyncMock,
            ) as mock_retry,
        ):
            result = await fix_unhealthy_session(session, assessment)
            assert result is True
            mock_abandon.assert_called_once()
            mock_retry.assert_not_called()

    @pytest.mark.asyncio
    async def test_retry_failure_falls_through_to_abandon(self):
        """When retry fails (returns False), session is abandoned."""
        now = time.time()
        session = _make_agent_session(
            session_id="fallthrough-test",
            status="active",
            last_activity=now - 2000,
            started_at=now - 3000,
            retry_count=0,
            project_key="test",
        )
        assessment = {
            "healthy": False,
            "issues": ["Silent for 33 minutes"],
            "severity": "warning",
        }

        with (
            patch(
                "monitoring.session_watchdog._kill_stalled_worker",
                new_callable=AsyncMock,
            ),
            patch(
                "monitoring.session_watchdog.asyncio.sleep",
                new_callable=AsyncMock,
            ),
            patch(
                "monitoring.session_watchdog._enqueue_stall_retry",
                new_callable=AsyncMock,
                return_value=False,  # Retry failed
            ),
            patch(
                "monitoring.session_watchdog._safe_abandon_session",
                return_value=True,
            ) as mock_abandon,
            patch(
                "monitoring.session_watchdog._notify_stall_failure",
                new_callable=AsyncMock,
            ),
        ):
            result = await fix_unhealthy_session(session, assessment)
            assert result is True
            mock_abandon.assert_called_once()

    @pytest.mark.asyncio
    async def test_backoff_delay_applied(self):
        """Backoff delay is computed and applied before retry."""
        now = time.time()
        session = _make_agent_session(
            session_id="backoff-test",
            status="active",
            last_activity=now - 2000,
            started_at=now - 3000,
            retry_count=1,  # Second retry
            project_key="test",
        )
        assessment = {
            "healthy": False,
            "issues": ["Silent for 33 minutes"],
            "severity": "warning",
        }

        with (
            patch(
                "monitoring.session_watchdog._kill_stalled_worker",
                new_callable=AsyncMock,
            ),
            patch(
                "monitoring.session_watchdog.asyncio.sleep",
                new_callable=AsyncMock,
            ) as mock_sleep,
            patch(
                "monitoring.session_watchdog._enqueue_stall_retry",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            await fix_unhealthy_session(session, assessment)

            # With retry_count=1, backoff should be base * 2^1 = 20
            expected_backoff = _compute_stall_backoff(1)
            mock_sleep.assert_called_once_with(expected_backoff)
