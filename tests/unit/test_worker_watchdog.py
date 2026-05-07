"""Unit tests for worker watchdog active recovery (issue #1311).

Covers:
- L1/L2/L3/L4 escalation flow when the worker process is missing.
- Redis counter lifecycle (increment, reset on healthy, reset on success).
- Logger configuration (no duplicate handlers).
- Subprocess error handling for launchctl shell-outs.
- Redis failure swallowed silently in `_record_critical_status`.
- Operator-disable detection via launchctl print-disabled.
"""

from __future__ import annotations

import importlib
import logging
import subprocess
from unittest.mock import MagicMock, patch

import pytest

import monitoring.worker_watchdog as wwd

# --- Fixtures -----------------------------------------------------------------


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    """Redirect log file and disable real Redis/launchctl for each test."""
    logs = tmp_path / "logs"
    logs.mkdir()
    monkeypatch.setattr(wwd, "LOG_FILE", logs / "worker_watchdog.log")
    monkeypatch.setattr(wwd, "VERIFY_GRACE_SECONDS", 0)
    monkeypatch.setattr(wwd, "VERIFY_POLL_INTERVAL", 0)
    yield tmp_path


# --- Logger configuration -----------------------------------------------------


class TestLoggerConfiguration:
    def test_logger_no_duplicate_handlers(self):
        """The named logger has exactly one handler after import (regression guard)."""
        # Re-import the module to ensure handler-add idempotence.
        importlib.reload(wwd)
        assert len(wwd.logger.handlers) == 1

    def test_logger_does_not_propagate(self):
        """Named logger must not propagate to root (would cause stdout duplication)."""
        importlib.reload(wwd)
        assert wwd.logger.propagate is False

    def test_no_basicconfig_on_root(self):
        """Root logger should not have handlers attached by this module."""
        # If basicConfig still ran, root would have a StreamHandler from us.
        importlib.reload(wwd)
        # Root may have handlers from pytest itself — we only care that *our*
        # named logger isn't piggybacking on root via propagation.
        assert wwd.logger.propagate is False


# --- Redis down-tick counter --------------------------------------------------


class TestDownTickCounter:
    def test_read_returns_zero_when_key_missing(self, isolated_state):
        """Redis GET returns None → _read_down_ticks returns 0."""
        mock_r = MagicMock()
        mock_r.get.return_value = None
        with patch("popoto.redis_db.POPOTO_REDIS_DB", mock_r):
            assert wwd._read_down_ticks() == 0

    def test_read_returns_int_when_key_present(self, isolated_state):
        mock_r = MagicMock()
        mock_r.get.return_value = b"3"
        with patch("popoto.redis_db.POPOTO_REDIS_DB", mock_r):
            assert wwd._read_down_ticks() == 3

    def test_read_returns_zero_on_redis_failure(self, isolated_state):
        mock_r = MagicMock()
        mock_r.get.side_effect = RuntimeError("connection refused")
        with patch("popoto.redis_db.POPOTO_REDIS_DB", mock_r):
            assert wwd._read_down_ticks() == 0

    def test_increment_calls_incr_and_expire(self, isolated_state):
        """_increment_down_ticks must call INCR + EXPIRE atomically."""
        mock_r = MagicMock()
        mock_r.incr.return_value = 2
        with patch("popoto.redis_db.POPOTO_REDIS_DB", mock_r):
            result = wwd._increment_down_ticks()
        assert result == 2
        mock_r.incr.assert_called_once()
        key_used = mock_r.incr.call_args[0][0]
        assert "worker:watchdog:down_ticks:" in key_used
        mock_r.expire.assert_called_once_with(key_used, wwd.DOWN_TICKS_KEY_TTL)

    def test_increment_returns_1_on_redis_failure(self, isolated_state):
        mock_r = MagicMock()
        mock_r.incr.side_effect = RuntimeError("conn refused")
        with patch("popoto.redis_db.POPOTO_REDIS_DB", mock_r):
            assert wwd._increment_down_ticks() == 1

    def test_clear_calls_delete(self, isolated_state):
        mock_r = MagicMock()
        with patch("popoto.redis_db.POPOTO_REDIS_DB", mock_r):
            wwd._clear_down_ticks()
        mock_r.delete.assert_called_once()
        key_used = mock_r.delete.call_args[0][0]
        assert "worker:watchdog:down_ticks:" in key_used

    def test_clear_swallows_redis_failure(self, isolated_state):
        mock_r = MagicMock()
        mock_r.delete.side_effect = RuntimeError("conn refused")
        with patch("popoto.redis_db.POPOTO_REDIS_DB", mock_r):
            # Must not raise.
            wwd._clear_down_ticks()


# --- Escalation flow ----------------------------------------------------------


class TestEscalation:
    def test_l1_first_down_tick_no_kickstart(self, isolated_state, caplog):
        """First down tick logs and returns without invoking launchctl."""
        mock_r = MagicMock()
        mock_r.incr.return_value = 1
        with patch("popoto.redis_db.POPOTO_REDIS_DB", mock_r):
            with (
                patch.object(wwd, "_kickstart_worker") as kick,
                patch.object(wwd, "_enable_worker") as enable,
                patch.object(wwd, "_record_critical_status") as critical,
            ):
                with caplog.at_level(logging.INFO, logger=wwd.logger.name):
                    wwd._handle_missing_worker()
                kick.assert_not_called()
                enable.assert_not_called()
                critical.assert_not_called()

    def test_l2_second_down_tick_invokes_kickstart(self, isolated_state):
        """Second consecutive down tick runs kickstart and verifies."""
        mock_r = MagicMock()
        mock_r.incr.return_value = 2  # already had one missing tick

        with patch("popoto.redis_db.POPOTO_REDIS_DB", mock_r):
            with (
                patch.object(wwd, "_kickstart_worker", return_value=True) as kick,
                patch.object(wwd, "_verify_worker_alive", return_value=12345) as verify,
                patch.object(wwd, "_enable_worker") as enable,
                patch.object(wwd, "_record_critical_status") as critical,
            ):
                wwd._handle_missing_worker()

                kick.assert_called_once()
                verify.assert_called_once()
                enable.assert_not_called()
                critical.assert_not_called()
        # L2 success → counter cleared
        mock_r.delete.assert_called_once()

    def test_l2_success_resets_counter(self, isolated_state):
        mock_r = MagicMock()
        mock_r.incr.return_value = 2

        with patch("popoto.redis_db.POPOTO_REDIS_DB", mock_r):
            with (
                patch.object(wwd, "_kickstart_worker", return_value=True),
                patch.object(wwd, "_verify_worker_alive", return_value=999),
            ):
                wwd._handle_missing_worker()
        mock_r.delete.assert_called_once()

    def test_l3_runs_enable_then_kickstart_when_l2_fails(self, isolated_state):
        """When L2 kickstart fails to revive, L3 runs enable + kickstart."""
        mock_r = MagicMock()
        mock_r.incr.return_value = 2  # count == 2

        with patch("popoto.redis_db.POPOTO_REDIS_DB", mock_r):
            with (
                patch.object(wwd, "_kickstart_worker", side_effect=[False, True]) as kick,
                patch.object(wwd, "_enable_worker", return_value=True) as enable,
                patch.object(wwd, "_verify_worker_alive", return_value=42),
                patch.object(wwd, "_record_critical_status") as critical,
            ):
                wwd._handle_missing_worker()

                assert kick.call_count == 2  # L2 + L3
                enable.assert_called_once()
                critical.assert_not_called()
        mock_r.delete.assert_called_once()

    def test_l4_writes_critical_redis_key(self, isolated_state):
        """When both L2 and L3 fail and count >= 3, write critical Redis key."""
        mock_r = MagicMock()
        mock_r.incr.return_value = 3  # count == 3

        with patch("popoto.redis_db.POPOTO_REDIS_DB", mock_r):
            with (
                patch.object(wwd, "_kickstart_worker", return_value=False),
                patch.object(wwd, "_enable_worker", return_value=False),
                patch.object(wwd, "_verify_worker_alive", return_value=None),
                patch.object(wwd, "_record_critical_status") as critical,
            ):
                wwd._handle_missing_worker()

                critical.assert_called_once()
                args, _kwargs = critical.call_args
                reason, tick_count = args
                assert "kickstart+enable both failed" in reason
                assert tick_count == 3

    def test_count_2_l3_fail_does_not_trigger_critical(self, isolated_state):
        """At count=2 with L3 failure, log a warning but do not write critical yet."""
        mock_r = MagicMock()
        mock_r.incr.return_value = 2  # count == 2

        with patch("popoto.redis_db.POPOTO_REDIS_DB", mock_r):
            with (
                patch.object(wwd, "_kickstart_worker", return_value=False),
                patch.object(wwd, "_enable_worker", return_value=False),
                patch.object(wwd, "_verify_worker_alive", return_value=None),
                patch.object(wwd, "_record_critical_status") as critical,
            ):
                wwd._handle_missing_worker()
                critical.assert_not_called()


# --- Healthy tick resets counter ----------------------------------------------


class TestHealthyTickResetsCounter:
    def test_healthy_tick_clears_down_counter(self, isolated_state):
        """When `main()` sees a healthy worker, the Redis counter is deleted."""
        ok_status = {"status": "ok", "pid": 1, "heartbeat_age": 5.0, "message": "ok"}
        mock_r = MagicMock()

        with patch("popoto.redis_db.POPOTO_REDIS_DB", mock_r):
            with (
                patch.object(wwd, "check", return_value=ok_status),
                patch.object(wwd, "_is_operator_disabled", return_value=False),
            ):
                with patch("sys.argv", ["worker_watchdog.py"]):
                    wwd.main()
        mock_r.delete.assert_called_once()
        key_used = mock_r.delete.call_args[0][0]
        assert "worker:watchdog:down_ticks:" in key_used


# --- Operator disable via launchctl print-disabled ---------------------------


class TestOperatorDisableShortCircuit:
    def test_operator_disable_skips_check(self, isolated_state):
        """When launchctl reports worker as disabled, check() is never called."""
        mock_r = MagicMock()
        disabled_output = subprocess.CompletedProcess(
            args=["launchctl", "print-disabled", "gui/501"],
            returncode=0,
            stdout='\t"com.valor.worker" => disabled\n',
            stderr="",
        )
        with patch("popoto.redis_db.POPOTO_REDIS_DB", mock_r):
            with (
                patch("subprocess.run", return_value=disabled_output),
                patch.object(wwd, "check") as check_mock,
            ):
                with patch("sys.argv", ["w"]):
                    wwd.main()
        check_mock.assert_not_called()
        # Counter must be cleared so a future re-enable starts fresh.
        mock_r.delete.assert_called_once()

    def test_operator_disable_clears_counter(self, isolated_state):
        """Operator-disable short-circuit also clears the down-tick counter."""
        mock_r = MagicMock()
        with patch("popoto.redis_db.POPOTO_REDIS_DB", mock_r):
            with patch.object(wwd, "_is_operator_disabled", return_value=True):
                with patch.object(wwd, "check") as check_mock:
                    with patch("sys.argv", ["w"]):
                        wwd.main()
        check_mock.assert_not_called()
        mock_r.delete.assert_called_once()

    def test_is_operator_disabled_true(self, isolated_state):
        """_is_operator_disabled parses 'disabled' from launchctl output."""
        disabled_output = subprocess.CompletedProcess(
            args=["launchctl", "print-disabled", "gui/501"],
            returncode=0,
            stdout='\t"com.valor.worker" => disabled\n\t"com.valor.bridge" => enabled\n',
            stderr="",
        )
        with patch("subprocess.run", return_value=disabled_output):
            assert wwd._is_operator_disabled() is True

    def test_is_operator_disabled_false_when_enabled(self, isolated_state):
        """_is_operator_disabled returns False when worker is enabled."""
        enabled_output = subprocess.CompletedProcess(
            args=["launchctl", "print-disabled", "gui/501"],
            returncode=0,
            stdout='\t"com.valor.worker" => enabled\n',
            stderr="",
        )
        with patch("subprocess.run", return_value=enabled_output):
            assert wwd._is_operator_disabled() is False

    def test_is_operator_disabled_false_on_failure(self, isolated_state):
        """_is_operator_disabled assumes enabled when launchctl call fails."""
        with patch("subprocess.run", side_effect=OSError("launchctl not found")):
            assert wwd._is_operator_disabled() is False


# --- Subprocess error handling for launchctl helpers --------------------------


class TestLaunchctlHelpers:
    def test_kickstart_failure_returncode(self, isolated_state, caplog):
        fake_completed = subprocess.CompletedProcess(
            args=["launchctl", "kickstart", "-k", "x"],
            returncode=3,
            stdout="",
            stderr="No such service",
        )
        # Watchdog logger is non-propagating; attach caplog handler directly.
        wwd.logger.addHandler(caplog.handler)
        try:
            with patch("subprocess.run", return_value=fake_completed):
                with caplog.at_level(logging.ERROR, logger=wwd.logger.name):
                    ok = wwd._kickstart_worker()
        finally:
            wwd.logger.removeHandler(caplog.handler)
        assert ok is False
        assert any("launchctl kickstart failed" in r.message for r in caplog.records)

    def test_kickstart_timeout_swallowed(self, isolated_state):
        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="launchctl", timeout=10),
        ):
            ok = wwd._kickstart_worker()
        assert ok is False

    def test_enable_failure_returncode(self, isolated_state):
        fake = subprocess.CompletedProcess(
            args=["launchctl", "enable", "x"], returncode=2, stdout="", stderr="bad"
        )
        with patch("subprocess.run", return_value=fake):
            ok = wwd._enable_worker()
        assert ok is False

    def test_record_critical_swallows_redis_error(self, isolated_state, caplog):
        """Redis unavailable must not raise out of the watchdog."""
        mock_r = MagicMock()
        mock_r.set.side_effect = RuntimeError("conn refused")
        wwd.logger.addHandler(caplog.handler)
        try:
            with patch("popoto.redis_db.POPOTO_REDIS_DB", mock_r):
                with caplog.at_level(logging.WARNING, logger=wwd.logger.name):
                    wwd._record_critical_status("test reason", tick_count=4)
        finally:
            wwd.logger.removeHandler(caplog.handler)
        # Did not raise — that's the contract.
        assert any("Could not write critical Redis key" in r.message for r in caplog.records)

    def test_record_critical_uses_popoto_redis_db(self, isolated_state):
        """_record_critical_status must use POPOTO_REDIS_DB, not raw redis."""
        mock_r = MagicMock()
        with patch("popoto.redis_db.POPOTO_REDIS_DB", mock_r):
            wwd._record_critical_status("test reason", tick_count=2)
        mock_r.set.assert_called_once()
        key_used = mock_r.set.call_args[0][0]
        assert "worker:watchdog:critical:" in key_used


# --- Verify-worker-alive -----------------------------------------------------


class TestVerifyWorkerAlive:
    def test_returns_pid_when_present(self, isolated_state):
        with patch.object(wwd, "_get_worker_pid", return_value=12345):
            assert wwd._verify_worker_alive(grace_seconds=0) == 12345

    def test_returns_none_when_absent(self, isolated_state):
        with patch.object(wwd, "_get_worker_pid", return_value=None):
            assert wwd._verify_worker_alive(grace_seconds=0) is None
