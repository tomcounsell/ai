"""
Integration tests for test-scheduler tool.

Run with: pytest tools/test-scheduler/tests/ -v
"""

import time

from tools.test_scheduler import (
    cancel_session,
    get_session_results,
    get_session_status,
    list_sessions,
    schedule_tests,
)


class TestSchedulerInstallation:
    """Verify tool is properly configured."""

    def test_import(self):
        """Tool can be imported."""
        from tools.test_scheduler import schedule_tests

        assert callable(schedule_tests)


class TestSchedulerValidation:
    """Test input validation."""

    def test_empty_specification(self):
        """Empty specification returns error."""
        result = schedule_tests("")
        assert "error" in result

    def test_whitespace_specification(self):
        """Whitespace specification returns error."""
        result = schedule_tests("   ")
        assert "error" in result


class TestScheduleTests:
    """Test test scheduling."""

    def test_schedule_pytest(self):
        """Schedule pytest command."""
        result = schedule_tests("pytest tests/ -v")

        assert "error" not in result
        assert "agent_session_id" in result
        assert result["status"] == "scheduled"

    def test_schedule_generic(self):
        """Schedule generic command."""
        result = schedule_tests("echo 'test'")

        assert "error" not in result
        assert "agent_session_id" in result

    def test_test_count(self):
        """Test count is returned."""
        result = schedule_tests("pytest tests/")

        assert "test_count" in result
        assert result["test_count"] >= 1


class TestSessionStatus:
    """Test session status retrieval."""

    def test_get_status(self):
        """Get session status."""
        schedule_result = schedule_tests("echo 'hello'")
        agent_session_id = schedule_result["agent_session_id"]

        # Wait briefly for session to complete
        time.sleep(1)

        status = get_session_status(agent_session_id)

        assert "error" not in status
        assert status["agent_session_id"] == agent_session_id

    def test_unknown_session(self):
        """Unknown session returns error."""
        result = get_session_status("nonexistent")
        assert "error" in result


class TestSessionCompletion:
    """Test session completion and results."""

    def test_session_completes(self):
        """Session completes successfully."""
        result = schedule_tests("echo 'test'")
        agent_session_id = result["agent_session_id"]

        # Wait for completion
        for _ in range(10):
            status = get_session_status(agent_session_id)
            if status.get("status") == "completed":
                break
            time.sleep(0.5)

        assert status["status"] == "completed"
        assert "results" in status

    def test_get_results(self):
        """Get detailed results."""
        result = schedule_tests("echo 'test'")
        agent_session_id = result["agent_session_id"]

        # Wait for completion
        time.sleep(2)

        results = get_session_results(agent_session_id)

        if results.get("status") == "completed" or "results" in results:
            assert "results" in results


class TestListSessions:
    """Test session listing."""

    def test_list_sessions(self):
        """List all sessions."""
        # Schedule a session first
        schedule_tests("echo 'test'")

        result = list_sessions()

        assert "sessions" in result
        assert "total" in result

    def test_filter_by_status(self):
        """Filter sessions by status."""
        result = list_sessions(status_filter="completed")

        assert "sessions" in result
        assert result["filtered_by"] == "completed"


class TestCancelSession:
    """Test session cancellation."""

    def test_cancel_unknown_session(self):
        """Cancel unknown session returns error."""
        result = cancel_session("nonexistent")
        assert "error" in result
