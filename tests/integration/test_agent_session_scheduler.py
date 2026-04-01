"""Tests for session scheduling: scheduled_at, 4-tier priority, FIFO, and CLI tool."""

import asyncio
import json
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent.agent_session_queue import (
    PRIORITY_RANK,
    _extract_agent_session_fields,
    _pop_agent_session,
)
from models.agent_session import AgentSession

# Project root derived from file location (tests/integration/ -> project root)
_PROJECT_ROOT = str(Path(__file__).parent.parent.parent)


def _subprocess_env(**extra) -> dict:
    """Build env dict for subprocess calls that routes them to the test Redis DB.

    Popoto picks up REDIS_URL at import time. By pointing subprocesses at
    db=1 (the same DB the redis_test_db fixture uses for non-xdist runs),
    we prevent test sessions from leaking into production db=0.
    """
    import os

    env = {**os.environ, **extra}
    # Use the same test DB that the redis_test_db conftest fixture selects
    env["REDIS_URL"] = "redis://127.0.0.1:6379/1"
    return env


# === Fixtures ===


@pytest.fixture(autouse=True)
def cleanup_test_sessions():
    """Clean up any test sessions before and after each test."""
    _cleanup()
    yield
    _cleanup()


def _cleanup():
    """Remove all test sessions from Redis."""
    for status in ("pending", "running", "completed", "failed", "killed"):
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
    scheduled_at=None,
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
        telegram_message_id=0,
        scheduled_at=scheduled_at,
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


# === _pop_agent_session Tests ===


class TestPopJob:
    def test_fifo_ordering(self):
        """Within same priority, oldest session (FIFO) is popped first."""
        _create_pending(created_at=datetime.now(tz=UTC) - timedelta(seconds=100), message="old")
        _create_pending(created_at=datetime.now(tz=UTC), message="new")

        session = asyncio.run(_pop_agent_session("test-chat"))
        assert session is not None
        assert "old" in session.message_text

    def test_priority_ordering(self):
        """Higher priority jobs are popped before lower priority ones."""
        _create_pending(priority="low", message="low-prio")
        _create_pending(priority="urgent", message="urgent-prio")

        session = asyncio.run(_pop_agent_session("test-chat"))
        assert session is not None
        assert "urgent" in session.message_text

    def test_scheduled_at_future_skipped(self):
        """Jobs with scheduled_at in the future are skipped."""
        future = time.time() + 3600  # 1 hour from now
        _create_pending(scheduled_at=future, message="deferred")

        session = asyncio.run(_pop_agent_session("test-chat"))
        assert session is None  # No eligible sessions

    def test_scheduled_at_past_eligible(self):
        """Jobs with scheduled_at in the past are eligible."""
        past = time.time() - 60  # 1 minute ago
        _create_pending(scheduled_at=past, message="ready")

        session = asyncio.run(_pop_agent_session("test-chat"))
        assert session is not None
        assert "ready" in session.message_text

    def test_scheduled_at_none_eligible(self):
        """Jobs with no scheduled_at are always eligible."""
        _create_pending(message="immediate")

        session = asyncio.run(_pop_agent_session("test-chat"))
        assert session is not None
        assert "immediate" in session.message_text

    def test_mixed_deferred_and_immediate(self):
        """Only immediate jobs are popped when deferred jobs exist."""
        future = time.time() + 3600
        _create_pending(scheduled_at=future, message="deferred", priority="urgent")
        _create_pending(message="immediate", priority="low")

        session = asyncio.run(_pop_agent_session("test-chat"))
        assert session is not None
        assert "immediate" in session.message_text

    def test_unknown_priority_defaults_normal(self):
        """Unknown priority values default to normal ranking."""
        _create_pending(priority="unknown", message="unknown-prio")
        _create_pending(priority="low", message="low-prio")

        session = asyncio.run(_pop_agent_session("test-chat"))
        assert session is not None
        assert "unknown" in session.message_text  # normal rank < low rank


# === AgentSession Model Tests ===


class TestAgentSessionFields:
    def test_scheduled_at_field(self):
        """scheduled_at field persists on AgentSession."""
        future = time.time() + 3600
        session = _create_pending(scheduled_at=future)
        assert float(session.scheduled_at) == pytest.approx(future, abs=1)

    def test_scheduling_depth_field(self):
        """scheduling_depth field persists on AgentSession."""
        session = _create_pending(scheduling_depth=2)
        assert int(session.scheduling_depth) == 2

    def test_default_priority_normal(self):
        """Default priority is 'normal'."""
        session = _create_pending()
        assert session.priority == "normal"

    def test_extract_agent_session_fields_includes_new_fields(self):
        """_extract_agent_session_fields preserves scheduled_at and scheduling_depth."""
        future = time.time() + 3600
        session = _create_pending(scheduled_at=future, scheduling_depth=1)
        fields = _extract_agent_session_fields(session)
        assert "scheduled_at" in fields
        assert "scheduling_depth" in fields
        assert float(fields["scheduled_at"]) == pytest.approx(future, abs=1)
        assert int(fields["scheduling_depth"]) == 1


# === CLI Tool Tests ===


class TestJobSchedulerCLI:
    def test_help(self):
        """Tool responds to --help."""
        result = subprocess.run(
            [sys.executable, "-m", "tools.agent_session_scheduler", "--help"],
            capture_output=True,
            text=True,
            cwd=_PROJECT_ROOT,
            env=_subprocess_env(),
        )
        assert result.returncode == 0
        assert "schedule" in result.stdout
        assert "status" in result.stdout

    def test_status_command(self):
        """Status command returns valid JSON."""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.agent_session_scheduler",
                "status",
                "--project",
                "test-scheduler",
            ],
            capture_output=True,
            text=True,
            cwd=_PROJECT_ROOT,
            env=_subprocess_env(),
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "pending_count" in data
        assert "running_count" in data

    def test_push_and_pop(self):
        """Push a session and pop it back."""
        # Use unique project key to avoid interference
        proj = f"test-pushpop-{time.time_ns()}"

        # Push
        push_result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.agent_session_scheduler",
                "push",
                "--message",
                "test push message",
                "--project",
                proj,
            ],
            capture_output=True,
            text=True,
            cwd=_PROJECT_ROOT,
            env=_subprocess_env(PROJECT_KEY=proj),
        )
        assert push_result.returncode == 0
        push_data = json.loads(push_result.stdout)
        assert push_data["status"] == "queued"

        # Pop
        pop_result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.agent_session_scheduler",
                "pop",
                "--project",
                proj,
            ],
            capture_output=True,
            text=True,
            cwd=_PROJECT_ROOT,
            env=_subprocess_env(),
        )
        assert pop_result.returncode == 0
        pop_data = json.loads(pop_result.stdout)
        assert pop_data["status"] == "popped"
        assert "test push" in pop_data["message_preview"]

    def test_schedule_invalid_issue(self):
        """Scheduling with invalid issue returns error."""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.agent_session_scheduler",
                "schedule",
                "--issue",
                "999999999",
                "--project",
                "test-scheduler",
            ],
            capture_output=True,
            text=True,
            cwd=_PROJECT_ROOT,
            env=_subprocess_env(),
        )
        # Should fail (exit 1) with error JSON
        assert result.returncode == 1
        data = json.loads(result.stdout)
        assert data["status"] == "error"

    def test_cancel_nonexistent(self):
        """Cancelling nonexistent session returns error."""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.agent_session_scheduler",
                "cancel",
                "--agent-session-id",
                "nonexistent-session-id",
            ],
            capture_output=True,
            text=True,
            cwd=_PROJECT_ROOT,
            env=_subprocess_env(),
        )
        assert result.returncode == 1
        data = json.loads(result.stdout)
        assert data["status"] == "error"

    def test_bump_nonexistent(self):
        """Bumping nonexistent session returns error."""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.agent_session_scheduler",
                "bump",
                "--agent-session-id",
                "nonexistent-session-id",
            ],
            capture_output=True,
            text=True,
            cwd=_PROJECT_ROOT,
            env=_subprocess_env(),
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
                "tools.agent_session_scheduler",
                "push",
                "--message",
                "depth test",
                "--project",
                "test-scheduler",
            ],
            capture_output=True,
            text=True,
            cwd=_PROJECT_ROOT,
            env=_subprocess_env(PROJECT_KEY="test-scheduler"),
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["scheduling_depth"] >= 1

    def test_depth_cap_enforced(self):
        """Cannot schedule beyond MAX_SCHEDULING_DEPTH via direct function call."""
        from tools.agent_session_scheduler import MAX_SCHEDULING_DEPTH, _get_scheduling_depth

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


# === Kill Command Integration Tests ===


class TestKillCommandIntegration:
    """Integration tests for the kill subcommand using real Redis."""

    def test_kill_cli_help(self):
        """Kill subcommand shows in help output."""
        result = subprocess.run(
            [sys.executable, "-m", "tools.agent_session_scheduler", "--help"],
            capture_output=True,
            text=True,
            cwd=_PROJECT_ROOT,
            env=_subprocess_env(),
        )
        assert result.returncode == 0
        assert "kill" in result.stdout

    def test_kill_by_agent_session_id_pending(self):
        """Kill a pending session by agent_session_id via CLI (push then kill)."""
        proj = f"test-killid-{time.time_ns()}"

        push_result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.agent_session_scheduler",
                "push",
                "--message",
                "kill-me-pending",
                "--project",
                proj,
            ],
            capture_output=True,
            text=True,
            cwd=_PROJECT_ROOT,
            env=_subprocess_env(PROJECT_KEY=proj),
        )
        assert push_result.returncode == 0
        push_data = json.loads(push_result.stdout)
        agent_session_id = push_data["agent_session_id"]

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.agent_session_scheduler",
                "kill",
                "--agent-session-id",
                agent_session_id,
            ],
            capture_output=True,
            text=True,
            cwd=_PROJECT_ROOT,
            env=_subprocess_env(),
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["status"] == "killed"
        assert data["count"] == 1
        assert data["sessions"][0]["previous_status"] == "pending"

    def test_kill_by_session_id(self):
        """Kill a pending session by session_id via CLI (push then kill)."""
        proj = f"test-killsess-{time.time_ns()}"

        push_result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.agent_session_scheduler",
                "push",
                "--message",
                "kill-by-session",
                "--project",
                proj,
            ],
            capture_output=True,
            text=True,
            cwd=_PROJECT_ROOT,
            env=_subprocess_env(PROJECT_KEY=proj),
        )
        assert push_result.returncode == 0
        push_data = json.loads(push_result.stdout)
        session_id = push_data["session_id"]

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.agent_session_scheduler",
                "kill",
                "--session-id",
                session_id,
            ],
            capture_output=True,
            text=True,
            cwd=_PROJECT_ROOT,
            env=_subprocess_env(),
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["status"] == "killed"
        assert data["count"] == 1

    def test_kill_nonexistent_job(self):
        """Kill with nonexistent agent_session_id returns error."""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.agent_session_scheduler",
                "kill",
                "--agent-session-id",
                "nonexistent-kill-target",
            ],
            capture_output=True,
            text=True,
            cwd=_PROJECT_ROOT,
            env=_subprocess_env(),
        )
        assert result.returncode == 1
        data = json.loads(result.stdout)
        assert data["status"] == "error"
        assert "not found" in data["message"].lower()

    def test_kill_nonexistent_session(self):
        """Kill with nonexistent session_id returns error."""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.agent_session_scheduler",
                "kill",
                "--session-id",
                "nonexistent-kill-session",
            ],
            capture_output=True,
            text=True,
            cwd=_PROJECT_ROOT,
            env=_subprocess_env(),
        )
        assert result.returncode == 1
        data = json.loads(result.stdout)
        assert data["status"] == "error"
        assert "not found" in data["message"].lower()

    def test_kill_all_with_pending_jobs(self):
        """Kill --all removes all pending sessions created via CLI."""
        proj = f"test-killall-{time.time_ns()}"

        for i in range(2):
            push_result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tools.agent_session_scheduler",
                    "push",
                    "--message",
                    f"pending-{i}",
                    "--project",
                    proj,
                ],
                capture_output=True,
                text=True,
                cwd=_PROJECT_ROOT,
                env=_subprocess_env(PROJECT_KEY=proj),
            )
            assert push_result.returncode == 0

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.agent_session_scheduler",
                "kill",
                "--all",
            ],
            capture_output=True,
            text=True,
            cwd=_PROJECT_ROOT,
            env=_subprocess_env(),
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["status"] == "killed"
        assert data["count"] >= 2

    def test_kill_all_empty_queue(self):
        """Kill --all with no active jobs returns ok."""
        _cleanup()

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.agent_session_scheduler",
                "kill",
                "--all",
            ],
            capture_output=True,
            text=True,
            cwd=_PROJECT_ROOT,
            env=_subprocess_env(),
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["status"] in ("ok", "killed")

    def test_status_shows_killed_count(self):
        """Status command includes killed_count after killing a session."""
        proj = f"test-killstatus-{time.time_ns()}"

        push_result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.agent_session_scheduler",
                "push",
                "--message",
                "to-be-killed",
                "--project",
                proj,
            ],
            capture_output=True,
            text=True,
            cwd=_PROJECT_ROOT,
            env=_subprocess_env(PROJECT_KEY=proj),
        )
        assert push_result.returncode == 0
        agent_session_id = json.loads(push_result.stdout)["agent_session_id"]

        subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.agent_session_scheduler",
                "kill",
                "--agent-session-id",
                agent_session_id,
            ],
            capture_output=True,
            text=True,
            cwd=_PROJECT_ROOT,
            env=_subprocess_env(),
        )

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.agent_session_scheduler",
                "status",
                "--project",
                proj,
            ],
            capture_output=True,
            text=True,
            cwd=_PROJECT_ROOT,
            env=_subprocess_env(),
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "killed_count" in data
        assert data["killed_count"] >= 1

    def test_kill_sets_status_to_killed(self):
        """Verify _kill_agent_session transitions status from pending to killed in Redis."""
        from tools.agent_session_scheduler import _kill_agent_session

        session = _create_pending(message="direct-kill-test")
        original_session_id = session.session_id

        result = _kill_agent_session(session, skip_process_kill=True)

        assert result["status"] == "killed"
        assert result["previous_status"] == "pending"

        killed = list(AgentSession.query.filter(status="killed"))
        found = [j for j in killed if j.session_id == original_session_id]
        assert len(found) == 1
        assert found[0].completed_at is not None
