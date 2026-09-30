"""Integration tests for the update-loop wedged detector.

Tests ``assess_update_flow()`` and related helpers in
``monitoring/bridge_watchdog.py``.

These tests mock Redis to avoid requiring a live server, but the logic
under test is real production code.

The contract these tests pin changed in #2475. The detector used to declare a
wedge on *silence alone*: fresh ``last_probe_ok`` plus a stale
``last_update_received`` was sufficient. That made a quiet night
indistinguishable from a wedged update loop, and because nothing ever seeds
``bridge:last_update_received`` on restart, each restart re-armed the same
verdict on the first tick past the startup grace window — a SIGKILL every ~6
minutes until real traffic arrived.

Three rules now hold, all of them forms of "bound the verdict to the process it
accuses":

* Silence is measured from the later of ``last_update_received`` and the bridge
  process's own start time, so a restart clears the accusation.
* A verdict requires ``bridge:last_missed_recovery`` — the reconciler having
  actually recovered a message the live path never delivered. That evidence
  comes from an independent API path, so unlike ``last_update_received`` and
  ``bridge:last_event:*`` (both written by the very handler under suspicion) it
  can distinguish "wedged" from "idle".
* That evidence must postdate the process's startup grace. A restart creates a
  gap which catchup and the reconciler then recover, so every restart stamps the
  evidence key on its way back up; admitting that stamp would let four quiet
  hours after any restart produce one spurious verdict.
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
    get_process_start_ts,
)
from monitoring.bridge_watchdog import logger as bw_logger

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_redis(
    last_update: float | None,
    last_probe: float | None,
    last_missed: float | None = None,
):
    """Build a minimal mock Redis client that returns fixed liveness values."""
    r = MagicMock()

    def _get(key):
        if key == "bridge:last_update_received":
            return str(last_update) if last_update is not None else None
        if key == "bridge:last_probe_ok":
            return str(last_probe) if last_probe is not None else None
        if key == "bridge:last_missed_recovery":
            return str(last_missed) if last_missed is not None else None
        return None

    r.get.side_effect = _get
    return r


def _assess(r, start_ts):
    """Run the detector with a pinned process start time."""
    with patch(
        "monitoring.bridge_watchdog.get_process_start_ts",
        return_value=start_ts,
    ):
        return assess_update_flow(r, bridge_pid=12345)


# ---------------------------------------------------------------------------
# Wedge verdicts: silence past the ceiling WITH recovery evidence
# ---------------------------------------------------------------------------


def test_wedged_past_ceiling_with_recovery_evidence():
    """Silence past the ceiling + fresh probe + a recovered message → wedged."""
    now = time.time()
    last_update = now - (UPDATE_STALENESS_CEILING + 3600)
    last_probe = now - 600
    last_missed = now - 300  # reconciler found a missed message 5 minutes ago

    r = _make_redis(last_update, last_probe, last_missed)
    start_ts = now - (UPDATE_STALENESS_CEILING + 7200)

    is_live, issue = _assess(r, start_ts)

    assert is_live is False, "Should be NOT live (wedged)"
    assert "wedged" in issue.lower()
    assert "reconciler" in issue.lower(), "The verdict must name its corroborating evidence"


def test_never_received_an_update_but_reconciler_recovered_is_wedged():
    """A bridge that has never stamped the beacon can still be wedged.

    ``last_update_received`` absent is not by itself a verdict (see the quiet
    tests below); it becomes one when the reconciler proves messages existed.
    """
    now = time.time()
    r = _make_redis(None, now - 600, now - 300)
    start_ts = now - (UPDATE_STALENESS_CEILING + 7200)

    is_live, issue = _assess(r, start_ts)

    assert is_live is False
    assert "never" in issue


# ---------------------------------------------------------------------------
# The #2475 false positives: silence WITHOUT recovery evidence
# ---------------------------------------------------------------------------


def test_quiet_account_past_ceiling_is_not_wedged():
    """Four hours with nobody sending anything is not a wedge.

    This is the #2475 storm shape: probe fresh (the reconciler runs every 180s,
    so it always is), beacon stale, and nothing whatsoever missed. The old rule
    declared a wedge here.
    """
    now = time.time()
    last_update = now - (UPDATE_STALENESS_CEILING + 3600)
    last_probe = now - 300

    r = _make_redis(last_update, last_probe, last_missed=None)
    start_ts = now - (UPDATE_STALENESS_CEILING + 7200)

    is_live, issue = _assess(r, start_ts)

    assert is_live is True, "A quiet account must not be accused of a wedge"
    assert issue == ""


def test_absent_beacon_on_fresh_redis_is_not_wedged():
    """Fresh/flushed Redis: no beacon, fresh probe, nothing missed → not wedged.

    The cold-start exemption only covers *both* keys being absent, and the
    reconciler stamps the probe within 180s of any connect. Under the old rule
    this shape produced a permanent restart loop on a newly provisioned host.
    """
    now = time.time()
    r = _make_redis(None, now - 60, last_missed=None)
    start_ts = now - (UPDATE_STALENESS_CEILING + 7200)

    is_live, issue = _assess(r, start_ts)

    assert is_live is True
    assert issue == ""


def test_recovery_stamped_during_startup_grace_does_not_corroborate():
    """A restart's own backfill is not evidence that the restarted process is wedged.

    Every restart creates a gap, and catchup plus the reconciler recover that gap
    on the way back up — so a routine restart reliably stamps
    ``bridge:last_missed_recovery`` inside its own grace window. The evidence
    window and the silence window are the same length, so without a floor that
    stamp stays admissible for a full ceiling, and four quiet hours after any
    restart would produce a spurious verdict.

    Constructed to isolate exactly that: the stamp is inside the grace window
    (290s < 300s after start) yet still inside the ceiling window
    (~3.9h < 4h), so the window check alone would admit it.
    """
    now = time.time()
    start_ts = now - (UPDATE_STALENESS_CEILING + 60)
    last_update = now - (UPDATE_STALENESS_CEILING + 3600)
    last_probe = now - 60
    last_missed = start_ts + (STARTUP_GRACE_SECONDS - 10)

    # Guard the construction itself: if these stop holding, the test has drifted
    # off the case it exists to cover and would pass for the wrong reason.
    assert (now - start_ts) >= UPDATE_STALENESS_CEILING, "silence must clear the ceiling"
    assert (now - last_missed) < UPDATE_STALENESS_CEILING, (
        "stamp must be inside the evidence window"
    )
    assert (last_missed - start_ts) < STARTUP_GRACE_SECONDS, "stamp must be inside the grace window"

    r = _make_redis(last_update, last_probe, last_missed)

    is_live, issue = _assess(r, start_ts)

    assert is_live is True, (
        "A recovery stamped during startup grace describes the restart, not a wedge "
        "in the process that came back"
    )
    assert issue == ""


def test_recovery_stamped_just_after_grace_does_corroborate():
    """The floor is a floor, not a blanket suppression of post-restart evidence."""
    now = time.time()
    start_ts = now - (UPDATE_STALENESS_CEILING + 60)
    last_update = now - (UPDATE_STALENESS_CEILING + 3600)
    last_probe = now - 60
    last_missed = start_ts + (STARTUP_GRACE_SECONDS + 10)

    r = _make_redis(last_update, last_probe, last_missed)

    is_live, issue = _assess(r, start_ts)

    assert is_live is False
    assert "wedged" in issue.lower()


def test_stale_recovery_evidence_does_not_corroborate():
    """Evidence older than the window is not evidence for this window."""
    now = time.time()
    last_update = now - (UPDATE_STALENESS_CEILING + 3600)
    last_probe = now - 300
    last_missed = now - (UPDATE_STALENESS_CEILING + 60)  # just outside the window

    r = _make_redis(last_update, last_probe, last_missed)
    start_ts = now - (UPDATE_STALENESS_CEILING * 2)

    is_live, _ = _assess(r, start_ts)

    assert is_live is True


# ---------------------------------------------------------------------------
# A restart clears the accusation
# ---------------------------------------------------------------------------


def test_restart_resets_the_silence_clock():
    """After a restart the beacon is still hours old — that must not re-fire.

    Nothing seeds ``bridge:last_update_received`` on restart, so the pre-restart
    timestamp survives. Measuring silence from process start is what stops the
    verdict from outliving the restart meant to cure it. Recovery evidence is
    present here, so the *only* thing keeping this healthy is the reset.
    """
    now = time.time()
    last_update = now - (UPDATE_STALENESS_CEILING + 7200)  # hours stale
    last_probe = now - 60
    last_missed = now - 120

    r = _make_redis(last_update, last_probe, last_missed)
    # Restarted 10 minutes ago: past the 5-minute grace, but only 10 minutes of
    # its own silence.
    start_ts = now - 600

    is_live, issue = _assess(r, start_ts)

    assert is_live is True, (
        "A freshly restarted bridge must not inherit the verdict that restarted it"
    )
    assert issue == ""


def test_wedge_re_fires_once_the_new_process_has_been_silent_long_enough():
    """The reset delays the verdict; it does not suppress a real recurrence."""
    now = time.time()
    last_update = now - (UPDATE_STALENESS_CEILING * 3)
    last_probe = now - 60
    last_missed = now - 120
    # This process has now itself been silent past the ceiling.
    start_ts = now - (UPDATE_STALENESS_CEILING + 600)

    r = _make_redis(last_update, last_probe, last_missed)

    is_live, issue = _assess(r, start_ts)

    assert is_live is False
    assert "wedged" in issue.lower()


# ---------------------------------------------------------------------------
# Probe / disconnect boundary
# ---------------------------------------------------------------------------


def test_stale_probe_is_a_disconnect_not_a_wedge():
    """A stale probe means the API layer may be down — the reconnect ladder owns it."""
    now = time.time()
    last_update = now - (UPDATE_STALENESS_CEILING + 3600)
    last_probe = now - (PROBE_FRESHNESS_SECONDS + 3600)
    last_missed = now - 300  # even with evidence, a disconnect is not a wedge

    r = _make_redis(last_update, last_probe, last_missed)
    start_ts = now - (UPDATE_STALENESS_CEILING + 7200)

    is_live, _ = _assess(r, start_ts)

    assert is_live is True


def test_silence_below_thresholds_is_not_wedged():
    """Ten minutes of quiet is not a wedge even with recovery evidence."""
    now = time.time()
    r = _make_redis(now - 600, now - 300, now - 60)
    start_ts = now - (UPDATE_STALENESS_CEILING + 600)

    is_live, issue = _assess(r, start_ts)

    assert is_live is True
    assert issue == ""


# ---------------------------------------------------------------------------
# Secondary accelerator
# ---------------------------------------------------------------------------


def test_secondary_accelerator_fires_with_recent_recovery():
    """Past the warn threshold with equally recent evidence → early warning."""
    now = time.time()
    last_update = now - (UPDATE_STALENESS_WARN + 300)
    last_probe = now - 60
    last_missed = now - 120  # inside the warn window

    r = _make_redis(last_update, last_probe, last_missed)
    start_ts = now - (UPDATE_STALENESS_CEILING + 3600)

    is_live, issue = _assess(r, start_ts)

    assert is_live is False
    assert "early warning" in issue.lower()


def test_secondary_accelerator_needs_recent_evidence():
    """A recovery from an hour ago does not accelerate a 35-minute silence."""
    now = time.time()
    last_update = now - (UPDATE_STALENESS_WARN + 300)
    last_probe = now - 60
    last_missed = now - (UPDATE_STALENESS_WARN + 900)  # older than the warn window

    r = _make_redis(last_update, last_probe, last_missed)
    start_ts = now - (UPDATE_STALENESS_CEILING + 3600)

    is_live, _ = _assess(r, start_ts)

    assert is_live is True


def test_secondary_accelerator_does_not_fire_without_evidence():
    """A 35-minute lull with nothing missed is a lull."""
    now = time.time()
    r = _make_redis(now - (UPDATE_STALENESS_WARN + 300), now - 60, last_missed=None)
    start_ts = now - (UPDATE_STALENESS_CEILING + 3600)

    is_live, _ = _assess(r, start_ts)

    assert is_live is True


# ---------------------------------------------------------------------------
# Fail-safes
# ---------------------------------------------------------------------------


def test_redis_exception_is_inconclusive(caplog):
    """Redis error → inconclusive → NOT flagged as wedged + WARNING logged."""
    r = MagicMock()
    r.get.side_effect = ConnectionError("Redis connection refused")

    now = time.time()
    start_ts = now - (STARTUP_GRACE_SECONDS + 3600)

    # bw_logger.propagate is False (issue #2643), so caplog's root-attached
    # handler never sees its records; attach explicitly and detach after.
    bw_logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.WARNING, logger="monitoring.bridge_watchdog"):
            is_live, issue = _assess(r, start_ts)
    finally:
        bw_logger.removeHandler(caplog.handler)

    assert is_live is True, "Redis error must be treated as inconclusive (not wedged)"
    assert issue == ""
    warning_messages = [rec.message for rec in caplog.records if rec.levelno == logging.WARNING]
    assert any(
        "bridge_update_flow_signal_unreadable" in m or "Redis error" in m for m in warning_messages
    ), f"Expected bridge_update_flow_signal_unreadable WARNING, got: {warning_messages}"


def test_unreadable_process_start_suppresses_verdict(caplog):
    """If process start time is unreadable, the verdict is suppressed (C3)."""
    now = time.time()
    r = _make_redis(now - (UPDATE_STALENESS_CEILING + 3600), now - 300, now - 60)

    # bw_logger.propagate is False (issue #2643), so caplog's root-attached
    # handler never sees its records; attach explicitly and detach after.
    bw_logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.WARNING, logger="monitoring.bridge_watchdog"):
            is_live, issue = _assess(r, start_ts=None)
    finally:
        bw_logger.removeHandler(caplog.handler)

    assert is_live is True, (
        "None start_ts must suppress the verdict — process age is the floor for "
        "the silence measurement, not just the grace window"
    )
    assert issue == ""
    warning_messages = [rec.message for rec in caplog.records if rec.levelno == logging.WARNING]
    assert any("fail-safe" in m.lower() for m in warning_messages), (
        f"Expected fail-safe WARNING, got: {warning_messages}"
    )


def test_missing_pid_suppresses_verdict():
    """No pid → no process age → inconclusive, same fail-safe."""
    now = time.time()
    r = _make_redis(now - (UPDATE_STALENESS_CEILING + 3600), now - 300, now - 60)

    is_live, issue = assess_update_flow(r, bridge_pid=None)

    assert is_live is True
    assert issue == ""


# ---------------------------------------------------------------------------
# Startup grace window
# ---------------------------------------------------------------------------


def test_within_startup_grace_no_verdict():
    """Absent signals within the grace window are a cold start, not a wedge."""
    now = time.time()
    r = _make_redis(None, None)

    is_live, issue = _assess(r, start_ts=now - 120)

    assert is_live is True
    assert issue == ""


def test_within_grace_stale_signals_no_verdict():
    """Signals left over from the previous run do not accuse the new process."""
    now = time.time()
    r = _make_redis(
        now - (UPDATE_STALENESS_CEILING + 7200),
        now - (PROBE_FRESHNESS_SECONDS + 1),
        now - 60,
    )

    is_live, _ = _assess(r, start_ts=now - 60)

    assert is_live is True


# ---------------------------------------------------------------------------
# get_process_start_ts unit tests
# ---------------------------------------------------------------------------


def test_get_process_start_ts_nonexistent_pid():
    """A non-existent PID returns None without raising."""
    result = get_process_start_ts(999999999)
    assert result is None


def test_get_process_start_ts_bad_pid():
    """PID 0 (invalid on macOS) returns None without raising."""
    result = get_process_start_ts(0)
    assert result is None


def test_get_process_start_ts_valid_format():
    """A well-formatted lstart string parses to a float timestamp."""
    mock_output = "Mon Jun 16 09:45:12 2026"
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=mock_output,
            stderr="",
        )
        result = get_process_start_ts(12345)

    assert result is not None
    assert isinstance(result, float)
    # The timestamp should be a plausible Unix timestamp (after 2020)
    assert result > 1577836800, "Parsed timestamp should be after 2020-01-01"


def test_get_process_start_ts_unparseable():
    """Unparseable lstart output returns None and logs a warning."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="not a date",
            stderr="",
        )
        result = get_process_start_ts(12345)

    assert result is None
