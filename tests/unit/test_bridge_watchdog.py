"""Unit tests for bridge watchdog zombie process detection and cleanup."""

import json
import signal
from unittest.mock import MagicMock, patch

import pytest

from monitoring.bridge_watchdog import (
    SOFT_INSTANCE_LIMIT,
    ZOMBIE_THRESHOLD_SECONDS,
    HealthStatus,
    _enumerate_claude_processes,
    _parse_elapsed_time,
    check_bridge_health,
    classify_zombies,
    kill_zombie_processes,
)
from monitoring.crash_tracker import CrashEvent

# --- _parse_elapsed_time tests ---


class TestParseElapsedTime:
    """Tests for ps etime format parsing."""

    def test_mm_ss(self):
        assert _parse_elapsed_time("05:23") == 5 * 60 + 23

    def test_hh_mm_ss(self):
        assert _parse_elapsed_time("01:05:23") == 1 * 3600 + 5 * 60 + 23

    def test_d_hh_mm_ss(self):
        assert _parse_elapsed_time("2-01:05:23") == 2 * 86400 + 1 * 3600 + 5 * 60 + 23

    def test_dd_hh_mm_ss(self):
        assert _parse_elapsed_time("12-01:05:23") == 12 * 86400 + 1 * 3600 + 5 * 60 + 23

    def test_zero(self):
        assert _parse_elapsed_time("00:00") == 0

    def test_just_seconds(self):
        assert _parse_elapsed_time("00:42") == 42

    def test_leading_whitespace(self):
        assert _parse_elapsed_time("  05:23") == 5 * 60 + 23

    def test_trailing_whitespace(self):
        assert _parse_elapsed_time("05:23  ") == 5 * 60 + 23

    def test_one_day_zero_time(self):
        assert _parse_elapsed_time("1-00:00:00") == 86400

    def test_invalid_format_raises(self):
        with pytest.raises(ValueError):
            _parse_elapsed_time("invalid")

    def test_too_many_colons_raises(self):
        with pytest.raises(ValueError):
            _parse_elapsed_time("1:2:3:4")


# --- _enumerate_claude_processes tests ---


class TestEnumerateClaudeProcesses:
    """Tests for process enumeration via ps."""

    SAMPLE_PS_OUTPUT = """\
  PID   ELAPSED  RSS COMMAND
12345    05:23 102400 claude --dangerously-skip-permissions
12346 1-02:30:00 524288 claude --dangerously-skip-permissions
12347    15:00  51200 /usr/local/bin/pyright --watch
99999    01:00  10240 /usr/bin/python3 some_other_process
"""

    @patch("monitoring.bridge_watchdog.subprocess.run")
    def test_enumerates_claude_and_pyright(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=self.SAMPLE_PS_OUTPUT,
            stderr="",
        )
        procs = _enumerate_claude_processes()
        # Should find claude and pyright, not some_other_process
        assert len(procs) == 3
        pids = [p["pid"] for p in procs]
        assert 12345 in pids
        assert 12346 in pids
        assert 12347 in pids
        assert 99999 not in pids

    @patch("monitoring.bridge_watchdog.subprocess.run")
    def test_parses_memory_correctly(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=self.SAMPLE_PS_OUTPUT,
            stderr="",
        )
        procs = _enumerate_claude_processes()
        # 102400 KB = 100.0 MB
        claude_proc = next(p for p in procs if p["pid"] == 12345)
        assert claude_proc["rss_mb"] == 100.0

    @patch("monitoring.bridge_watchdog.subprocess.run")
    def test_parses_etime_correctly(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=self.SAMPLE_PS_OUTPUT,
            stderr="",
        )
        procs = _enumerate_claude_processes()
        old_proc = next(p for p in procs if p["pid"] == 12346)
        # 1 day + 2h + 30min = 95400s
        assert old_proc["etime_seconds"] == 86400 + 2 * 3600 + 30 * 60

    @patch("monitoring.bridge_watchdog.subprocess.run")
    def test_ps_failure_returns_empty(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="error",
        )
        procs = _enumerate_claude_processes()
        assert procs == []

    @patch("monitoring.bridge_watchdog.subprocess.run")
    def test_ps_exception_returns_empty(self, mock_run):
        mock_run.side_effect = Exception("timeout")
        procs = _enumerate_claude_processes()
        assert procs == []

    @patch("monitoring.bridge_watchdog.subprocess.run")
    def test_skips_malformed_lines(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "  PID   ELAPSED  RSS COMMAND\nbadline\n"
                "12345    05:23 102400 claude --dangerously-skip-permissions\n"
            ),
            stderr="",
        )
        procs = _enumerate_claude_processes()
        # badline is skipped, but it doesn't match pattern anyway
        # The claude line should parse
        assert len(procs) == 1

    @patch("monitoring.bridge_watchdog.subprocess.run")
    def test_skips_bridge_watchdog_itself(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "  PID   ELAPSED  RSS COMMAND\n"
                "12345    05:23 102400 "
                "python monitoring/bridge_watchdog.py --check-only\n"
            ),
            stderr="",
        )
        procs = _enumerate_claude_processes()
        assert len(procs) == 0

    @patch("monitoring.bridge_watchdog.subprocess.run")
    def test_skips_grep_processes(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="  PID   ELAPSED  RSS COMMAND\n12345    05:23 102400 grep -E claude\n",
            stderr="",
        )
        procs = _enumerate_claude_processes()
        assert len(procs) == 0

    @patch("monitoring.bridge_watchdog.subprocess.run")
    def test_no_matching_processes(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "  PID   ELAPSED  RSS COMMAND\n12345    05:23 102400 /usr/bin/python3 myapp.py\n"
            ),
            stderr="",
        )
        procs = _enumerate_claude_processes()
        assert procs == []


# --- classify_zombies tests ---


class TestClassifyZombies:
    """Tests for zombie vs active classification."""

    def test_separates_zombies_from_active(self):
        processes = [
            {"pid": 1, "etime_seconds": 100, "rss_mb": 50.0, "command": "claude"},
            {"pid": 2, "etime_seconds": 8000, "rss_mb": 500.0, "command": "claude"},
            {"pid": 3, "etime_seconds": 7199, "rss_mb": 100.0, "command": "claude"},
        ]
        zombies, active = classify_zombies(processes)
        assert len(zombies) == 1
        assert zombies[0]["pid"] == 2
        assert len(active) == 2

    def test_exact_threshold_is_zombie(self):
        processes = [
            {
                "pid": 1,
                "etime_seconds": ZOMBIE_THRESHOLD_SECONDS,
                "rss_mb": 50.0,
                "command": "claude",
            },
        ]
        zombies, active = classify_zombies(processes)
        assert len(zombies) == 1
        assert len(active) == 0

    def test_just_below_threshold_is_active(self):
        processes = [
            {
                "pid": 1,
                "etime_seconds": ZOMBIE_THRESHOLD_SECONDS - 1,
                "rss_mb": 50.0,
                "command": "claude",
            },
        ]
        zombies, active = classify_zombies(processes)
        assert len(zombies) == 0
        assert len(active) == 1

    def test_empty_list(self):
        zombies, active = classify_zombies([])
        assert zombies == []
        assert active == []

    def test_custom_threshold(self):
        processes = [
            {"pid": 1, "etime_seconds": 600, "rss_mb": 50.0, "command": "claude"},
        ]
        zombies, active = classify_zombies(processes, threshold_seconds=300)
        assert len(zombies) == 1

    def test_all_zombies(self):
        processes = [
            {"pid": 1, "etime_seconds": 10000, "rss_mb": 50.0, "command": "claude"},
            {"pid": 2, "etime_seconds": 20000, "rss_mb": 100.0, "command": "pyright"},
        ]
        zombies, active = classify_zombies(processes)
        assert len(zombies) == 2
        assert len(active) == 0

    def test_all_active(self):
        processes = [
            {"pid": 1, "etime_seconds": 100, "rss_mb": 50.0, "command": "claude"},
            {"pid": 2, "etime_seconds": 200, "rss_mb": 100.0, "command": "pyright"},
        ]
        zombies, active = classify_zombies(processes)
        assert len(zombies) == 0
        assert len(active) == 2


# --- kill_zombie_processes tests ---


class TestKillZombieProcesses:
    """Tests for zombie process killing with SIGTERM/SIGKILL escalation."""

    @patch("monitoring.bridge_watchdog.time.sleep")
    @patch("monitoring.bridge_watchdog.os.kill")
    def test_sigterm_kills_process(self, mock_kill, mock_sleep):
        """Process exits after SIGTERM."""
        zombies = [{"pid": 12345, "etime_seconds": 8000, "rss_mb": 500.0, "command": "claude"}]

        # First call: SIGTERM, second call (os.kill(pid, 0)): ProcessLookupError
        mock_kill.side_effect = [None, ProcessLookupError()]

        killed = kill_zombie_processes(zombies)
        assert killed == 1
        mock_kill.assert_any_call(12345, signal.SIGTERM)

    @patch("monitoring.bridge_watchdog.time.sleep")
    @patch("monitoring.bridge_watchdog.os.kill")
    def test_escalates_to_sigkill(self, mock_kill, mock_sleep):
        """Process survives SIGTERM, gets SIGKILL."""
        zombies = [{"pid": 12345, "etime_seconds": 8000, "rss_mb": 500.0, "command": "claude"}]

        # SIGTERM succeeds, all 6 os.kill(pid, 0) succeed (process alive), then SIGKILL
        mock_kill.side_effect = [None, None, None, None, None, None, None, None]

        killed = kill_zombie_processes(zombies)
        assert killed == 1
        mock_kill.assert_any_call(12345, signal.SIGTERM)
        mock_kill.assert_any_call(12345, signal.SIGKILL)

    @patch("monitoring.bridge_watchdog.time.sleep")
    @patch("monitoring.bridge_watchdog.os.kill")
    def test_process_already_gone(self, mock_kill, mock_sleep):
        """Process died between detection and kill attempt."""
        zombies = [{"pid": 12345, "etime_seconds": 8000, "rss_mb": 500.0, "command": "claude"}]

        mock_kill.side_effect = ProcessLookupError()

        killed = kill_zombie_processes(zombies)
        assert killed == 1  # Still counts as "handled"

    @patch("monitoring.bridge_watchdog.time.sleep")
    @patch("monitoring.bridge_watchdog.os.kill")
    def test_permission_denied(self, mock_kill, mock_sleep):
        """Cannot kill process owned by another user."""
        zombies = [{"pid": 12345, "etime_seconds": 8000, "rss_mb": 500.0, "command": "claude"}]

        mock_kill.side_effect = PermissionError()

        killed = kill_zombie_processes(zombies)
        assert killed == 0

    @patch("monitoring.bridge_watchdog.time.sleep")
    @patch("monitoring.bridge_watchdog.os.kill")
    def test_multiple_zombies(self, mock_kill, mock_sleep):
        """Kills multiple zombie processes."""
        zombies = [
            {"pid": 100, "etime_seconds": 8000, "rss_mb": 500.0, "command": "claude"},
            {"pid": 200, "etime_seconds": 9000, "rss_mb": 300.0, "command": "pyright"},
        ]

        # Both exit after SIGTERM
        mock_kill.side_effect = [None, ProcessLookupError(), None, ProcessLookupError()]

        killed = kill_zombie_processes(zombies)
        assert killed == 2

    def test_empty_zombie_list(self):
        """No zombies to kill."""
        killed = kill_zombie_processes([])
        assert killed == 0


# --- HealthStatus tests ---


class TestHealthStatus:
    """Tests for extended HealthStatus dataclass."""

    def test_default_zombie_fields(self):
        status = HealthStatus(
            healthy=True,
            process_running=True,
            logs_fresh=True,
            no_crash_pattern=True,
            issues=[],
            recovery_level=0,
        )
        assert status.zombie_count == 0
        assert status.zombie_pids == []
        assert status.zombie_memory_mb == 0.0
        assert status.active_claude_count == 0
        assert status.human_alert_needed is False
        assert status.restart_circuit_open is False

    def test_alert_signal_fields_settable(self):
        """issue #2396: human_alert_needed / restart_circuit_open are independent
        of recovery_level -- a level-2 action can coexist with an alert signal."""
        status = HealthStatus(
            healthy=False,
            process_running=True,
            logs_fresh=True,
            no_crash_pattern=True,
            issues=["5 crashes in last 30 minutes"],
            recovery_level=2,
            human_alert_needed=True,
            restart_circuit_open=True,
        )
        assert status.recovery_level == 2
        assert status.human_alert_needed is True
        assert status.restart_circuit_open is True

    def test_zombie_fields_populated(self):
        status = HealthStatus(
            healthy=False,
            process_running=True,
            logs_fresh=True,
            no_crash_pattern=True,
            issues=["2 zombies"],
            recovery_level=0,
            zombie_count=2,
            zombie_pids=[123, 456],
            zombie_memory_mb=1750.5,
            active_claude_count=3,
        )
        assert status.zombie_count == 2
        assert status.zombie_pids == [123, 456]
        assert status.zombie_memory_mb == 1750.5
        assert status.active_claude_count == 3


# --- check_bridge_health integration ---


class TestCheckBridgeHealthZombieIntegration:
    """Tests that check_bridge_health populates zombie fields."""

    @patch("monitoring.bridge_watchdog._enumerate_claude_processes")
    @patch("monitoring.bridge_watchdog.get_recent_crashes")
    @patch("monitoring.bridge_watchdog.detect_crash_pattern")
    @patch("monitoring.bridge_watchdog.are_logs_fresh")
    @patch("monitoring.bridge_watchdog.is_bridge_running")
    def test_populates_zombie_data(
        self,
        mock_running,
        mock_logs,
        mock_crash,
        mock_crashes,
        mock_enumerate,
    ):
        from monitoring.bridge_watchdog import check_bridge_health

        mock_running.return_value = (True, 1234)
        mock_logs.return_value = True
        mock_crash.return_value = (False, None)
        mock_crashes.return_value = []
        mock_enumerate.return_value = [
            {"pid": 100, "etime_seconds": 100, "rss_mb": 50.0, "command": "claude"},
            {"pid": 200, "etime_seconds": 10000, "rss_mb": 600.0, "command": "claude"},
        ]

        status = check_bridge_health()
        assert status.zombie_count == 1
        assert status.zombie_pids == [200]
        assert status.zombie_memory_mb == 600.0
        assert status.active_claude_count == 1

    @patch("monitoring.bridge_watchdog._enumerate_claude_processes")
    @patch("monitoring.bridge_watchdog.get_recent_crashes")
    @patch("monitoring.bridge_watchdog.detect_crash_pattern")
    @patch("monitoring.bridge_watchdog.are_logs_fresh")
    @patch("monitoring.bridge_watchdog.is_bridge_running")
    def test_no_zombies_still_populates(
        self,
        mock_running,
        mock_logs,
        mock_crash,
        mock_crashes,
        mock_enumerate,
    ):
        from monitoring.bridge_watchdog import check_bridge_health

        mock_running.return_value = (True, 1234)
        mock_logs.return_value = True
        mock_crash.return_value = (False, None)
        mock_crashes.return_value = []
        mock_enumerate.return_value = []

        status = check_bridge_health()
        assert status.zombie_count == 0
        assert status.zombie_pids == []
        assert status.active_claude_count == 0
        assert status.healthy is True


# --- --check-only output format ---


class TestCheckOnlyOutput:
    """Tests for --check-only output format including zombie data."""

    @patch("monitoring.bridge_watchdog.check_bridge_health")
    def test_check_only_includes_zombie_section(self, mock_health, capsys, tmp_path):
        from monitoring import bridge_watchdog as bw

        mock_health.return_value = HealthStatus(
            healthy=True,
            process_running=True,
            logs_fresh=True,
            no_crash_pattern=True,
            issues=[],
            recovery_level=0,
            zombie_count=0,
            zombie_pids=[],
            zombie_memory_mb=0.0,
            active_claude_count=2,
        )

        with (
            patch.object(bw, "COOLDOWN_FILE", tmp_path / "no-cooldown"),
            patch("sys.argv", ["bridge_watchdog.py", "--check-only"]),
        ):
            result = bw.main()

        output = capsys.readouterr().out
        assert "Zombie processes: 0" in output
        assert "Active claude instances: 2" in output
        assert "Human alert needed: False" in output
        assert "Alert on cooldown: False" in output
        assert "Restart circuit open: False" in output
        assert result == 0

    @patch("monitoring.bridge_watchdog.check_bridge_health")
    def test_check_only_with_zombies(self, mock_health, capsys):
        from monitoring.bridge_watchdog import main

        mock_health.return_value = HealthStatus(
            healthy=False,
            process_running=True,
            logs_fresh=True,
            no_crash_pattern=True,
            issues=["2 zombie process(es) detected"],
            recovery_level=0,
            zombie_count=2,
            zombie_pids=[123, 456],
            zombie_memory_mb=1750.5,
            active_claude_count=3,
        )

        with patch("sys.argv", ["bridge_watchdog.py", "--check-only"]):
            result = main()

        output = capsys.readouterr().out
        assert "Zombie processes: 2" in output
        assert "Zombie PIDs: [123, 456]" in output
        assert "Zombie memory: 1750.5MB" in output
        assert "Active claude instances: 3" in output
        assert result == 1  # Not healthy due to zombies

    @patch("monitoring.bridge_watchdog.check_bridge_health")
    def test_check_only_instance_limit_warning(self, mock_health, capsys):
        from monitoring.bridge_watchdog import main

        mock_health.return_value = HealthStatus(
            healthy=True,
            process_running=True,
            logs_fresh=True,
            no_crash_pattern=True,
            issues=[],
            recovery_level=0,
            zombie_count=0,
            zombie_pids=[],
            zombie_memory_mb=0.0,
            active_claude_count=SOFT_INSTANCE_LIMIT + 1,
        )

        with patch("sys.argv", ["bridge_watchdog.py", "--check-only"]):
            main()

        output = capsys.readouterr().out
        assert "WARNING" in output
        assert "soft limit" in output


# --- Crash detection on bridge death ---


class TestCrashDetectionOnBridgeDeath:
    """Tests that check_bridge_health calls log_crash when bridge is dead."""

    @patch("monitoring.bridge_watchdog._enumerate_claude_processes")
    @patch("monitoring.bridge_watchdog.get_recent_crashes")
    @patch("monitoring.bridge_watchdog.detect_crash_pattern")
    @patch("monitoring.bridge_watchdog.are_logs_fresh")
    @patch("monitoring.bridge_watchdog.is_bridge_running")
    @patch("monitoring.bridge_watchdog.log_crash")
    def test_calls_log_crash_when_bridge_not_running(
        self,
        mock_log_crash,
        mock_running,
        mock_logs,
        mock_crash,
        mock_crashes,
        mock_enumerate,
    ):
        """When bridge is not running, check_bridge_health calls log_crash."""
        mock_running.return_value = (False, None)
        mock_logs.return_value = False
        mock_crash.return_value = (False, None)
        mock_crashes.return_value = []
        mock_enumerate.return_value = []

        status = check_bridge_health()

        assert not status.process_running
        mock_log_crash.assert_called_once_with("bridge_dead_on_watchdog_check")

    @patch("monitoring.bridge_watchdog._enumerate_claude_processes")
    @patch("monitoring.bridge_watchdog.get_recent_crashes")
    @patch("monitoring.bridge_watchdog.detect_crash_pattern")
    @patch("monitoring.bridge_watchdog.are_logs_fresh")
    @patch("monitoring.bridge_watchdog.is_bridge_running")
    @patch("monitoring.bridge_watchdog.log_crash")
    def test_does_not_call_log_crash_when_bridge_running(
        self,
        mock_log_crash,
        mock_running,
        mock_logs,
        mock_crash,
        mock_crashes,
        mock_enumerate,
    ):
        """When bridge is running, log_crash should NOT be called."""
        mock_running.return_value = (True, 1234)
        mock_logs.return_value = True
        mock_crash.return_value = (False, None)
        mock_crashes.return_value = []
        mock_enumerate.return_value = []

        status = check_bridge_health()

        assert status.process_running
        mock_log_crash.assert_not_called()

    @patch("monitoring.bridge_watchdog._enumerate_claude_processes")
    @patch("monitoring.bridge_watchdog.get_recent_crashes")
    @patch("monitoring.bridge_watchdog.detect_crash_pattern")
    @patch("monitoring.bridge_watchdog.are_logs_fresh")
    @patch("monitoring.bridge_watchdog.is_bridge_running")
    @patch("monitoring.bridge_watchdog.log_crash")
    def test_log_crash_failure_does_not_break_health_check(
        self,
        mock_log_crash,
        mock_running,
        mock_logs,
        mock_crash,
        mock_crashes,
        mock_enumerate,
    ):
        """If log_crash raises, check_bridge_health should still return."""
        mock_running.return_value = (False, None)
        mock_logs.return_value = False
        mock_crash.return_value = (False, None)
        mock_crashes.return_value = []
        mock_enumerate.return_value = []
        mock_log_crash.side_effect = Exception("Redis connection failed")

        # Should not raise
        status = check_bridge_health()
        assert not status.process_running
        assert status.recovery_level >= 1


# --- Hibernation suppression tests ---


class TestHibernationSuppression:
    """Tests that watchdog suppresses restart loop when bridge is hibernating."""

    @patch("monitoring.bridge_watchdog.check_bridge_health")
    @patch("monitoring.bridge_watchdog.execute_recovery")
    def test_hibernating_suppresses_recovery(self, mock_recovery, mock_health, tmp_path):
        """When hibernating, run_health_check returns True without executing recovery."""
        from monitoring.bridge_watchdog import run_health_check

        mock_health.return_value = HealthStatus(
            healthy=False,
            process_running=False,
            logs_fresh=False,
            no_crash_pattern=True,
            issues=["Bridge not running"],
            recovery_level=1,
        )

        with patch("bridge.hibernation.AUTH_REQUIRED_FLAG", tmp_path / "bridge-auth-required") as _:
            flag = tmp_path / "bridge-auth-required"
            flag.write_text("auth-required")
            result = run_health_check()

        assert result is True
        mock_recovery.assert_not_called()

    @patch("monitoring.bridge_watchdog.check_bridge_health")
    @patch("monitoring.bridge_watchdog.execute_recovery")
    def test_not_hibernating_proceeds_to_recovery(self, mock_recovery, mock_health, tmp_path):
        """Without hibernation flag, normal recovery proceeds."""
        from monitoring.bridge_watchdog import run_health_check

        mock_health.return_value = HealthStatus(
            healthy=False,
            process_running=False,
            logs_fresh=False,
            no_crash_pattern=True,
            issues=["Bridge not running"],
            recovery_level=1,
        )
        mock_recovery.return_value = True

        with patch("bridge.hibernation.AUTH_REQUIRED_FLAG", tmp_path / "bridge-auth-required"):
            # Flag file does NOT exist
            run_health_check()

        mock_recovery.assert_called_once()

    def test_hibernating_logs_message(self, tmp_path, caplog):
        """Watchdog logs a clear message when suppressing recovery due to hibernation."""
        import logging

        from monitoring.bridge_watchdog import run_health_check

        with (
            patch("bridge.hibernation.AUTH_REQUIRED_FLAG", tmp_path / "bridge-auth-required"),
            caplog.at_level(logging.INFO, logger="monitoring.bridge_watchdog"),
        ):
            flag = tmp_path / "bridge-auth-required"
            flag.write_text("auth-required")
            run_health_check()

        assert any("hibernating" in r.message.lower() for r in caplog.records)

    @patch("monitoring.bridge_watchdog.check_bridge_health")
    def test_check_only_shows_hibernating_state(self, mock_health, tmp_path, capsys):
        """--check-only output includes hibernation state."""
        from monitoring.bridge_watchdog import main

        mock_health.return_value = HealthStatus(
            healthy=True,
            process_running=True,
            logs_fresh=True,
            no_crash_pattern=True,
            issues=[],
            recovery_level=0,
        )

        with (
            patch("bridge.hibernation.AUTH_REQUIRED_FLAG", tmp_path / "bridge-auth-required"),
            patch("sys.argv", ["bridge_watchdog.py", "--check-only"]),
        ):
            flag = tmp_path / "bridge-auth-required"
            flag.write_text("auth-required")
            main()

        output = capsys.readouterr().out
        assert "Hibernating: True" in output

    @patch("monitoring.bridge_watchdog.check_bridge_health")
    def test_check_only_shows_not_hibernating(self, mock_health, tmp_path, capsys):
        """--check-only shows Hibernating: False when flag absent."""
        from monitoring.bridge_watchdog import main

        mock_health.return_value = HealthStatus(
            healthy=True,
            process_running=True,
            logs_fresh=True,
            no_crash_pattern=True,
            issues=[],
            recovery_level=0,
        )

        with (
            patch("bridge.hibernation.AUTH_REQUIRED_FLAG", tmp_path / "bridge-auth-required"),
            patch("sys.argv", ["bridge_watchdog.py", "--check-only"]),
        ):
            # Flag does NOT exist
            main()

        output = capsys.readouterr().out
        assert "Hibernating: False" in output


# --- Update release-verify signals (issue #1898) ---


class TestUpdateReleaseSignals:
    """Planned-restart suppression + sentinel/undrained-report reads (#1898)."""

    HEALTHY = dict(
        healthy=True,
        process_running=True,
        logs_fresh=True,
        no_crash_pattern=True,
        issues=[],
        recovery_level=0,
    )

    def _patches(self, bw, tmp_path):
        """Common patch set: hibernation off, tmp paths for every signal file."""
        return (
            patch("bridge.hibernation.AUTH_REQUIRED_FLAG", tmp_path / "no-hibernation"),
            patch.object(bw, "RECOVERY_LOCK", tmp_path / "no-recovery-lock"),
            patch.object(bw, "UPDATE_RESTART_MARKER", tmp_path / "update-restart-in-progress"),
            patch.object(bw, "UPDATE_RELEASE_FAILED_SENTINEL", tmp_path / "update-release-failed"),
            patch.object(bw, "UPDATE_PENDING_REPORT", tmp_path / "update-pending-report"),
        )

    @patch("monitoring.bridge_watchdog.execute_recovery")
    @patch("monitoring.bridge_watchdog.check_bridge_health")
    @patch("monitoring.bridge_watchdog.log_crash")
    def test_fresh_marker_suppresses_health_check_and_recovery(
        self, mock_crash, mock_health, mock_recovery, tmp_path
    ):
        """Decision 19: a fresh marker early-returns True BEFORE
        check_bridge_health — no crash logged, no recovery escalation."""
        from monitoring import bridge_watchdog as bw

        p1, p2, p3, p4, p5 = self._patches(bw, tmp_path)
        with p1, p2, p3, p4, p5:
            (tmp_path / "update-restart-in-progress").write_text("1234567890\n")
            assert bw.run_health_check() is True

        mock_health.assert_not_called()
        mock_recovery.assert_not_called()
        mock_crash.assert_not_called()
        # The watchdog never consumes a fresh marker (the fresh bridge does).
        assert (tmp_path / "update-restart-in-progress").exists()

    @patch("monitoring.bridge_watchdog.execute_recovery")
    @patch("monitoring.bridge_watchdog.check_bridge_health")
    def test_aged_out_marker_resumes_normal_health_checking(
        self, mock_health, mock_recovery, tmp_path
    ):
        import os as _os
        import time as _time

        from monitoring import bridge_watchdog as bw

        mock_health.return_value = HealthStatus(**self.HEALTHY)
        marker = tmp_path / "update-restart-in-progress"
        p1, p2, p3, p4, p5 = self._patches(bw, tmp_path)
        with p1, p2, p3, p4, p5:
            marker.write_text("old\n")
            aged = _time.time() - (bw.UPDATE_RESTART_MARKER_TTL_SECONDS + 30)
            _os.utime(marker, (aged, aged))
            assert bw.run_health_check() is True

        mock_health.assert_called_once()
        mock_recovery.assert_not_called()
        assert not marker.exists()  # aged-out marker removed

    def test_marker_ttl_never_shorter_than_report_ttl(self):
        """Decision 26: the suppression window must never expire before the
        boot window it protects; both anchor to STARTUP_GRACE_SECONDS + 60."""
        from monitoring import bridge_watchdog as bw
        from scripts.update import verify_release as vr

        assert bw.UPDATE_RESTART_MARKER_TTL_SECONDS >= bw.UPDATE_REPORT_TTL_SECONDS
        assert bw.UPDATE_REPORT_TTL_SECONDS == bw.STARTUP_GRACE_SECONDS + 60
        # verify_release's marker-freshness window shares the same constant.
        assert vr.UPDATE_RESTART_MARKER_TTL_SECONDS == bw.UPDATE_RESTART_MARKER_TTL_SECONDS

    def test_sentinel_surfaced(self, tmp_path):
        from monitoring import bridge_watchdog as bw

        sentinel = tmp_path / "update-release-failed"
        sentinel.write_text('{"process": "bridge", "boot_sha": "659756a4"}\n')
        with (
            patch.object(bw, "UPDATE_RELEASE_FAILED_SENTINEL", sentinel),
            patch.object(bw, "UPDATE_PENDING_REPORT", tmp_path / "no-report"),
        ):
            issues = bw.check_update_release_signals()
        assert len(issues) == 1
        assert "update-release-failed sentinel present" in issues[0]
        assert "659756a4" in issues[0]

    def test_undrained_report_past_ttl_surfaced(self, tmp_path):
        import json as _json
        import time as _time

        from monitoring import bridge_watchdog as bw

        report = tmp_path / "update-pending-report"
        report.write_text(
            _json.dumps(
                {"chat_id": "1", "staged_ts": _time.time() - bw.UPDATE_REPORT_TTL_SECONDS - 30}
            )
        )
        with (
            patch.object(bw, "UPDATE_PENDING_REPORT", report),
            patch.object(bw, "UPDATE_RELEASE_FAILED_SENTINEL", tmp_path / "no-sentinel"),
        ):
            issues = bw.check_update_release_signals()
        assert len(issues) == 1
        assert "undrained" in issues[0]
        assert "never have come up" in issues[0]

    def test_fresh_report_not_surfaced(self, tmp_path):
        import json as _json
        import time as _time

        from monitoring import bridge_watchdog as bw

        report = tmp_path / "update-pending-report"
        report.write_text(_json.dumps({"chat_id": "1", "staged_ts": _time.time()}))
        with (
            patch.object(bw, "UPDATE_PENDING_REPORT", report),
            patch.object(bw, "UPDATE_RELEASE_FAILED_SENTINEL", tmp_path / "no-sentinel"),
        ):
            assert bw.check_update_release_signals() == []

    @patch("monitoring.bridge_watchdog.execute_recovery")
    @patch("monitoring.bridge_watchdog.check_bridge_health")
    def test_signals_logged_critical_in_health_check(
        self, mock_health, mock_recovery, tmp_path, caplog
    ):
        import logging as _logging

        from monitoring import bridge_watchdog as bw

        mock_health.return_value = HealthStatus(**self.HEALTHY)
        p1, p2, p3, p4, p5 = self._patches(bw, tmp_path)
        with (
            p1,
            p2,
            p3,
            p4,
            p5,
            caplog.at_level(_logging.CRITICAL, logger="monitoring.bridge_watchdog"),
        ):
            (tmp_path / "update-release-failed").write_text('{"process": "bridge"}\n')
            bw.run_health_check()

        assert any("[update-release]" in r.message for r in caplog.records)


# --- issue #2396: crash-count signal split from action level ---


def _wedge_crash(ts: float) -> CrashEvent:
    return CrashEvent(
        timestamp=ts,
        event_type="crash",
        commit_sha="abc123",
        commit_age_seconds=100.0,
        reason="bridge_update_loop_wedged",
    )


def _other_crash(ts: float, reason: str = "some_other_crash") -> CrashEvent:
    return CrashEvent(
        timestamp=ts,
        event_type="crash",
        commit_sha="abc123",
        commit_age_seconds=100.0,
        reason=reason,
    )


class TestCrashStormActionAlertSplit:
    """check_bridge_health(): crash-count storm no longer overrides
    recovery_level to a no-op 5 -- it sets human_alert_needed and
    (reason-aware) restart_circuit_open instead."""

    @patch("monitoring.bridge_watchdog._enumerate_claude_processes")
    @patch("monitoring.bridge_watchdog.get_recent_crashes")
    @patch("monitoring.bridge_watchdog.detect_crash_pattern")
    @patch("monitoring.bridge_watchdog.are_logs_fresh")
    @patch("monitoring.bridge_watchdog.is_bridge_running")
    def test_wedge_dominated_crash_storm_livelock_regression(
        self, mock_running, mock_logs, mock_crash, mock_crashes, mock_enumerate
    ):
        """SC1: a large all-wedge storm (12 crashes, well above the threshold)
        never opens the circuit and never suppresses the action level -- there
        is no attempt ceiling on the wedge restart."""
        import time as _time

        mock_running.return_value = (True, 1234)
        mock_logs.return_value = True
        mock_crash.return_value = (False, None)
        now = _time.time()
        mock_crashes.return_value = [_wedge_crash(now - i) for i in range(12)]
        mock_enumerate.return_value = []

        status = check_bridge_health()

        assert status.human_alert_needed is True
        assert status.restart_circuit_open is False
        # recovery_level never escalated to a former "5" -- it stays whatever
        # the other checks computed (0 here, since nothing else fired).
        assert status.recovery_level in (0, 1, 2, 3, 4)

    @patch("monitoring.bridge_watchdog._enumerate_claude_processes")
    @patch("monitoring.bridge_watchdog.get_recent_crashes")
    @patch("monitoring.bridge_watchdog.detect_crash_pattern")
    @patch("monitoring.bridge_watchdog.are_logs_fresh")
    @patch("monitoring.bridge_watchdog.is_bridge_running")
    def test_non_wedge_storm_opens_circuit(
        self, mock_running, mock_logs, mock_crash, mock_crashes, mock_enumerate
    ):
        """C2: a storm of non-wedge crashes opens restart_circuit_open while
        still requesting a human alert (today's throttle is preserved)."""
        import time as _time

        mock_running.return_value = (True, 1234)
        mock_logs.return_value = True
        mock_crash.return_value = (False, None)
        now = _time.time()
        mock_crashes.return_value = [_other_crash(now - i) for i in range(5)]
        mock_enumerate.return_value = []

        status = check_bridge_health()

        assert status.human_alert_needed is True
        assert status.restart_circuit_open is True

    @patch("monitoring.bridge_watchdog._enumerate_claude_processes")
    @patch("monitoring.bridge_watchdog.get_recent_crashes")
    @patch("monitoring.bridge_watchdog.detect_crash_pattern")
    @patch("monitoring.bridge_watchdog.are_logs_fresh")
    @patch("monitoring.bridge_watchdog.is_bridge_running")
    def test_mixed_50_50_storm_opens_circuit(
        self, mock_running, mock_logs, mock_crash, mock_crashes, mock_enumerate
    ):
        """Re-critique blocker: WEDGE_DOMINANCE_FRACTION = 0.9 means a bare
        50/50 mixed storm (3 wedge + 3 non-wedge) is NOT wedge-dominated and
        must open the circuit -- a 0.5 bar would incorrectly let it through."""
        import time as _time

        mock_running.return_value = (True, 1234)
        mock_logs.return_value = True
        mock_crash.return_value = (False, None)
        now = _time.time()
        mock_crashes.return_value = [_wedge_crash(now - i) for i in range(3)] + [
            _other_crash(now - i) for i in range(3)
        ]
        mock_enumerate.return_value = []

        status = check_bridge_health()

        assert status.human_alert_needed is True
        assert status.restart_circuit_open is True

    @patch("monitoring.bridge_watchdog._enumerate_claude_processes")
    @patch("monitoring.bridge_watchdog.get_recent_crashes")
    @patch("monitoring.bridge_watchdog.detect_crash_pattern")
    @patch("monitoring.bridge_watchdog.are_logs_fresh")
    @patch("monitoring.bridge_watchdog.is_bridge_running")
    def test_below_threshold_no_alert_no_circuit(
        self, mock_running, mock_logs, mock_crash, mock_crashes, mock_enumerate
    ):
        """Fewer than CRASH_STORM_THRESHOLD crashes: neither signal fires."""
        import time as _time

        mock_running.return_value = (True, 1234)
        mock_logs.return_value = True
        mock_crash.return_value = (False, None)
        now = _time.time()
        mock_crashes.return_value = [_other_crash(now)]
        mock_enumerate.return_value = []

        status = check_bridge_health()

        assert status.human_alert_needed is False
        assert status.restart_circuit_open is False

    def test_safety_constants_env_overridable(self, monkeypatch):
        """Re-critique Concern 3: the three safety-critical constants are read
        via os.environ.get, not hard-coded -- verified by re-importing the
        module with overridden env vars."""
        import importlib

        monkeypatch.setenv("CRASH_STORM_THRESHOLD", "9")
        monkeypatch.setenv("WEDGE_DOMINANCE_FRACTION", "0.75")
        monkeypatch.setenv("WATCHDOG_ALERT_COOLDOWN_SECONDS", "42")

        from monitoring import bridge_watchdog as bw

        reloaded = importlib.reload(bw)
        try:
            assert reloaded.CRASH_STORM_THRESHOLD == 9
            assert reloaded.WEDGE_DOMINANCE_FRACTION == 0.75
            assert reloaded.WATCHDOG_ALERT_COOLDOWN_SECONDS == 42
        finally:
            # Restore module state for subsequent tests in the same process.
            monkeypatch.delenv("CRASH_STORM_THRESHOLD", raising=False)
            monkeypatch.delenv("WEDGE_DOMINANCE_FRACTION", raising=False)
            monkeypatch.delenv("WATCHDOG_ALERT_COOLDOWN_SECONDS", raising=False)
            importlib.reload(bw)


class TestAlertCooldown:
    """_alert_cooldown_open() / _alert_cooldown_remaining(): file-sentinel
    create-or-refresh cooldown gate (issue #2396, C1)."""

    def test_window_open_when_no_sentinel(self, tmp_path):
        from monitoring import bridge_watchdog as bw

        with patch.object(bw, "COOLDOWN_FILE", tmp_path / "cooldown"):
            assert bw._alert_cooldown_open() is True
            # Stamped as a side effect -- immediately re-checking is closed.
            assert bw._alert_cooldown_open() is False

    def test_cooldown_expired_refires_and_restamps(self, tmp_path):
        import os as _os
        import time as _time

        from monitoring import bridge_watchdog as bw

        sentinel = tmp_path / "cooldown"
        sentinel.write_text("x")
        aged = _time.time() - (bw.WATCHDOG_ALERT_COOLDOWN_SECONDS + 10)
        _os.utime(sentinel, (aged, aged))

        with patch.object(bw, "COOLDOWN_FILE", sentinel):
            assert bw._alert_cooldown_open() is True
            mtime_after = sentinel.stat().st_mtime

        assert _time.time() - mtime_after < 5  # freshly re-stamped

    def test_check_only_variant_never_stamps(self, tmp_path):
        """--check-only must never consume the cooldown window."""
        from monitoring import bridge_watchdog as bw

        sentinel = tmp_path / "cooldown"
        with patch.object(bw, "COOLDOWN_FILE", sentinel):
            assert bw._alert_cooldown_remaining() is False
            assert not sentinel.exists()  # read-only variant creates nothing

    def test_check_only_variant_reports_within_window(self, tmp_path):
        from monitoring import bridge_watchdog as bw

        sentinel = tmp_path / "cooldown"
        sentinel.write_text("x")
        with patch.object(bw, "COOLDOWN_FILE", sentinel):
            assert bw._alert_cooldown_remaining() is True


class TestAlertHumanOfCrashStorm:
    """_alert_human_of_crash_storm(): fail-quiet, deduplicated Telegram alert.

    Every test here patches ``_machine_owns_alert_project`` explicitly: the
    Telegram-enqueue path only runs on the machine owning the alert's
    ``project_key``, so without the patch these assertions would pass or fail
    depending on which host the suite runs on.
    """

    def test_enqueues_agent_session_when_cooldown_open(self, tmp_path):
        from monitoring import bridge_watchdog as bw

        with (
            patch.object(bw, "COOLDOWN_FILE", tmp_path / "cooldown"),
            patch.object(bw, "_machine_owns_alert_project", return_value=True),
            patch("models.agent_session.AgentSession") as mock_session_cls,
        ):
            mock_instance = MagicMock()
            mock_session_cls.return_value = mock_instance

            bw._alert_human_of_crash_storm(["5 crashes in last 30 minutes"])

            mock_session_cls.assert_called_once()
            mock_instance.save.assert_called_once()

    def test_second_call_within_cooldown_is_noop(self, tmp_path):
        from monitoring import bridge_watchdog as bw

        with (
            patch.object(bw, "COOLDOWN_FILE", tmp_path / "cooldown"),
            patch.object(bw, "_machine_owns_alert_project", return_value=True),
            patch("models.agent_session.AgentSession") as mock_session_cls,
        ):
            mock_instance = MagicMock()
            mock_session_cls.return_value = mock_instance

            bw._alert_human_of_crash_storm(["issue 1"])
            bw._alert_human_of_crash_storm(["issue 2"])

            mock_session_cls.assert_called_once()

    def test_exception_in_agent_session_does_not_raise(self, tmp_path):
        """Fail-quiet contract: an exception building/saving the notification
        session must not propagate."""
        from monitoring import bridge_watchdog as bw

        with (
            patch.object(bw, "COOLDOWN_FILE", tmp_path / "cooldown"),
            patch.object(bw, "_machine_owns_alert_project", return_value=True),
            patch("models.agent_session.AgentSession", side_effect=Exception("boom")),
        ):
            # Should not raise.
            bw._alert_human_of_crash_storm(["issue"])


class TestCrashStormAlertOwnershipGate:
    """The alert session is a Telegram *send* instruction only the machine
    owning ``project_key`` can claim. On any other machine the enqueue is
    unrunnable by construction, so each 30-minute alert window stranded another
    pending session -- 75 accumulated over three days on a host owning only
    ``cyndra`` while the alert hardcoded ``valor``.
    """

    def test_unowned_project_files_issue_instead_of_enqueuing(self, tmp_path):
        from monitoring import bridge_watchdog as bw

        with (
            patch.object(bw, "COOLDOWN_FILE", tmp_path / "cooldown"),
            patch.object(bw, "_machine_owns_alert_project", return_value=False),
            patch.object(bw, "_file_crash_storm_issue") as mock_file_issue,
            patch("models.agent_session.AgentSession") as mock_session_cls,
        ):
            bw._alert_human_of_crash_storm(["5 crashes in last 30 minutes"])

            mock_session_cls.assert_not_called()
            mock_file_issue.assert_called_once()

    def test_owned_project_does_not_file_issue(self, tmp_path):
        from monitoring import bridge_watchdog as bw

        with (
            patch.object(bw, "COOLDOWN_FILE", tmp_path / "cooldown"),
            patch.object(bw, "_machine_owns_alert_project", return_value=True),
            patch.object(bw, "_file_crash_storm_issue") as mock_file_issue,
            patch("models.agent_session.AgentSession") as mock_session_cls,
            patch("agent.agent_session_queue.publish_session_notify"),
        ):
            bw._alert_human_of_crash_storm(["issue"])

            mock_session_cls.assert_called_once()
            mock_file_issue.assert_not_called()

    def test_unresolvable_ownership_fails_closed_on_enqueue(self, tmp_path):
        """An unreadable projects.json / unresolvable machine name must suppress
        the enqueue rather than stranding a session. Safe because the GitHub
        fallback still delivers the signal."""
        from monitoring import bridge_watchdog as bw

        with patch(
            "config.machine.get_machine_project_keys", side_effect=OSError("no projects.json")
        ):
            assert bw._machine_owns_alert_project("valor") is False

    def test_ownership_resolves_through_canonical_seam(self):
        from monitoring import bridge_watchdog as bw

        with patch("config.machine.get_machine_project_keys", return_value=["cyndra", "royop"]):
            assert bw._machine_owns_alert_project("cyndra") is True
            assert bw._machine_owns_alert_project("valor") is False


class TestFileCrashStormIssue:
    """_file_crash_storm_issue(): deduped against open issues, fail-quiet.

    Every test stubs ``subprocess.run`` -- an unstubbed run files a real
    GitHub issue (this happened once while developing the gate).
    """

    def test_files_issue_when_none_open(self):
        from monitoring import bridge_watchdog as bw

        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if "list" in cmd:
                return MagicMock(returncode=0, stdout="[]", stderr="")
            return MagicMock(returncode=0, stdout="https://github.com/o/r/issues/1", stderr="")

        with patch.object(bw.subprocess, "run", side_effect=fake_run):
            bw._file_crash_storm_issue("summary", ["issue"])

        assert any("create" in c for c in calls), "expected a gh issue create call"

    def test_skips_when_issue_already_open(self):
        """A machine wedged for days accumulates one tracking issue, not one
        per 30-minute alert window."""
        from monitoring import bridge_watchdog as bw

        calls = []
        marker = f"[crash-storm] {bw._hostname()}"

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return MagicMock(
                returncode=0,
                stdout=json.dumps([{"title": f"{marker}: bridge watchdog exhausted recovery"}]),
                stderr="",
            )

        with patch.object(bw.subprocess, "run", side_effect=fake_run):
            bw._file_crash_storm_issue("summary", ["issue"])

        assert not any("create" in c for c in calls), "should not file a duplicate issue"

    def test_dedup_does_not_use_lagging_search_api(self):
        """``--search`` hits GitHub's search index, which lags creation and let
        two nearby calls both file an issue. Dedup must list by label instead."""
        from monitoring import bridge_watchdog as bw

        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if "list" in cmd:
                return MagicMock(returncode=0, stdout="[]", stderr="")
            return MagicMock(returncode=0, stdout="ok", stderr="")

        with patch.object(bw.subprocess, "run", side_effect=fake_run):
            bw._file_crash_storm_issue("summary", ["issue"])

        list_cmd = next(c for c in calls if "list" in c)
        assert "--search" not in list_cmd
        assert "--label" in list_cmd

    def test_unrelated_open_issue_does_not_suppress_filing(self):
        """Only a matching host marker counts as a duplicate."""
        from monitoring import bridge_watchdog as bw

        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if "list" in cmd:
                return MagicMock(
                    returncode=0,
                    stdout=json.dumps([{"title": "[crash-storm] some-other-host: ..."}]),
                    stderr="",
                )
            return MagicMock(returncode=0, stdout="ok", stderr="")

        with patch.object(bw.subprocess, "run", side_effect=fake_run):
            bw._file_crash_storm_issue("summary", ["issue"])

        assert any("create" in c for c in calls), "another host's storm must not suppress ours"

    def test_gh_failure_does_not_raise(self):
        from monitoring import bridge_watchdog as bw

        with patch.object(bw.subprocess, "run", side_effect=Exception("gh missing")):
            # Should not raise.
            bw._file_crash_storm_issue("summary", ["issue"])


class TestAlertHumanOfCrashStormNotify:
    """_alert_human_of_crash_storm() publishes a session-notify AFTER save
    (issue #2439) -- this was the notify-less construct-and-save site that
    let crash-storm alerts strand until the worker's periodic health scan.
    """

    def test_publishes_notify_after_save_on_success(self, tmp_path):
        from monitoring import bridge_watchdog as bw

        with (
            patch.object(bw, "COOLDOWN_FILE", tmp_path / "cooldown"),
            patch.object(bw, "_machine_owns_alert_project", return_value=True),
            patch("models.agent_session.AgentSession") as mock_session_cls,
            patch("agent.agent_session_queue.publish_session_notify") as mock_publish,
        ):
            mock_instance = MagicMock()
            mock_session_cls.return_value = mock_instance

            bw._alert_human_of_crash_storm(["5 crashes in last 30 minutes"])

            mock_instance.save.assert_called_once()
            mock_publish.assert_called_once_with(mock_instance)

    def test_publish_failure_is_swallowed_alert_save_still_succeeds(self, tmp_path):
        """A notify-publish failure must never undo or mask the successful
        alert save -- the health scan remains the backstop."""
        from monitoring import bridge_watchdog as bw

        with (
            patch.object(bw, "COOLDOWN_FILE", tmp_path / "cooldown"),
            patch.object(bw, "_machine_owns_alert_project", return_value=True),
            patch("models.agent_session.AgentSession") as mock_session_cls,
            patch(
                "agent.agent_session_queue.publish_session_notify",
                side_effect=RuntimeError("redis down"),
            ),
        ):
            mock_instance = MagicMock()
            mock_session_cls.return_value = mock_instance

            # Should not raise.
            bw._alert_human_of_crash_storm(["issue"])

            mock_instance.save.assert_called_once()


class TestRecoveryExhaustedFallback:
    """execute_recovery(): level 5 is no longer a valid dispatch target;
    both former level-4 fallback paths route through _recovery_exhausted()
    (issue #2396, B1)."""

    def test_level_5_no_longer_dispatched(self, tmp_path):
        from monitoring import bridge_watchdog as bw

        with patch.object(bw, "RECOVERY_LOCK", tmp_path / "recovery-lock"):
            # level 5 falls through every elif and returns the bare False at
            # the bottom of execute_recovery -- it is not a valid level.
            assert bw.execute_recovery(5, ["issues"]) is False

    @patch("monitoring.bridge_watchdog._alert_human_of_crash_storm")
    @patch("monitoring.bridge_watchdog.log_crash")
    def test_auto_revert_disabled_routes_to_recovery_exhausted(
        self, mock_log_crash, mock_alert, tmp_path
    ):
        from monitoring import bridge_watchdog as bw

        with (
            patch.object(bw, "RECOVERY_LOCK", tmp_path / "recovery-lock"),
            patch.object(bw, "AUTO_REVERT_ENABLED_FILE", tmp_path / "not-enabled"),
        ):
            result = bw.execute_recovery(4, ["crash pattern detected"])

        assert result is False
        mock_log_crash.assert_called_once()
        assert "Recovery exhausted" in mock_log_crash.call_args[0][0]
        mock_alert.assert_called_once()

    @patch("monitoring.bridge_watchdog._alert_human_of_crash_storm")
    @patch("monitoring.bridge_watchdog.log_crash")
    @patch("monitoring.bridge_watchdog.revert_last_commit")
    @patch("monitoring.bridge_watchdog.restart_bridge")
    @patch("monitoring.bridge_watchdog.kill_stale_processes")
    @patch("monitoring.bridge_watchdog._kill_detected_zombies")
    @patch("monitoring.bridge_watchdog.clear_lock_files")
    def test_revert_failure_routes_to_recovery_exhausted(
        self,
        mock_clear_locks,
        mock_kill_zombies,
        mock_kill_stale,
        mock_restart,
        mock_revert,
        mock_log_crash,
        mock_alert,
        tmp_path,
    ):
        from monitoring import bridge_watchdog as bw

        mock_revert.return_value = False
        auto_revert_file = tmp_path / "auto-revert-enabled"
        auto_revert_file.touch()

        with (
            patch.object(bw, "RECOVERY_LOCK", tmp_path / "recovery-lock"),
            patch.object(bw, "AUTO_REVERT_ENABLED_FILE", auto_revert_file),
        ):
            result = bw.execute_recovery(4, ["crash pattern detected"])

        assert result is False
        mock_restart.assert_not_called()
        mock_log_crash.assert_called_once()
        mock_alert.assert_called_once()


class TestRunHealthCheckAlertAndCircuitWiring:
    """run_health_check(): fires the alert independent of the action level
    and skips execute_recovery() entirely when the circuit is open."""

    @patch("monitoring.bridge_watchdog.execute_recovery")
    @patch("monitoring.bridge_watchdog._alert_human_of_crash_storm")
    @patch("monitoring.bridge_watchdog.check_bridge_health")
    def test_circuit_open_skips_execute_recovery_but_still_alerts(
        self, mock_health, mock_alert, mock_recovery, tmp_path
    ):
        from monitoring import bridge_watchdog as bw

        mock_health.return_value = HealthStatus(
            healthy=False,
            process_running=True,
            logs_fresh=True,
            no_crash_pattern=True,
            issues=["5 crashes in last 30 minutes"],
            recovery_level=0,
            human_alert_needed=True,
            restart_circuit_open=True,
        )

        with (
            patch("bridge.hibernation.AUTH_REQUIRED_FLAG", tmp_path / "no-hibernation"),
            patch.object(bw, "RECOVERY_LOCK", tmp_path / "no-recovery-lock"),
            patch.object(bw, "UPDATE_RESTART_MARKER", tmp_path / "no-marker"),
        ):
            result = bw.run_health_check()

        assert result is False
        mock_alert.assert_called_once()
        mock_recovery.assert_not_called()

    @patch("monitoring.bridge_watchdog.execute_recovery")
    @patch("monitoring.bridge_watchdog._alert_human_of_crash_storm")
    @patch("monitoring.bridge_watchdog.check_bridge_health")
    def test_wedge_dominated_storm_alerts_and_still_executes_recovery(
        self, mock_health, mock_alert, mock_recovery, tmp_path
    ):
        """A wedge-dominated storm: human_alert_needed True, circuit closed
        -- execute_recovery() still runs with the real action level."""
        from monitoring import bridge_watchdog as bw

        mock_health.return_value = HealthStatus(
            healthy=False,
            process_running=True,
            logs_fresh=True,
            no_crash_pattern=True,
            issues=["update loop wedged", "12 crashes in last 30 minutes"],
            recovery_level=2,
            human_alert_needed=True,
            restart_circuit_open=False,
        )
        mock_recovery.return_value = True

        with (
            patch("bridge.hibernation.AUTH_REQUIRED_FLAG", tmp_path / "no-hibernation"),
            patch.object(bw, "RECOVERY_LOCK", tmp_path / "no-recovery-lock"),
            patch.object(bw, "UPDATE_RESTART_MARKER", tmp_path / "no-marker"),
        ):
            result = bw.run_health_check()

        assert result is True
        mock_alert.assert_called_once()
        mock_recovery.assert_called_once_with(2, mock_health.return_value.issues)
