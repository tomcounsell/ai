"""Tests for the kill command in job_scheduler.

Validates:
1. cmd_kill with nonexistent job_id returns structured error
2. cmd_kill with empty --job-id returns error
3. cmd_kill --all with no jobs returns "nothing to kill"
4. _find_process_by_session_id returns None for unknown session
5. _kill_process handles ProcessLookupError (already dead)
6. _kill_job sets status to "killed" via delete-and-recreate
7. cmd_status includes killed_count and killed_jobs in output
8. Recovery functions filter on status="running" (killed jobs excluded by query)
"""

import argparse
import json
import signal
from unittest.mock import MagicMock, patch

from tools.job_scheduler import (
    _find_process_by_session_id,
    _kill_job,
    _kill_process,
    cmd_kill,
    cmd_status,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_args(**kwargs) -> argparse.Namespace:
    """Build an argparse.Namespace with kill-command defaults."""
    defaults = {
        "job_id": None,
        "session_id": None,
        "all": False,
        "project": "valor",
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _make_status_args(**kwargs) -> argparse.Namespace:
    defaults = {"project": "valor"}
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


class _FakeJob:
    """Minimal stand-in for AgentSession in unit tests."""

    def __init__(self, job_id="job-123", session_id="sess-abc", status="running", **extra):
        self.job_id = job_id
        self.session_id = session_id
        self.status = status
        self.priority = extra.get("priority", "normal")
        self.message_text = extra.get("message_text", "/sdlc test")
        self.created_at = extra.get("created_at", 1700000000)
        self.started_at = extra.get("started_at", None)
        self.scheduled_after = extra.get("scheduled_after", None)
        self.issue_url = extra.get("issue_url", None)
        self.parent_job_id = extra.get("parent_job_id", None)
        self.completed_at = extra.get("completed_at", None)
        self._deleted = False

    def delete(self):
        self._deleted = True


class _FakeQuery:
    """Minimal stand-in for AgentSession.query with filter()."""

    def __init__(self, jobs_by_status=None):
        self._jobs = jobs_by_status or {}

    def filter(self, **kwargs):
        status = kwargs.get("status")
        project = kwargs.get("project_key")
        jobs = self._jobs.get(status, [])
        if project:
            return [j for j in jobs if True]  # project filtering not modeled
        return jobs


# ---------------------------------------------------------------------------
# _find_process_by_session_id
# ---------------------------------------------------------------------------


class TestFindProcessBySessionId:
    def test_returns_none_for_empty_session_id(self):
        assert _find_process_by_session_id("") is None
        assert _find_process_by_session_id(None) is None

    def test_returns_none_when_pgrep_finds_nothing(self):
        fake_result = MagicMock(returncode=1, stdout="")
        with patch("tools.job_scheduler.subprocess.run", return_value=fake_result):
            assert _find_process_by_session_id("nonexistent-session-xyz") is None

    def test_returns_pid_when_pgrep_finds_process(self):
        fake_result = MagicMock(returncode=0, stdout="12345\n")
        with (
            patch("tools.job_scheduler.subprocess.run", return_value=fake_result),
            patch("tools.job_scheduler.os.getpid", return_value=99999),
        ):
            assert _find_process_by_session_id("some-session") == 12345

    def test_skips_own_pid(self):
        fake_result = MagicMock(returncode=0, stdout="99999\n12345\n")
        with (
            patch("tools.job_scheduler.subprocess.run", return_value=fake_result),
            patch("tools.job_scheduler.os.getpid", return_value=99999),
        ):
            assert _find_process_by_session_id("some-session") == 12345

    def test_returns_none_when_only_own_pid(self):
        fake_result = MagicMock(returncode=0, stdout="99999\n")
        with (
            patch("tools.job_scheduler.subprocess.run", return_value=fake_result),
            patch("tools.job_scheduler.os.getpid", return_value=99999),
        ):
            assert _find_process_by_session_id("some-session") is None

    def test_handles_subprocess_exception(self):
        with patch("tools.job_scheduler.subprocess.run", side_effect=OSError("fail")):
            assert _find_process_by_session_id("some-session") is None

    def test_handles_subprocess_timeout(self):
        """pgrep timing out returns None rather than crashing."""
        import subprocess

        with patch(
            "tools.job_scheduler.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="pgrep", timeout=5),
        ):
            assert _find_process_by_session_id("some-session") is None


# ---------------------------------------------------------------------------
# _kill_process
# ---------------------------------------------------------------------------


class TestKillProcess:
    def test_handles_already_dead_process(self):
        """ProcessLookupError on SIGTERM means process is already gone."""
        with patch("os.kill", side_effect=ProcessLookupError):
            result = _kill_process(12345)
        assert result["pid"] == 12345
        assert result["action"] == "already_dead"

    def test_handles_permission_denied(self):
        with patch("os.kill", side_effect=PermissionError):
            result = _kill_process(12345)
        assert result["pid"] == 12345
        assert result["action"] == "permission_denied"

    def test_sigterm_kills_successfully(self):
        """SIGTERM succeeds and process dies within wait window."""
        call_count = 0

        def fake_kill(pid, sig):
            nonlocal call_count
            call_count += 1
            if sig == signal.SIGTERM:
                return  # SIGTERM sent OK
            if sig == 0:
                # First check: still alive; second check: dead
                if call_count <= 2:
                    return
                raise ProcessLookupError

        with patch("os.kill", side_effect=fake_kill), patch("time.sleep"):
            result = _kill_process(12345)
        assert result["pid"] == 12345
        assert result["action"] == "terminated_sigterm"

    def test_escalates_to_sigkill(self):
        """Process survives SIGTERM; SIGKILL is sent."""

        def fake_kill(pid, sig):
            if sig == signal.SIGTERM:
                return
            if sig == 0:
                return  # Always alive during checks
            if sig == signal.SIGKILL:
                return  # SIGKILL sent

        with patch("os.kill", side_effect=fake_kill), patch("time.sleep"):
            result = _kill_process(12345)
        assert result["pid"] == 12345
        assert result["action"] == "terminated_sigkill"

    def test_sigkill_permission_denied(self):
        """Process survives SIGTERM; SIGKILL gets permission denied."""

        def fake_kill(pid, sig):
            if sig == signal.SIGTERM:
                return
            if sig == 0:
                return  # Always alive during checks
            if sig == signal.SIGKILL:
                raise PermissionError()

        with patch("os.kill", side_effect=fake_kill), patch("time.sleep"):
            result = _kill_process(12345)
        assert result["pid"] == 12345
        assert result["action"] == "permission_denied"

    def test_dies_between_sigterm_and_sigkill(self):
        """Process dies just as SIGKILL is attempted (race condition)."""

        def fake_kill(pid, sig):
            if sig == signal.SIGTERM:
                return
            if sig == 0:
                return  # Alive during check window
            if sig == signal.SIGKILL:
                raise ProcessLookupError()  # Died right before SIGKILL

        with patch("os.kill", side_effect=fake_kill), patch("time.sleep"):
            result = _kill_process(12345)
        assert result["pid"] == 12345
        assert result["action"] == "terminated_sigterm"


# ---------------------------------------------------------------------------
# _kill_job
# ---------------------------------------------------------------------------


class TestKillJob:
    @patch("tools.job_scheduler._find_process_by_session_id", return_value=None)
    def test_sets_status_to_killed(self, _mock_find):
        """_kill_job transitions job status to 'killed' via delete-and-recreate."""
        job = _FakeJob(job_id="job-abc", session_id="sess-1", status="running")
        created_job = _FakeJob(job_id="job-new", session_id="sess-1", status="killed")

        fake_fields = {
            "session_id": "sess-1",
            "status": "running",
            "priority": "normal",
        }

        with (
            patch("agent.job_queue._extract_job_fields", return_value=dict(fake_fields)),
            patch(
                "models.agent_session.AgentSession.create", return_value=created_job
            ) as mock_create,
        ):
            result = _kill_job(job)

        assert result["status"] == "killed"
        assert result["previous_status"] == "running"
        assert job._deleted is True
        # Verify the create call used status="killed"
        create_kwargs = mock_create.call_args[1]
        assert create_kwargs["status"] == "killed"
        assert "completed_at" in create_kwargs

    def test_skip_process_kill_for_pending(self):
        """For pending jobs, skip_process_kill=True skips process termination."""
        job = _FakeJob(job_id="job-pend", session_id="sess-2", status="pending")
        created_job = _FakeJob(job_id="job-new", session_id="sess-2", status="killed")

        with (
            patch(
                "agent.job_queue._extract_job_fields",
                return_value={"session_id": "sess-2", "status": "pending"},
            ),
            patch("models.agent_session.AgentSession.create", return_value=created_job),
            patch("tools.job_scheduler._find_process_by_session_id") as mock_find,
        ):
            result = _kill_job(job, skip_process_kill=True)

        mock_find.assert_not_called()
        assert result["status"] == "killed"

    @patch("tools.job_scheduler._find_process_by_session_id", return_value=42)
    @patch(
        "tools.job_scheduler._kill_process",
        return_value={"pid": 42, "action": "terminated_sigterm"},
    )
    def test_kills_process_when_running(self, mock_kill_proc, mock_find):
        """For running jobs, _kill_job finds and kills the process."""
        job = _FakeJob(job_id="job-run", session_id="sess-3", status="running")
        created_job = _FakeJob(job_id="job-new", session_id="sess-3", status="killed")

        with (
            patch(
                "agent.job_queue._extract_job_fields",
                return_value={"session_id": "sess-3", "status": "running"},
            ),
            patch("models.agent_session.AgentSession.create", return_value=created_job),
        ):
            result = _kill_job(job)

        mock_find.assert_called_once_with("sess-3")
        mock_kill_proc.assert_called_once_with(42)
        assert result["process"]["action"] == "terminated_sigterm"
        assert result["status"] == "killed"


# ---------------------------------------------------------------------------
# cmd_kill
# ---------------------------------------------------------------------------


class TestCmdKill:
    def test_nonexistent_job_id_returns_error(self, capsys):
        """cmd_kill with a job_id that doesn't exist returns structured error."""
        fake_query = _FakeQuery(jobs_by_status={})

        with patch("models.agent_session.AgentSession.query", fake_query), patch("time.sleep"):
            ret = cmd_kill(_make_args(job_id="nonexistent-job"))

        assert ret == 1
        output = json.loads(capsys.readouterr().out)
        assert output["status"] == "error"
        assert "not found" in output["message"]

    def test_empty_job_id_returns_error(self, capsys):
        """cmd_kill with empty --job-id returns error."""
        ret = cmd_kill(_make_args(job_id="  "))
        assert ret == 1
        output = json.loads(capsys.readouterr().out)
        assert output["status"] == "error"
        assert "empty" in output["message"].lower()

    def test_empty_session_id_returns_error(self, capsys):
        """cmd_kill with empty --session-id returns error."""
        ret = cmd_kill(_make_args(session_id="  "))
        assert ret == 1
        output = json.loads(capsys.readouterr().out)
        assert output["status"] == "error"
        assert "empty" in output["message"].lower()

    def test_kill_all_with_no_jobs(self, capsys):
        """cmd_kill --all with no running/pending jobs returns 'nothing to kill'."""
        fake_query = _FakeQuery(jobs_by_status={})

        with patch("models.agent_session.AgentSession.query", fake_query):
            ret = cmd_kill(_make_args(all=True))

        assert ret == 0
        output = json.loads(capsys.readouterr().out)
        assert "no running or pending" in output["message"].lower()

    def test_kill_all_kills_running_and_pending(self, capsys):
        """cmd_kill --all kills both running and pending jobs."""
        running_job = _FakeJob(job_id="run-1", session_id="s-1", status="running")
        pending_job = _FakeJob(job_id="pend-1", session_id="s-2", status="pending")
        fake_query = _FakeQuery(
            jobs_by_status={
                "running": [running_job],
                "pending": [pending_job],
            }
        )

        created_jobs = [
            _FakeJob(job_id="new-1", status="killed"),
            _FakeJob(job_id="new-2", status="killed"),
        ]
        create_call_count = [0]

        def fake_create(**kwargs):
            job = created_jobs[create_call_count[0]]
            create_call_count[0] += 1
            return job

        with (
            patch("models.agent_session.AgentSession.query", fake_query),
            patch("models.agent_session.AgentSession.create", side_effect=fake_create),
            patch("agent.job_queue._extract_job_fields", return_value={"status": "running"}),
            patch("tools.job_scheduler._find_process_by_session_id", return_value=None),
        ):
            ret = cmd_kill(_make_args(all=True))

        assert ret == 0
        output = json.loads(capsys.readouterr().out)
        assert output["status"] == "killed"
        assert output["count"] == 2

    def test_kill_specific_job_by_id(self, capsys):
        """cmd_kill --job-id finds and kills the target job."""
        target = _FakeJob(job_id="target-job", session_id="s-target", status="running")
        other = _FakeJob(job_id="other-job", session_id="s-other", status="running")
        fake_query = _FakeQuery(jobs_by_status={"running": [other, target]})
        created = _FakeJob(job_id="new-target", status="killed")

        with (
            patch("models.agent_session.AgentSession.query", fake_query),
            patch("models.agent_session.AgentSession.create", return_value=created),
            patch("agent.job_queue._extract_job_fields", return_value={"status": "running"}),
            patch("tools.job_scheduler._find_process_by_session_id", return_value=None),
        ):
            ret = cmd_kill(_make_args(job_id="target-job"))

        assert ret == 0
        output = json.loads(capsys.readouterr().out)
        assert output["status"] == "killed"
        assert output["count"] == 1
        assert output["jobs"][0]["job_id"] == "target-job"

    def test_no_identifier_returns_error(self, capsys):
        """cmd_kill with no --job-id, --session-id, or --all returns error."""
        ret = cmd_kill(_make_args())
        assert ret == 1
        output = json.loads(capsys.readouterr().out)
        assert output["status"] == "error"
        assert "required" in output["message"].lower()

    def test_kill_by_session_id(self, capsys):
        """cmd_kill --session-id finds and kills the target job."""
        target = _FakeJob(job_id="job-sess", session_id="target-session", status="running")
        fake_query = _FakeQuery(jobs_by_status={"running": [target]})
        created = _FakeJob(job_id="new-sess", status="killed")

        with (
            patch("models.agent_session.AgentSession.query", fake_query),
            patch("models.agent_session.AgentSession.create", return_value=created),
            patch("agent.job_queue._extract_job_fields", return_value={"status": "running"}),
            patch("tools.job_scheduler._find_process_by_session_id", return_value=None),
        ):
            ret = cmd_kill(_make_args(session_id="target-session"))

        assert ret == 0
        output = json.loads(capsys.readouterr().out)
        assert output["status"] == "killed"
        assert output["count"] == 1

    def test_nonexistent_session_id_returns_error(self, capsys):
        """cmd_kill --session-id with unknown session returns error."""
        fake_query = _FakeQuery(jobs_by_status={})

        with patch("models.agent_session.AgentSession.query", fake_query):
            ret = cmd_kill(_make_args(session_id="nonexistent-session"))

        assert ret == 1
        output = json.loads(capsys.readouterr().out)
        assert output["status"] == "error"
        assert "not found" in output["message"]

    def test_kill_handles_redis_exception(self, capsys):
        """cmd_kill handles unexpected Redis exceptions gracefully."""

        class ExplodingQuery:
            def filter(self, **kwargs):
                raise ConnectionError("Redis down")

        with patch("models.agent_session.AgentSession.query", ExplodingQuery()):
            ret = cmd_kill(_make_args(all=True))

        assert ret == 1
        output = json.loads(capsys.readouterr().out)
        assert output["status"] == "error"
        assert "failed" in output["message"].lower()


# ---------------------------------------------------------------------------
# cmd_status includes killed counts
# ---------------------------------------------------------------------------


class TestStatusIncludesKilled:
    def test_status_includes_killed_count(self, capsys):
        """cmd_status output includes killed_count and killed_jobs."""
        killed_job = _FakeJob(job_id="k-1", session_id="sk-1", status="killed")
        fake_query = _FakeQuery(
            jobs_by_status={
                "pending": [],
                "running": [],
                "completed": [],
                "waiting_for_children": [],
                "killed": [killed_job],
            }
        )

        with (
            patch("models.agent_session.AgentSession.query", fake_query),
            patch("agent.job_queue.PRIORITY_RANK", {"urgent": 0, "high": 1, "normal": 2, "low": 3}),
        ):
            ret = cmd_status(_make_status_args())

        assert ret == 0
        output = json.loads(capsys.readouterr().out)
        assert output["killed_count"] == 1
        assert len(output["killed_jobs"]) == 1
        assert output["killed_jobs"][0]["job_id"] == "k-1"

    def test_status_no_killed_jobs(self, capsys):
        """cmd_status with no killed jobs omits killed_jobs key."""
        fake_query = _FakeQuery(
            jobs_by_status={
                "pending": [],
                "running": [],
                "completed": [],
                "waiting_for_children": [],
                "killed": [],
            }
        )

        with (
            patch("models.agent_session.AgentSession.query", fake_query),
            patch("agent.job_queue.PRIORITY_RANK", {"urgent": 0, "high": 1, "normal": 2, "low": 3}),
        ):
            ret = cmd_status(_make_status_args())

        assert ret == 0
        output = json.loads(capsys.readouterr().out)
        assert output["killed_count"] == 0
        assert "killed_jobs" not in output  # Only shown when there are killed jobs


# ---------------------------------------------------------------------------
# Recovery functions filter on status="running" (killed excluded)
# ---------------------------------------------------------------------------


class TestRecoveryExcludesKilled:
    """Verify that recovery functions query status='running', not killed."""

    def test_recover_interrupted_jobs_filters_running(self):
        """_recover_interrupted_jobs queries status='running', excluding killed."""
        from agent.job_queue import _recover_interrupted_jobs

        # Mock AgentSession.query.filter to capture the status argument
        calls = []

        class CapturingQuery:
            def filter(self, **kwargs):
                calls.append(kwargs)
                return []

        with patch("agent.job_queue.AgentSession") as mock_cls:
            mock_cls.query = CapturingQuery()
            result = _recover_interrupted_jobs("valor")

        assert result == 0
        # The function should filter on status="running"
        assert any(c.get("status") == "running" for c in calls)
        # No call should filter on status="killed"
        assert not any(c.get("status") == "killed" for c in calls)

    def test_reset_running_jobs_filters_running(self):
        """_reset_running_jobs queries status='running', excluding killed."""
        import asyncio

        from agent.job_queue import _reset_running_jobs

        calls = []

        class CapturingQuery:
            async def async_filter(self, **kwargs):
                calls.append(kwargs)
                return []

        with patch("agent.job_queue.AgentSession") as mock_cls:
            mock_cls.query = CapturingQuery()
            result = asyncio.run(_reset_running_jobs("valor"))

        assert result == 0
        assert any(c.get("status") == "running" for c in calls)
        assert not any(c.get("status") == "killed" for c in calls)
