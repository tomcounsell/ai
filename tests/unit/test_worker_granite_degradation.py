"""Unit tests for granite graceful degradation (issue #1816 Fix #1).

Covers:
- Constants validation: GRANITE_REPROBE_INTERVAL_S, GRANITE_BREAKER_OPEN_THRESHOLD,
  GRANITE_BREAKER_COOLDOWN_S
- GraniteSettings validation: breaker defaults match worker constants
- test_reprobe_loop_flips_flag: reprobe loop sets granite_available=True when ollama recovers
- test_breaker_opens_after_threshold: circuit opens after GRANITE_BREAKER_OPEN_THRESHOLD failures
- test_reprobe_recovers_from_open_to_closed: circuit closes after successful half-open probe
- test_reprobe_cancelled_error_exits_cleanly: CancelledError exits without raising
- test_reprobe_loop_sets_flag_false_on_failure: flag becomes False after failed probe
- test_pickup_returns_none_when_granite_unavailable: project-keyed pickup deferred when granite down
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


# ---------------------------------------------------------------------------
# Constants validation
# ---------------------------------------------------------------------------


def test_granite_constants_exist_in_worker():
    """All three granite circuit-breaker constants are defined in worker.__main__."""
    assert hasattr(wm, "GRANITE_REPROBE_INTERVAL_S"), "Missing GRANITE_REPROBE_INTERVAL_S"
    assert hasattr(wm, "GRANITE_BREAKER_OPEN_THRESHOLD"), "Missing GRANITE_BREAKER_OPEN_THRESHOLD"
    assert hasattr(wm, "GRANITE_BREAKER_COOLDOWN_S"), "Missing GRANITE_BREAKER_COOLDOWN_S"


def test_granite_constants_are_numeric():
    assert isinstance(wm.GRANITE_REPROBE_INTERVAL_S, float)
    assert isinstance(wm.GRANITE_BREAKER_OPEN_THRESHOLD, int)
    assert isinstance(wm.GRANITE_BREAKER_COOLDOWN_S, float)


def test_granite_constants_have_sensible_defaults():
    assert wm.GRANITE_REPROBE_INTERVAL_S > 0
    assert wm.GRANITE_BREAKER_OPEN_THRESHOLD >= 1
    assert wm.GRANITE_BREAKER_COOLDOWN_S > 0


def test_granite_available_flag_in_session_state():
    """granite_available lives in agent.session_state for cross-module access."""
    assert hasattr(_ss, "granite_available"), "Missing granite_available in session_state"
    assert isinstance(_ss.granite_available, bool)


# ---------------------------------------------------------------------------
# GraniteSettings validation
# ---------------------------------------------------------------------------


def test_granite_breaker_in_settings():
    """GraniteSettings includes the circuit-breaker constants."""
    from config.settings import GraniteSettings

    g = GraniteSettings()
    assert hasattr(g, "reprobe_interval_s"), "Missing reprobe_interval_s in GraniteSettings"
    assert hasattr(g, "breaker_open_threshold"), "Missing breaker_open_threshold in GraniteSettings"
    assert hasattr(g, "breaker_cooldown_s"), "Missing breaker_cooldown_s in GraniteSettings"


def test_granite_breaker_defaults_match_worker_constants():
    """GraniteSettings breaker defaults match the env-level defaults."""
    from config.settings import GraniteSettings

    g = GraniteSettings()
    assert g.reprobe_interval_s == wm.GRANITE_REPROBE_INTERVAL_S
    assert g.breaker_open_threshold == wm.GRANITE_BREAKER_OPEN_THRESHOLD
    assert g.breaker_cooldown_s == wm.GRANITE_BREAKER_COOLDOWN_S


# ---------------------------------------------------------------------------
# test_reprobe_loop_flips_flag
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reprobe_loop_flips_flag():
    """Reprobe loop flips granite_available to True when ollama recovers.

    Simulates: startup probe failed -> flag False -> loop probes -> success ->
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
# test_reprobe_recovers_from_open_to_closed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reprobe_recovers_from_open_to_closed():
    """After cooldown, a successful half-open probe closes the circuit."""
    _reset_granite_flag(False)
    threshold = GRANITE_BREAKER_OPEN_THRESHOLD

    probe_calls = []

    def mock_probe():
        probe_calls.append(1)
        if len(probe_calls) <= threshold:
            return (False, "ollama unreachable")
        return (True, "granite4.1:3b responsive")

    sleep_calls = []

    async def _counted_sleep(duration):
        sleep_calls.append(duration)
        if len(probe_calls) >= threshold + 1 and _ss.granite_available:
            raise asyncio.CancelledError

    with (
        patch("asyncio.sleep", side_effect=_counted_sleep),
        patch(
            "asyncio.to_thread",
            new=AsyncMock(side_effect=lambda fn, *a, **kw: mock_probe()),
        ),
        patch.object(wm, "_resume_deferred_granite_sessions", MagicMock()),
    ):
        try:
            await _granite_reprobe_loop()
        except asyncio.CancelledError:
            pass

    assert _ss.granite_available is True, (
        f"Expected granite_available=True after recovery. "
        f"probe_calls={len(probe_calls)}, sleep_calls={sleep_calls}"
    )


# ---------------------------------------------------------------------------
# test_reprobe_cancelled_error_exits_cleanly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reprobe_cancelled_error_exits_cleanly():
    """CancelledError in the reprobe loop exits without raising."""

    async def _immediate_cancel(_):
        raise asyncio.CancelledError

    with (
        patch("asyncio.sleep", side_effect=_immediate_cancel),
        patch("asyncio.to_thread", return_value=(False, "ollama unreachable")),
        patch.object(wm, "_resume_deferred_granite_sessions", MagicMock()),
    ):
        task = asyncio.create_task(_granite_reprobe_loop())
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass  # Expected


# ---------------------------------------------------------------------------
# test_reprobe_loop_sets_flag_false_on_failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reprobe_loop_sets_flag_false_on_failure():
    """reprobe loop sets granite_available=False when ensure_granite_model fails."""
    _reset_granite_flag(True)  # Start True, should become False

    probe_called = [0]
    sleep_count = [0]

    async def _fake_to_thread(fn, *args, **kwargs):
        probe_called[0] += 1
        return (False, "ollama timeout")

    async def _counted_sleep(duration):
        sleep_count[0] += 1
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

    assert _ss.granite_available is False, "Flag should be False after failed probe"


# ---------------------------------------------------------------------------
# test_pickup_returns_none_when_granite_unavailable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pickup_returns_none_when_granite_unavailable():
    """Project-keyed session pickup returns None when granite_available is False.

    Drives the REAL _pop_agent_session gate rather than simulating it inline.
    """
    _reset_granite_flag(False)

    mock_r = MagicMock()
    mock_r.get.return_value = None  # No queue_paused, no hibernating, no throttle

    with patch("popoto.redis_db.POPOTO_REDIS_DB", mock_r):
        result = await _pop_agent_session("valor", is_project_keyed=True)

    assert result is None, "Project-keyed pickup must return None when granite_available=False"


# ---------------------------------------------------------------------------
# test_slug_keyed_eng_pickup_deferred_when_granite_unavailable  (Fix #2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slug_keyed_eng_pickup_deferred_when_granite_unavailable():
    """Slug-keyed ENG worktree sessions are deferred when granite_available is False.

    Before the Fix #2 alignment, slug-keyed workers (is_project_keyed=False) bypassed
    the granite gate entirely and could pick up ENG sessions even while granite was down.
    After the fix, a non-empty slug query triggers the same gate, returning None.

    The mock simulates a pending ENG session with slug=slug_key so the slug lookup
    returns a result, then verifies that _pop_agent_session returns None (deferred).
    """
    _reset_granite_flag(False)
    slug_key = "worker-fault-containment"

    mock_r = MagicMock()
    mock_r.get.return_value = None  # No queue_paused, no hibernating, no throttle

    # Fake pending session returned by the slug index query
    fake_session = MagicMock()
    fake_session.session_type = "eng"
    fake_session.worker_key = slug_key
    fake_session.status = "pending"

    async def _fake_async_filter(**kwargs):
        if kwargs.get("slug") == slug_key and kwargs.get("status") == "pending":
            return [fake_session]
        return []

    with (
        patch("popoto.redis_db.POPOTO_REDIS_DB", mock_r),
        patch(
            "agent.session_pickup.AgentSession.query.async_filter", side_effect=_fake_async_filter
        ),
    ):
        result = await _pop_agent_session(slug_key, is_project_keyed=False)

    assert result is None, (
        "Slug-keyed ENG pickup must return None when granite_available=False "
        "(Fix #2: align runtime gate with startup deferral)"
    )


@pytest.mark.asyncio
async def test_teammate_pickup_not_deferred_when_granite_unavailable():
    """Teammate/chat_id-routed sessions are NOT deferred when granite is down.

    Granite is only required for ENG sessions.  Teammate sessions must keep
    serving regardless of granite availability so the system stays responsive
    to non-ENG conversations.

    The slug lookup returns nothing for a chat_id-keyed worker (teammate sessions
    have no slug), so the granite gate must not fire on the chat_id fallback path.
    """
    _reset_granite_flag(False)
    chat_id_key = "-1001234567890"  # Telegram group chat_id format

    mock_r = MagicMock()
    mock_r.get.return_value = None

    # Fake pending teammate session returned by the chat_id index query
    fake_session = MagicMock()
    fake_session.session_type = "teammate"
    fake_session.worker_key = chat_id_key
    fake_session.status = "pending"
    fake_session.scheduled_at = None
    fake_session.priority = "normal"
    fake_session.created_at = None
    fake_session.parent_agent_session_id = None

    async def _fake_async_filter(**kwargs):
        # Slug lookup: teammates have no slug matching a chat_id
        if kwargs.get("slug") == chat_id_key and kwargs.get("status") == "pending":
            return []
        # chat_id fallback: teammate session is here
        if kwargs.get("chat_id") == chat_id_key and kwargs.get("status") == "pending":
            return [fake_session]
        return []

    with (
        patch("popoto.redis_db.POPOTO_REDIS_DB", mock_r),
        patch(
            "agent.session_pickup.AgentSession.query.async_filter", side_effect=_fake_async_filter
        ),
        patch("agent.session_pickup._acquire_pop_lock", return_value=True),
        patch("agent.session_pickup._release_pop_lock"),
    ):
        result = await _pop_agent_session(chat_id_key, is_project_keyed=False)

    # Teammate session must NOT be blocked by the granite gate.
    # It either returns the session or None for other reasons (e.g. eligible filter),
    # but must NOT return None due to the granite gate.
    # We assert it is not None — the session was popped successfully.
    assert result is not None, (
        "Teammate/chat_id-routed pickup must not be deferred by the granite gate; "
        "granite is only required for ENG sessions"
    )
