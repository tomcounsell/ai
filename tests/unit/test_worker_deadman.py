"""Unit tests for the worker dead-man's-switch (issue #1815 fix #1).

Covers _heartbeat_cycle() (the per-tick body) and the supporting seams:
- Fresh beacon + enabled  -> green write, no _self_kill
- Stale beacon + enabled  -> _self_kill called, critical log emitted with age
- Stale beacon + disabled -> _self_kill NOT called, only logs
- None beacon within grace period -> unarmed, green write, no abort
- None beacon past WORKER_DEADMAN_STARTUP_GRACE_MAX + enabled -> startup-freeze guard fires
- None beacon past grace + disabled -> no abort
- FS write failure -> no abort (error swallowed by _green_heartbeat_write)
- Graceful shutdown (_heartbeat_thread_main stop event) -> no abort
- Beacon-age info log on green path (once per minute cadence)
- _self_kill() dumps thread stacks then delivers SIGKILL (dump-before-kill;
  kill fires even if the dump raises)
"""

from __future__ import annotations

import os
import signal
import time
from unittest.mock import MagicMock, call, patch

import pytest

import worker.__main__ as wm
from worker.__main__ import (
    WORKER_DEADMAN_STALENESS_THRESHOLD,
    WORKER_DEADMAN_STARTUP_GRACE_MAX,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_cycle(
    tick_value: float | None,
    *,
    armed: bool = False,
    thread_start_offset: float = 5.0,
    beacon_log_next: float = 0.0,
    enabled: bool = True,
    threshold: int = WORKER_DEADMAN_STALENESS_THRESHOLD,
    grace_max: int = WORKER_DEADMAN_STARTUP_GRACE_MAX,
) -> tuple[list[str], list[str], bool, float]:
    """Call _heartbeat_cycle once with controlled state.

    Returns (self_kill_calls, write_calls, new_armed, new_beacon_log_next).
    All real side effects are intercepted via patch so no FS or process
    operations occur.
    """
    now = time.monotonic()
    thread_start = now - thread_start_offset

    self_kill_calls: list[str] = []
    write_calls: list[str] = []

    def fake_self_kill() -> None:
        self_kill_calls.append("killed")

    def fake_green_write() -> None:
        write_calls.append("written")

    def fake_get_loop_tick() -> float | None:
        return tick_value

    # Freeze time.monotonic so beacon_age is deterministic.
    mono_sequence = [now]

    def fake_monotonic() -> float:
        return mono_sequence[0]

    with (
        patch.object(wm, "_self_kill", fake_self_kill),
        patch.object(wm, "_green_heartbeat_write", fake_green_write),
        patch.object(wm, "WORKER_DEADMAN_ENABLED", enabled),
        patch.object(wm, "WORKER_DEADMAN_STALENESS_THRESHOLD", threshold),
        patch.object(wm, "WORKER_DEADMAN_STARTUP_GRACE_MAX", grace_max),
        patch("agent.session_state.get_loop_tick", fake_get_loop_tick),
        patch("time.monotonic", fake_monotonic),
    ):
        new_armed, new_beacon_log_next = wm._heartbeat_cycle(armed, thread_start, beacon_log_next)

    return self_kill_calls, write_calls, new_armed, new_beacon_log_next


# ---------------------------------------------------------------------------
# Fresh beacon tests
# ---------------------------------------------------------------------------


class TestDeadmanFreshBeacon:
    """Fresh beacon (tick within threshold + enabled) -> green write, no kill."""

    def test_fresh_beacon_no_kill(self):
        """A recent tick (well within threshold) must not trigger _self_kill."""
        now = time.monotonic()
        fresh_tick = now - 1.0  # 1 second ago, far below 90s threshold

        self_kills, writes, new_armed, _ = _run_cycle(
            fresh_tick,
            armed=True,  # pre-armed so we go straight to the staleness check
            enabled=True,
            threshold=90,
        )

        assert not self_kills, "Should not self-kill with fresh beacon"
        assert writes, "Should write green heartbeat with fresh beacon"
        assert new_armed, "Armed state must be preserved"

    def test_fresh_beacon_disabled_no_kill(self):
        """With ENABLED=false and fresh beacon, still no kill and still writes."""
        now = time.monotonic()
        fresh_tick = now - 1.0

        self_kills, writes, _, _ = _run_cycle(
            fresh_tick,
            armed=True,
            enabled=False,
            threshold=90,
        )

        assert not self_kills
        assert writes


# ---------------------------------------------------------------------------
# Stale beacon tests
# ---------------------------------------------------------------------------


class TestDeadmanStaleBeacon:
    """Stale beacon (tick exceeds threshold) -> _self_kill called when enabled."""

    def test_stale_beacon_enabled_kills(self, caplog):
        """Beacon age exceeding threshold + ENABLED=true -> _self_kill called."""
        import logging

        now = time.monotonic()
        # tick 120 seconds ago, threshold is 90 — beacon is stale
        stale_tick = now - 120.0

        with caplog.at_level(logging.CRITICAL, logger="worker"):
            self_kills, writes, _, _ = _run_cycle(
                stale_tick,
                armed=True,
                enabled=True,
                threshold=90,
                thread_start_offset=200.0,
            )

        assert self_kills, "Should self-kill with stale beacon + enabled"
        assert not writes, "Should NOT write green heartbeat when loop is frozen"
        assert any(
            "beacon" in r.message.lower() and "abort" in r.message.lower()
            for r in caplog.records
            if r.levelno == logging.CRITICAL
        ), f"Expected CRITICAL log about frozen beacon; got: {[r.message for r in caplog.records]}"

    def test_stale_beacon_logs_beacon_age(self, caplog):
        """Critical log includes the beacon age in seconds."""
        import logging

        now = time.monotonic()
        stale_tick = now - 150.0  # 150s stale

        with caplog.at_level(logging.CRITICAL, logger="worker"):
            _run_cycle(
                stale_tick,
                armed=True,
                enabled=True,
                threshold=90,
                thread_start_offset=200.0,
            )

        critical_messages = [r.message for r in caplog.records if r.levelno == logging.CRITICAL]
        # The age should appear somewhere in the critical message (~150s).
        assert any(any(str(age) in msg for age in range(148, 153)) for msg in critical_messages), (
            f"Expected beacon age ~150s in critical log, got: {critical_messages}"
        )

    def test_stale_beacon_disabled_no_kill(self, caplog):
        """Stale beacon + ENABLED=false -> no _self_kill, only logs."""
        import logging

        now = time.monotonic()
        stale_tick = now - 120.0

        with caplog.at_level(logging.CRITICAL, logger="worker"):
            self_kills, writes, _, _ = _run_cycle(
                stale_tick,
                armed=True,
                enabled=False,
                threshold=90,
                thread_start_offset=200.0,
            )

        assert not self_kills, "Should NOT self-kill with ENABLED=false"
        # Critical log still emitted for observability
        assert any(
            "beacon" in r.message.lower() for r in caplog.records if r.levelno == logging.CRITICAL
        )


# ---------------------------------------------------------------------------
# None beacon tests
# ---------------------------------------------------------------------------


class TestDeadmanNoneBeacon:
    """None beacon (loop-tick task not yet started) — unarmed state tests."""

    def test_none_beacon_within_grace_no_abort(self):
        """None beacon but within STARTUP_GRACE_MAX -> no abort, unconditional write."""
        # thread running 10s, grace is 300s: well within window
        self_kills, writes, new_armed, _ = _run_cycle(
            None,
            armed=False,
            enabled=True,
            grace_max=300,
            thread_start_offset=10.0,
        )

        assert not self_kills, "Should not kill during startup grace period"
        assert writes, "Should write green heartbeat during grace period"
        assert not new_armed, "Switch must not arm on a None tick"

    def test_none_beacon_past_grace_enabled_kills(self, caplog):
        """None beacon past STARTUP_GRACE_MAX + ENABLED=true -> startup-freeze guard fires."""
        import logging

        # thread running 400s, grace is 300s: past the window
        with caplog.at_level(logging.CRITICAL, logger="worker"):
            self_kills, writes, _, _ = _run_cycle(
                None,
                armed=False,
                enabled=True,
                grace_max=300,
                thread_start_offset=400.0,
            )

        assert self_kills, "Should self-kill past startup grace with enabled"
        assert any(
            "startup" in r.message.lower() or "freeze" in r.message.lower()
            for r in caplog.records
            if r.levelno == logging.CRITICAL
        )

    def test_none_beacon_past_grace_disabled_no_kill(self, caplog):
        """None beacon past grace + ENABLED=false -> no abort."""
        import logging

        with caplog.at_level(logging.CRITICAL, logger="worker"):
            self_kills, writes, _, _ = _run_cycle(
                None,
                armed=False,
                enabled=False,
                grace_max=300,
                thread_start_offset=400.0,
            )

        # No kill even though past grace
        assert not self_kills
        # Critical log still emitted for observability
        assert any(
            "startup" in r.message.lower() or "freeze" in r.message.lower()
            for r in caplog.records
            if r.levelno == logging.CRITICAL
        )


# ---------------------------------------------------------------------------
# Write failure test
# ---------------------------------------------------------------------------


class TestDeadmanWriteFailure:
    """FS write failure -> no abort (error swallowed by _green_heartbeat_write)."""

    def test_write_failure_no_abort(self, caplog):
        """OSError in _write_worker_heartbeat should NOT trigger _self_kill.

        _green_heartbeat_write already wraps the write in try/except and
        swallows the error (logs a WARNING). This test verifies the contract
        end-to-end by letting the real _green_heartbeat_write run with a
        patched _write_worker_heartbeat that raises.
        """
        import logging

        now = time.monotonic()
        fresh_tick = now - 1.0
        self_kill_calls: list[str] = []

        def fake_self_kill() -> None:
            self_kill_calls.append("killed")

        def bad_write() -> None:
            raise OSError("disk full")

        def fake_get_loop_tick() -> float | None:
            return fresh_tick

        thread_start = now - 5.0

        with (
            patch.object(wm, "_self_kill", fake_self_kill),
            patch.object(wm, "WORKER_DEADMAN_ENABLED", True),
            patch.object(wm, "WORKER_DEADMAN_STALENESS_THRESHOLD", 90),
            patch.object(wm, "WORKER_DEADMAN_STARTUP_GRACE_MAX", 300),
            patch("agent.session_state.get_loop_tick", fake_get_loop_tick),
            patch("agent.agent_session_queue._write_worker_heartbeat", bad_write),
            patch("time.monotonic", return_value=now),
        ):
            with caplog.at_level(logging.WARNING, logger="worker"):
                wm._heartbeat_cycle(True, thread_start, 0.0)

        assert not self_kill_calls, "Write failure must NOT trigger _self_kill"
        assert any(
            "write failed" in r.message for r in caplog.records if r.levelno == logging.WARNING
        )


# ---------------------------------------------------------------------------
# Graceful shutdown test
# ---------------------------------------------------------------------------


class TestDeadmanGracefulShutdown:
    """Graceful shutdown -> no abort even with stale beacon."""

    def test_shutdown_no_abort(self):
        """If _heartbeat_stop_event is set, the thread exits cleanly without abort."""
        self_kill_calls: list[str] = []

        # Simulate event already set: wait() returns True immediately.
        fake_event = MagicMock()
        fake_event.wait.return_value = True  # stop immediately on first wait

        def fake_self_kill() -> None:
            self_kill_calls.append("killed")

        mock_cycle = MagicMock(return_value=(False, 0.0))
        with (
            patch.object(wm, "_heartbeat_stop_event", fake_event),
            patch.object(wm, "_self_kill", fake_self_kill),
            # Prevent any _heartbeat_cycle side effects (not called anyway)
            patch.object(wm, "_heartbeat_cycle", mock_cycle),
        ):
            wm._heartbeat_thread_main()

        assert not self_kill_calls, "Graceful shutdown must not trigger _self_kill"
        # The loop never ran _heartbeat_cycle since wait() immediately returned True
        mock_cycle.assert_not_called()


# ---------------------------------------------------------------------------
# Beacon-age audit log test
# ---------------------------------------------------------------------------


class TestDeadmanBeaconAgeLog:
    """Beacon-age logger.info('[deadman] beacon age=...') emitted on green path."""

    def test_beacon_age_info_logged_on_first_green_tick(self, caplog):
        """When beacon is fresh and beacon_log_next=0.0, info-level age line appears."""
        import logging

        now = time.monotonic()
        fresh_tick = now - 5.0  # 5s old, well within threshold

        with caplog.at_level(logging.INFO, logger="worker"):
            self_kills, writes, _, new_beacon_log_next = _run_cycle(
                fresh_tick,
                armed=True,
                enabled=True,
                threshold=90,
                beacon_log_next=0.0,  # first tick after a full minute since last log
            )

        assert writes
        assert not self_kills
        assert any(
            "[deadman] beacon age=" in r.message
            for r in caplog.records
            if r.levelno == logging.INFO
        ), f"Expected beacon-age info log; got: {[r.message for r in caplog.records]}"
        assert new_beacon_log_next > now, "beacon_log_next should be bumped ~60s into the future"

    def test_beacon_age_not_logged_before_cadence_expires(self, caplog):
        """When beacon_log_next is in the future, no beacon-age line emitted."""
        import logging

        now = time.monotonic()
        fresh_tick = now - 5.0
        future_next = now + 30.0  # cadence not yet expired

        with caplog.at_level(logging.INFO, logger="worker"):
            _, _, _, new_beacon_log_next = _run_cycle(
                fresh_tick,
                armed=True,
                enabled=True,
                threshold=90,
                beacon_log_next=future_next,
            )

        assert not any("[deadman] beacon age=" in r.message for r in caplog.records), (
            "Should NOT emit beacon-age log before cadence expires"
        )
        assert new_beacon_log_next == future_next, "beacon_log_next should be unchanged"


# ---------------------------------------------------------------------------
# _self_kill seam test
# ---------------------------------------------------------------------------


class TestSelfKillSeam:
    """_self_kill() dumps thread stacks, then delivers SIGKILL so launchd respawns.

    SIGKILL is as unswallowable as the former abort path but raises no macOS
    crash-report dialog or Python .ips file (#1844). The thread dump must land
    BEFORE the kill (forensic evidence in logs/worker_error.log), and the kill
    must fire even if the dump raises (the `finally` guarantee).
    """

    def test_self_kill_sends_sigkill(self):
        """_self_kill dumps threads, then SIGKILLs this pid — dump strictly before kill."""
        manager = MagicMock()
        with (
            patch("worker.__main__.faulthandler.dump_traceback") as mock_dump,
            patch("worker.__main__.os.kill") as mock_kill,
        ):
            manager.attach_mock(mock_dump, "dump")
            manager.attach_mock(mock_kill, "kill")
            wm._self_kill()

        mock_kill.assert_called_once_with(os.getpid(), signal.SIGKILL)
        assert mock_dump.called, "faulthandler thread dump must run before the kill"
        # Ordering: the dump call is recorded before the kill call.
        assert manager.mock_calls.index(call.dump(all_threads=True)) < manager.mock_calls.index(
            call.kill(os.getpid(), signal.SIGKILL)
        )

    def test_self_kill_sigkill_fires_even_if_dump_raises(self):
        """The SIGKILL is in a `finally` — it fires even when the dump blows up."""
        with (
            patch(
                "worker.__main__.faulthandler.dump_traceback",
                side_effect=RuntimeError("stderr closed"),
            ),
            patch("worker.__main__.os.kill") as mock_kill,
        ):
            # The dump exception propagates out AFTER the finally runs os.kill.
            with pytest.raises(RuntimeError):
                wm._self_kill()
        mock_kill.assert_called_once_with(os.getpid(), signal.SIGKILL)


# ---------------------------------------------------------------------------
# Beacon accessor round-trip
# ---------------------------------------------------------------------------


class TestBeaconRoundTrip:
    """bump_loop_tick() / get_loop_tick() in agent.session_state."""

    def test_bump_then_get_round_trip(self):
        """bump sets a monotonic timestamp; get reads it back; None means unticked."""
        import agent.session_state as ss

        original = ss.last_loop_tick
        try:
            ss.last_loop_tick = None
            assert ss.get_loop_tick() is None, "Unticked beacon must read as None (unarmed)"

            with patch.object(ss.time, "monotonic", return_value=4242.0):
                ss.bump_loop_tick()
            assert ss.get_loop_tick() == 4242.0, "get_loop_tick must return the bumped value"
        finally:
            ss.last_loop_tick = original
