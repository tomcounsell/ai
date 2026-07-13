"""Unit tests for agent.session_stall_classifier (issue #1538).

Tests classify_session_stall() and read_project_health_counters() in isolation.
All session objects are SimpleNamespace stubs — no real Redis, no real models.
to_unix_ts is patched only where the test needs to simulate an unparseable ts.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import patch

from agent.session_stall_classifier import (
    _RUNNING_PROBE_STATUSES,
    IDLE_STALL_SECS,
    IDLE_SUSPECT_SECS,
    NEVER_STARTED_CONFIRM_MARGIN_SECS,
    NEVER_STARTED_GRACE_SECS,
    RECOVERY_SUSPECT_COUNT,
    TOOL_TIMEOUT_SUSPECT_COUNT,
    classify_session_stall,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _session(status: str = "running", started_at=None, created_at=None) -> SimpleNamespace:
    """Build a minimal session-like object."""
    now = time.time()
    return SimpleNamespace(
        status=status,
        started_at=started_at,
        # Default: created 10 minutes ago so never-started grace is exceeded.
        created_at=created_at if created_at is not None else (now - 1500),
    )


def _recent_turn_event() -> dict:
    """A turn_end event timestamped < IDLE_SUSPECT_SECS ago."""
    ts = time.time() - 30  # 30 seconds ago — well within the healthy window
    return {"type": "turn_end", "ts": ts}


def _old_turn_event() -> dict:
    """A turn_end event timestamped > IDLE_STALL_SECS ago."""
    ts = time.time() - (IDLE_STALL_SECS + 300)
    return {"type": "turn_end", "ts": ts}


def _idle_gap_event(duration_secs: float) -> dict:
    return {"type": "idle_gap", "gap_seconds": duration_secs}


def _kill_transition_event(to_status: str = "killed") -> dict:
    return {"type": "status_transition", "data": {"to": to_status}}


# ---------------------------------------------------------------------------
# 1. Healthy verdicts
# ---------------------------------------------------------------------------


class TestHealthyVerdicts:
    def test_empty_events_non_probe_status_returns_not_started_probe(self):
        # "dormant" is not in _RUNNING_PROBE_STATUSES.
        session = _session(status="dormant")
        verdict = classify_session_stall([], session=session)
        assert verdict.level == "healthy"
        assert verdict.reason == "not_started_probe"

    def test_empty_events_no_session_returns_not_started_probe(self):
        verdict = classify_session_stall([])
        assert verdict.level == "healthy"
        assert verdict.reason == "not_started_probe"

    def test_recent_turn_event_returns_healthy(self):
        # Include a turn_start so has_turn_start=True (skips never-started branch).
        # The turn_end timestamped 30s ago then triggers recent_turn_activity.
        session = _session(status="running")
        events = [
            {"type": "turn_start", "ts": time.time() - 35},
            _recent_turn_event(),
        ]
        verdict = classify_session_stall(events, session=session)
        assert verdict.level == "healthy"
        assert verdict.reason == "recent_turn_activity"

    def test_session_created_recently_within_grace_returns_healthy(self):
        # Session created only 10 seconds ago — still within NEVER_STARTED_GRACE_SECS.
        now = time.time()
        session = _session(status="running", created_at=now - 10)
        verdict = classify_session_stall([], session=session)
        assert verdict.level == "healthy"
        # The never-started branch falls through (elapsed < grace) → no_events
        assert verdict.reason in {"no_events", "not_started_probe", "healthy", "unclassifiable"}

    def test_no_concerning_signals_returns_healthy(self):
        # Short idle gap well below suspect threshold.
        # Include a turn_start so has_turn_start=True (skips never-started branch).
        # The old turn_end ensures recent_turn check doesn't short-circuit to
        # healthy before reaching the idle-gap logic.
        events = [
            {"type": "turn_start", "ts": time.time() - 1500},
            _old_turn_event(),
            _idle_gap_event(60.0),
        ]
        session = _session(status="running")
        verdict = classify_session_stall(events, session=session)
        assert verdict.level == "healthy"
        assert verdict.reason == "no_concerning_signals"


# ---------------------------------------------------------------------------
# 2. Never-started rule
# ---------------------------------------------------------------------------


class TestNeverStartedRule:
    def test_running_status_zero_turn_start_elapsed_past_grace_is_stalled(self):
        # session created 700 seconds ago, status=running, no turn_start events.
        now = time.time()
        session = _session(status="running", created_at=now - 1500)
        verdict = classify_session_stall([], session=session)
        assert verdict.level == "stalled"
        assert verdict.reason == "never_started"
        assert verdict.signals["elapsed_secs"] > NEVER_STARTED_GRACE_SECS

    def test_active_status_zero_turn_start_elapsed_past_grace_is_stalled(self):
        now = time.time()
        session = _session(status="active", created_at=now - 1500)
        verdict = classify_session_stall([], session=session)
        assert verdict.level == "stalled"
        assert verdict.reason == "never_started"

    def test_paused_status_is_in_probe_set_and_triggers_never_started(self):
        assert "paused" in _RUNNING_PROBE_STATUSES
        now = time.time()
        session = _session(status="paused", created_at=now - 1500)
        verdict = classify_session_stall([], session=session)
        assert verdict.level == "stalled"
        assert verdict.reason == "never_started"

    def test_pending_status_zero_turn_start_long_elapsed_is_not_never_started(self):
        # pending is excluded from _RUNNING_PROBE_STATUSES — it's #1313's domain.
        now = time.time()
        session = _session(status="pending", created_at=now - 1500)
        verdict = classify_session_stall([], session=session)
        assert verdict.level == "healthy"
        assert verdict.reason == "not_started_probe"

    def test_dormant_status_zero_events_is_healthy_not_started_probe(self):
        session = _session(status="dormant")
        verdict = classify_session_stall([], session=session)
        assert verdict.level == "healthy"
        assert verdict.reason == "not_started_probe"

    def test_started_at_used_preferentially_over_created_at(self):
        # started_at is recent (10 seconds ago) but created_at is old.
        now = time.time()
        session = SimpleNamespace(
            status="running",
            started_at=now - 10,  # within grace
            created_at=now - 1500,  # outside grace
        )
        verdict = classify_session_stall([], session=session)
        # elapsed from started_at = 10s < NEVER_STARTED_GRACE_SECS → healthy
        assert verdict.level == "healthy"

    def test_started_at_none_falls_back_to_created_at(self):
        # started_at=None → fall back to created_at which is old.
        now = time.time()
        session = SimpleNamespace(
            status="running",
            started_at=None,
            created_at=now - 1500,
        )
        verdict = classify_session_stall([], session=session)
        assert verdict.level == "stalled"
        assert verdict.reason == "never_started"


class TestNeverStartedProgressGuard:
    """Telemetry turn_start writes can lag or be lost while a session progresses.

    The progress-field guard must suppress the never_started false positive when
    the AgentSession's own fields show work, while leaving genuinely-idle
    sessions flagged.
    """

    def test_positive_turn_count_skips_never_started(self):
        # No turn_start events, old created_at — but turn_count proves it started.
        now = time.time()
        session = SimpleNamespace(
            status="running",
            started_at=now - 1500,
            created_at=now - 1500,
            turn_count=28,
        )
        verdict = classify_session_stall([], session=session)
        assert verdict.level == "healthy"
        assert verdict.reason == "progress_fields_fresh"
        assert verdict.signals["turn_count"] == 28

    def test_fresh_last_tool_use_skips_never_started(self):
        import datetime

        now = time.time()
        session = SimpleNamespace(
            status="running",
            started_at=now - 1500,
            created_at=now - 1500,
            turn_count=0,  # no completed turns yet
            last_tool_use_at=datetime.datetime.now(datetime.UTC),  # firing now
        )
        verdict = classify_session_stall([], session=session)
        assert verdict.level == "healthy"
        assert verdict.reason == "progress_fields_fresh"

    def test_stale_progress_fields_still_never_started(self):
        # turn_count zero AND last activity well outside the suspect window →
        # the guard must NOT fire; the session is genuinely never-started.
        now = time.time()
        session = SimpleNamespace(
            status="running",
            started_at=now - 1500,
            created_at=now - 1500,
            turn_count=0,
            last_tool_use_at=now - (IDLE_SUSPECT_SECS + 200),
        )
        verdict = classify_session_stall([], session=session)
        assert verdict.level == "stalled"
        assert verdict.reason == "never_started"

    def test_confirm_margin_applied_below_threshold_is_healthy(self):
        # Elapsed sits in the (grace, grace+margin] band → not yet stalled.
        now = time.time()
        elapsed = NEVER_STARTED_GRACE_SECS + (NEVER_STARTED_CONFIRM_MARGIN_SECS / 2)
        session = SimpleNamespace(
            status="running",
            started_at=now - elapsed,
            created_at=now - elapsed,
            turn_count=0,
        )
        verdict = classify_session_stall([], session=session)
        assert verdict.level == "healthy"
        assert verdict.reason != "never_started"

    def test_confirm_margin_reported_in_signals(self):
        now = time.time()
        session = SimpleNamespace(
            status="running",
            started_at=now - 1500,
            created_at=now - 1500,
            turn_count=0,
        )
        verdict = classify_session_stall([], session=session)
        assert verdict.reason == "never_started"
        assert verdict.signals["confirm_margin_secs"] == NEVER_STARTED_CONFIRM_MARGIN_SECS


# ---------------------------------------------------------------------------
# 3. Elapsed / timezone guard
# ---------------------------------------------------------------------------


class TestElapsedTimezoneGuard:
    def test_naive_datetime_created_at_no_exception(self):
        import datetime

        now_naive = datetime.datetime.utcnow()  # naive — no tzinfo
        session = SimpleNamespace(
            status="running",
            started_at=None,
            created_at=now_naive,
        )
        # Should not raise — to_unix_ts handles naive datetimes as UTC.
        verdict = classify_session_stall([], session=session)
        assert verdict.level in {"healthy", "stalled", "suspect", "unclassifiable"}

    def test_aware_datetime_created_at_no_exception(self):
        import datetime

        now_aware = datetime.datetime.now(datetime.UTC)
        session = SimpleNamespace(
            status="running",
            started_at=None,
            created_at=now_aware,
        )
        verdict = classify_session_stall([], session=session)
        assert verdict.level in {"healthy", "stalled", "suspect", "unclassifiable"}

    def test_unparseable_timestamp_returns_healthy_not_stalled(self):
        # Patch to_unix_ts to always return None (simulates unparseable timestamp).
        # _classify does `from bridge.utc import to_unix_ts` inside its body,
        # so we patch `bridge.utc.to_unix_ts` which is what gets imported.
        session = _session(status="running", created_at="not-a-date")
        with patch("bridge.utc.to_unix_ts", return_value=None):
            verdict = classify_session_stall([], session=session)
        # With ts=None the never-started branch is skipped → falls through → healthy
        assert verdict.level == "healthy"
        assert verdict.reason != "never_started"

    def test_session_none_no_exception(self):
        # session=None: no never-started check, just event analysis.
        verdict = classify_session_stall([], session=None)
        assert verdict.level == "healthy"


# ---------------------------------------------------------------------------
# 4. Idle gap signals
# ---------------------------------------------------------------------------


def _idle_events(duration: float) -> list[dict]:
    """Build a minimal event list for idle-gap tests.

    Always includes a turn_start so has_turn_start=True (skips never-started
    branch). Includes an old turn_end so the recent-turn check doesn't mask
    the idle gap result. Then adds the idle_gap event.
    """
    old_ts = time.time() - (IDLE_STALL_SECS + 300)
    return [
        {"type": "turn_start", "ts": old_ts},
        {"type": "turn_end", "ts": old_ts},
        _idle_gap_event(duration),
    ]


class TestIdleGapSignals:
    def test_idle_gap_exceeding_stall_threshold_returns_stalled(self):
        events = _idle_events(IDLE_STALL_SECS + 1)
        session = _session(status="running")
        verdict = classify_session_stall(events, session=session)
        assert verdict.level == "stalled"
        assert verdict.reason == "idle_gap_exceeded_stall"

    def test_idle_gap_in_suspect_range_returns_suspect(self):
        duration = (IDLE_SUSPECT_SECS + IDLE_STALL_SECS) / 2  # between the two thresholds
        events = _idle_events(duration)
        session = _session(status="running")
        verdict = classify_session_stall(events, session=session)
        assert verdict.level == "suspect"
        assert verdict.reason == "idle_gap_exceeded_suspect"

    def test_short_idle_gap_returns_healthy(self):
        events = _idle_events(IDLE_SUSPECT_SECS - 1)
        session = _session(status="running")
        verdict = classify_session_stall(events, session=session)
        assert verdict.level == "healthy"

    def test_idle_gap_top_level(self):
        # duration in top-level gap_seconds (real recorder schema)
        events = _idle_events(IDLE_STALL_SECS + 10)
        session = _session(status="running")
        verdict = classify_session_stall(events, session=session)
        assert verdict.level == "stalled"

    def test_idle_gap_in_top_level_field(self):
        # Fallback: duration_secs at the top level of the event dict (no data wrapper).
        old_ts = time.time() - (IDLE_STALL_SECS + 300)
        events = [
            {"type": "turn_start", "ts": old_ts},
            {"type": "turn_end", "ts": old_ts},
            {"type": "idle_gap", "duration_secs": IDLE_STALL_SECS + 10},
        ]
        session = _session(status="running")
        verdict = classify_session_stall(events, session=session)
        assert verdict.level == "stalled"


# ---------------------------------------------------------------------------
# 5. Kill-transition signals
# ---------------------------------------------------------------------------


def _kill_events(to_status: str) -> list[dict]:
    """Build a minimal event list for kill-transition tests.

    Always includes a turn_start so has_turn_start=True (skips never-started
    branch). The turn_start is old so the recent-turn check doesn't mask the
    kill-transition result.
    """
    old_ts = time.time() - (IDLE_STALL_SECS + 300)
    return [
        {"type": "turn_start", "ts": old_ts},
        _kill_transition_event(to_status),
    ]


class TestKillTransitionSignals:
    def test_kill_transition_killed_returns_stalled(self):
        events = _kill_events("killed")
        session = _session(status="running")
        verdict = classify_session_stall(events, session=session)
        assert verdict.level == "stalled"
        assert verdict.reason == "kill_transition"

    def test_kill_transition_failed_returns_stalled(self):
        events = _kill_events("failed")
        session = _session(status="running")
        verdict = classify_session_stall(events, session=session)
        assert verdict.level == "stalled"
        assert verdict.reason == "kill_transition"

    def test_kill_transition_cancelled_returns_stalled(self):
        events = _kill_events("cancelled")
        session = _session(status="running")
        verdict = classify_session_stall(events, session=session)
        assert verdict.level == "stalled"

    def test_non_kill_transition_does_not_flag(self):
        old_ts = time.time() - (IDLE_STALL_SECS + 300)
        events = [
            {"type": "turn_start", "ts": old_ts},
            {"type": "status_transition", "data": {"to": "paused"}},
        ]
        session = _session(status="running")
        verdict = classify_session_stall(events, session=session)
        assert verdict.level == "healthy"


# ---------------------------------------------------------------------------
# 6. Fail-soft on malformed input
# ---------------------------------------------------------------------------


class TestFailSoftMalformedInput:
    def test_events_list_with_non_dict_entries_no_raise(self):
        events = [None, "bad", 42, {"type": "turn_start", "ts": time.time()}]  # type: ignore[list-item]
        session = _session(status="running")
        # Should not raise — returns a verdict.
        verdict = classify_session_stall(events, session=session)
        assert verdict.level in {"healthy", "suspect", "stalled"}

    def test_event_missing_type_field_no_raise(self):
        events = [{"ts": time.time()}, {"data": {}}]
        session = _session(status="running")
        verdict = classify_session_stall(events, session=session)
        assert verdict.level in {"healthy", "suspect", "stalled"}

    def test_session_none_no_raise_returns_healthy(self):
        verdict = classify_session_stall([], session=None)
        assert verdict.level == "healthy"

    def test_events_all_malformed_no_raise(self):
        events = [None, None, None]  # type: ignore[list-item]
        verdict = classify_session_stall(events, session=None)
        assert verdict.level in {"healthy", "suspect", "stalled", "unclassifiable"}

    def test_exception_inside_classify_returns_healthy_unclassifiable(self):
        # Force an exception deep in _classify by using a session that raises on attribute access.
        class _Bomb:
            @property
            def status(self):
                raise RuntimeError("deliberate boom")

        verdict = classify_session_stall([], session=_Bomb())
        assert verdict.level == "healthy"
        assert verdict.reason == "unclassifiable"


# ---------------------------------------------------------------------------
# 7. Counter corroboration
# ---------------------------------------------------------------------------


def _counter_events() -> list[dict]:
    """Minimal event list for counter-corroboration tests.

    has_turn_start=True (skips never-started) + old turn_end (doesn't mask
    by recent_turn_activity). No idle_gap so the counter-only path is taken.
    """
    old_ts = time.time() - (IDLE_STALL_SECS + 300)
    return [
        {"type": "turn_start", "ts": old_ts},
        {"type": "turn_end", "ts": old_ts},
    ]


class TestCounterCorroboration:
    def test_tool_timeouts_at_threshold_triggers_suspect(self):
        # No idle_gap events, but tool_timeout counters are at the threshold.
        counters = {"tool_timeouts:tier1": TOOL_TIMEOUT_SUSPECT_COUNT}
        events = _counter_events()
        session = _session(status="running")
        verdict = classify_session_stall(events, session=session, project_counters=counters)
        assert verdict.level == "suspect"
        assert verdict.reason == "project_counter_suspect"

    def test_recoveries_at_threshold_triggers_suspect(self):
        counters = {"recoveries:kill": RECOVERY_SUSPECT_COUNT}
        events = _counter_events()
        session = _session(status="running")
        verdict = classify_session_stall(events, session=session, project_counters=counters)
        assert verdict.level == "suspect"
        assert verdict.reason == "project_counter_suspect"

    def test_counters_below_threshold_no_suspect(self):
        counters = {
            "tool_timeouts:tier1": TOOL_TIMEOUT_SUSPECT_COUNT - 1,
            "recoveries:kill": RECOVERY_SUSPECT_COUNT - 1,
        }
        events = _counter_events()
        session = _session(status="running")
        verdict = classify_session_stall(events, session=session, project_counters=counters)
        assert verdict.level == "healthy"

    def test_multiple_timeout_keys_summed(self):
        # Split across two keys; combined total meets threshold.
        half = TOOL_TIMEOUT_SUSPECT_COUNT // 2
        counters = {
            "tool_timeouts:tier1": half,
            "tool_timeouts:tier2": TOOL_TIMEOUT_SUSPECT_COUNT - half,
        }
        events = _counter_events()
        session = _session(status="running")
        verdict = classify_session_stall(events, session=session, project_counters=counters)
        assert verdict.level == "suspect"


# ---------------------------------------------------------------------------
# 8. Threshold constants pinned (regression guard)
# ---------------------------------------------------------------------------


class TestThresholdConstants:
    def test_never_started_grace_secs_pinned(self):
        assert NEVER_STARTED_GRACE_SECS == 1200

    def test_idle_suspect_secs_pinned(self):
        assert IDLE_SUSPECT_SECS == 300

    def test_idle_stall_secs_pinned(self):
        assert IDLE_STALL_SECS == 600

    def test_tool_timeout_suspect_count_pinned(self):
        assert TOOL_TIMEOUT_SUSPECT_COUNT == 3

    def test_recovery_suspect_count_pinned(self):
        assert RECOVERY_SUSPECT_COUNT == 2
