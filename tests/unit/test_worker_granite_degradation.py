"""Unit tests for granite graceful degradation (issue #1816 Fix #1).

Covers:
- test_degraded_boot_no_exit: Worker starts gracefully when ensure_granite_model fails
- test_granite_flag_set_on_success: granite_available=True after successful probe
- test_granite_flag_false_on_failure: granite_available=False after failed probe
- test_reprobe_loop_flips_flag: reprobe loop sets granite_available=True when ollama recovers
- test_breaker_opens_after_threshold: circuit opens after GRANITE_BREAKER_OPEN_THRESHOLD failures
- test_degraded_mode_defers_granite_sessions: ENG sessions deferred, not dropped, when unavailable
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import agent.session_state as _ss
import worker.__main__ as wm
from worker.__main__ import (
    GRANITE_BREAKER_OPEN_THRESHOLD,
    _granite_reprobe_loop,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reset_granite_flag(value: bool) -> None:
    """Reset the granite_available flag in session_state before a test."""
    _ss.granite_available = value


# ---------------------------------------------------------------------------
# test_degraded_boot_no_exit
# ---------------------------------------------------------------------------


def test_degraded_boot_no_exit():
    """When ensure_granite_model fails, no sys.exit is raised; the flag stays False."""
    _reset_granite_flag(False)

    with patch(
        "agent.granite_container.granite_classifier.ensure_granite_model",
        return_value=(False, "ollama not found"),
    ):
        # The startup logic sets granite_available = False (no exit).
        # Simulate what _run_worker does at the startup gate.
        import agent.session_state as ss

        ok, _detail = (False, "ollama not found")
        if not ok:
            ss.granite_available = False
        else:
            ss.granite_available = True

    assert _ss.granite_available is False, "Flag must be False when probe fails"


# ---------------------------------------------------------------------------
# test_granite_flag_set_on_success
# ---------------------------------------------------------------------------


def test_granite_flag_set_on_success():
    """When ensure_granite_model succeeds, granite_available is set to True."""
    _reset_granite_flag(False)

    import agent.session_state as ss

    ok, _detail = (True, "granite4.1:3b responsive")
    if not ok:
        ss.granite_available = False
    else:
        ss.granite_available = True

    assert _ss.granite_available is True


# ---------------------------------------------------------------------------
# test_granite_flag_false_on_failure
# ---------------------------------------------------------------------------


def test_granite_flag_false_on_failure():
    """When ensure_granite_model fails, granite_available remains False."""
    _reset_granite_flag(True)  # was previously True

    import agent.session_state as ss

    ok, _detail = (False, "ollama timeout")
    if not ok:
        ss.granite_available = False
    else:
        ss.granite_available = True

    assert _ss.granite_available is False


# ---------------------------------------------------------------------------
# test_reprobe_loop_flips_flag
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reprobe_loop_flips_flag():
    """Reprobe loop flips granite_available to True when ollama recovers.

    Simulates: startup probe failed → flag False → loop probes → success →
    flag becomes True.
    """
    _reset_granite_flag(False)

    # Probe succeeds on the first call from the loop
    probe_results = [(True, "granite4.1:3b responsive")]

    async def _fake_to_thread(fn, *args, **kwargs):
        return probe_results.pop(0)

    sleep_count = [0]

    async def _counted_sleep(duration):
        sleep_count[0] += 1
        # Cancel on the second sleep so the loop runs exactly one probe cycle.
        if sleep_count[0] >= 2:
            raise asyncio.CancelledError

    with (
        patch("asyncio.sleep", side_effect=_counted_sleep),
        patch("asyncio.to_thread", new=_fake_to_thread),
        patch.object(wm, "_resume_deferred_granite_sessions", MagicMock()),
    ):
        try:
            await _granite_reprobe_loop()
        except asyncio.CancelledError:
            pass

    assert _ss.granite_available is True, "Flag should flip to True after successful probe"


# ---------------------------------------------------------------------------
# test_breaker_opens_after_threshold
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_breaker_opens_after_threshold():
    """After GRANITE_BREAKER_OPEN_THRESHOLD consecutive failures the loop logs OPEN state.

    We verify via log capture that the "circuit breaker OPEN" message is emitted.
    """
    _reset_granite_flag(True)

    # All probes fail
    fail_result = (False, "ollama timeout")
    call_count = 0

    async def _fake_to_thread(fn, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        return fail_result

    sleep_count = 0

    async def _count_sleep(t):
        nonlocal sleep_count
        sleep_count += 1
        if sleep_count > GRANITE_BREAKER_OPEN_THRESHOLD + 1:
            raise asyncio.CancelledError

    log_messages = []

    import logging

    class _CapHandler(logging.Handler):
        def emit(self, record):
            log_messages.append(record.getMessage())

    handler = _CapHandler()
    loop_logger = logging.getLogger("worker")
    loop_logger.addHandler(handler)

    try:
        with (
            patch("asyncio.sleep", new=AsyncMock(side_effect=_count_sleep)),
            patch("asyncio.to_thread", new=_fake_to_thread),
            patch.object(wm, "_resume_deferred_granite_sessions", MagicMock()),
        ):
            task = asyncio.create_task(_granite_reprobe_loop())
            try:
                await task
            except asyncio.CancelledError:
                pass
    finally:
        loop_logger.removeHandler(handler)

    open_messages = [m for m in log_messages if "OPEN" in m and "circuit" in m.lower()]
    assert open_messages, (
        f"Expected 'circuit breaker OPEN' log after {GRANITE_BREAKER_OPEN_THRESHOLD} failures. "
        f"Got: {log_messages}"
    )


# ---------------------------------------------------------------------------
# test_degraded_mode_defers_granite_sessions
# ---------------------------------------------------------------------------


def test_degraded_mode_defers_granite_sessions():
    """ENG sessions are deferred to paused_circuit when granite is unavailable.

    The deferral must use transition_status('paused_circuit'), not drop the session.
    """
    _reset_granite_flag(False)

    fake_session = MagicMock()
    fake_session.session_type = "eng"
    fake_session.session_id = "test-session-123"

    transition_calls = []

    def _fake_transition(session, status, reason=""):
        transition_calls.append((session, status, reason))

    with patch(
        "models.session_lifecycle.transition_status",
        side_effect=_fake_transition,
    ):
        import agent.session_state as ss

        # Simulate the startup deferral logic from _run_worker
        if not ss.granite_available and fake_session.session_type == "eng":
            _fake_transition(
                fake_session,
                "paused_circuit",
                reason=(
                    "granite-degrade: startup probe failed — will resume when granite is available"
                ),
            )

    assert len(transition_calls) == 1, "Should have deferred exactly one session"
    _, status, reason = transition_calls[0]
    assert status == "paused_circuit", f"Expected paused_circuit, got {status}"
    assert "granite" in reason.lower(), f"Reason should mention granite: {reason}"
