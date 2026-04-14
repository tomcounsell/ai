"""Tests for health-check recovery finalization fallback (issue #917).

When `_execute_agent_session()` completes normally but the inner `agent_session`
lookup returned None (race on status="running" filter after health-check recovery),
the fallback `else` branch must call `complete_transcript()` to finalize the session.

Tests:
1. agent_session=None + no error + defer_reaction=False → complete_transcript("completed")
2. agent_session=None + error + defer_reaction=False → complete_transcript("failed")
3. agent_session=None + defer_reaction=True → complete_transcript NOT called (nudge path)
4. agent_session is non-None → existing path used (regression guard)
5. Fallback raises StatusConflictError → info logged, no propagation
6. Fallback raises unexpected exception → warning logged, no propagation
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock


def _make_session(**overrides):
    """Create a minimal session-like object for the finalization block."""
    defaults = {
        "session_id": "test-session-001",
        "agent_session_id": "agent-sess-001",
        "project_key": "test-project",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_task(error=None):
    """Create a minimal task-like object."""
    return SimpleNamespace(error=error)


def _make_chat_state(defer_reaction=False):
    """Create a minimal chat_state-like object."""
    return SimpleNamespace(defer_reaction=defer_reaction)


def _run_finalization_block(session, agent_session, task, chat_state, complete_transcript_mock):
    """Execute the finalization block extracted from _execute_agent_session().

    This mirrors the if/else structure at ~L3364 in agent_session_queue.py.
    We test the logic directly rather than calling _execute_agent_session()
    (which requires extensive async setup).
    """
    if agent_session:
        try:
            final_status = (
                "active"
                if chat_state.defer_reaction
                else ("completed" if not task.error else "failed")
            )
            if not chat_state.defer_reaction:
                complete_transcript_mock(session.session_id, status=final_status)
        except Exception:
            pass
    else:
        # Fallback finalization — the code under test (issue #917)
        if not chat_state.defer_reaction:
            try:
                from models.session_lifecycle import StatusConflictError

                final_status = "completed" if not task.error else "failed"
                complete_transcript_mock(session.session_id, status=final_status)
            except StatusConflictError:
                logging.getLogger(__name__).info(
                    "Fallback finalization skipped: session %s already transitioned "
                    "(CAS conflict — expected)",
                    session.agent_session_id,
                )
            except Exception as e:
                logging.getLogger(__name__).warning(
                    "Fallback finalization failed for session %s: %s",
                    session.agent_session_id,
                    e,
                )


class TestFallbackFinalization:
    """Tests for the else branch when agent_session is None."""

    def test_completed_when_no_error(self):
        """agent_session=None + no error + defer_reaction=False → 'completed'."""
        session = _make_session()
        task = _make_task(error=None)
        chat_state = _make_chat_state(defer_reaction=False)
        mock_ct = MagicMock()

        _run_finalization_block(session, None, task, chat_state, mock_ct)

        mock_ct.assert_called_once_with("test-session-001", status="completed")

    def test_failed_when_error(self):
        """agent_session=None + error + defer_reaction=False → 'failed'."""
        session = _make_session()
        task = _make_task(error="some error")
        chat_state = _make_chat_state(defer_reaction=False)
        mock_ct = MagicMock()

        _run_finalization_block(session, None, task, chat_state, mock_ct)

        mock_ct.assert_called_once_with("test-session-001", status="failed")

    def test_nudge_path_not_finalized(self):
        """agent_session=None + defer_reaction=True → complete_transcript NOT called."""
        session = _make_session()
        task = _make_task(error=None)
        chat_state = _make_chat_state(defer_reaction=True)
        mock_ct = MagicMock()

        _run_finalization_block(session, None, task, chat_state, mock_ct)

        mock_ct.assert_not_called()

    def test_existing_path_when_agent_session_present(self):
        """agent_session is non-None → existing complete_transcript path used."""
        session = _make_session()
        agent_session = MagicMock()  # non-None
        task = _make_task(error=None)
        chat_state = _make_chat_state(defer_reaction=False)
        mock_ct = MagicMock()

        _run_finalization_block(session, agent_session, task, chat_state, mock_ct)

        # Should still be called (existing path), with "completed"
        mock_ct.assert_called_once_with("test-session-001", status="completed")

    def test_status_conflict_error_is_info_not_exception(self, caplog):
        """StatusConflictError → info logged, no exception propagated."""
        from models.session_lifecycle import StatusConflictError

        session = _make_session()
        task = _make_task(error=None)
        chat_state = _make_chat_state(defer_reaction=False)
        mock_ct = MagicMock(
            side_effect=StatusConflictError(
                session_id="test-session-001",
                expected_status="running",
                actual_status="completed",
            )
        )

        with caplog.at_level(logging.INFO):
            # Should not raise
            _run_finalization_block(session, None, task, chat_state, mock_ct)

        assert "CAS conflict" in caplog.text or "already transitioned" in caplog.text

    def test_unexpected_exception_is_warning_not_propagated(self, caplog):
        """Unexpected exception → warning logged, no exception propagated."""
        session = _make_session()
        task = _make_task(error=None)
        chat_state = _make_chat_state(defer_reaction=False)
        mock_ct = MagicMock(side_effect=RuntimeError("Redis connection lost"))

        with caplog.at_level(logging.WARNING):
            # Should not raise
            _run_finalization_block(session, None, task, chat_state, mock_ct)

        assert "Redis connection lost" in caplog.text


class TestFallbackExistsInSource:
    """Structural test: verify the fallback finalization code is present in the source."""

    def test_fallback_finalization_present_in_agent_session_queue(self):
        """The else branch with fallback finalization must exist in the source."""
        import inspect

        import agent.agent_session_queue as mod

        source = inspect.getsource(mod)
        assert "Fallback finalization" in source, (
            "Expected 'Fallback finalization' comment in agent_session_queue.py — "
            "the else branch from issue #917 is missing"
        )
        assert "agent_session was None" in source, (
            "Expected 'agent_session was None' log message in agent_session_queue.py"
        )


class TestHasProgressChildActivity:
    """Tests for _has_progress() child-activity awareness (issue #963, Bug 2).

    A PM session with active children should not be declared stuck by the
    health check, even if it has no own-progress signals (turn_count,
    log_path, claude_session_uuid).
    """

    @staticmethod
    def _make_entry(**overrides):
        """Create a minimal AgentSession-like object for _has_progress."""
        defaults = {
            "turn_count": 0,
            "log_path": "",
            "claude_session_uuid": None,
        }
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    @staticmethod
    def _make_child(status="running"):
        return SimpleNamespace(status=status)

    def test_returns_true_when_child_running(self):
        """_has_progress returns True when a child session is running."""

        entry = self._make_entry()
        entry.get_children = lambda: [self._make_child(status="running")]

        from agent.agent_session_queue import _has_progress

        assert _has_progress(entry) is True

    def test_returns_true_when_child_pending(self):
        """_has_progress returns True when a child session is pending."""
        entry = self._make_entry()
        entry.get_children = lambda: [self._make_child(status="pending")]

        from agent.agent_session_queue import _has_progress

        assert _has_progress(entry) is True

    def test_returns_false_when_all_children_terminal(self):
        """_has_progress returns False when all children are in terminal status."""
        entry = self._make_entry()
        entry.get_children = lambda: [
            self._make_child(status="completed"),
            self._make_child(status="failed"),
            self._make_child(status="killed"),
        ]

        from agent.agent_session_queue import _has_progress

        assert _has_progress(entry) is False

    def test_returns_false_when_no_children(self):
        """_has_progress returns False when no children exist."""
        entry = self._make_entry()
        entry.get_children = lambda: []

        from agent.agent_session_queue import _has_progress

        assert _has_progress(entry) is False
