"""Tests for remote update: shell script, bridge intercept, restart flag lifecycle."""

import os
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Project root
PROJECT_DIR = Path(__file__).parent.parent


# =============================================================================
# Shell Script Tests
# =============================================================================


class TestRemoteUpdateScript:
    """Test scripts/remote-update.sh behavior."""

    SCRIPT = str(PROJECT_DIR / "scripts" / "remote-update.sh")

    def test_script_exists_and_is_executable(self):
        script = Path(self.SCRIPT)
        assert script.exists(), "scripts/remote-update.sh should exist"
        assert os.access(str(script), os.X_OK), "Script should be executable"

    def test_already_up_to_date(self):
        """When HEAD matches remote, script exits 0 with 'Already up to date'."""
        # Ensure we're on main and up to date
        result = subprocess.run(
            ["bash", self.SCRIPT],
            cwd=str(PROJECT_DIR),
            capture_output=True,
            text=True,
            timeout=30,
        )
        # Since we just pulled (or are current), expect "Already up to date"
        assert result.returncode == 0
        assert "Already up to date" in result.stdout or "commit(s)" in result.stdout

    def test_no_restart_flag_when_up_to_date(self):
        """When already up to date, no restart flag should be written."""
        flag = PROJECT_DIR / "data" / "restart-requested"
        # Remove any existing flag
        flag.unlink(missing_ok=True)

        result = subprocess.run(
            ["bash", self.SCRIPT],
            cwd=str(PROJECT_DIR),
            capture_output=True,
            text=True,
            timeout=30,
        )

        if "Already up to date" in result.stdout:
            assert (
                not flag.exists()
            ), "No restart flag should be written when up to date"

    def test_lockfile_prevents_concurrent_runs(self):
        """Second invocation should skip if lock is held."""
        lock_dir = PROJECT_DIR / "data" / "update.lock"
        lock_dir.mkdir(parents=True, exist_ok=True)
        try:
            result = subprocess.run(
                ["bash", self.SCRIPT],
                cwd=str(PROJECT_DIR),
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert result.returncode == 0
            assert "Another update is already running" in result.stdout
        finally:
            lock_dir.rmdir()

    def test_lockfile_cleaned_up_on_exit(self):
        """Lock directory should be removed after script completes."""
        lock_dir = PROJECT_DIR / "data" / "update.lock"
        lock_dir.unlink(missing_ok=True) if lock_dir.is_file() else None
        if lock_dir.exists():
            lock_dir.rmdir()

        subprocess.run(
            ["bash", self.SCRIPT],
            cwd=str(PROJECT_DIR),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert not lock_dir.exists(), "Lock directory should be cleaned up"

    def test_log_prefix_on_all_lines(self):
        """All output lines should have a log prefix."""
        result = subprocess.run(
            ["bash", self.SCRIPT],
            cwd=str(PROJECT_DIR),
            capture_output=True,
            text=True,
            timeout=30,
        )
        for line in result.stdout.strip().split("\n"):
            if line.strip():
                # Accept both [remote-update] (shell) and [update] (Python module) prefixes
                assert line.startswith("[remote-update]") or line.startswith(
                    "[update]"
                ), f"Line missing prefix: {line!r}"


# =============================================================================
# Restart Flag Tests
# =============================================================================


class TestRestartFlag:
    """Test restart flag lifecycle in job_queue.py."""

    def setup_method(self):
        """Ensure clean state for each test."""
        from agent.job_queue import _RESTART_FLAG

        _RESTART_FLAG.parent.mkdir(parents=True, exist_ok=True)
        _RESTART_FLAG.unlink(missing_ok=True)

    def teardown_method(self):
        """Clean up flag after each test."""
        from agent.job_queue import _RESTART_FLAG

        _RESTART_FLAG.unlink(missing_ok=True)

    def test_check_restart_flag_returns_false_when_no_flag(self):
        from agent.job_queue import _check_restart_flag

        assert _check_restart_flag() is False

    def test_check_restart_flag_returns_true_when_flag_exists_and_no_jobs(self):
        from agent.job_queue import _RESTART_FLAG, _check_restart_flag

        _RESTART_FLAG.write_text("2026-02-02T10:00:00Z 3 commit(s)")

        with patch("agent.job_queue.RedisJob") as mock_redis:
            mock_redis.query.filter.return_value = []
            assert _check_restart_flag() is True

    def test_check_restart_flag_defers_when_jobs_running(self):
        from agent.job_queue import (
            _RESTART_FLAG,
            _active_workers,
            _check_restart_flag,
        )

        _RESTART_FLAG.write_text("2026-02-02T10:00:00Z 1 commit(s)")

        # Simulate an active worker
        mock_task = MagicMock()
        mock_task.done.return_value = False
        _active_workers["testproject"] = mock_task

        try:
            with patch("agent.job_queue.RedisJob") as mock_redis:
                # Return running jobs for the project
                mock_redis.query.filter.return_value = [MagicMock()]
                assert _check_restart_flag() is False
        finally:
            _active_workers.pop("testproject", None)

    def test_clear_restart_flag_removes_file(self):
        from agent.job_queue import _RESTART_FLAG, clear_restart_flag

        _RESTART_FLAG.write_text("test content")
        assert clear_restart_flag() is True
        assert not _RESTART_FLAG.exists()

    def test_clear_restart_flag_returns_false_when_no_file(self):
        from agent.job_queue import clear_restart_flag

        assert clear_restart_flag() is False

    def test_trigger_restart_removes_flag_and_sends_sigterm(self):
        from agent.job_queue import _RESTART_FLAG, _trigger_restart

        _RESTART_FLAG.write_text("test")

        with patch("agent.job_queue.os.kill") as mock_kill:
            _trigger_restart()

        assert not _RESTART_FLAG.exists()
        mock_kill.assert_called_once_with(os.getpid(), 15)  # SIGTERM = 15


# =============================================================================
# Worker Loop Restart Check Tests
# =============================================================================


class TestWorkerRestartCheck:
    """Test that the worker loop checks the restart flag between jobs."""

    def setup_method(self):
        from agent.job_queue import _RESTART_FLAG

        _RESTART_FLAG.parent.mkdir(parents=True, exist_ok=True)
        _RESTART_FLAG.unlink(missing_ok=True)

    def teardown_method(self):
        from agent.job_queue import _RESTART_FLAG

        _RESTART_FLAG.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_worker_checks_flag_when_queue_empty(self):
        """Worker should check restart flag when queue becomes empty."""
        from agent.job_queue import _RESTART_FLAG

        _RESTART_FLAG.write_text("2026-02-02T10:00:00Z 1 commit(s)")

        with (
            patch("agent.job_queue._pop_job", return_value=None),
            patch(
                "agent.job_queue._check_restart_flag", return_value=True
            ) as mock_check,
            patch("agent.job_queue._trigger_restart") as mock_restart,
        ):
            from agent.job_queue import _worker_loop

            await _worker_loop("testproject")

        mock_check.assert_called_once()
        mock_restart.assert_called_once()

    @pytest.mark.asyncio
    async def test_worker_checks_flag_after_job_completion(self):
        """Worker should check restart flag after completing a job."""
        mock_job = MagicMock()
        mock_job.job_id = "test-123"
        mock_job.project_key = "testproject"

        call_count = 0

        async def pop_side_effect(project_key):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_job
            return None

        with (
            patch("agent.job_queue._pop_job", side_effect=pop_side_effect),
            patch("agent.job_queue._execute_job", new_callable=AsyncMock),
            patch("agent.job_queue._complete_job", new_callable=AsyncMock),
            patch(
                "agent.job_queue._check_restart_flag", return_value=True
            ) as mock_check,
            patch("agent.job_queue._trigger_restart") as mock_restart,
        ):
            from agent.job_queue import _worker_loop

            await _worker_loop("testproject")

        # Should have been called at least once (after job completion)
        assert mock_check.call_count >= 1
        mock_restart.assert_called()


# =============================================================================
# Bridge Command Intercept Tests
# =============================================================================


class TestBridgeUpdateCommand:
    """Test the /update command handling in the bridge."""

    def test_handle_update_command_exists(self):
        """The _handle_update_command function should be importable."""
        # We can't easily import the bridge module (requires Telegram creds etc.)
        # But we can verify the function exists by reading the source
        bridge_path = PROJECT_DIR / "bridge" / "telegram_bridge.py"
        source = bridge_path.read_text()
        assert "async def _handle_update_command" in source
        assert "scripts/remote-update.sh" in source

    def test_update_intercept_before_message_processing(self):
        """The /update check should come before message storage."""
        bridge_path = PROJECT_DIR / "bridge" / "telegram_bridge.py"
        source = bridge_path.read_text()

        # Find positions
        update_pos = source.find("/update")
        store_pos = source.find("store_message(")

        assert (
            update_pos < store_pos
        ), "/update intercept should come before store_message"

    def test_restart_flag_cleanup_in_startup(self):
        """Bridge startup should clear stale restart flags."""
        bridge_path = PROJECT_DIR / "bridge" / "telegram_bridge.py"
        source = bridge_path.read_text()
        assert "clear_restart_flag" in source


# =============================================================================
# Service Manager Tests
# =============================================================================


class TestServiceManager:
    """Test valor-service.sh has update cron support."""

    SERVICE_SCRIPT = str(PROJECT_DIR / "scripts" / "valor-service.sh")

    def test_update_plist_defined(self):
        source = Path(self.SERVICE_SCRIPT).read_text()
        assert "com.valor.update" in source

    def test_install_creates_both_plists(self):
        source = Path(self.SERVICE_SCRIPT).read_text()
        # install_service should reference both bridge and update plists
        assert "UPDATE_PLIST_PATH" in source
        assert "StartCalendarInterval" in source

    def test_uninstall_removes_both_plists(self):
        source = Path(self.SERVICE_SCRIPT).read_text()
        # uninstall should handle update plist
        assert source.count("UPDATE_PLIST_PATH") >= 2  # defined + used in uninstall

    def test_cron_schedule_06_and_18(self):
        source = Path(self.SERVICE_SCRIPT).read_text()
        assert "<integer>6</integer>" in source
        assert "<integer>18</integer>" in source
