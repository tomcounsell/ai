"""Unit tests for the bridge watchdog health check, recovery, and alert wiring."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from monitoring.bridge_watchdog import (
    HealthStatus,
    check_bridge_health,
)
from monitoring.crash_tracker import CrashEvent

# --- HealthStatus tests ---


class TestHealthStatus:
    """Tests for extended HealthStatus dataclass."""

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


# --- check_bridge_health integration ---


class TestCheckBridgeHealthUpdateFlow:
    """Tests that check_bridge_health reports the update-flow verdict.

    ``assess_update_flow`` runs against the real watchdog Redis, so its verdict
    is a property of whichever machine happens to run the suite. On a host with
    no bridge it reports the update loop wedged, which makes ``healthy`` False
    for a reason unrelated to the input under test, so it stays mocked.
    """

    @patch("monitoring.bridge_watchdog.assess_update_flow")
    @patch("monitoring.bridge_watchdog.get_recent_crashes")
    @patch("monitoring.bridge_watchdog.detect_crash_pattern")
    @patch("monitoring.bridge_watchdog.are_logs_fresh")
    @patch("monitoring.bridge_watchdog.is_bridge_running")
    def test_a_wedged_update_flow_makes_the_bridge_unhealthy(
        self,
        mock_running,
        mock_logs,
        mock_crash,
        mock_crashes,
        mock_update_flow,
    ):
        """A wedged update-flow verdict makes an otherwise healthy bridge unhealthy."""
        from monitoring.bridge_watchdog import check_bridge_health

        mock_running.return_value = (True, 1234)
        mock_logs.return_value = True
        mock_crash.return_value = (False, None)
        mock_crashes.return_value = []
        mock_update_flow.return_value = (False, "update loop wedged: test")

        status = check_bridge_health()
        assert status.healthy is False
        assert "update loop wedged: test" in status.issues


# --- --check-only output format ---


class TestCheckOnlyOutput:
    """Tests for --check-only output format."""

    @patch("monitoring.bridge_watchdog.check_bridge_health")
    def test_check_only_output_has_no_process_sweep_lines(self, mock_health, capsys):
        from monitoring import bridge_watchdog as bw

        mock_health.return_value = HealthStatus(
            healthy=True,
            process_running=True,
            logs_fresh=True,
            no_crash_pattern=True,
            issues=[],
            recovery_level=0,
        )

        with patch("sys.argv", ["bridge_watchdog.py", "--check-only"]):
            result = bw.main()

        output = capsys.readouterr().out
        assert "Process running: True" in output
        assert "Human alert needed: False" in output
        assert "Restart circuit open: False" in output
        assert "Zombie" not in output
        assert "Active claude instances" not in output
        assert result == 0


# --- Crash detection on bridge death ---


class TestCrashDetectionOnBridgeDeath:
    """Tests that check_bridge_health calls log_crash when bridge is dead."""

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
    ):
        """When bridge is not running, check_bridge_health calls log_crash."""
        mock_running.return_value = (False, None)
        mock_logs.return_value = False
        mock_crash.return_value = (False, None)
        mock_crashes.return_value = []

        status = check_bridge_health()

        assert not status.process_running
        mock_log_crash.assert_called_once_with("bridge_dead_on_watchdog_check")

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
    ):
        """When bridge is running, log_crash should NOT be called."""
        mock_running.return_value = (True, 1234)
        mock_logs.return_value = True
        mock_crash.return_value = (False, None)
        mock_crashes.return_value = []

        status = check_bridge_health()

        assert status.process_running
        mock_log_crash.assert_not_called()

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
    ):
        """If log_crash raises, check_bridge_health should still return."""
        mock_running.return_value = (False, None)
        mock_logs.return_value = False
        mock_crash.return_value = (False, None)
        mock_crashes.return_value = []
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

        from monitoring.bridge_watchdog import logger as bw_logger
        from monitoring.bridge_watchdog import run_health_check

        # bw_logger.propagate is False (issue #2643), so caplog's root-attached
        # handler never sees its records; attach explicitly and detach after.
        bw_logger.addHandler(caplog.handler)
        try:
            with (
                patch("bridge.hibernation.AUTH_REQUIRED_FLAG", tmp_path / "bridge-auth-required"),
                caplog.at_level(logging.INFO, logger="monitoring.bridge_watchdog"),
            ):
                flag = tmp_path / "bridge-auth-required"
                flag.write_text("auth-required")
                run_health_check()
        finally:
            bw_logger.removeHandler(caplog.handler)

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
        # bw.logger.propagate is False (issue #2643), so caplog's root-attached
        # handler never sees its records; attach explicitly and detach after.
        bw.logger.addHandler(caplog.handler)
        try:
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
        finally:
            bw.logger.removeHandler(caplog.handler)

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

    @patch("monitoring.bridge_watchdog.get_recent_crashes")
    @patch("monitoring.bridge_watchdog.detect_crash_pattern")
    @patch("monitoring.bridge_watchdog.are_logs_fresh")
    @patch("monitoring.bridge_watchdog.is_bridge_running")
    def test_wedge_dominated_crash_storm_livelock_regression(
        self, mock_running, mock_logs, mock_crash, mock_crashes
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

        status = check_bridge_health()

        assert status.human_alert_needed is True
        assert status.restart_circuit_open is False
        # recovery_level never escalated to a former "5" -- it stays whatever
        # the other checks computed (0 here, since nothing else fired).
        assert status.recovery_level in (0, 1, 2, 3, 4)

    @patch("monitoring.bridge_watchdog.get_recent_crashes")
    @patch("monitoring.bridge_watchdog.detect_crash_pattern")
    @patch("monitoring.bridge_watchdog.are_logs_fresh")
    @patch("monitoring.bridge_watchdog.is_bridge_running")
    def test_non_wedge_storm_opens_circuit(self, mock_running, mock_logs, mock_crash, mock_crashes):
        """C2: a storm of non-wedge crashes opens restart_circuit_open while
        still requesting a human alert (today's throttle is preserved)."""
        import time as _time

        mock_running.return_value = (True, 1234)
        mock_logs.return_value = True
        mock_crash.return_value = (False, None)
        now = _time.time()
        mock_crashes.return_value = [_other_crash(now - i) for i in range(5)]

        status = check_bridge_health()

        assert status.human_alert_needed is True
        assert status.restart_circuit_open is True

    @patch("monitoring.bridge_watchdog.get_recent_crashes")
    @patch("monitoring.bridge_watchdog.detect_crash_pattern")
    @patch("monitoring.bridge_watchdog.are_logs_fresh")
    @patch("monitoring.bridge_watchdog.is_bridge_running")
    def test_mixed_50_50_storm_opens_circuit(
        self, mock_running, mock_logs, mock_crash, mock_crashes
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

        status = check_bridge_health()

        assert status.human_alert_needed is True
        assert status.restart_circuit_open is True

    @patch("monitoring.bridge_watchdog.get_recent_crashes")
    @patch("monitoring.bridge_watchdog.detect_crash_pattern")
    @patch("monitoring.bridge_watchdog.are_logs_fresh")
    @patch("monitoring.bridge_watchdog.is_bridge_running")
    def test_below_threshold_no_alert_no_circuit(
        self, mock_running, mock_logs, mock_crash, mock_crashes
    ):
        """Fewer than CRASH_STORM_THRESHOLD crashes: neither signal fires."""
        import time as _time

        mock_running.return_value = (True, 1234)
        mock_logs.return_value = True
        mock_crash.return_value = (False, None)
        now = _time.time()
        mock_crashes.return_value = [_other_crash(now)]

        status = check_bridge_health()

        assert status.human_alert_needed is False
        assert status.restart_circuit_open is False

    def test_safety_constants_env_overridable(self, monkeypatch):
        """Re-critique Concern 3: the safety-critical constants are read
        via os.environ.get, not hard-coded -- verified by re-importing the
        module with overridden env vars."""
        import importlib

        monkeypatch.setenv("CRASH_STORM_THRESHOLD", "9")
        monkeypatch.setenv("WEDGE_DOMINANCE_FRACTION", "0.75")

        from monitoring import bridge_watchdog as bw

        reloaded = importlib.reload(bw)
        try:
            assert reloaded.CRASH_STORM_THRESHOLD == 9
            assert reloaded.WEDGE_DOMINANCE_FRACTION == 0.75
        finally:
            # Restore module state for subsequent tests in the same process.
            monkeypatch.delenv("CRASH_STORM_THRESHOLD", raising=False)
            monkeypatch.delenv("WEDGE_DOMINANCE_FRACTION", raising=False)
            importlib.reload(bw)


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

    @patch("monitoring.bridge_watchdog.log_crash")
    def test_auto_revert_disabled_routes_to_recovery_exhausted(self, mock_log_crash, tmp_path):
        from monitoring import bridge_watchdog as bw

        with (
            patch.object(bw, "RECOVERY_LOCK", tmp_path / "recovery-lock"),
            patch.object(bw, "AUTO_REVERT_ENABLED_FILE", tmp_path / "not-enabled"),
        ):
            result = bw.execute_recovery(4, ["crash pattern detected"])

        assert result is False
        mock_log_crash.assert_called_once()
        assert "Recovery exhausted" in mock_log_crash.call_args[0][0]

    @patch("monitoring.bridge_watchdog.log_crash")
    @patch("monitoring.bridge_watchdog.revert_last_commit")
    @patch("monitoring.bridge_watchdog.restart_bridge")
    @patch("monitoring.bridge_watchdog.kill_stale_processes")
    @patch("monitoring.bridge_watchdog.clear_lock_files")
    def test_revert_failure_routes_to_recovery_exhausted(
        self,
        mock_clear_locks,
        mock_kill_stale,
        mock_restart,
        mock_revert,
        mock_log_crash,
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


class TestRunHealthCheckCircuitWiring:
    """run_health_check(): skips execute_recovery() entirely when the
    restart circuit is open."""

    @patch("monitoring.bridge_watchdog.execute_recovery")
    @patch("monitoring.bridge_watchdog.check_bridge_health")
    def test_circuit_open_skips_execute_recovery(self, mock_health, mock_recovery, tmp_path):
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
        mock_recovery.assert_not_called()

    @patch("monitoring.bridge_watchdog.execute_recovery")
    @patch("monitoring.bridge_watchdog.check_bridge_health")
    def test_wedge_dominated_storm_still_executes_recovery(
        self, mock_health, mock_recovery, tmp_path
    ):
        """A wedge-dominated storm: circuit closed -- execute_recovery()
        still runs with the real action level."""
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
        mock_recovery.assert_called_once_with(2, mock_health.return_value.issues)


# --- The watchdog never signals claude / pyright processes (issue #3592) ---

_INTERACTIVE_CLAUDE_PID = 170
_PYRIGHT_PID = 171
_PS_TABLE = (
    "  PID     ELAPSED    RSS COMMAND\n"
    f"  {_INTERACTIVE_CLAUDE_PID}     03:00:12  568000 "
    "claude --continue --permission-mode bypassPermissions --model opus\n"
    f"  {_PYRIGHT_PID}     03:00:12  650000 pyright-langserver --stdio\n"
)


def _fake_subprocess_run(cmd, *args, **kwargs):
    """Answer every ``ps`` with a table holding a 3h-old interactive claude
    session and a 3h-old pyright; every other command succeeds silently."""
    result = MagicMock()
    result.returncode = 0
    result.stderr = ""
    result.stdout = _PS_TABLE if cmd and cmd[0] == "ps" else ""
    return result


def _stat_or_none(path):
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    return (st.st_size, st.st_mtime_ns)


class TestWatchdogNeverSignalsClaudeProcesses:
    """An interactive ``claude`` session or a ``pyright`` older than 2h is never
    signalled by the watchdog, from the health check or from any recovery level.

    Orphan cleanup belongs to the worker's ownership-gated reapers; the watchdog
    has no evidence of ownership, only age, so it must not signal these at all.
    """

    @pytest.fixture(autouse=True)
    def _checkout_data_untouched(self):
        """No test in this class may write the checkout's crash history or
        recovery lock, the files the live watchdog reads."""
        from monitoring import bridge_watchdog as bw
        from monitoring import crash_tracker

        watched = (crash_tracker.CRASH_HISTORY_FILE, bw.RECOVERY_LOCK)
        before = [_stat_or_none(p) for p in watched]
        yield
        assert [_stat_or_none(p) for p in watched] == before

    @staticmethod
    def _signalled_pids(mock_kill):
        return {c.args[0] for c in mock_kill.call_args_list if c.args}

    @patch("monitoring.bridge_watchdog.log_crash")
    @patch("monitoring.bridge_watchdog.assess_scan_health", return_value=(True, ""))
    @patch("monitoring.bridge_watchdog._get_watchdog_redis")
    @patch("monitoring.bridge_watchdog.assess_update_flow", return_value=(True, ""))
    @patch("monitoring.bridge_watchdog.get_recent_crashes", return_value=[])
    @patch("monitoring.bridge_watchdog.detect_crash_pattern", return_value=(False, None))
    @patch("monitoring.bridge_watchdog.are_logs_fresh", return_value=True)
    @patch("monitoring.bridge_watchdog.is_bridge_running", return_value=(True, 1234))
    @patch("time.sleep")
    @patch("os.kill")
    @patch("subprocess.run", side_effect=_fake_subprocess_run)
    def test_health_check_never_signals_claude_or_pyright(
        self,
        mock_run,
        mock_kill,
        mock_sleep,
        mock_running,
        mock_logs,
        mock_crash,
        mock_crashes,
        mock_update_flow,
        mock_redis,
        mock_scan_health,
        mock_log_crash,
    ):
        from monitoring.bridge_watchdog import check_bridge_health

        status = check_bridge_health()

        signalled = self._signalled_pids(mock_kill)
        assert _INTERACTIVE_CLAUDE_PID not in signalled
        assert _PYRIGHT_PID not in signalled
        assert status.healthy is True, f"unexpected issues: {status.issues}"
        mock_log_crash.assert_not_called()

    @pytest.mark.parametrize("level", [2, 3, 4])
    @patch("monitoring.bridge_watchdog.log_crash")
    @patch("monitoring.bridge_watchdog.revert_last_commit", return_value=False)
    @patch("monitoring.bridge_watchdog.clear_lock_files", return_value=0)
    @patch("monitoring.bridge_watchdog.restart_bridge", return_value=True)
    @patch("monitoring.bridge_watchdog.kill_stale_processes", return_value=0)
    @patch("time.sleep")
    @patch("os.kill")
    @patch("subprocess.run", side_effect=_fake_subprocess_run)
    def test_recovery_never_signals_claude_or_pyright(
        self,
        mock_run,
        mock_kill,
        mock_sleep,
        mock_kill_stale,
        mock_restart,
        mock_clear_locks,
        mock_revert,
        mock_log_crash,
        level,
        tmp_path,
    ):
        from monitoring import bridge_watchdog as bw

        auto_revert_file = tmp_path / "auto-revert-enabled"
        if level == 4:
            auto_revert_file.touch()

        with (
            patch.object(bw, "RECOVERY_LOCK", tmp_path / "recovery-lock"),
            patch.object(bw, "AUTO_REVERT_ENABLED_FILE", auto_revert_file),
        ):
            bw.execute_recovery(level, ["bridge unhealthy"])

        signalled = self._signalled_pids(mock_kill)
        assert _INTERACTIVE_CLAUDE_PID not in signalled
        assert _PYRIGHT_PID not in signalled


class TestKillStaleProcessesOnlyKillsBridgeInterpreter:
    """``pgrep -f telegram_bridge.py`` matches every command line that mentions
    the file. ``kill_stale_processes`` must SIGKILL only a Python interpreter
    whose script argument is ``bridge/telegram_bridge.py``, never an operator's
    ``claude``, ``vim`` or ``tail`` that merely names it."""

    _PROCESS_TABLE = {
        201: "claude --continue --permission-mode bypassPermissions bridge/telegram_bridge.py",
        202: "vim /Users/op/src/ai/bridge/telegram_bridge.py",
        203: "tail -f bridge/telegram_bridge.py",
        204: "/opt/homebrew/bin/python3 -m ruff check bridge/telegram_bridge.py",
        205: "/opt/homebrew/Cellar/python@3.14/3.14.7/Frameworks/Python.framework/Versions/3.14"
        "/Resources/Python.app/Contents/MacOS/Python /Users/op/src/ai/bridge/telegram_bridge.py",
    }
    _BRIDGE_PID = 205

    def _fake_run(self, cmd, *args, **kwargs):
        if cmd[:2] == ["pgrep", "-f"]:
            out = "\n".join(str(pid) for pid in self._PROCESS_TABLE)
            return subprocess.CompletedProcess(cmd, 0, stdout=out + "\n", stderr="")
        if cmd[0] == "ps":
            out = "\n".join(f"{pid} {args_}" for pid, args_ in self._PROCESS_TABLE.items())
            return subprocess.CompletedProcess(cmd, 0, stdout=out + "\n", stderr="")
        raise AssertionError(f"unexpected subprocess call: {cmd}")

    @patch("os.kill")
    def test_only_the_bridge_interpreter_is_killed(self, mock_kill):
        from monitoring.bridge_watchdog import kill_stale_processes

        with patch("subprocess.run", side_effect=self._fake_run):
            killed = kill_stale_processes()

        assert [c.args for c in mock_kill.call_args_list] == [(self._BRIDGE_PID, 9)]
        assert killed == 1
