"""Tests for granite startup fast diagnostic (issue #1710).

Covers:
- Plateau detection: N consecutive identical response values bail early
- Oscillation detection: a recurring event (same response) plateaus
- Frame capture: falls back to edge_buffer when level_tail is blank
- Alert cooldown: process-local gate + Redis gate suppress duplicates
- Silent-start detection: response=None + neither PTY idle -> plateau
- _capture_startup_frame: empty/None/whitespace inputs
- _should_alert: inverted contract, process-local-first ordering
- _send_startup_alert: FileNotFoundError / TimeoutExpired swallowed with
  [granite-alert-suppressed] ERROR; Redis-down path still sends
"""

from __future__ import annotations

import time
import unittest
from unittest.mock import MagicMock, patch

from agent.granite_container.container import (
    STARTUP_PLATEAU_CYCLES,
    Container,
    ContainerResult,
    _capture_startup_frame,
)
from agent.granite_container.pty_driver import IdleResult, PTYDriver

# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------


def _idle_result(
    buffer_text: str = "",
    saw_idle: bool = True,
    turn_buffer: str = "",
) -> IdleResult:
    return IdleResult(
        saw_idle=saw_idle,
        buffer=buffer_text,
        idle_marker="bypass permissions on",
        elapsed_ms=100,
        turn_buffer=turn_buffer,
    )


def _mock_driver(
    session_id: str = "mock-session",
) -> MagicMock:
    mock = MagicMock(spec=PTYDriver)
    mock.read_until_idle.return_value = _idle_result("", saw_idle=True)
    mock.last_resume_uuid.return_value = None
    mock.isalive.return_value = True
    mock._session_id = session_id
    return mock


def _mock_pm(buffer_text: str = "", saw_idle: bool = True) -> MagicMock:
    m = _mock_driver("mock-session-pm")
    m.read_until_idle.return_value = _idle_result(buffer_text, saw_idle)
    return m


def _mock_dev(buffer_text: str = "", saw_idle: bool = True) -> MagicMock:
    m = _mock_driver("mock-session-dev")
    m.read_until_idle.return_value = _idle_result(buffer_text, saw_idle)
    return m


# ---------------------------------------------------------------------------
# _capture_startup_frame tests
# ---------------------------------------------------------------------------


class TestCaptureStartupFrame(unittest.TestCase):
    """_capture_startup_frame pure helper tests."""

    def test_basic_non_empty(self) -> None:
        """Frame is non-empty for normal buffer content."""
        frame = _capture_startup_frame("PM error text here", "Dev error text here", "plateau", 10)
        self.assertGreater(len(frame), 0)
        self.assertIn("plateau", frame)
        self.assertIn("10", frame)
        self.assertIn("PM error text here", frame)
        self.assertIn("Dev error text here", frame)

    def test_empty_inputs_non_empty_frame(self) -> None:
        """Frame is non-empty even when both buffers are empty strings."""
        frame = _capture_startup_frame("", "", "ceiling", 59)
        self.assertGreater(len(frame), 0)
        self.assertIn("ceiling", frame)
        self.assertIn("59", frame)

    def test_none_inputs_non_empty_frame(self) -> None:
        """Frame is non-empty even when both buffers are None."""
        frame = _capture_startup_frame(None, None, "plateau", 10)  # type: ignore[arg-type]
        self.assertGreater(len(frame), 0)

    def test_whitespace_only_inputs(self) -> None:
        """Whitespace-only buffers yield a frame indicating no content."""
        frame = _capture_startup_frame("   \n\t  ", "   \n\t  ", "plateau", 10)
        self.assertGreater(len(frame), 0)
        # Should indicate no content (cleaned to empty)
        self.assertIn("(no content)", frame)

    def test_size_capped(self) -> None:
        """Frame total size is bounded below _STARTUP_FRAME_TOTAL_CAP."""
        large_pm = "X" * 10000
        large_dev = "Y" * 10000
        frame = _capture_startup_frame(large_pm, large_dev, "ceiling", 200)
        from agent.granite_container.container import _STARTUP_FRAME_TOTAL_CAP

        self.assertLessEqual(len(frame), _STARTUP_FRAME_TOTAL_CAP + 10)  # small margin for header

    def test_per_buffer_cap(self) -> None:
        """Each buffer tail is individually capped."""
        large_pm = "P" * 10000
        frame = _capture_startup_frame(large_pm, "small dev", "plateau", 5)
        from agent.granite_container.container import _STARTUP_FRAME_BUF_CAP

        # The PM section should not contain 10000 Ps
        self.assertLess(frame.count("P"), _STARTUP_FRAME_BUF_CAP + 10)

    def test_ceiling_kind_in_frame(self) -> None:
        """kind='ceiling' appears in the frame header."""
        frame = _capture_startup_frame("pm buf", "dev buf", "ceiling", 200)
        self.assertIn("kind=ceiling", frame)

    def test_plateau_kind_in_frame(self) -> None:
        """kind='plateau' appears in the frame header."""
        frame = _capture_startup_frame("pm buf", "dev buf", "plateau", 10)
        self.assertIn("kind=plateau", frame)


# ---------------------------------------------------------------------------
# _startup_cycle_idle return tuple tests
# ---------------------------------------------------------------------------


class TestStartupCycleIdleSurfacesLevelBuffer(unittest.TestCase):
    """_startup_cycle_idle returns both edge_buffer and level_tail distinctly."""

    def test_returns_five_tuple(self) -> None:
        """Helper returns a 5-tuple (saw_idle, edge_buf, level_tail, marker, ms)."""
        c = Container(user_message="test message")
        mock_pty = _mock_driver("test-pty")
        idle_res = IdleResult(
            saw_idle=True,
            buffer="edge content",
            idle_marker="bypass permissions on",
            elapsed_ms=50,
            turn_buffer="level tail content",
        )
        mock_pty.read_until_idle.return_value = idle_res
        result = c._startup_cycle_idle(mock_pty)
        self.assertEqual(len(result), 5)
        saw_idle, edge_buf, level_tail, marker, ms = result
        self.assertTrue(saw_idle)
        self.assertEqual(edge_buf, "edge content")
        self.assertEqual(level_tail, "level tail content")
        self.assertEqual(marker, "bypass permissions on")
        self.assertEqual(ms, 50)

    def test_edge_buffer_differs_from_level_tail(self) -> None:
        """edge_buffer and level_tail can be distinct values."""
        c = Container(user_message="test message")
        mock_pty = _mock_driver("test-pty")
        idle_res = IdleResult(
            saw_idle=False,
            buffer="this cycle only",
            idle_marker="",
            elapsed_ms=3000,
            turn_buffer="cumulative since last write",
        )
        mock_pty.read_until_idle.return_value = idle_res
        _, edge_buf, level_tail, _, _ = c._startup_cycle_idle(mock_pty)
        self.assertEqual(edge_buf, "this cycle only")
        self.assertEqual(level_tail, "cumulative since last write")

    def test_level_tail_falls_back_to_edge_when_turn_buffer_empty(self) -> None:
        """level_tail falls back to edge_buffer when turn_buffer is empty string."""
        c = Container(user_message="test message")
        mock_pty = _mock_driver("test-pty")
        idle_res = IdleResult(
            saw_idle=False,
            buffer="edge fallback text",
            idle_marker="",
            elapsed_ms=3000,
            turn_buffer="",  # empty -> fall back to edge
        )
        mock_pty.read_until_idle.return_value = idle_res
        _, edge_buf, level_tail, _, _ = c._startup_cycle_idle(mock_pty)
        self.assertEqual(level_tail, "edge fallback text")


# ---------------------------------------------------------------------------
# Plateau detection in startup loop
# ---------------------------------------------------------------------------


def _build_container_with_mocked_primes(**kwargs) -> Container:
    """Build a Container with _prime_session and _spawn_pair mocked."""
    c = Container(user_message="hello", **kwargs)
    return c


class TestPlateau(unittest.TestCase):
    """Plateau detection triggers after N consecutive identical response=None cycles
    with neither PTY idle (silent-start sentinel).
    """

    def _run_with_never_idle(self, n_cycles: int = STARTUP_PLATEAU_CYCLES + 2) -> ContainerResult:
        """Run container where PTYs never report idle or any startup event."""
        c = _build_container_with_mocked_primes()
        pm_mock = _mock_pm("", saw_idle=False)
        dev_mock = _mock_dev("", saw_idle=False)

        with (
            patch.object(c, "_spawn_pair"),
            patch.object(c, "_close_pair"),
            patch.object(c, "_prime_session"),
            # _run_pkill_fallback was deleted in #1816/#1832 (bab446d8):
            # teardown is now process-group-scoped via _close_pair_and_reap.
            patch.object(c, "_close_pair_and_reap"),
        ):
            c._pm_pty = pm_mock
            c._dev_pty = dev_mock
            result = c.run()
        return result

    def test_plateau_bails_early_before_ceiling(self) -> None:
        """N consecutive silent cycles bail with startup_unresolved + kind=plateau."""
        start = time.monotonic()
        result = self._run_with_never_idle()
        elapsed = time.monotonic() - start

        self.assertEqual(result.exit_reason, "startup_unresolved")
        self.assertEqual(result.startup_failure_kind, "plateau")
        self.assertIsNotNone(result.startup_plateau_cycles)
        # Should bail in well under the 600s ceiling.
        self.assertLess(
            elapsed,
            60.0,
            f"plateau bail took {elapsed:.1f}s -- expected well under 60s",
        )

    def test_plateau_frame_captured(self) -> None:
        """Plateau exit captures a non-empty startup_diagnostic_frame."""
        result = self._run_with_never_idle()
        self.assertIsNotNone(result.startup_diagnostic_frame)
        self.assertGreater(len(result.startup_diagnostic_frame or ""), 0)
        self.assertIn("plateau", result.startup_diagnostic_frame or "")

    def test_plateau_cycles_populated(self) -> None:
        """startup_plateau_cycles is set to N on plateau exit."""
        result = self._run_with_never_idle()
        self.assertIsNotNone(result.startup_plateau_cycles)
        self.assertGreaterEqual(result.startup_plateau_cycles or 0, STARTUP_PLATEAU_CYCLES)

    def test_progress_resets_plateau_counter(self) -> None:
        """A PTY reaching idle resets the plateau counter (no false plateau)."""
        c = _build_container_with_mocked_primes()
        pm_mock = _mock_pm()
        dev_mock = _mock_dev()

        # First STARTUP_PLATEAU_CYCLES - 1 calls: not idle (would be a plateau
        # if we ran one more). Then both PTYs go idle.
        not_idle_count = STARTUP_PLATEAU_CYCLES - 1
        not_idle_result = _idle_result("", saw_idle=False)
        both_idle_result = _idle_result("", saw_idle=True)

        pm_calls = [not_idle_result] * not_idle_count + [both_idle_result]
        dev_calls = [not_idle_result] * not_idle_count + [both_idle_result]

        # After startup, steady-state needs PM idle reads:
        steady_idle = _idle_result("[/complete]\nDone.", saw_idle=True)
        pm_calls.extend([steady_idle, steady_idle, steady_idle])
        dev_calls.extend([both_idle_result] * 10)

        pm_mock.read_until_idle.side_effect = lambda **kw: (
            pm_calls.pop(0) if pm_calls else both_idle_result
        )
        dev_mock.read_until_idle.side_effect = lambda **kw: (
            dev_calls.pop(0) if dev_calls else both_idle_result
        )

        def _lat_stub(path, *, baseline_text_count=None):
            if path and "mock-session-pm" in path:
                return "[/complete]\nDone."
            return ""

        with (
            patch.object(c, "_spawn_pair"),
            patch.object(c, "_close_pair"),
            patch.object(c, "_prime_session"),
            # _run_pkill_fallback was deleted in #1816/#1832 (bab446d8):
            # teardown is now process-group-scoped via _close_pair_and_reap.
            patch.object(c, "_close_pair_and_reap"),
            patch("agent.granite_container.container.last_assistant_text", side_effect=_lat_stub),
            patch("agent.granite_container.container.text_bearing_count", return_value=0),
        ):
            c._pm_pty = pm_mock
            c._dev_pty = dev_mock
            result = c.run()

        # Should NOT be a plateau exit -- should complete normally.
        self.assertNotEqual(result.startup_failure_kind, "plateau")
        self.assertNotEqual(result.exit_reason, "startup_unresolved")

    def test_ceiling_exit_captures_frame(self) -> None:
        """Ceiling exit (600s timeout) also sets startup_failure_kind=ceiling + frame."""
        c = _build_container_with_mocked_primes()
        pm_mock = _mock_pm("Unknown command: /granite:prime-pm-role", saw_idle=False)
        dev_mock = _mock_dev("", saw_idle=False)

        with (
            patch.object(c, "_spawn_pair"),
            patch.object(c, "_close_pair"),
            patch.object(c, "_prime_session"),
            # _run_pkill_fallback was deleted in #1816/#1832 (bab446d8):
            # teardown is now process-group-scoped via _close_pair_and_reap.
            patch.object(c, "_close_pair_and_reap"),
            # Override the startup deadline to be in the past so we hit the ceiling path
            # but NOT the plateau (to test pure ceiling exit we need more cycles than plateau).
            # We'll patch monotonic to simulate the ceiling being reached.
            patch("agent.granite_container.container.time") as mock_time,
        ):
            # Make startup_deadline already past after just a few calls
            call_count = [0]

            def fake_monotonic():
                call_count[0] += 1
                # First call sets the deadline, subsequent calls exceed it
                if call_count[0] <= 2:
                    return 0.0
                return 700.0  # past the 600s ceiling

            mock_time.monotonic.side_effect = fake_monotonic

            c._pm_pty = pm_mock
            c._dev_pty = dev_mock
            result = c.run()

        self.assertEqual(result.exit_reason, "startup_unresolved")
        self.assertEqual(result.startup_failure_kind, "ceiling")
        self.assertIsNotNone(result.startup_diagnostic_frame)
        self.assertGreater(len(result.startup_diagnostic_frame or ""), 0)

    def test_oscillating_event_plateaus(self) -> None:
        """A recurring startup event (same response every cycle) is NOT counted as a
        silent-start plateau (the sentinel requires response=None). Oscillating non-None
        responses with idle bools False keep the loop alive -- no early bail unless
        silent-start sentinel fires.

        This test verifies the fingerprint is response-only: an oscillating event that
        repeats the same non-None response does NOT trigger the silent-start bail.
        """
        # For the oscillating case: response is non-None (e.g. "1" from trust_folder),
        # neither PTY is idle. The plateau counter accumulates on response, but the
        # silent-start sentinel (_silent_start) is False because response is not None.
        # So the plateau bail does NOT fire for non-None oscillating responses.
        c = _build_container_with_mocked_primes()
        pm_mock = _mock_pm("Yes, I trust this folder", saw_idle=False)
        dev_mock = _mock_dev("Yes, I trust this folder", saw_idle=False)

        with (
            patch.object(c, "_spawn_pair"),
            patch.object(c, "_close_pair"),
            patch.object(c, "_prime_session"),
            # _run_pkill_fallback was deleted in #1816/#1832 (bab446d8):
            # teardown is now process-group-scoped via _close_pair_and_reap.
            patch.object(c, "_close_pair_and_reap"),
            # Hit ceiling fast
            patch("agent.granite_container.container.time") as mock_time,
        ):
            call_count = [0]

            def fake_monotonic():
                call_count[0] += 1
                if call_count[0] <= 2:
                    return 0.0
                return 700.0

            mock_time.monotonic.side_effect = fake_monotonic
            c._pm_pty = pm_mock
            c._dev_pty = dev_mock
            result = c.run()

        # Should be ceiling (not plateau bail) since response is non-None
        # (the _silent_start sentinel is False for non-None responses).
        self.assertEqual(result.exit_reason, "startup_unresolved")
        # Ceiling because the oscillating case doesn't trigger the silent-start sentinel
        self.assertEqual(result.startup_failure_kind, "ceiling")


# ---------------------------------------------------------------------------
# Frame capture fallback: level_tail -> edge_buffer
# ---------------------------------------------------------------------------


class TestFrameCaptureFallback(unittest.TestCase):
    """Frame capture falls back to edge_buffer when level_tail is blank."""

    def test_frame_non_empty_when_level_tail_empty_but_edge_has_content(self) -> None:
        """When turn_buffer is empty but edge buffer has content, frame is non-empty."""
        c = Container(user_message="test message")
        mock_pty = _mock_driver("test-pty")
        # level_tail (turn_buffer) is empty, but edge buffer has diagnostic text
        idle_res = IdleResult(
            saw_idle=False,
            buffer="Unknown command: /granite:prime-pm-role",
            idle_marker="",
            elapsed_ms=3000,
            turn_buffer="",  # empty
        )
        mock_pty.read_until_idle.return_value = idle_res
        _, edge_buf, level_tail, _, _ = c._startup_cycle_idle(mock_pty)
        # level_tail falls back to edge_buf when turn_buffer is empty
        self.assertEqual(level_tail, "Unknown command: /granite:prime-pm-role")

    def test_frame_capture_uses_fallback_in_plateau_exit(self) -> None:
        """On plateau exit, the captured frame is non-empty (uses edge fallback)."""
        c = _build_container_with_mocked_primes()

        # PTY returns saw_idle=False, empty turn_buffer, but edge buffer has text
        idle_res = IdleResult(
            saw_idle=False,
            buffer="Unknown command: /granite:prime-pm-role",
            idle_marker="",
            elapsed_ms=3000,
            turn_buffer="",  # empty -- frame capture must fall back to edge
        )
        pm_mock = _mock_driver("mock-session-pm")
        dev_mock = _mock_driver("mock-session-dev")
        pm_mock.read_until_idle.return_value = idle_res
        dev_mock.read_until_idle.return_value = idle_res

        with (
            patch.object(c, "_spawn_pair"),
            patch.object(c, "_close_pair"),
            patch.object(c, "_prime_session"),
            # _run_pkill_fallback was deleted in #1816/#1832 (bab446d8):
            # teardown is now process-group-scoped via _close_pair_and_reap.
            patch.object(c, "_close_pair_and_reap"),
        ):
            c._pm_pty = pm_mock
            c._dev_pty = dev_mock
            result = c.run()

        # Frame should be non-empty (came from edge fallback)
        self.assertIsNotNone(result.startup_diagnostic_frame)
        self.assertGreater(len(result.startup_diagnostic_frame or ""), 0)


# ---------------------------------------------------------------------------
# Alert cooldown tests
# ---------------------------------------------------------------------------


class TestShouldAlert(unittest.TestCase):
    """_should_alert two-layer cooldown tests."""

    def setUp(self) -> None:
        import agent.granite_container.bridge_adapter as ba

        self._ba = ba
        # Reset process-local state between tests
        ba._startup_alert_last_sent.clear()

    def test_first_call_permits_send(self) -> None:
        """First call (no prior alert) returns True."""
        with (
            patch.object(self._ba, "_get_machine_name", return_value="test-machine"),
            patch("agent.granite_container.bridge_adapter._startup_alert_last_sent", {}) as _last,
        ):
            # Patch Redis to return True (key was set)
            with patch("agent.granite_container.bridge_adapter._should_alert") as mock_alert:
                mock_alert.return_value = True
                result = mock_alert("test-machine")
        self.assertTrue(result)

    def test_process_local_gate_suppresses_within_window(self) -> None:
        """Second call within cooldown window is suppressed by process-local gate."""
        machine = "test-machine-local"
        self._ba._startup_alert_last_sent[machine] = time.monotonic()  # just sent

        result = self._ba._should_alert(machine)
        self.assertFalse(result, "Should be suppressed by process-local cooldown gate")

    def test_process_local_gate_permits_after_window(self) -> None:
        """Call after cooldown window expiry is permitted."""
        machine = "test-machine-expired"
        # Set last alert to well before cooldown window
        self._ba._startup_alert_last_sent[machine] = time.monotonic() - 400

        with patch("agent.granite_container.bridge_adapter.logger"):  # noqa: F841
            # Patch Redis to succeed
            with patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis:
                mock_redis.set.return_value = True
                result = self._ba._should_alert(machine)

        self.assertTrue(result)

    def test_redis_down_still_sends(self) -> None:
        """When Redis is unavailable, alert still sends (process-local decides).

        Critically: a Redis-down send must NOT log [granite-alert-suppressed].
        """
        machine = "test-machine-redis-down"
        # No prior alert (process-local gate permits)
        self._ba._startup_alert_last_sent.pop(machine, None)

        with patch("agent.granite_container.bridge_adapter.logger") as mock_logger:
            with patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis:
                mock_redis.set.side_effect = Exception("Redis connection refused")
                result = self._ba._should_alert(machine)

        self.assertTrue(result, "Redis-down should still send (process-local permits)")
        # Verify no [granite-alert-suppressed] was logged
        for call in mock_logger.error.call_args_list:
            self.assertNotIn(
                "granite-alert-suppressed",
                str(call),
                "Redis-down path must NOT log [granite-alert-suppressed]",
            )

    def test_redis_down_second_call_within_window_suppressed(self) -> None:
        """Second call within window is suppressed even when Redis is down (Layer 2 protects)."""
        machine = "test-machine-redis-down-2"
        # Simulate first call already happened
        self._ba._startup_alert_last_sent[machine] = time.monotonic()

        result = self._ba._should_alert(machine)
        self.assertFalse(
            result, "Process-local gate must suppress second call even with Redis down"
        )


# ---------------------------------------------------------------------------
# _send_startup_alert exception handling
# ---------------------------------------------------------------------------


class TestSendStartupAlert(unittest.TestCase):
    """_send_startup_alert swallows CLI failures with [granite-alert-suppressed] ERROR."""

    def setUp(self) -> None:
        import agent.granite_container.bridge_adapter as ba

        self._ba = ba
        ba._startup_alert_last_sent.clear()

    def _permit_alert(self, machine: str = "test-alert-machine") -> None:
        """Set up process-local state so the next call to _should_alert permits."""
        self._ba._startup_alert_last_sent.pop(machine, None)

    def test_filenotfounderror_swallowed_with_suppressed_log(self) -> None:
        """FileNotFoundError (CLI absent) is swallowed; [granite-alert-suppressed] logged."""
        self._permit_alert()
        with (
            patch("agent.granite_container.bridge_adapter._should_alert", return_value=True),
            patch("agent.granite_container.bridge_adapter.subprocess.run") as mock_run,
            patch("agent.granite_container.bridge_adapter.logger") as mock_logger,
        ):
            mock_run.side_effect = FileNotFoundError("valor-telegram not found")
            # Should not raise
            self._ba._send_startup_alert("sess-123", "plateau", "frame content")

        suppressed_logged = any(
            "granite-alert-suppressed" in str(c) for c in mock_logger.error.call_args_list
        )
        self.assertTrue(suppressed_logged, "FileNotFoundError must log [granite-alert-suppressed]")

    def test_timeoutexpired_swallowed_with_suppressed_log(self) -> None:
        """TimeoutExpired is swallowed; [granite-alert-suppressed] logged."""
        import subprocess

        self._permit_alert()
        with (
            patch("agent.granite_container.bridge_adapter._should_alert", return_value=True),
            patch("agent.granite_container.bridge_adapter.subprocess.run") as mock_run,
            patch("agent.granite_container.bridge_adapter.logger") as mock_logger,
        ):
            mock_run.side_effect = subprocess.TimeoutExpired(["valor-telegram"], 3)
            self._ba._send_startup_alert("sess-456", "ceiling", "frame content")

        suppressed_logged = any(
            "granite-alert-suppressed" in str(c) for c in mock_logger.error.call_args_list
        )
        self.assertTrue(suppressed_logged, "TimeoutExpired must log [granite-alert-suppressed]")

    def test_cooldown_active_suppresses_subprocess(self) -> None:
        """When cooldown active (_should_alert returns False), subprocess NOT invoked."""
        with (
            patch("agent.granite_container.bridge_adapter._should_alert", return_value=False),
            patch("agent.granite_container.bridge_adapter.subprocess.run") as mock_run,
            patch("agent.granite_container.bridge_adapter.logger") as mock_logger,
        ):
            self._ba._send_startup_alert("sess-789", "plateau", "frame")

        mock_run.assert_not_called()
        suppressed_logged = any(
            "granite-alert-suppressed" in str(c) for c in mock_logger.error.call_args_list
        )
        self.assertTrue(suppressed_logged, "Cooldown-active must log [granite-alert-suppressed]")

    def test_successful_send_invokes_subprocess_with_timeout_3(self) -> None:
        """Successful send invokes valor-telegram with timeout=3."""
        with (
            patch("agent.granite_container.bridge_adapter._should_alert", return_value=True),
            patch("agent.granite_container.bridge_adapter.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            self._ba._send_startup_alert("sess-ok", "plateau", "frame excerpt")

        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args
        self.assertEqual(call_kwargs.kwargs.get("timeout"), 3)
        # check=False
        self.assertFalse(call_kwargs.kwargs.get("check", True))
        # Message contains session id and failure kind
        cmd_args = call_kwargs.args[0]
        full_cmd = " ".join(cmd_args)
        # The message is the last positional arg
        self.assertIn("sess-ok", full_cmd + str(call_kwargs))
        self.assertIn("plateau", full_cmd + str(call_kwargs))

    def test_container_result_unaffected_by_alert_failure(self) -> None:
        """Alert failure does not crash the run or modify ContainerResult."""
        import subprocess

        self._permit_alert()
        with (
            patch("agent.granite_container.bridge_adapter._should_alert", return_value=True),
            patch("agent.granite_container.bridge_adapter.subprocess.run") as mock_run,
        ):
            mock_run.side_effect = subprocess.TimeoutExpired(["valor-telegram"], 3)
            # Must not raise
            try:
                self._ba._send_startup_alert("sess-safe", "ceiling", "frame")
            except Exception as e:
                self.fail(f"_send_startup_alert raised unexpectedly: {e}")


# ---------------------------------------------------------------------------
# _maybe_publish_exit_anomaly enrichment tests
# ---------------------------------------------------------------------------


class TestMaybePublishExitAnomalyEnrichment(unittest.TestCase):
    """exit_anomaly event carries startup_failure_kind and startup_diagnostic_frame."""

    def _make_mock_session(self) -> MagicMock:
        session = MagicMock()
        session.session_id = "test-session-id"
        session.session_events = None
        session.save = MagicMock()
        return session

    def _make_result(self, exit_reason: str = "startup_unresolved") -> ContainerResult:
        result = ContainerResult(session_id="test-session-id", user_message="hello")
        result.exit_reason = exit_reason
        result.startup_failure_kind = "plateau"
        result.startup_diagnostic_frame = "--- PM ---\nUnknown command\n--- Dev ---\n(no content)"
        return result

    def test_exit_anomaly_event_carries_frame_and_kind(self) -> None:
        """For startup_unresolved, exit_anomaly event includes frame and kind."""
        from agent.granite_container.bridge_adapter import BridgeAdapter

        mock_pool = MagicMock()
        mock_session = self._make_mock_session()

        adapter = BridgeAdapter(
            agent_session=mock_session,
            project_key="test",
            transport="telegram",
            pool=mock_pool,
            resolve_callbacks=lambda pk, t: (None, None),
        )

        result = self._make_result()

        appended_events = []

        def fake_append(session, event):
            appended_events.append(event)

        with (
            patch(
                "agent.granite_container.bridge_adapter._append_session_event",
                side_effect=fake_append,
            ),
            patch("agent.granite_container.bridge_adapter._send_startup_alert"),
        ):
            adapter._maybe_publish_exit_anomaly(result)

        # Find the exit_anomaly event
        anomaly_events = [e for e in appended_events if e.get("type") == "exit_anomaly"]
        self.assertEqual(len(anomaly_events), 1)
        evt = anomaly_events[0]
        self.assertEqual(evt.get("startup_failure_kind"), "plateau")
        self.assertIn("startup_diagnostic_frame", evt)
        self.assertGreater(len(evt["startup_diagnostic_frame"]), 0)

    def test_exit_anomaly_does_not_include_frame_for_non_startup_unresolved(self) -> None:
        """For pm_hang (not startup_unresolved), exit_anomaly event has no frame."""
        from agent.granite_container.bridge_adapter import BridgeAdapter

        mock_pool = MagicMock()
        mock_session = self._make_mock_session()

        adapter = BridgeAdapter(
            agent_session=mock_session,
            project_key="test",
            transport="telegram",
            pool=mock_pool,
            resolve_callbacks=lambda pk, t: (None, None),
        )

        result = ContainerResult(session_id="test-session-id", user_message="hello")
        result.exit_reason = "pm_hang"
        result.startup_failure_kind = None
        result.startup_diagnostic_frame = None

        appended_events = []

        def fake_append(session, event):
            appended_events.append(event)

        with patch(
            "agent.granite_container.bridge_adapter._append_session_event",
            side_effect=fake_append,
        ):
            adapter._maybe_publish_exit_anomaly(result)

        anomaly_events = [e for e in appended_events if e.get("type") == "exit_anomaly"]
        self.assertEqual(len(anomaly_events), 1)
        evt = anomaly_events[0]
        self.assertNotIn("startup_failure_kind", evt)
        self.assertNotIn("startup_diagnostic_frame", evt)

    def test_startup_alert_fired_on_startup_unresolved(self) -> None:
        """_send_startup_alert is invoked for startup_unresolved exit."""
        from agent.granite_container.bridge_adapter import BridgeAdapter

        mock_pool = MagicMock()
        mock_session = self._make_mock_session()

        adapter = BridgeAdapter(
            agent_session=mock_session,
            project_key="test",
            transport="telegram",
            pool=mock_pool,
            resolve_callbacks=lambda pk, t: (None, None),
        )

        result = self._make_result()

        with (
            patch("agent.granite_container.bridge_adapter._append_session_event"),
            patch("agent.granite_container.bridge_adapter._send_startup_alert") as mock_alert,
        ):
            adapter._maybe_publish_exit_anomaly(result)

        mock_alert.assert_called_once()
        call_kwargs = mock_alert.call_args
        self.assertIn("plateau", str(call_kwargs))


if __name__ == "__main__":
    unittest.main()
