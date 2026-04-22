"""Tests for the post-compaction nudge guard in agent/output_router.py.

Issue #1127. The guard short-circuits to ``"defer_post_compact"`` when
``last_compaction_ts`` is within ``POST_COMPACT_NUDGE_GUARD_SECONDS`` of
``now``. Covers the None / stale / fresh / boundary cases.
"""

from __future__ import annotations

import time

from agent.output_router import (
    MAX_NUDGE_COUNT,
    POST_COMPACT_NUDGE_GUARD_SECONDS,
    determine_delivery_action,
)


class TestLastCompactionTsNone:
    """When last_compaction_ts is None, guard is inactive — existing logic runs."""

    def test_none_delivers_normal(self):
        action = determine_delivery_action(
            msg="output",
            stop_reason="end_turn",
            auto_continue_count=0,
            max_nudge_count=MAX_NUDGE_COUNT,
            last_compaction_ts=None,
        )
        assert action == "deliver"

    def test_none_pm_sdlc_still_nudges(self):
        action = determine_delivery_action(
            msg="working",
            stop_reason="end_turn",
            auto_continue_count=0,
            max_nudge_count=MAX_NUDGE_COUNT,
            session_type="pm",
            classification_type="sdlc",
            last_compaction_ts=None,
        )
        assert action == "nudge_continue"


class TestLastCompactionTsFresh:
    """When last_compaction_ts is within 30s of now, action defers."""

    def test_fresh_defers(self):
        action = determine_delivery_action(
            msg="output",
            stop_reason="end_turn",
            auto_continue_count=0,
            max_nudge_count=MAX_NUDGE_COUNT,
            last_compaction_ts=time.time(),
        )
        assert action == "defer_post_compact"

    def test_fresh_defers_even_for_empty_msg(self):
        """Defer takes precedence over nudge_empty."""
        action = determine_delivery_action(
            msg="",
            stop_reason="end_turn",
            auto_continue_count=0,
            max_nudge_count=MAX_NUDGE_COUNT,
            last_compaction_ts=time.time(),
        )
        assert action == "defer_post_compact"

    def test_fresh_defers_for_pm_sdlc(self):
        """Defer takes precedence over PM/SDLC nudge_continue."""
        action = determine_delivery_action(
            msg="hi",
            stop_reason="end_turn",
            auto_continue_count=0,
            max_nudge_count=MAX_NUDGE_COUNT,
            session_type="pm",
            classification_type="sdlc",
            last_compaction_ts=time.time() - 5.0,
        )
        assert action == "defer_post_compact"

    def test_fresh_defers_for_rate_limited(self):
        """Defer takes precedence over nudge_rate_limited."""
        action = determine_delivery_action(
            msg="",
            stop_reason="rate_limited",
            auto_continue_count=0,
            max_nudge_count=MAX_NUDGE_COUNT,
            last_compaction_ts=time.time(),
        )
        assert action == "defer_post_compact"


class TestLastCompactionTsStale:
    """When last_compaction_ts is older than 30s, guard is inactive."""

    def test_stale_delivers_normal(self):
        action = determine_delivery_action(
            msg="output",
            stop_reason="end_turn",
            auto_continue_count=0,
            max_nudge_count=MAX_NUDGE_COUNT,
            last_compaction_ts=time.time() - 100.0,
        )
        assert action == "deliver"

    def test_stale_nudges_empty_msg(self):
        action = determine_delivery_action(
            msg="",
            stop_reason="end_turn",
            auto_continue_count=0,
            max_nudge_count=MAX_NUDGE_COUNT,
            last_compaction_ts=time.time() - 100.0,
        )
        assert action == "nudge_empty"


class TestLastCompactionTsBoundary:
    """Boundary test at exactly 30s — NOT deferred (strict <)."""

    def test_exactly_30s_not_deferred(self, monkeypatch):
        """Age == POST_COMPACT_NUDGE_GUARD_SECONDS is NOT within the guard window.

        The guard uses strict `<` so the boundary case falls through to normal
        classification. We freeze time.time() to get deterministic age.
        """
        frozen_now = 1_000_000.0
        monkeypatch.setattr(time, "time", lambda: frozen_now)
        # last_compaction_ts = now - 30.0 → age is exactly POST_COMPACT_NUDGE_GUARD_SECONDS
        action = determine_delivery_action(
            msg="output",
            stop_reason="end_turn",
            auto_continue_count=0,
            max_nudge_count=MAX_NUDGE_COUNT,
            last_compaction_ts=frozen_now - float(POST_COMPACT_NUDGE_GUARD_SECONDS),
        )
        assert action == "deliver"

    def test_one_ms_inside_window_defers(self, monkeypatch):
        frozen_now = 1_000_000.0
        monkeypatch.setattr(time, "time", lambda: frozen_now)
        action = determine_delivery_action(
            msg="output",
            stop_reason="end_turn",
            auto_continue_count=0,
            max_nudge_count=MAX_NUDGE_COUNT,
            last_compaction_ts=frozen_now - (POST_COMPACT_NUDGE_GUARD_SECONDS - 0.001),
        )
        assert action == "defer_post_compact"


class TestGuardPrecedence:
    """The defer guard runs AFTER terminal/completion_sent guards, not before."""

    def test_terminal_status_still_delivers_already_completed(self):
        """A terminal session does NOT defer — it delivers cleanly."""
        action = determine_delivery_action(
            msg="final",
            stop_reason="end_turn",
            auto_continue_count=0,
            max_nudge_count=MAX_NUDGE_COUNT,
            session_status="completed",
            last_compaction_ts=time.time(),
        )
        assert action == "deliver_already_completed"

    def test_completion_sent_still_drops(self):
        """A session that already delivered does NOT defer — it drops."""
        action = determine_delivery_action(
            msg="stray output",
            stop_reason="end_turn",
            auto_continue_count=0,
            max_nudge_count=MAX_NUDGE_COUNT,
            completion_sent=True,
            last_compaction_ts=time.time(),
        )
        assert action == "drop"


class TestInvalidLastCompactionTs:
    """Defensive: malformed last_compaction_ts does not crash the router."""

    def test_string_becomes_deliver(self):
        """A non-numeric last_compaction_ts falls through to normal classification."""
        action = determine_delivery_action(
            msg="output",
            stop_reason="end_turn",
            auto_continue_count=0,
            max_nudge_count=MAX_NUDGE_COUNT,
            last_compaction_ts="not a timestamp",  # type: ignore[arg-type]
        )
        assert action == "deliver"
