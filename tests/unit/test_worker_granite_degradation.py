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
from agent.session_pickup import _pop_agent_session
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


async def _run_loop_one_cycle(probe_result: tuple) -> None:
    """Run _granite_reprobe_loop through exactly one probe cycle then cancel."""
    sleep_count = [0]

    async def _counted_sleep(duration):
        sleep_count[0] += 1
        if sleep_count[0] >= 2:
            raise asyncio.CancelledError

    async def _fake_to_thread(fn, *args, **kwargs):
        return probe_result

    with (
        patch("asyncio.sleep", side_effect=_counted_sleep),
        patch("asyncio.to_thread", new=_fake_to_thread),
        patch.object(wm, "_resume_deferred_granite_sessions", MagicMock()),
    ):
        try:
            await _granite_reprobe_loop()
        except asyncio.CancelledError:
            pass


# ---------------------------------------------------------------------------
# test_degraded_boot_no_exit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_degraded_boot_no_exit():
    """When ensure_granite_model fails, the reprobe loop handles it without crashing.

    Calls _granite_reprobe_loop directly with a failing probe — verifies the loop
    exits via CancelledError (no sys.exit, no unhandled exception) and
    granite_available stays False.
    """
    _reset_granite_flag(False)
    await _run_loop_one_cycle((False, "ollama not found"))
    assert _ss.granite_available is False, "Flag must stay False when probe fails"


# ---------------------------------------------------------------------------
# test_granite_flag_set_on_success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_granite_flag_set_on_success():
    """When ensure_granite_model succeeds, _granite_reprobe_loop sets granite_available=True."""
    _reset_granite_flag(False)
    await _run_loop_one_cycle((True, "granite4.1:3b responsive"))
    assert _ss.granite_available is True, "Flag must be True after successful probe"


# ---------------------------------------------------------------------------
# test_granite_flag_false_on_failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_granite_flag_false_on_failure():
    """When ensure_granite_model fails, _granite_reprobe_loop sets granite_available=False."""
    _reset_granite_flag(True)  # was previously True
    await _run_loop_one_cycle((False, "ollama timeout"))
    assert _ss.granite_available is False, "Flag must be False after failed probe"


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


@pytest.mark.asyncio
async def test_degraded_mode_defers_granite_sessions():
    """_pop_agent_session returns None for project-keyed workers when granite is unavailable.

    Calls _pop_agent_session directly with is_project_keyed=True and granite_available=False.
    The granite gate short-circuits before any Redis query (Redis fails open in unit tests),
    so no session is picked up — it stays in the queue until granite recovers.
    """

    _reset_granite_flag(False)

    # Redis is unavailable in unit tests; the sustainability guard fails open,
    # allowing execution to reach the granite gate (is_project_keyed=True check).
    result = await _pop_agent_session("valor", is_project_keyed=True)

    assert result is None, (
        "Expected None — project-keyed session pickup must be deferred when granite is down"
    )
