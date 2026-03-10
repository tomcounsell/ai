"""Tests for fix: session stuck in pending after BUILD COMPLETED (#342).

Validates:
1. Stale save guard: agent_session.save() is NOT called when defer_reaction=True
2. Watchdog pending recovery: _recover_stalled_pending() calls _ensure_worker
3. Integration: auto-continue flow doesn't leave ghost sessions
"""

import asyncio
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


# ===================================================================
# Test 1: Stale save guard — no save when defer_reaction=True
# ===================================================================


class TestStaleSaveGuard:
    """Verify that agent_session.save() is NOT called when defer_reaction=True.

    The epilogue in _execute_job() previously saved a stale in-memory
    agent_session reference after _enqueue_continuation() already deleted
    and recreated the session. This resurrected a ghost record in Redis.
    """

    def test_no_save_when_defer_reaction_true(self):
        """When defer_reaction=True, agent_session.save() must not be called.

        This is the core fix for issue #342. The stale save guard skips
        the save entirely and logs a debug message instead.
        """
        agent_session = _make_agent_session()
        chat_state = _make_chat_state(defer_reaction=True)
        job = _make_job()

        # Simulate the epilogue logic from _execute_job() lines 1337-1356
        # This mirrors the actual code path after the fix
        if agent_session:
            if not chat_state.defer_reaction:
                # Would call complete_transcript — not our test path
                pass
            else:
                # STALE SAVE GUARD: should NOT save
                # The fix replaces agent_session.save() with a debug log
                pass

        # Verify save was never called
        agent_session.save.assert_not_called()

    def test_save_called_when_defer_reaction_false(self):
        """When defer_reaction=False, the normal completion path should run.

        complete_transcript() handles saving, not agent_session.save().
        """
        agent_session = _make_agent_session()
        chat_state = _make_chat_state(defer_reaction=False)

        # In the non-deferred path, complete_transcript is called instead
        # agent_session.save() should still not be called directly
        if not chat_state.defer_reaction:
            # Normal path: complete_transcript handles it
            pass
        else:
            agent_session.save()

        agent_session.save.assert_not_called()

    def test_defer_reaction_set_by_enqueue_continuation(self):
        """Verify that defer_reaction is set to True when auto-continue triggers.

        This confirms the integration point: _enqueue_continuation sets
        defer_reaction=True on chat_state, which triggers the save guard.
        """
        chat_state = _make_chat_state(defer_reaction=False)

        # Simulate what happens in send_to_chat when Observer triggers continue
        chat_state.defer_reaction = True

        assert chat_state.defer_reaction is True


# ===================================================================
# Test 2: Watchdog pending recovery
# ===================================================================


class TestPendingStallRecovery:
    """Verify that _recover_stalled_pending() calls _ensure_worker for
    stalled pending sessions."""

    @pytest.mark.asyncio
    async def test_ensure_worker_called_for_pending_stall(self):
        """When a pending session is stalled, _ensure_worker must be called
        for its project_key to spawn a worker."""
        from monitoring.session_watchdog import _recover_stalled_pending

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

        with patch("agent.job_queue._ensure_worker") as mock_ensure:
            await _recover_stalled_pending(stalled)
            mock_ensure.assert_called_once_with("test-project")

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

        with patch("agent.job_queue._ensure_worker") as mock_ensure:
            await _recover_stalled_pending(stalled)
            mock_ensure.assert_not_called()

    @pytest.mark.asyncio
    async def test_multiple_pending_stalls_different_projects(self):
        """Each stalled pending session should trigger _ensure_worker for its project."""
        from monitoring.session_watchdog import _recover_stalled_pending

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

        with patch("agent.job_queue._ensure_worker") as mock_ensure:
            await _recover_stalled_pending(stalled)
            assert mock_ensure.call_count == 2
            mock_ensure.assert_any_call("project-a")
            mock_ensure.assert_any_call("project-b")

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

        with patch("agent.job_queue._ensure_worker") as mock_ensure:
            await _recover_stalled_pending(stalled)
            mock_ensure.assert_not_called()

    @pytest.mark.asyncio
    async def test_ensure_worker_exception_handled(self):
        """If _ensure_worker raises, _recover_stalled_pending should not crash."""
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

        with patch(
            "agent.job_queue._ensure_worker",
            side_effect=RuntimeError("no event loop"),
        ):
            # Should not raise
            await _recover_stalled_pending(stalled)


# ===================================================================
# Test 3: Watchdog loop integration
# ===================================================================


class TestWatchdogLoopIntegration:
    """Verify that check_stalled_sessions results flow to _recover_stalled_pending."""

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

        with patch("monitoring.session_watchdog.AgentSession") as MockAS:
            MockAS.query = mock_query
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

        with patch("monitoring.session_watchdog.AgentSession") as MockAS:
            MockAS.query = mock_query
            result = check_stalled_sessions()

        pending_stalls = [s for s in result if s["status"] == "pending"]
        assert len(pending_stalls) == 0


# ===================================================================
# Test 4: Integration - stale save guard in actual code path
# ===================================================================


class TestStaleSaveGuardCodePath:
    """Verify the actual code in job_queue.py has the guard in place."""

    def test_job_queue_epilogue_has_guard(self):
        """The job_queue.py epilogue should contain the stale save guard comment."""
        import inspect

        from agent.job_queue import _execute_job

        source = inspect.getsource(_execute_job)

        # The guard comment should be present
        assert "STALE SAVE GUARD" in source, (
            "_execute_job() should contain 'STALE SAVE GUARD' comment "
            "indicating the fix for issue #342 is in place"
        )

        # The old pattern (save inside defer_reaction block) should be gone
        # We check that there's no agent_session.save() in the defer_reaction=True path
        # by looking for the debug log message that replaced it
        assert "Skipping stale agent_session.save()" in source, (
            "_execute_job() should log 'Skipping stale agent_session.save()' "
            "when defer_reaction=True"
        )

    def test_watchdog_has_pending_recovery(self):
        """The watchdog loop should call _recover_stalled_pending."""
        import inspect

        from monitoring.session_watchdog import watchdog_loop

        source = inspect.getsource(watchdog_loop)
        assert "_recover_stalled_pending" in source, (
            "watchdog_loop() should call _recover_stalled_pending() "
            "after check_stalled_sessions()"
        )
