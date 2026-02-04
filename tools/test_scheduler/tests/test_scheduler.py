"""
Integration tests for test-scheduler tool.

Run with: pytest tools/test-scheduler/tests/ -v
"""

import time
import pytest

from tools.test_scheduler import (
    schedule_tests,
    get_job_status,
    list_jobs,
    cancel_job,
    get_job_results,
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
        assert "job_id" in result
        assert result["status"] == "scheduled"

    def test_schedule_generic(self):
        """Schedule generic command."""
        result = schedule_tests("echo 'test'")

        assert "error" not in result
        assert "job_id" in result

    def test_test_count(self):
        """Test count is returned."""
        result = schedule_tests("pytest tests/")

        assert "test_count" in result
        assert result["test_count"] >= 1


class TestJobStatus:
    """Test job status retrieval."""

    def test_get_status(self):
        """Get job status."""
        schedule_result = schedule_tests("echo 'hello'")
        job_id = schedule_result["job_id"]

        # Wait briefly for job to complete
        time.sleep(1)

        status = get_job_status(job_id)

        assert "error" not in status
        assert status["job_id"] == job_id

    def test_unknown_job(self):
        """Unknown job returns error."""
        result = get_job_status("nonexistent")
        assert "error" in result


class TestJobCompletion:
    """Test job completion and results."""

    def test_job_completes(self):
        """Job completes successfully."""
        result = schedule_tests("echo 'test'")
        job_id = result["job_id"]

        # Wait for completion
        for _ in range(10):
            status = get_job_status(job_id)
            if status.get("status") == "completed":
                break
            time.sleep(0.5)

        assert status["status"] == "completed"
        assert "results" in status

    def test_get_results(self):
        """Get detailed results."""
        result = schedule_tests("echo 'test'")
        job_id = result["job_id"]

        # Wait for completion
        time.sleep(2)

        results = get_job_results(job_id)

        if results.get("status") == "completed" or "results" in results:
            assert "results" in results


class TestListJobs:
    """Test job listing."""

    def test_list_jobs(self):
        """List all jobs."""
        # Schedule a job first
        schedule_tests("echo 'test'")

        result = list_jobs()

        assert "jobs" in result
        assert "total" in result

    def test_filter_by_status(self):
        """Filter jobs by status."""
        result = list_jobs(status_filter="completed")

        assert "jobs" in result
        assert result["filtered_by"] == "completed"


class TestCancelJob:
    """Test job cancellation."""

    def test_cancel_unknown_job(self):
        """Cancel unknown job returns error."""
        result = cancel_job("nonexistent")
        assert "error" in result
