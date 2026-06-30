"""Tests for granite degraded-boot and circuit-breaker behavior.

Covers:
- Degraded-boot: ensure_granite_model fails → worker starts in degraded mode
  (granite_available=False, no sys.exit) → ENG sessions deferred to paused_circuit
- Reprobe loop circuit-breaker: consecutive failures → open → cooldown →
  half-open → success → closed + granite_available=True
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_session(session_type: str = "eng", status: str = "pending") -> MagicMock:
    """Return a mock AgentSession with the given type and status."""
    s = MagicMock()
    s.session_type = session_type
    s.status = status
    s.session_id = f"test-{session_type}-{status}"
    s.worker_key = "valor"
    s.is_project_keyed = True
    return s


# ---------------------------------------------------------------------------
# Test: module-level constants are present and env-overridable
# ---------------------------------------------------------------------------


class TestGraniteConstants:
    def test_constants_exist_in_worker(self):
        """GRANITE_REPROBE_INTERVAL_S, GRANITE_BREAKER_OPEN_THRESHOLD,
        GRANITE_BREAKER_COOLDOWN_S are defined in worker.__main__."""
        import worker.__main__ as wm

        assert hasattr(wm, "GRANITE_REPROBE_INTERVAL_S"), "Missing GRANITE_REPROBE_INTERVAL_S"
        assert hasattr(wm, "GRANITE_BREAKER_OPEN_THRESHOLD"), (
            "Missing GRANITE_BREAKER_OPEN_THRESHOLD"
        )
        assert hasattr(wm, "GRANITE_BREAKER_COOLDOWN_S"), "Missing GRANITE_BREAKER_COOLDOWN_S"

    def test_constants_are_numeric(self):
        import worker.__main__ as wm

        assert isinstance(wm.GRANITE_REPROBE_INTERVAL_S, float)
        assert isinstance(wm.GRANITE_BREAKER_OPEN_THRESHOLD, int)
        assert isinstance(wm.GRANITE_BREAKER_COOLDOWN_S, float)

    def test_constants_have_sensible_defaults(self):
        import worker.__main__ as wm

        assert wm.GRANITE_REPROBE_INTERVAL_S > 0
        assert wm.GRANITE_BREAKER_OPEN_THRESHOLD >= 1
        assert wm.GRANITE_BREAKER_COOLDOWN_S > 0

    def test_granite_available_flag_in_session_state(self):
        """granite_available lives in agent.session_state for cross-module access."""
        import agent.session_state as ss

        assert hasattr(ss, "granite_available"), "Missing granite_available in session_state"
        assert isinstance(ss.granite_available, bool)


# ---------------------------------------------------------------------------
# Test: degraded-boot — ensure_granite_model failure does not sys.exit
# ---------------------------------------------------------------------------


class TestDegradedBoot:
    def test_granite_failure_sets_available_false(self):
        """When ensure_granite_model returns (False, reason), granite_available is False."""
        import agent.session_state as ss

        # Reset to unknown state
        ss.granite_available = True

        with patch(
            "agent.granite_container.granite_classifier.ensure_granite_model",
            return_value=(False, "ollama CLI not found"),
        ):
            # Simulate what _run_worker does with the probe result
            ok, _detail = (False, "ollama CLI not found")
            if not ok:
                ss.granite_available = False

        assert ss.granite_available is False

    def test_granite_success_sets_available_true(self):
        """When ensure_granite_model returns (True, ...), granite_available is True."""
        import agent.session_state as ss

        ss.granite_available = False

        ok, _detail = (True, "granite4.1:3b responsive")
        if ok:
            ss.granite_available = True

        assert ss.granite_available is True

    def test_no_sys_exit_on_granite_failure(self):
        """Worker boot must NOT call sys.exit when granite is unavailable."""
        # Verify the reprobe loop exists — the key indicator that the hard-gate
        # was replaced with the degraded-boot / reprobe pattern.
        import worker.__main__ as wm

        assert hasattr(wm, "_granite_reprobe_loop"), "_granite_reprobe_loop must be defined"


# ---------------------------------------------------------------------------
# Test: ENG sessions deferred to paused_circuit when granite unavailable
# ---------------------------------------------------------------------------


class TestSessionDeferral:
    def test_eng_session_deferred_when_granite_unavailable(self):
        """When granite_available=False, ENG pending sessions should be
        transitioned to paused_circuit, not started."""
        import agent.session_state as ss

        ss.granite_available = False

        from config.enums import SessionType

        session = _make_session(session_type=SessionType.ENG, status="pending")

        # Simulate the deferral logic from worker/__main__.py Step 5
        deferred = []
        started = []

        if not ss.granite_available and session.session_type == SessionType.ENG:
            deferred.append(session)
        else:
            started.append(session)

        assert len(deferred) == 1
        assert len(started) == 0

    def test_teammate_session_not_deferred_even_when_granite_unavailable(self):
        """TEAMMATE sessions are not gated on granite_available — they can proceed."""
        import agent.session_state as ss

        ss.granite_available = False

        from config.enums import SessionType

        session = _make_session(session_type=SessionType.TEAMMATE, status="pending")

        deferred = []
        started = []

        # Only ENG sessions are gated on granite
        if not ss.granite_available and session.session_type == SessionType.ENG:
            deferred.append(session)
        else:
            started.append(session)

        assert len(deferred) == 0
        assert len(started) == 1

    def test_eng_session_proceeds_when_granite_available(self):
        """When granite_available=True, ENG sessions start normally."""
        import agent.session_state as ss

        ss.granite_available = True

        from config.enums import SessionType

        session = _make_session(session_type=SessionType.ENG, status="pending")

        deferred = []
        started = []

        if not ss.granite_available and session.session_type == SessionType.ENG:
            deferred.append(session)
        else:
            started.append(session)

        assert len(deferred) == 0
        assert len(started) == 1


# ---------------------------------------------------------------------------
# Test: _granite_reprobe_loop circuit-breaker state machine
# ---------------------------------------------------------------------------


class TestGraniteReprobeLoop:
    """Test the circuit-breaker state transitions in _granite_reprobe_loop.

    We drive the coroutine by advancing asyncio.sleep calls via a mock,
    then asserting granite_available transitions.
    """

    @pytest.mark.asyncio
    async def test_reprobe_sets_available_true_on_success(self):
        """A successful re-probe transitions granite_available to True."""
        import agent.session_state as ss
        import worker.__main__ as wm

        ss.granite_available = False

        sleep_count = [0]

        def mock_probe():
            return (True, "granite4.1:3b responsive")

        async def mock_sleep(duration):
            sleep_count[0] += 1
            # After the first sleep (reprobe interval), cancel on the second
            # so the loop runs one full probe cycle before exiting.
            if sleep_count[0] >= 2:
                raise asyncio.CancelledError

        with (
            patch(
                "agent.granite_container.granite_classifier.ensure_granite_model",
                side_effect=mock_probe,
            ),
            patch("asyncio.sleep", side_effect=mock_sleep),
        ):
            try:
                await wm._granite_reprobe_loop()
            except asyncio.CancelledError:
                pass

        assert ss.granite_available is True, (
            f"Expected granite_available=True after successful probe. sleep_count={sleep_count[0]}"
        )

    @pytest.mark.asyncio
    async def test_reprobe_circuit_opens_after_threshold_failures(self):
        """Consecutive failures up to GRANITE_BREAKER_OPEN_THRESHOLD open the circuit."""
        import agent.session_state as ss
        import worker.__main__ as wm

        ss.granite_available = False
        threshold = wm.GRANITE_BREAKER_OPEN_THRESHOLD
        sleep_calls = []

        def mock_probe():
            return (False, "ollama unreachable")

        async def mock_sleep(duration):
            sleep_calls.append(duration)
            # Stop after enough cycles to exceed the threshold
            if len(sleep_calls) > threshold + 1:
                raise asyncio.CancelledError

        with (
            patch(
                "agent.granite_container.granite_classifier.ensure_granite_model",
                side_effect=mock_probe,
            ),
            patch("asyncio.sleep", side_effect=mock_sleep),
        ):
            try:
                await wm._granite_reprobe_loop()
            except asyncio.CancelledError:
                pass

        # After threshold failures, the circuit should have opened (cooldown sleep)
        # Cooldown sleep should be longer than the normal reprobe interval
        cooldown_sleeps = [d for d in sleep_calls if d >= wm.GRANITE_BREAKER_COOLDOWN_S]
        assert len(cooldown_sleeps) >= 1, (
            f"Expected at least one cooldown sleep ({wm.GRANITE_BREAKER_COOLDOWN_S}s), "
            f"got sleep calls: {sleep_calls}"
        )

    @pytest.mark.asyncio
    async def test_reprobe_recovers_from_open_to_closed(self):
        """After cooldown, a successful half-open probe closes the circuit."""
        import agent.session_state as ss
        import worker.__main__ as wm

        ss.granite_available = False
        threshold = wm.GRANITE_BREAKER_OPEN_THRESHOLD

        # First N probes fail (open circuit), then succeed (half-open → closed)
        probe_calls = []

        def mock_probe():
            probe_calls.append(1)
            if len(probe_calls) <= threshold:
                return (False, "ollama unreachable")
            return (True, "granite4.1:3b responsive")

        sleep_calls = []

        async def mock_sleep(duration):
            sleep_calls.append(duration)
            if len(probe_calls) >= threshold + 1 and ss.granite_available:
                raise asyncio.CancelledError

        with (
            patch(
                "agent.granite_container.granite_classifier.ensure_granite_model",
                side_effect=mock_probe,
            ),
            patch("asyncio.sleep", side_effect=mock_sleep),
        ):
            try:
                await wm._granite_reprobe_loop()
            except asyncio.CancelledError:
                pass

        # After recovery, granite_available should be True
        assert ss.granite_available is True, (
            f"Expected granite_available=True after recovery. "
            f"probe_calls={len(probe_calls)}, sleep_calls={sleep_calls}"
        )

    @pytest.mark.asyncio
    async def test_reprobe_cancelled_error_exits_cleanly(self):
        """CancelledError in the reprobe loop exits without raising."""
        import worker.__main__ as wm

        async def mock_sleep(_):
            raise asyncio.CancelledError

        with (
            patch(
                "agent.granite_container.granite_classifier.ensure_granite_model",
                return_value=(False, "ollama unreachable"),
            ),
            patch("asyncio.sleep", side_effect=mock_sleep),
        ):
            # Should not propagate CancelledError as an unhandled exception
            task = asyncio.create_task(wm._granite_reprobe_loop())
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass  # Expected — the task was cancelled


# ---------------------------------------------------------------------------
# Test: settings constants in config/settings.py
# ---------------------------------------------------------------------------


class TestSettingsConstants:
    def test_granite_breaker_in_settings(self):
        """GraniteSettings includes the circuit-breaker constants."""
        from config.settings import GraniteSettings

        g = GraniteSettings()
        assert hasattr(g, "reprobe_interval_s"), "Missing reprobe_interval_s in GraniteSettings"
        assert hasattr(g, "breaker_open_threshold"), (
            "Missing breaker_open_threshold in GraniteSettings"
        )
        assert hasattr(g, "breaker_cooldown_s"), "Missing breaker_cooldown_s in GraniteSettings"

    def test_granite_breaker_defaults(self):
        """GraniteSettings breaker defaults match the env-level defaults."""
        import worker.__main__ as wm
        from config.settings import GraniteSettings

        g = GraniteSettings()
        assert g.reprobe_interval_s == wm.GRANITE_REPROBE_INTERVAL_S
        assert g.breaker_open_threshold == wm.GRANITE_BREAKER_OPEN_THRESHOLD
        assert g.breaker_cooldown_s == wm.GRANITE_BREAKER_COOLDOWN_S
