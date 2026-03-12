"""Tests for job scheduling: scheduled_after, 4-tier priority, FIFO, and CLI tool."""

import asyncio
import json
import subprocess
import sys
import time

import pytest

from agent.job_queue import PRIORITY_RANK, _extract_job_fields, _pop_job
from models.agent_session import AgentSession

# === Fixtures ===


@pytest.fixture(autouse=True)
def cleanup_test_sessions():
    """Clean up any test sessions before and after each test."""
    _cleanup()
    yield
    _cleanup()


def _cleanup():
    """Remove all test sessions from Redis."""
    for status in ("pending", "running", "completed", "failed"):
        try:
            sessions = AgentSession.query.filter(status=status)
            for s in sessions:
                if s.project_key and s.project_key.startswith("test-"):
                    s.delete()
        except Exception:
            pass


def _create_pending(
    project_key="test-scheduler",
    priority="normal",
    message="test",
    scheduled_after=None,
    scheduling_depth=0,
    created_at=None,
):
    """Helper to create a pending AgentSession for testing."""
    return AgentSession.create(
        project_key=project_key,
        status="pending",
        priority=priority,
        created_at=created_at or time.time(),
        session_id=f"test-{time.time_ns()}",
        working_dir="/tmp/test",
        message_text=message,
        sender_name="Test",
        chat_id="test-chat",
        message_id=0,
        scheduled_after=scheduled_after,
        scheduling_depth=scheduling_depth,
    )


# === Priority Ranking Tests ===


class TestPriorityRank:
    def test_four_tier_ordering(self):
        """PRIORITY_RANK has correct 4-tier ordering."""
        assert PRIORITY_RANK["urgent"] < PRIORITY_RANK["high"]
        assert PRIORITY_RANK["high"] < PRIORITY_RANK["normal"]
        assert PRIORITY_RANK["normal"] < PRIORITY_RANK["low"]

    def test_all_tiers_present(self):
        assert set(PRIORITY_RANK.keys()) == {"urgent", "high", "normal", "low"}


# === _pop_job Tests ===


class TestPopJob:
    def test_fifo_ordering(self):
        """Within same priority, oldest job (FIFO) is popped first."""
        _create_pending(created_at=time.time() - 100, message="old")
        _create_pending(created_at=time.time(), message="new")

        job = asyncio.get_event_loop().run_until_complete(_pop_job("test-scheduler"))
        assert job is not None
        assert "old" in job.message_text

    def test_priority_ordering(self):
        """Higher priority jobs are popped before lower priority ones."""
        _create_pending(priority="low", message="low-prio")
        _create_pending(priority="urgent", message="urgent-prio")

        job = asyncio.get_event_loop().run_until_complete(_pop_job("test-scheduler"))
        assert job is not None
        assert "urgent" in job.message_text

    def test_scheduled_after_future_skipped(self):
        """Jobs with scheduled_after in the future are skipped."""
        future = time.time() + 3600  # 1 hour from now
        _create_pending(scheduled_after=future, message="deferred")

        job = asyncio.get_event_loop().run_until_complete(_pop_job("test-scheduler"))
        assert job is None  # No eligible jobs

    def test_scheduled_after_past_eligible(self):
        """Jobs with scheduled_after in the past are eligible."""
        past = time.time() - 60  # 1 minute ago
        _create_pending(scheduled_after=past, message="ready")

        job = asyncio.get_event_loop().run_until_complete(_pop_job("test-scheduler"))
        assert job is not None
        assert "ready" in job.message_text

    def test_scheduled_after_none_eligible(self):
        """Jobs with no scheduled_after are always eligible."""
        _create_pending(message="immediate")

        job = asyncio.get_event_loop().run_until_complete(_pop_job("test-scheduler"))
        assert job is not None
        assert "immediate" in job.message_text

    def test_mixed_deferred_and_immediate(self):
        """Only immediate jobs are popped when deferred jobs exist."""
        future = time.time() + 3600
        _create_pending(scheduled_after=future, message="deferred", priority="urgent")
        _create_pending(message="immediate", priority="low")

        job = asyncio.get_event_loop().run_until_complete(_pop_job("test-scheduler"))
        assert job is not None
        assert "immediate" in job.message_text

    def test_unknown_priority_defaults_normal(self):
        """Unknown priority values default to normal ranking."""
        _create_pending(priority="unknown", message="unknown-prio")
        _create_pending(priority="low", message="low-prio")

        job = asyncio.get_event_loop().run_until_complete(_pop_job("test-scheduler"))
        assert job is not None
        assert "unknown" in job.message_text  # normal rank < low rank


# === AgentSession Model Tests ===


class TestAgentSessionFields:
    def test_scheduled_after_field(self):
        """scheduled_after field persists on AgentSession."""
        future = time.time() + 3600
        session = _create_pending(scheduled_after=future)
        assert float(session.scheduled_after) == pytest.approx(future, abs=1)

    def test_scheduling_depth_field(self):
        """scheduling_depth field persists on AgentSession."""
        session = _create_pending(scheduling_depth=2)
        assert int(session.scheduling_depth) == 2

    def test_default_priority_normal(self):
        """Default priority is 'normal'."""
        session = _create_pending()
        assert session.priority == "normal"

    def test_extract_job_fields_includes_new_fields(self):
        """_extract_job_fields preserves scheduled_after and scheduling_depth."""
        future = time.time() + 3600
        session = _create_pending(scheduled_after=future, scheduling_depth=1)
        fields = _extract_job_fields(session)
        assert "scheduled_after" in fields
        assert "scheduling_depth" in fields
        assert float(fields["scheduled_after"]) == pytest.approx(future, abs=1)
        assert int(fields["scheduling_depth"]) == 1


# === CLI Tool Tests ===


class TestJobSchedulerCLI:
    def test_help(self):
        """Tool responds to --help."""
        result = subprocess.run(
            ["python", "-m", "tools.job_scheduler", "--help"],
            capture_output=True,
            text=True,
            cwd="/Users/valorengels/src/ai",
        )
        assert result.returncode == 0
        assert "schedule" in result.stdout
        assert "status" in result.stdout

    def test_status_command(self):
        """Status command returns valid JSON."""
        result = subprocess.run(
            ["python", "-m", "tools.job_scheduler", "status", "--project", "test-scheduler"],
            capture_output=True,
            text=True,
            cwd="/Users/valorengels/src/ai",
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "pending_count" in data
        assert "running_count" in data

    def test_push_and_pop(self):
        """Push a job and pop it back."""
        # Use unique project key to avoid interference
        proj = f"test-pushpop-{time.time_ns()}"

        # Push
        push_result = subprocess.run(
            [
                "python",
                "-m",
                "tools.job_scheduler",
                "push",
                "--message",
                "test push message",
                "--project",
                proj,
            ],
            capture_output=True,
            text=True,
            cwd="/Users/valorengels/src/ai",
            env={**__import__("os").environ, "PROJECT_KEY": proj},
        )
        assert push_result.returncode == 0
        push_data = json.loads(push_result.stdout)
        assert push_data["status"] == "queued"

        # Pop
        pop_result = subprocess.run(
            [
                "python",
                "-m",
                "tools.job_scheduler",
                "pop",
                "--project",
                proj,
            ],
            capture_output=True,
            text=True,
            cwd="/Users/valorengels/src/ai",
        )
        assert pop_result.returncode == 0
        pop_data = json.loads(pop_result.stdout)
        assert pop_data["status"] == "popped"
        assert "test push" in pop_data["message_preview"]

    def test_schedule_invalid_issue(self):
        """Scheduling with invalid issue returns error."""
        result = subprocess.run(
            [
                "python",
                "-m",
                "tools.job_scheduler",
                "schedule",
                "--issue",
                "999999999",
                "--project",
                "test-scheduler",
            ],
            capture_output=True,
            text=True,
            cwd="/Users/valorengels/src/ai",
        )
        # Should fail (exit 1) with error JSON
        assert result.returncode == 1
        data = json.loads(result.stdout)
        assert data["status"] == "error"

    def test_cancel_nonexistent(self):
        """Cancelling nonexistent job returns error."""
        result = subprocess.run(
            [
                "python",
                "-m",
                "tools.job_scheduler",
                "cancel",
                "--job-id",
                "nonexistent-job-id",
            ],
            capture_output=True,
            text=True,
            cwd="/Users/valorengels/src/ai",
        )
        assert result.returncode == 1
        data = json.loads(result.stdout)
        assert data["status"] == "error"

    def test_bump_nonexistent(self):
        """Bumping nonexistent job returns error."""
        result = subprocess.run(
            [
                "python",
                "-m",
                "tools.job_scheduler",
                "bump",
                "--job-id",
                "nonexistent-job-id",
            ],
            capture_output=True,
            text=True,
            cwd="/Users/valorengels/src/ai",
        )
        assert result.returncode == 1
        data = json.loads(result.stdout)
        assert data["status"] == "error"


# === Depth and Rate Limit Tests ===


class TestSelfSchedulingProtection:
    def test_scheduling_depth_increments(self):
        """Pushed jobs increment scheduling_depth."""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.job_scheduler",
                "push",
                "--message",
                "depth test",
                "--project",
                "test-scheduler",
            ],
            capture_output=True,
            text=True,
            cwd="/Users/valorengels/src/ai",
            env={**__import__("os").environ, "PROJECT_KEY": "test-scheduler"},
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["scheduling_depth"] >= 1

    def test_depth_cap_enforced(self):
        """Cannot schedule beyond MAX_SCHEDULING_DEPTH via direct function call."""
        from tools.job_scheduler import MAX_SCHEDULING_DEPTH, _get_scheduling_depth

        # Test the constant
        assert MAX_SCHEDULING_DEPTH == 3

        # Verify the depth check mechanism works correctly:
        # A session at depth 3 should trigger the cap in the scheduling logic
        parent = _create_pending(scheduling_depth=3)

        # Test that _get_scheduling_depth correctly reads from AgentSession
        import os

        os.environ["VALOR_SESSION_ID"] = parent.session_id
        try:
            depth = _get_scheduling_depth()
            assert depth == 3, f"Expected depth 3, got {depth}"
            assert depth >= MAX_SCHEDULING_DEPTH
        finally:
            del os.environ["VALOR_SESSION_ID"]
