"""Tests for agent/output_router.py — delivery action routing logic.

Covers the waiting_for_children guard (issue #1004) and general routing.
"""

import pytest

from agent.output_router import (
    MAX_NUDGE_COUNT,
    PIPELINE_COMPLETE_MARKER,
    determine_delivery_action,
)


class TestWaitingForChildrenGuard:
    """Issue #1004: PM in waiting_for_children must deliver, not nudge."""

    def test_pm_sdlc_waiting_for_children_delivers(self):
        """PM+SDLC session in waiting_for_children returns deliver, not nudge_continue."""
        action = determine_delivery_action(
            msg="Dispatched BUILD. Waiting for completion.",
            stop_reason="end_turn",
            auto_continue_count=0,
            max_nudge_count=MAX_NUDGE_COUNT,
            session_status="waiting_for_children",
            session_type="pm",
            classification_type="sdlc",
        )
        assert action == "deliver"

    def test_pm_sdlc_running_still_nudges(self):
        """PM+SDLC session in running status still returns nudge_continue."""
        action = determine_delivery_action(
            msg="Working on the pipeline...",
            stop_reason="end_turn",
            auto_continue_count=0,
            max_nudge_count=MAX_NUDGE_COUNT,
            session_status="running",
            session_type="pm",
            classification_type="sdlc",
        )
        assert action == "nudge_continue"

    def test_pm_sdlc_active_still_nudges(self):
        """PM+SDLC session in active status still returns nudge_continue."""
        action = determine_delivery_action(
            msg="Assessing pipeline state...",
            stop_reason="end_turn",
            auto_continue_count=0,
            max_nudge_count=MAX_NUDGE_COUNT,
            session_status="active",
            session_type="pm",
            classification_type="sdlc",
        )
        assert action == "nudge_continue"

    def test_teammate_waiting_for_children_unaffected(self):
        """Teammate session in waiting_for_children delivers normally (not PM path)."""
        action = determine_delivery_action(
            msg="Some output from teammate.",
            stop_reason="end_turn",
            auto_continue_count=0,
            max_nudge_count=MAX_NUDGE_COUNT,
            session_status="waiting_for_children",
            session_type="teammate",
            classification_type=None,
        )
        # Teammate sessions don't hit the PM+SDLC path, so they deliver normally
        assert action == "deliver"

    def test_waiting_for_children_with_none_session_type(self):
        """waiting_for_children guard should not trigger without session_type=pm."""
        action = determine_delivery_action(
            msg="Some output.",
            stop_reason="end_turn",
            auto_continue_count=0,
            max_nudge_count=MAX_NUDGE_COUNT,
            session_status="waiting_for_children",
            session_type=None,
            classification_type=None,
        )
        assert action == "deliver"

    def test_pm_sdlc_waiting_for_children_with_pipeline_complete_marker(self):
        """Even with PIPELINE_COMPLETE_MARKER, waiting_for_children guard takes precedence."""
        action = determine_delivery_action(
            msg=f"Done. {PIPELINE_COMPLETE_MARKER}",
            stop_reason="end_turn",
            auto_continue_count=0,
            max_nudge_count=MAX_NUDGE_COUNT,
            session_status="waiting_for_children",
            session_type="pm",
            classification_type="sdlc",
        )
        # waiting_for_children guard fires before the PM+SDLC check
        assert action == "deliver"

    def test_pm_non_sdlc_waiting_for_children_delivers(self):
        """PM non-SDLC session in waiting_for_children still delivers."""
        action = determine_delivery_action(
            msg="Waiting for child.",
            stop_reason="end_turn",
            auto_continue_count=0,
            max_nudge_count=MAX_NUDGE_COUNT,
            session_status="waiting_for_children",
            session_type="pm",
            classification_type="collaboration",
        )
        assert action == "deliver"

    @pytest.mark.parametrize("session_status", [None, "running", "active", "pending"])
    def test_non_waiting_statuses_do_not_trigger_guard(self, session_status):
        """Only waiting_for_children triggers the early deliver guard."""
        action = determine_delivery_action(
            msg="Pipeline work in progress.",
            stop_reason="end_turn",
            auto_continue_count=0,
            max_nudge_count=MAX_NUDGE_COUNT,
            session_status=session_status,
            session_type="pm",
            classification_type="sdlc",
        )
        assert action == "nudge_continue"


class TestExistingRouting:
    """Ensure existing routing behavior is preserved."""

    def test_terminal_status_delivers_already_completed(self):
        action = determine_delivery_action(
            msg="final output",
            stop_reason="end_turn",
            auto_continue_count=0,
            max_nudge_count=MAX_NUDGE_COUNT,
            session_status="completed",
        )
        assert action == "deliver_already_completed"

    def test_completion_sent_drops(self):
        action = determine_delivery_action(
            msg="more output",
            stop_reason="end_turn",
            auto_continue_count=0,
            max_nudge_count=MAX_NUDGE_COUNT,
            completion_sent=True,
        )
        assert action == "drop"

    def test_pm_sdlc_pipeline_complete_marker(self):
        action = determine_delivery_action(
            msg=f"All done! {PIPELINE_COMPLETE_MARKER}",
            stop_reason="end_turn",
            auto_continue_count=0,
            max_nudge_count=MAX_NUDGE_COUNT,
            session_type="pm",
            classification_type="sdlc",
        )
        assert action == "deliver_pipeline_complete"

    def test_pm_sdlc_normal_nudges(self):
        action = determine_delivery_action(
            msg="Working on BUILD stage...",
            stop_reason="end_turn",
            auto_continue_count=0,
            max_nudge_count=MAX_NUDGE_COUNT,
            session_type="pm",
            classification_type="sdlc",
        )
        assert action == "nudge_continue"

    def test_rate_limited_nudges(self):
        action = determine_delivery_action(
            msg="partial",
            stop_reason="rate_limited",
            auto_continue_count=0,
            max_nudge_count=MAX_NUDGE_COUNT,
        )
        assert action == "nudge_rate_limited"

    def test_empty_output_nudges(self):
        action = determine_delivery_action(
            msg="",
            stop_reason="end_turn",
            auto_continue_count=0,
            max_nudge_count=MAX_NUDGE_COUNT,
        )
        assert action == "nudge_empty"

    def test_normal_end_turn_delivers(self):
        action = determine_delivery_action(
            msg="Here is the answer.",
            stop_reason="end_turn",
            auto_continue_count=0,
            max_nudge_count=MAX_NUDGE_COUNT,
        )
        assert action == "deliver"
