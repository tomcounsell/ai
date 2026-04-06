"""Unit tests for _cleanup_stale_sessions in scripts/update/run.py.

Covers:
- Sessions with a live _active_workers entry are skipped (Bug 1 fix)
- Sessions without a live worker and past age threshold are killed via
  finalize_session, not raw delete-and-recreate (Bug 3 fix)
- finalize_session exception inside the loop does not abort remaining cleanup
- Sessions younger than the threshold are not killed
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def _make_session(
    session_id="session-1",
    agent_session_id="agent-1",
    chat_id="chat_1",
    status="running",
    age_seconds=7200,  # 2 hours old by default (past 120 min threshold)
):
    """Create a minimal session-like object for testing."""
    created = datetime.fromtimestamp(time.time() - age_seconds, tz=UTC)
    return SimpleNamespace(
        session_id=session_id,
        agent_session_id=agent_session_id,
        chat_id=chat_id,
        status=status,
        created_at=created,
        delete=MagicMock(),
        save=MagicMock(),
        log_lifecycle_transition=MagicMock(),
    )


def _run_cleanup(sessions_by_status, active_workers=None, age_minutes=120):
    """Run _cleanup_stale_sessions with injected mocks.

    Returns (killed_count, finalize_mock) so tests can assert on both.
    """
    from pathlib import Path

    from scripts.update.run import _cleanup_stale_sessions

    active_workers = active_workers or {}

    def mock_filter(status=None):
        return sessions_by_status.get(status, [])

    with (
        patch("models.agent_session.AgentSession") as mock_as_class,
        patch("models.session_lifecycle.finalize_session") as mock_finalize,
    ):
        # Patch the import inside the function
        mock_as_class.query.filter.side_effect = mock_filter

        import agent.agent_session_queue as queue_module

        original = getattr(queue_module, "_active_workers", {})
        try:
            queue_module._active_workers = active_workers
            killed = _cleanup_stale_sessions(Path("/tmp"), age_minutes=age_minutes)
        finally:
            queue_module._active_workers = original

    return killed, mock_finalize


class TestCleanupSkipsLiveWorkers:
    """Sessions with live _active_workers entries are not killed."""

    def test_live_worker_skips_session(self):
        """Session whose chat_id has a live (not-done) asyncio Task is skipped."""
        from pathlib import Path

        from scripts.update.run import _cleanup_stale_sessions

        stale_session = _make_session(chat_id="chat_active", age_seconds=7200)
        sessions_by_status = {"running": [stale_session], "pending": []}

        # Create a mock task that is NOT done
        live_task = MagicMock(spec=asyncio.Task)
        live_task.done.return_value = False

        active_workers = {"chat_active": live_task}

        def mock_filter(status=None):
            return sessions_by_status.get(status, [])

        with (
            patch("models.agent_session.AgentSession") as mock_as_class,
            patch("models.session_lifecycle.finalize_session") as mock_finalize,
        ):
            mock_as_class.query.filter.side_effect = mock_filter

            import agent.agent_session_queue as queue_module

            original = getattr(queue_module, "_active_workers", {})
            try:
                queue_module._active_workers = active_workers
                killed = _cleanup_stale_sessions(Path("/tmp"), age_minutes=120)
            finally:
                queue_module._active_workers = original

        assert killed == 0, "Should not kill session with live worker"
        mock_finalize.assert_not_called()

    def test_done_worker_does_not_protect_session(self):
        """Session whose chat_id worker is done() is treated as orphaned."""
        from pathlib import Path

        from scripts.update.run import _cleanup_stale_sessions

        stale_session = _make_session(chat_id="chat_done", age_seconds=7200)
        sessions_by_status = {"running": [stale_session], "pending": []}

        # Task that IS done — should not protect the session
        done_task = MagicMock(spec=asyncio.Task)
        done_task.done.return_value = True

        active_workers = {"chat_done": done_task}

        def mock_filter(status=None):
            return sessions_by_status.get(status, [])

        with (
            patch("models.agent_session.AgentSession") as mock_as_class,
            patch("models.session_lifecycle.finalize_session") as mock_finalize,
        ):
            mock_as_class.query.filter.side_effect = mock_filter

            import agent.agent_session_queue as queue_module

            original = getattr(queue_module, "_active_workers", {})
            try:
                queue_module._active_workers = active_workers
                killed = _cleanup_stale_sessions(Path("/tmp"), age_minutes=120)
            finally:
                queue_module._active_workers = original

        assert killed == 1, "Should kill session with done worker"
        mock_finalize.assert_called_once()
        args, kwargs = mock_finalize.call_args
        assert args[0] is stale_session
        assert args[1] == "killed"
        assert kwargs.get("skip_checkpoint") is True

    def test_no_chat_id_falls_through_to_age_check(self):
        """Session with no chat_id is not protected by registry — uses age check."""
        from pathlib import Path

        from scripts.update.run import _cleanup_stale_sessions

        # Session with None chat_id, old enough to be killed
        stale_session = _make_session(chat_id=None, age_seconds=7200)
        sessions_by_status = {"running": [stale_session], "pending": []}

        def mock_filter(status=None):
            return sessions_by_status.get(status, [])

        with (
            patch("models.agent_session.AgentSession") as mock_as_class,
            patch("models.session_lifecycle.finalize_session") as mock_finalize,
        ):
            mock_as_class.query.filter.side_effect = mock_filter

            import agent.agent_session_queue as queue_module

            original = getattr(queue_module, "_active_workers", {})
            try:
                queue_module._active_workers = {}
                killed = _cleanup_stale_sessions(Path("/tmp"), age_minutes=120)
            finally:
                queue_module._active_workers = original

        assert killed == 1
        mock_finalize.assert_called_once()


class TestCleanupUsesLifecycleLayer:
    """_cleanup_stale_sessions routes terminal transitions through finalize_session."""

    def test_finalize_session_called_not_raw_delete(self):
        """No raw s.delete() / AgentSession.create() — finalize_session is used."""
        from pathlib import Path

        from scripts.update.run import _cleanup_stale_sessions

        stale_session = _make_session(age_seconds=7200)
        sessions_by_status = {"running": [stale_session], "pending": []}

        def mock_filter(status=None):
            return sessions_by_status.get(status, [])

        with (
            patch("models.agent_session.AgentSession") as mock_as_class,
            patch("models.session_lifecycle.finalize_session") as mock_finalize,
        ):
            mock_as_class.query.filter.side_effect = mock_filter

            import agent.agent_session_queue as queue_module

            original = getattr(queue_module, "_active_workers", {})
            try:
                queue_module._active_workers = {}
                _cleanup_stale_sessions(Path("/tmp"), age_minutes=120)
            finally:
                queue_module._active_workers = original

        # finalize_session must be called with killed status and skip_checkpoint
        mock_finalize.assert_called_once_with(
            stale_session,
            "killed",
            reason="stale cleanup (no live process)",
            skip_checkpoint=True,
        )
        # Raw delete must NOT have been called
        stale_session.delete.assert_not_called()

    def test_finalize_exception_does_not_abort_loop(self):
        """A finalize_session failure for one session does not stop cleanup of others."""
        from pathlib import Path

        from scripts.update.run import _cleanup_stale_sessions

        s1 = _make_session(session_id="s1", agent_session_id="a1", age_seconds=7200)
        s2 = _make_session(session_id="s2", agent_session_id="a2", age_seconds=7200)
        sessions_by_status = {"running": [s1, s2], "pending": []}

        call_count = [0]

        def finalize_side_effect(session, status, **kwargs):
            call_count[0] += 1
            if session is s1:
                raise RuntimeError("Redis exploded")
            # s2 finishes normally

        def mock_filter(status=None):
            return sessions_by_status.get(status, [])

        with (
            patch("models.agent_session.AgentSession") as mock_as_class,
            patch("models.session_lifecycle.finalize_session", side_effect=finalize_side_effect),
        ):
            mock_as_class.query.filter.side_effect = mock_filter

            import agent.agent_session_queue as queue_module

            original = getattr(queue_module, "_active_workers", {})
            try:
                queue_module._active_workers = {}
                killed = _cleanup_stale_sessions(Path("/tmp"), age_minutes=120)
            finally:
                queue_module._active_workers = original

        # s1 failed but s2 should still have been attempted
        assert call_count[0] == 2, "finalize_session must be called for both sessions"
        # Only s2 succeeded
        assert killed == 1


class TestCleanupAgeThreshold:
    """Sessions younger than the threshold are not killed."""

    def test_young_session_not_killed(self):
        """Session younger than age_minutes threshold is left alone."""
        from pathlib import Path

        from scripts.update.run import _cleanup_stale_sessions

        # Only 10 minutes old — below 120 min threshold
        young_session = _make_session(age_seconds=600)
        sessions_by_status = {"running": [young_session], "pending": []}

        def mock_filter(status=None):
            return sessions_by_status.get(status, [])

        with (
            patch("models.agent_session.AgentSession") as mock_as_class,
            patch("models.session_lifecycle.finalize_session") as mock_finalize,
        ):
            mock_as_class.query.filter.side_effect = mock_filter

            import agent.agent_session_queue as queue_module

            original = getattr(queue_module, "_active_workers", {})
            try:
                queue_module._active_workers = {}
                killed = _cleanup_stale_sessions(Path("/tmp"), age_minutes=120)
            finally:
                queue_module._active_workers = original

        assert killed == 0
        mock_finalize.assert_not_called()
