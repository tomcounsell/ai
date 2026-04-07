"""Tests for pending session stall recovery and stale save guard.

Validates:
1. Stale save guard: agent_session.save() is NOT called when defer_reaction=True
2. Watchdog pending recovery: _recover_stalled_pending() calls _ensure_worker
3. Kill+retry path: kills stuck worker, applies backoff, and re-enqueues
4. Retry exhaustion path: abandons session and notifies after max retries
5. Edge cases: session not found, empty stalled list, unknown project_key
"""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_agent_session(
    session_id="test-stuck-001",
    status="pending",
    started_at="DEFAULT",
    created_at="DEFAULT",
    last_activity="DEFAULT",
    project_key="test",
    chat_id="12345",
    job_id="job-stuck-001",
    retry_count=0,
    message_text="test message",
    message_id=1,
):
    """Create a fake AgentSession for testing."""
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
        last_stall_reason=None,
        message_text=message_text,
    )
    ns._get_history_list = lambda: []
    ns.log_lifecycle_transition = MagicMock()
    ns.save = MagicMock()
    ns.delete = MagicMock()
    return ns


def _make_job(
    session_id="test-stuck-001",
    job_id="job-stuck-001",
    project_key="test",
    chat_id="12345",
    message_id=1,
    message_text="test message",
    working_dir="/tmp",
    sender_name="test",
    work_item_slug=None,
    classification_type=None,
):
    """Create a fake Job for testing."""
    return SimpleNamespace(
        session_id=session_id,
        job_id=job_id,
        project_key=project_key,
        chat_id=chat_id,
        message_id=message_id,
        message_text=message_text,
        working_dir=working_dir,
        sender_name=sender_name,
        work_item_slug=work_item_slug,
        classification_type=classification_type,
        error=None,
    )


def _make_chat_state(defer_reaction=False, completion_sent=False, auto_continue_count=0):
    """Create a fake SendToChatResult."""
    return SimpleNamespace(
        defer_reaction=defer_reaction,
        completion_sent=completion_sent,
        auto_continue_count=auto_continue_count,
    )


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


# ===================================================================
# Test 1: Stale save guard
# ===================================================================


class TestStaleSaveGuard:
    """Verify that _execute_job skips session cleanup when defer_reaction is True.

    The epilogue in _execute_job() must skip session cleanup when
    defer_reaction is True (auto-continue path), because the continuation
    job has already been enqueued with a new session record.

    The production code now logs "Skipping session cleanup" instead of
    having a named STALE SAVE GUARD comment block.
    """

    def test_epilogue_skips_cleanup_when_deferred(self):
        """The _execute_job epilogue must skip session cleanup when
        defer_reaction is True (auto-continue path).
        """
        import inspect

        from agent.job_queue import _execute_job

        source = inspect.getsource(_execute_job)

        assert "defer_reaction" in source, (
            "_execute_job() must reference defer_reaction — the auto-continue guard is missing"
        )
        assert "Skipping session cleanup" in source, (
            "_execute_job() must log 'Skipping session cleanup' when defer_reaction=True"
        )

    def test_job_queue_epilogue_has_defer_reaction_guard(self):
        """The job_queue.py epilogue should skip cleanup when defer_reaction is True."""
        import inspect

        from agent.job_queue import _execute_job

        source = inspect.getsource(_execute_job)

        # The defer_reaction guard should be present
        assert "defer_reaction" in source, (
            "_execute_job() should reference defer_reaction "
            "indicating the auto-continue guard is in place"
        )
        assert "Skipping session cleanup" in source, (
            "_execute_job() should log 'Skipping session cleanup' when defer_reaction=True"
        )

    def test_watchdog_has_pending_recovery(self):
        """The watchdog loop should call _recover_stalled_pending."""
        import inspect

        from monitoring.session_watchdog import watchdog_loop

        source = inspect.getsource(watchdog_loop)
        assert "_recover_stalled_pending" in source, (
            "watchdog_loop() should call _recover_stalled_pending() after check_stalled_sessions()"
        )


# ===================================================================
# Test 2: Watchdog pending recovery
# ===================================================================


class TestPendingRecovery:
    """Verify that _recover_stalled_pending() kills stuck workers and retries
    stalled pending sessions (updated for kill+retry flow)."""

    @pytest.mark.asyncio
    async def test_kills_worker_and_retries_for_pending_stall(self):
        """When a pending session is stalled, should kill worker and re-enqueue."""
        from monitoring.session_watchdog import _recover_stalled_pending

        session = _make_agent_session(session_id="stuck-001", retry_count=0)
        stalled = [
            {
                "session_id": "stuck-001",
                "status": "pending",
                "duration": 600,
                "threshold": 300,
                "project_key": "test-project",
                "last_history": "BUILD COMPLETED",
            }
        ]

        with (
            patch("monitoring.session_watchdog.AgentSession") as mock_as_cls,
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
            mock_kill.assert_called_once_with("test-project")
            mock_retry.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_action_for_non_pending_stalls(self):
        """_recover_stalled_pending should ignore non-pending stalls."""
        from monitoring.session_watchdog import _recover_stalled_pending

        stalled = [
            {
                "session_id": "active-001",
                "status": "active",
                "duration": 1200,
                "threshold": 600,
                "project_key": "test-project",
                "last_history": "some output",
            },
            {
                "session_id": "running-001",
                "status": "running",
                "duration": 3600,
                "threshold": 2700,
                "project_key": "test-project",
                "last_history": "some output",
            },
        ]

        with (
            patch("monitoring.session_watchdog.AgentSession") as mock_as_cls,
            patch("agent.job_queue._ensure_worker"),
        ):
            await _recover_stalled_pending(stalled)
            mock_as_cls.query.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_multiple_pending_stalls_different_projects(self):
        """Each stalled pending session should trigger kill+retry for its project."""
        from monitoring.session_watchdog import _recover_stalled_pending

        session_a = _make_agent_session(
            session_id="stuck-001", project_key="project-a", retry_count=0
        )
        session_b = _make_agent_session(
            session_id="stuck-002", project_key="project-b", retry_count=0
        )
        stalled = [
            {
                "session_id": "stuck-001",
                "status": "pending",
                "duration": 600,
                "threshold": 300,
                "project_key": "project-a",
                "last_history": "no history",
            },
            {
                "session_id": "stuck-002",
                "status": "pending",
                "duration": 400,
                "threshold": 300,
                "project_key": "project-b",
                "last_history": "no history",
            },
        ]

        with (
            patch("monitoring.session_watchdog.AgentSession") as mock_as_cls,
            patch(
                "monitoring.session_watchdog._kill_stalled_worker",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_kill,
            patch(
                "monitoring.session_watchdog._enqueue_stall_retry",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch("asyncio.sleep", new_callable=AsyncMock),
            patch("agent.job_queue._ensure_worker"),
        ):
            mock_as_cls.query.get.side_effect = [session_a, session_b]
            await _recover_stalled_pending(stalled)
            assert mock_kill.call_count == 2
            mock_kill.assert_any_call("project-a")
            mock_kill.assert_any_call("project-b")

    @pytest.mark.asyncio
    async def test_skips_unknown_project_key(self):
        """Stalled sessions with unknown project_key ('?') should be skipped."""
        from monitoring.session_watchdog import _recover_stalled_pending

        stalled = [
            {
                "session_id": "stuck-001",
                "status": "pending",
                "duration": 600,
                "threshold": 300,
                "project_key": "?",
                "last_history": "no history",
            }
        ]

        with (
            patch("monitoring.session_watchdog.AgentSession") as mock_as_cls,
            patch("agent.job_queue._ensure_worker"),
        ):
            await _recover_stalled_pending(stalled)
            mock_as_cls.query.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_exception_handled_gracefully(self):
        """If recovery raises, _recover_stalled_pending should not crash."""
        from monitoring.session_watchdog import _recover_stalled_pending

        stalled = [
            {
                "session_id": "stuck-001",
                "status": "pending",
                "duration": 600,
                "threshold": 300,
                "project_key": "test-project",
                "last_history": "no history",
            }
        ]

        with (
            patch("monitoring.session_watchdog.AgentSession") as mock_as_cls,
            patch(
                "monitoring.session_watchdog._kill_stalled_worker",
                new_callable=AsyncMock,
                side_effect=RuntimeError("simulated failure"),
            ),
            patch("agent.job_queue._ensure_worker"),
        ):
            mock_as_cls.query.get.return_value = _make_agent_session(retry_count=0)
            # Should not raise
            await _recover_stalled_pending(stalled)

    def test_check_stalled_sessions_returns_pending(self):
        """check_stalled_sessions should detect stalled pending sessions."""
        from monitoring.session_watchdog import check_stalled_sessions

        now = time.time()
        pending_session = _make_agent_session(
            session_id="pending-stalled",
            status="pending",
            created_at=now - 600,  # 10 minutes old, threshold is 5 min
            started_at=None,
        )

        mock_query = MagicMock()
        mock_query.filter = MagicMock(
            side_effect=lambda **kw: [pending_session] if kw.get("status") == "pending" else []
        )

        with patch("monitoring.session_watchdog.AgentSession") as mock_as:
            mock_as.query = mock_query
            result = check_stalled_sessions()

        pending_stalls = [s for s in result if s["status"] == "pending"]
        assert len(pending_stalls) == 1
        assert pending_stalls[0]["session_id"] == "pending-stalled"
        assert pending_stalls[0]["project_key"] == "test"

    def test_check_stalled_sessions_no_false_positive(self):
        """A recently created pending session should NOT be flagged as stalled."""
        from monitoring.session_watchdog import check_stalled_sessions

        now = time.time()
        fresh_session = _make_agent_session(
            session_id="pending-fresh",
            status="pending",
            created_at=now - 10,  # 10 seconds old, well within threshold
            started_at=None,
        )

        mock_query = MagicMock()
        mock_query.filter = MagicMock(
            side_effect=lambda **kw: [fresh_session] if kw.get("status") == "pending" else []
        )

        with patch("monitoring.session_watchdog.AgentSession") as mock_as:
            mock_as.query = mock_query
            result = check_stalled_sessions()

        pending_stalls = [s for s in result if s["status"] == "pending"]
        assert len(pending_stalls) == 0


# ===================================================================
# Test 3: Kill+retry recovery
# ===================================================================


class TestKillRetryRecovery:
    """Test the kill+retry path in _recover_stalled_pending()."""

    @pytest.mark.asyncio
    async def test_kills_worker_and_retries_on_first_stall(self):
        """On first stall (retry_count=0), should kill worker, backoff, and re-enqueue."""
        from monitoring.session_watchdog import _recover_stalled_pending

        session = _make_agent_session(
            session_id="test-pending-001",
            project_key="test-project",
            retry_count=0,
        )
        stalled = [_make_stall_info()]

        with (
            patch("monitoring.session_watchdog.AgentSession") as mock_as_cls,
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

        session = _make_agent_session(
            session_id="test-pending-001",
            project_key="test-project",
            retry_count=2,
        )
        stalled = [_make_stall_info()]

        with (
            patch("monitoring.session_watchdog.AgentSession") as mock_as_cls,
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

        session = _make_agent_session(
            session_id="test-pending-001",
            project_key="test-project",
            retry_count=0,
        )
        stalled = [_make_stall_info()]

        with (
            patch("monitoring.session_watchdog.AgentSession") as mock_as_cls,
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


# ===================================================================
# Test 4: Retry exhaustion
# ===================================================================


class TestRetryExhaustion:
    """Test the retry exhaustion path in _recover_stalled_pending()."""

    @pytest.mark.asyncio
    async def test_abandons_after_max_retries(self):
        """When retry_count >= STALL_MAX_RETRIES, should abandon and notify."""
        from monitoring.session_watchdog import _recover_stalled_pending

        session = _make_agent_session(
            session_id="test-pending-001",
            project_key="test-project",
            retry_count=3,
        )
        stalled = [_make_stall_info()]

        with (
            patch("monitoring.session_watchdog.AgentSession") as mock_as_cls,
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

        session = _make_agent_session(
            session_id="test-pending-001",
            project_key="test-project",
            retry_count=3,
        )
        stalled = [_make_stall_info()]

        with (
            patch("monitoring.session_watchdog.AgentSession") as mock_as_cls,
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


# ===================================================================
# Test 5: Edge cases
# ===================================================================


class TestEdgeCases:
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
            patch("monitoring.session_watchdog.AgentSession") as mock_as_cls,
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
            patch("monitoring.session_watchdog.AgentSession") as mock_as_cls,
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
            patch("monitoring.session_watchdog.AgentSession") as mock_as_cls,
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
        session2 = _make_agent_session(session_id="ok-001", retry_count=0, project_key="project-b")
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
            patch("monitoring.session_watchdog.AgentSession") as mock_as_cls,
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

        session = _make_agent_session(
            session_id="test-pending-001",
            project_key="test-project",
            retry_count=None,
        )
        stalled = [_make_stall_info()]

        with (
            patch("monitoring.session_watchdog.AgentSession") as mock_as_cls,
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


# ===================================================================
# Test: String project_key and push-* session handling
# ===================================================================


class TestStringProjectKeyRecovery:
    """Verify that _recover_stalled_pending handles plain string project_keys
    (common in push-* sessions) without AttributeError."""

    @pytest.mark.asyncio
    async def test_string_project_key_does_not_raise(self):
        """A plain string project_key should be coerced safely — no AttributeError."""
        from monitoring.session_watchdog import _recover_stalled_pending

        session = _make_agent_session(
            session_id="push-abc123",
            project_key="plain-string-key",
            retry_count=0,
        )
        stalled = [
            _make_stall_info(
                session_id="push-abc123",
                project_key="plain-string-key",
                duration=600,
            )
        ]

        with (
            patch("monitoring.session_watchdog.AgentSession") as mock_as_cls,
            patch(
                "monitoring.session_watchdog._kill_stalled_worker",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_kill,
            patch(
                "monitoring.session_watchdog._enqueue_stall_retry",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch("asyncio.sleep", new_callable=AsyncMock),
            patch("agent.job_queue._ensure_worker"),
        ):
            mock_as_cls.query.get.return_value = session
            # Should NOT raise AttributeError
            await _recover_stalled_pending(stalled)
            mock_kill.assert_called_once_with("plain-string-key")

    @pytest.mark.asyncio
    async def test_none_project_key_skipped(self):
        """Session with None project_key should be skipped gracefully."""
        from monitoring.session_watchdog import _recover_stalled_pending

        stalled = [
            _make_stall_info(
                session_id="push-none",
                project_key=None,
                duration=600,
            )
        ]

        with (
            patch("monitoring.session_watchdog.AgentSession") as mock_as_cls,
            patch("agent.job_queue._ensure_worker"),
        ):
            await _recover_stalled_pending(stalled)
            # Should not attempt to load the session
            mock_as_cls.query.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_orphan_push_session_abandoned_after_threshold(self):
        """push-* session stuck >1 hour with no history should be abandoned."""
        from monitoring.session_watchdog import _recover_stalled_pending

        session = _make_agent_session(
            session_id="push-orphan-001",
            project_key="orphan-project",
            retry_count=0,
        )
        stalled = [
            {
                "session_id": "push-orphan-001",
                "status": "pending",
                "duration": 4000,  # >3600 threshold
                "threshold": 300,
                "project_key": "orphan-project",
                "last_history": "no history",
            }
        ]

        with (
            patch("monitoring.session_watchdog.AgentSession") as mock_as_cls,
            patch(
                "monitoring.session_watchdog._safe_abandon_session",
                return_value=True,
            ) as mock_abandon,
            patch(
                "monitoring.session_watchdog._notify_stall_failure",
                new_callable=AsyncMock,
            ) as mock_notify,
            patch("agent.job_queue._ensure_worker"),
        ):
            mock_as_cls.query.get.return_value = session
            await _recover_stalled_pending(stalled)
            mock_abandon.assert_called_once()
            mock_notify.assert_called_once()
            # Verify the abandon reason mentions orphan push-*
            reason = mock_abandon.call_args[0][1]
            assert "orphan push-*" in reason

    @pytest.mark.asyncio
    async def test_session_status_changed_skips_recovery(self):
        """If session status changed since stall detection, skip recovery."""
        from monitoring.session_watchdog import _recover_stalled_pending

        session = _make_agent_session(
            session_id="push-changed",
            project_key="test-project",
            status="running",  # Changed from pending
            retry_count=0,
        )
        stalled = [
            _make_stall_info(
                session_id="push-changed",
                project_key="test-project",
            )
        ]

        with (
            patch("monitoring.session_watchdog.AgentSession") as mock_as_cls,
            patch(
                "monitoring.session_watchdog._kill_stalled_worker",
                new_callable=AsyncMock,
            ) as mock_kill,
            patch("agent.job_queue._ensure_worker"),
        ):
            mock_as_cls.query.get.return_value = session
            await _recover_stalled_pending(stalled)
            # Should NOT attempt to kill worker since status changed
            mock_kill.assert_not_called()


class TestKillStalledWorkerGuards:
    """Verify _kill_stalled_worker handles edge cases gracefully."""

    @pytest.mark.asyncio
    async def test_none_project_key_returns_false(self):
        """_kill_stalled_worker(None) should return False gracefully."""
        from monitoring.session_watchdog import _kill_stalled_worker

        result = await _kill_stalled_worker(None)
        assert result is False

    @pytest.mark.asyncio
    async def test_empty_string_returns_false(self):
        """_kill_stalled_worker('') should return False gracefully."""
        from monitoring.session_watchdog import _kill_stalled_worker

        result = await _kill_stalled_worker("")
        assert result is False

    @pytest.mark.asyncio
    async def test_question_mark_returns_false(self):
        """_kill_stalled_worker('?') should return False gracefully."""
        from monitoring.session_watchdog import _kill_stalled_worker

        result = await _kill_stalled_worker("?")
        assert result is False
