"""Tests for pending session stall recovery with kill+retry flow.

Validates:
1. Kill+retry path: _recover_stalled_pending() kills stuck worker,
   applies backoff, and re-enqueues the session
2. Retry exhaustion path: abandons session and notifies after max retries
3. Edge cases: session not found, empty stalled list, unknown project_key
"""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_agent_session(
    session_id="test-pending-001",
    status="pending",
    project_key="test-project",
    retry_count=0,
    message_text="test message",
    chat_id="12345",
    job_id="job-pending-001",
    message_id=1,
):
    """Create a fake AgentSession for testing."""
    now = time.time()
    ns = SimpleNamespace(
        session_id=session_id,
        job_id=job_id,
        status=status,
        started_at=None,
        created_at=now - 600,
        last_activity=now,
        project_key=project_key,
        chat_id=chat_id,
        message_id=message_id,
        last_transition_at=None,
        retry_count=retry_count,
        last_stall_reason=None,
        message_text=message_text,
    )
    ns._get_history_list = lambda: []
    ns.log_lifecycle_transition = MagicMock()
    ns.save = MagicMock()
    ns.delete = MagicMock()
    return ns


def _make_stall_info(
    session_id="test-pending-001",
    status="pending",
    duration=600,
    project_key="test-project",
):
    """Create a stalled session dict as returned by check_stalled_sessions()."""
    return {
        "session_id": session_id,
        "status": status,
        "duration": duration,
        "threshold": 300,
        "project_key": project_key,
        "last_history": "no history",
    }


class TestPendingStallKillRetry:
    """Test the kill+retry path in _recover_stalled_pending()."""

    @pytest.mark.asyncio
    async def test_kills_worker_and_retries_on_first_stall(self):
        """On first stall (retry_count=0), should kill worker, backoff, and re-enqueue."""
        from monitoring.session_watchdog import _recover_stalled_pending

        session = _make_agent_session(retry_count=0)
        stalled = [_make_stall_info()]

        with (
            patch(
                "monitoring.session_watchdog.AgentSession"
            ) as mock_as_cls,
            patch(
                "monitoring.session_watchdog._kill_stalled_worker",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_kill,
            patch(
                "monitoring.session_watchdog._enqueue_stall_retry",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_retry,
            patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
            patch("agent.job_queue._ensure_worker"),
        ):
            mock_as_cls.query.get.return_value = session
            await _recover_stalled_pending(stalled)

            mock_kill.assert_called_once_with("test-project")
            mock_sleep.assert_called_once()
            mock_retry.assert_called_once()
            # Verify session and stall_reason were passed to _enqueue_stall_retry
            args = mock_retry.call_args
            assert args[0][0] is session
            assert "pending stall" in args[0][1]

    @pytest.mark.asyncio
    async def test_backoff_increases_with_retry_count(self):
        """Backoff should increase with retry_count (exponential)."""
        from monitoring.session_watchdog import _recover_stalled_pending

        session = _make_agent_session(retry_count=2)
        stalled = [_make_stall_info()]

        with (
            patch(
                "monitoring.session_watchdog.AgentSession"
            ) as mock_as_cls,
            patch(
                "monitoring.session_watchdog._kill_stalled_worker",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "monitoring.session_watchdog._enqueue_stall_retry",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
            patch("agent.job_queue._ensure_worker"),
        ):
            mock_as_cls.query.get.return_value = session
            await _recover_stalled_pending(stalled)

            # retry_count=2 with base=10 -> 10 * 2^2 = 40s
            sleep_val = mock_sleep.call_args[0][0]
            assert sleep_val == 40.0

    @pytest.mark.asyncio
    async def test_handles_kill_returning_false(self):
        """When _kill_stalled_worker returns False (no worker), should still retry."""
        from monitoring.session_watchdog import _recover_stalled_pending

        session = _make_agent_session(retry_count=0)
        stalled = [_make_stall_info()]

        with (
            patch(
                "monitoring.session_watchdog.AgentSession"
            ) as mock_as_cls,
            patch(
                "monitoring.session_watchdog._kill_stalled_worker",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "monitoring.session_watchdog._enqueue_stall_retry",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_retry,
            patch("asyncio.sleep", new_callable=AsyncMock),
            patch("agent.job_queue._ensure_worker"),
        ):
            mock_as_cls.query.get.return_value = session
            await _recover_stalled_pending(stalled)

            mock_retry.assert_called_once()


class TestPendingStallRetryExhaustion:
    """Test the retry exhaustion path in _recover_stalled_pending()."""

    @pytest.mark.asyncio
    async def test_abandons_after_max_retries(self):
        """When retry_count >= STALL_MAX_RETRIES, should abandon and notify."""
        from monitoring.session_watchdog import _recover_stalled_pending

        session = _make_agent_session(retry_count=3)
        stalled = [_make_stall_info()]

        with (
            patch(
                "monitoring.session_watchdog.AgentSession"
            ) as mock_as_cls,
            patch(
                "monitoring.session_watchdog._safe_abandon_session",
                return_value=True,
            ) as mock_abandon,
            patch(
                "monitoring.session_watchdog._notify_stall_failure",
                new_callable=AsyncMock,
            ) as mock_notify,
            patch(
                "monitoring.session_watchdog.STALL_MAX_RETRIES",
                3,
            ),
            patch("agent.job_queue._ensure_worker"),
        ):
            mock_as_cls.query.get.return_value = session
            await _recover_stalled_pending(stalled)

            mock_abandon.assert_called_once()
            abandon_args = mock_abandon.call_args
            assert abandon_args[0][0] is session
            assert "retries exhausted" in abandon_args[0][1]
            mock_notify.assert_called_once()

    @pytest.mark.asyncio
    async def test_does_not_kill_worker_on_exhaustion(self):
        """When retries exhausted, should NOT kill worker (just abandon)."""
        from monitoring.session_watchdog import _recover_stalled_pending

        session = _make_agent_session(retry_count=3)
        stalled = [_make_stall_info()]

        with (
            patch(
                "monitoring.session_watchdog.AgentSession"
            ) as mock_as_cls,
            patch(
                "monitoring.session_watchdog._kill_stalled_worker",
                new_callable=AsyncMock,
            ) as mock_kill,
            patch(
                "monitoring.session_watchdog._safe_abandon_session",
                return_value=True,
            ),
            patch(
                "monitoring.session_watchdog._notify_stall_failure",
                new_callable=AsyncMock,
            ),
            patch(
                "monitoring.session_watchdog.STALL_MAX_RETRIES",
                3,
            ),
            patch("agent.job_queue._ensure_worker"),
        ):
            mock_as_cls.query.get.return_value = session
            await _recover_stalled_pending(stalled)

            mock_kill.assert_not_called()


class TestPendingStallEdgeCases:
    """Test edge cases in _recover_stalled_pending()."""

    @pytest.mark.asyncio
    async def test_empty_stalled_list(self):
        """Empty stalled list should be a no-op."""
        from monitoring.session_watchdog import _recover_stalled_pending

        # Should not raise or call anything
        await _recover_stalled_pending([])

    @pytest.mark.asyncio
    async def test_skips_non_pending_stalls(self):
        """Non-pending stalls should be ignored."""
        from monitoring.session_watchdog import _recover_stalled_pending

        stalled = [
            _make_stall_info(session_id="active-001", status="active"),
            _make_stall_info(session_id="running-001", status="running"),
        ]

        with (
            patch(
                "monitoring.session_watchdog.AgentSession"
            ) as mock_as_cls,
            patch("agent.job_queue._ensure_worker"),
        ):
            await _recover_stalled_pending(stalled)
            mock_as_cls.query.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_unknown_project_key(self):
        """Sessions with project_key='?' should be skipped."""
        from monitoring.session_watchdog import _recover_stalled_pending

        stalled = [_make_stall_info(project_key="?")]

        with (
            patch(
                "monitoring.session_watchdog.AgentSession"
            ) as mock_as_cls,
            patch("agent.job_queue._ensure_worker"),
        ):
            await _recover_stalled_pending(stalled)
            mock_as_cls.query.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_session_deleted_between_detection_and_recovery(self):
        """If session is deleted from Redis between detection and recovery, skip it."""
        from monitoring.session_watchdog import _recover_stalled_pending

        stalled = [_make_stall_info()]

        with (
            patch(
                "monitoring.session_watchdog.AgentSession"
            ) as mock_as_cls,
            patch(
                "monitoring.session_watchdog._kill_stalled_worker",
                new_callable=AsyncMock,
            ) as mock_kill,
            patch("agent.job_queue._ensure_worker"),
        ):
            mock_as_cls.query.get.return_value = None
            await _recover_stalled_pending(stalled)

            # Should not attempt kill or retry
            mock_kill.assert_not_called()

    @pytest.mark.asyncio
    async def test_exception_in_one_session_does_not_block_others(self):
        """If processing one session raises, the next should still be processed."""
        from monitoring.session_watchdog import _recover_stalled_pending

        session1 = _make_agent_session(session_id="fail-001", retry_count=0)
        session2 = _make_agent_session(
            session_id="ok-001", retry_count=0, project_key="project-b"
        )
        stalled = [
            _make_stall_info(session_id="fail-001", project_key="project-a"),
            _make_stall_info(session_id="ok-001", project_key="project-b"),
        ]

        call_count = {"kill": 0}

        async def kill_side_effect(project_key):
            call_count["kill"] += 1
            if project_key == "project-a":
                raise RuntimeError("simulated failure")
            return True

        with (
            patch(
                "monitoring.session_watchdog.AgentSession"
            ) as mock_as_cls,
            patch(
                "monitoring.session_watchdog._kill_stalled_worker",
                new_callable=AsyncMock,
                side_effect=kill_side_effect,
            ),
            patch(
                "monitoring.session_watchdog._enqueue_stall_retry",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_retry,
            patch("asyncio.sleep", new_callable=AsyncMock),
            patch("agent.job_queue._ensure_worker"),
        ):
            mock_as_cls.query.get.side_effect = [session1, session2]
            await _recover_stalled_pending(stalled)

            # First session failed during kill, but second should still be retried
            # The exception is caught per-session, so mock_retry should be called
            # only for session2 (session1 errored before reaching retry)
            assert mock_retry.call_count == 1

    @pytest.mark.asyncio
    async def test_retry_count_none_treated_as_zero(self):
        """Sessions with retry_count=None (legacy) should be treated as retry 0."""
        from monitoring.session_watchdog import _recover_stalled_pending

        session = _make_agent_session(retry_count=None)
        stalled = [_make_stall_info()]

        with (
            patch(
                "monitoring.session_watchdog.AgentSession"
            ) as mock_as_cls,
            patch(
                "monitoring.session_watchdog._kill_stalled_worker",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_kill,
            patch(
                "monitoring.session_watchdog._enqueue_stall_retry",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_retry,
            patch("asyncio.sleep", new_callable=AsyncMock),
            patch("agent.job_queue._ensure_worker"),
        ):
            mock_as_cls.query.get.return_value = session
            await _recover_stalled_pending(stalled)

            mock_kill.assert_called_once()
            mock_retry.assert_called_once()
