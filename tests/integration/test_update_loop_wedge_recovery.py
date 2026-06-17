"""Integration tests for the update-loop wedged detector.

Tests the assess_update_flow() function and related helpers in
monitoring/bridge_watchdog.py, covering the full wedged-vs-quiet
decision matrix from the plan.

These tests mock Redis to avoid requiring a live server, but the logic
under test is real production code.

Test matrix (labelled a–g per the task spec):
  (a) Account-wide silence past ceiling + healthy probe, no recently-active chat
      → wedged verdict, restart authorised (B1 incident shape)
  (b) Silence BELOW the ceiling → no restart
  (c) Stale last_update but failing last_probe (disconnect) → NOT wedged
  (d) Redis raises exception → inconclusive (not wedged) + WARNING logged
  (e) Reconciler-only activity (probe fresh, update stale past ceiling)
      → still wedged (B2: reconciler activity does NOT re-green update signal)
  (f) get_bridge_process_start_ts returns None → no restart authorised (B3)
  (g) Within startup grace window → healthy, no restart
"""

import logging
import time
from unittest.mock import MagicMock, patch

from monitoring.bridge_watchdog import (
    PROBE_FRESHNESS_SECONDS,
    STARTUP_GRACE_SECONDS,
    UPDATE_STALENESS_CEILING,
    UPDATE_STALENESS_WARN,
    assess_update_flow,
    get_bridge_process_start_ts,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_redis(last_update: float | None, last_probe: float | None):
    """Build a minimal mock Redis client that returns fixed liveness values."""
    r = MagicMock()

    def _get(key):
        if key == "bridge:last_update_received":
            return str(last_update) if last_update is not None else None
        if key == "bridge:last_probe_ok":
            return str(last_probe) if last_probe is not None else None
        return None

    r.get.side_effect = _get
    return r


# ---------------------------------------------------------------------------
# (a) B1 incident shape: long silence past ceiling, probe fresh → wedged
# ---------------------------------------------------------------------------


def test_a_wedged_past_ceiling(caplog):
    """Account-wide silence > ceiling with healthy probe → wedged verdict."""
    now = time.time()
    # last_update 5 hours ago (> 4h ceiling)
    last_update = now - (UPDATE_STALENESS_CEILING + 3600)
    # probe was 10 minutes ago (fresh)
    last_probe = now - 600

    r = _make_redis(last_update, last_probe)

    # Bridge started well past the grace window
    start_ts = now - (STARTUP_GRACE_SECONDS + 3600)
    with patch(
        "monitoring.bridge_watchdog.get_bridge_process_start_ts",
        return_value=start_ts,
    ):
        is_live, issue = assess_update_flow(r, bridge_pid=12345)

    assert is_live is False, "Should be NOT live (wedged)"
    assert "wedged" in issue.lower()
    assert "last_update_received" in issue


# ---------------------------------------------------------------------------
# (b) Silence below ceiling → no restart
# ---------------------------------------------------------------------------


def test_b_quiet_below_warn_threshold():
    """Silence shorter than the warn threshold (30m) is not a wedge — no restart."""
    now = time.time()
    # last_update 10 minutes ago (< 30m warn threshold)
    last_update = now - 600
    last_probe = now - 300

    r = _make_redis(last_update, last_probe)

    start_ts = now - (STARTUP_GRACE_SECONDS + 600)
    with patch(
        "monitoring.bridge_watchdog.get_bridge_process_start_ts",
        return_value=start_ts,
    ):
        is_live, issue = assess_update_flow(r, bridge_pid=12345)

    assert is_live is True, "Should be live — silence is below the warn threshold"
    assert issue == ""


# ---------------------------------------------------------------------------
# (c) Stale update + failing probe (disconnect) → NOT wedged
# ---------------------------------------------------------------------------


def test_c_stale_update_stale_probe_not_wedged():
    """When last_probe_ok is also stale, the API layer itself is down.

    This should NOT trigger the wedge detector — the existing reconnect
    ladder owns disconnection recovery.
    """
    now = time.time()
    # Both signals are stale
    last_update = now - (UPDATE_STALENESS_CEILING + 3600)
    last_probe = now - (PROBE_FRESHNESS_SECONDS + 3600)  # probe is old/stale

    r = _make_redis(last_update, last_probe)

    start_ts = now - (STARTUP_GRACE_SECONDS + 3600)
    with patch(
        "monitoring.bridge_watchdog.get_bridge_process_start_ts",
        return_value=start_ts,
    ):
        is_live, issue = assess_update_flow(r, bridge_pid=12345)

    assert is_live is True, (
        "Should be live from wedge perspective — probe stale means "
        "disconnect, not a wedge (reconnect ladder owns it)"
    )


# ---------------------------------------------------------------------------
# (d) Redis raises exception → inconclusive, not wedged, WARNING logged
# ---------------------------------------------------------------------------


def test_d_redis_exception_inconclusive(caplog):
    """Redis error → inconclusive → NOT flagged as wedged + WARNING logged."""
    r = MagicMock()
    r.get.side_effect = ConnectionError("Redis connection refused")

    now = time.time()
    start_ts = now - (STARTUP_GRACE_SECONDS + 3600)

    with patch(
        "monitoring.bridge_watchdog.get_bridge_process_start_ts",
        return_value=start_ts,
    ):
        with caplog.at_level(logging.WARNING, logger="monitoring.bridge_watchdog"):
            is_live, issue = assess_update_flow(r, bridge_pid=12345)

    assert is_live is True, "Redis error must be treated as inconclusive (not wedged)"
    assert issue == ""
    # A WARNING containing the signal-unreadable marker must be emitted
    warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "bridge_update_flow_signal_unreadable" in m or "Redis error" in m for m in warning_messages
    ), f"Expected bridge_update_flow_signal_unreadable WARNING, got: {warning_messages}"


# ---------------------------------------------------------------------------
# (e) B2 regression: reconciler-only activity does NOT re-green update signal
# ---------------------------------------------------------------------------


def test_e_reconciler_only_still_wedged():
    """Reconciler activity (probe fresh) does not excuse stale last_update_received.

    This is the B2 regression: the PRIMARY rule fires on account-wide silence
    regardless of how recently last_probe_ok was written.  The update signal
    and the probe signal are independent — probe being fresh just confirms the
    API layer is healthy, making the update silence MORE suspicious.
    """
    now = time.time()
    # last_update stale past ceiling
    last_update = now - (UPDATE_STALENESS_CEILING + 1800)
    # Reconciler ran 5 minutes ago (very fresh probe)
    last_probe = now - 300

    r = _make_redis(last_update, last_probe)

    start_ts = now - (STARTUP_GRACE_SECONDS + 3600)
    with patch(
        "monitoring.bridge_watchdog.get_bridge_process_start_ts",
        return_value=start_ts,
    ):
        is_live, issue = assess_update_flow(r, bridge_pid=12345)

    assert is_live is False, (
        "B2: fresh last_probe_ok must NOT excuse stale last_update_received "
        "— reconciler activity does not re-green the update signal"
    )
    assert "wedged" in issue.lower()


# ---------------------------------------------------------------------------
# (f) B3 fail-safe: get_bridge_process_start_ts returns None → no restart
# ---------------------------------------------------------------------------


def test_f_start_ts_none_suppresses_wedge(caplog):
    """If process start time is unreadable, wedge verdict is suppressed (C3)."""
    now = time.time()
    # Signals show a wedge condition
    last_update = now - (UPDATE_STALENESS_CEILING + 3600)
    last_probe = now - 300

    r = _make_redis(last_update, last_probe)

    with patch(
        "monitoring.bridge_watchdog.get_bridge_process_start_ts",
        return_value=None,
    ):
        with caplog.at_level(logging.WARNING, logger="monitoring.bridge_watchdog"):
            is_live, issue = assess_update_flow(r, bridge_pid=12345)

    assert is_live is True, (
        "B3 fail-safe: None start_ts must suppress wedge verdict — "
        "never authorise a restart when process age is unreadable"
    )
    assert issue == ""
    warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("fail-safe" in m.lower() or "None" in m for m in warning_messages), (
        f"Expected fail-safe WARNING, got: {warning_messages}"
    )


# ---------------------------------------------------------------------------
# (g) Within startup grace window → healthy, no restart
# ---------------------------------------------------------------------------


def test_g_within_startup_grace_no_restart():
    """Stale signals within the grace window are not a wedge (cold start)."""
    now = time.time()
    # Signals look stale / absent
    last_update = None
    last_probe = None

    r = _make_redis(last_update, last_probe)

    # Bridge started only 2 minutes ago (within 5-minute grace)
    start_ts = now - 120

    with patch(
        "monitoring.bridge_watchdog.get_bridge_process_start_ts",
        return_value=start_ts,
    ):
        is_live, issue = assess_update_flow(r, bridge_pid=12345)

    assert is_live is True, "Within grace window — cold start is healthy"
    assert issue == ""


def test_g_within_grace_stale_signals_no_restart():
    """Stale signals (not None) within grace window are still healthy."""
    now = time.time()
    # Signals from a previous bridge run
    last_update = now - (UPDATE_STALENESS_CEILING + 7200)
    last_probe = now - (PROBE_FRESHNESS_SECONDS + 1)

    r = _make_redis(last_update, last_probe)

    # Bridge just started 1 minute ago
    start_ts = now - 60

    with patch(
        "monitoring.bridge_watchdog.get_bridge_process_start_ts",
        return_value=start_ts,
    ):
        is_live, issue = assess_update_flow(r, bridge_pid=12345)

    assert is_live is True, (
        "Stale signals from previous bridge run within grace window are not wedged"
    )


# ---------------------------------------------------------------------------
# Secondary accelerator: warn window fires before ceiling
# ---------------------------------------------------------------------------


def test_secondary_accelerator_warn_window():
    """Silence past WARN threshold but below ceiling triggers early warning."""
    now = time.time()
    # last_update in the warn window
    last_update = now - (UPDATE_STALENESS_WARN + 300)  # 35 minutes
    last_probe = now - 60  # very fresh

    r = _make_redis(last_update, last_probe)

    start_ts = now - (STARTUP_GRACE_SECONDS + 3600)
    with patch(
        "monitoring.bridge_watchdog.get_bridge_process_start_ts",
        return_value=start_ts,
    ):
        is_live, issue = assess_update_flow(r, bridge_pid=12345)

    assert is_live is False, "Secondary accelerator should fire in warn window"
    assert "early warning" in issue.lower() or "possibly wedged" in issue.lower()


# ---------------------------------------------------------------------------
# get_bridge_process_start_ts unit tests
# ---------------------------------------------------------------------------


def test_get_bridge_process_start_ts_nonexistent_pid():
    """A non-existent PID returns None without raising."""
    result = get_bridge_process_start_ts(999999999)
    assert result is None


def test_get_bridge_process_start_ts_bad_pid():
    """PID 0 (invalid on macOS) returns None without raising."""
    result = get_bridge_process_start_ts(0)
    assert result is None


def test_get_bridge_process_start_ts_valid_format():
    """A well-formatted lstart string parses to a float timestamp."""
    mock_output = "Mon Jun 16 09:45:12 2026"
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=mock_output,
            stderr="",
        )
        result = get_bridge_process_start_ts(12345)

    assert result is not None
    assert isinstance(result, float)
    # The timestamp should be a plausible Unix timestamp (after 2020)
    assert result > 1577836800, "Parsed timestamp should be after 2020-01-01"


def test_get_bridge_process_start_ts_unparseable():
    """Unparseable lstart output returns None and logs a warning."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="not a date",
            stderr="",
        )
        result = get_bridge_process_start_ts(12345)

    assert result is None
