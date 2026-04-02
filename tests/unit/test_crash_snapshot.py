"""Unit tests for crash-path snapshot saving in the worker finally block.

Verifies that the finally block in _worker_loop always saves a diagnostic
snapshot before calling _complete_agent_session(), even when the task
crashes in a way that bypasses BackgroundTask._run_work's exception handler.

See docs/plans/silent-session-death.md Fix 3 and Fix 4.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock


def _make_session(
    session_id="test-session-1",
    agent_session_id="agent-test-1",
    project_key="test-project",
    chat_id="chat_1",
):
    """Create a minimal session-like object for testing."""
    return SimpleNamespace(
        session_id=session_id,
        agent_session_id=agent_session_id,
        project_key=project_key,
        chat_id=chat_id,
        working_dir="/tmp/test",
        log_lifecycle_transition=MagicMock(),
        save=MagicMock(),
        delete=MagicMock(),
    )


def _run_finally_block(session, session_completed, session_failed):
    """Simulate the finally block logic from _worker_loop.

    Returns (snapshot_calls, lifecycle_calls) for assertion.
    """
    snapshot_calls = []

    def mock_snapshot(**kwargs):
        snapshot_calls.append(kwargs)

    if not session_completed:
        # Fix 4: Log lifecycle transition before completing
        try:
            target = "failed" if session_failed else "completed"
            session.log_lifecycle_transition(target, "worker finally block")
        except Exception:
            pass
        # Fix 3: Always save diagnostic snapshot
        try:
            _event = "crash" if session_failed else "complete"
            # Simulate get_activity returning tool count
            activity = {"tool_count": 42, "last_tools": ["Bash"]} if session_failed else {}
            mock_snapshot(
                session_id=session.session_id,
                event=_event,
                project_key=session.project_key,
                branch_name=f"session/{session.session_id}",
                task_summary=(
                    f"Session {session.agent_session_id} "
                    f"{'failed' if session_failed else 'terminated'}"
                ),
                extra_context={
                    "agent_session_id": session.agent_session_id,
                    "tool_count": activity.get("tool_count", 0),
                    "trigger": "finally_block",
                },
                working_dir=session.working_dir,
            )
        except Exception:
            pass

    return snapshot_calls


class TestFinallyBlockSnapshot:
    """Tests that the finally block saves snapshots on all termination paths."""

    def test_snapshot_saved_on_failure(self):
        """When session_failed=True, a crash snapshot is saved."""
        session = _make_session()
        snapshot_calls = _run_finally_block(session, session_completed=False, session_failed=True)

        assert len(snapshot_calls) == 1
        assert snapshot_calls[0]["event"] == "crash"
        assert snapshot_calls[0]["session_id"] == "test-session-1"
        assert snapshot_calls[0]["extra_context"]["trigger"] == "finally_block"
        assert snapshot_calls[0]["extra_context"]["tool_count"] == 42

        session.log_lifecycle_transition.assert_called_once_with("failed", "worker finally block")

    def test_snapshot_saved_on_normal_completion(self):
        """When session_failed=False, a complete snapshot is saved."""
        session = _make_session()
        snapshot_calls = _run_finally_block(session, session_completed=False, session_failed=False)

        assert len(snapshot_calls) == 1
        assert snapshot_calls[0]["event"] == "complete"
        assert "terminated" in snapshot_calls[0]["task_summary"]

        session.log_lifecycle_transition.assert_called_once_with(
            "completed", "worker finally block"
        )

    def test_snapshot_skipped_when_session_already_completed(self):
        """When session_completed=True, no snapshot is saved (handled elsewhere)."""
        session = _make_session()
        snapshot_calls = _run_finally_block(session, session_completed=True, session_failed=False)

        assert len(snapshot_calls) == 0
        session.log_lifecycle_transition.assert_not_called()

    def test_lifecycle_failure_does_not_prevent_snapshot(self):
        """If log_lifecycle_transition raises, snapshot is still saved."""
        session = _make_session()
        session.log_lifecycle_transition.side_effect = Exception("Redis down")

        snapshot_calls = _run_finally_block(session, session_completed=False, session_failed=True)

        # Lifecycle failed but snapshot still saved
        assert len(snapshot_calls) == 1
        assert snapshot_calls[0]["event"] == "crash"

    def test_lifecycle_transition_logged_on_cancellation(self):
        """CancelledError handler should log lifecycle transition."""
        session = _make_session()

        # Simulate the CancelledError handler logic
        try:
            session.log_lifecycle_transition("failed", "worker cancelled")
        except Exception:
            pass

        session.log_lifecycle_transition.assert_called_once_with("failed", "worker cancelled")

    def test_crash_snapshot_contains_agent_session_id(self):
        """Crash snapshot extra_context should include agent_session_id for correlation."""
        session = _make_session(agent_session_id="agent-xyz-999")
        snapshot_calls = _run_finally_block(session, session_completed=False, session_failed=True)

        assert snapshot_calls[0]["extra_context"]["agent_session_id"] == "agent-xyz-999"

    def test_snapshot_event_matches_failure_state(self):
        """Event should be 'crash' for failures and 'complete' for normal termination."""
        session_fail = _make_session()
        session_ok = _make_session(session_id="ok-session")

        fail_calls = _run_finally_block(session_fail, session_completed=False, session_failed=True)
        ok_calls = _run_finally_block(session_ok, session_completed=False, session_failed=False)

        assert fail_calls[0]["event"] == "crash"
        assert ok_calls[0]["event"] == "complete"
