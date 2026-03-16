"""Unit tests for bridge watchdog zombie process detection and cleanup."""

import signal
from unittest.mock import MagicMock, patch

import pytest

from monitoring.bridge_watchdog import (
    SOFT_INSTANCE_LIMIT,
    ZOMBIE_THRESHOLD_SECONDS,
    HealthStatus,
    _enumerate_claude_processes,
    _parse_elapsed_time,
    classify_zombies,
    kill_zombie_processes,
)

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
12345    05:23 102400 /usr/local/bin/claude --session abc
12346 1-02:30:00 524288 /usr/local/bin/claude --session old
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
                "12345    05:23 102400 /usr/local/bin/claude\n"
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
    def test_check_only_includes_zombie_section(self, mock_health, capsys):
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
            active_claude_count=2,
        )

        with patch("sys.argv", ["bridge_watchdog.py", "--check-only"]):
            result = main()

        output = capsys.readouterr().out
        assert "Zombie processes: 0" in output
        assert "Active claude instances: 2" in output
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
