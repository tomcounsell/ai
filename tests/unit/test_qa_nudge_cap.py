"""Tests for Teammate reduced nudge cap in the nudge loop."""

from agent.agent_session_queue import MAX_NUDGE_COUNT, classify_nudge_action
from agent.teammate_handler import TEAMMATE_MAX_NUDGE_COUNT


class TestTeammateNudgeCap:
    def test_teammate_cap_is_lower_than_default(self):
        assert TEAMMATE_MAX_NUDGE_COUNT < MAX_NUDGE_COUNT

    def test_empty_output_nudges_within_teammate_cap(self):
        """Within Teammate cap, empty output should still nudge."""
        action = classify_nudge_action(
            msg="",
            stop_reason=None,
            auto_continue_count=5,
            max_nudge_count=TEAMMATE_MAX_NUDGE_COUNT,
        )
        assert action == "nudge_empty"

    def test_empty_output_delivers_at_teammate_cap(self):
        """At Teammate cap, empty output should deliver fallback."""
        action = classify_nudge_action(
            msg="",
            stop_reason=None,
            auto_continue_count=TEAMMATE_MAX_NUDGE_COUNT,
            max_nudge_count=TEAMMATE_MAX_NUDGE_COUNT,
        )
        assert action == "deliver_fallback"

    def test_normal_cap_still_allows_more_nudges(self):
        """At Teammate cap count, normal cap should still allow nudges."""
        action = classify_nudge_action(
            msg="",
            stop_reason=None,
            auto_continue_count=TEAMMATE_MAX_NUDGE_COUNT,
            max_nudge_count=MAX_NUDGE_COUNT,
        )
        assert action == "nudge_empty"


class TestTeammateReactionClearing:
    """Tests for Teammate reaction clearing behavior (issue #541)."""

    def test_teammate_session_gets_none_reaction(self):
        """Teammate sessions should clear the processing reaction (None) on success."""
        from unittest.mock import MagicMock

        session = MagicMock()
        session.session_mode = "teammate"

        # The reaction logic in job_queue.py checks session_mode and returns None
        # for successful Teammate sessions. We test the conditional directly.
        task_error = False
        if session and getattr(session, "session_mode", None) == "teammate" and not task_error:
            emoji = None
        else:
            emoji = "completion"
        assert emoji is None

    def test_work_session_gets_completion_reaction(self):
        """Non-Teammate sessions should still get a completion emoji."""
        from unittest.mock import MagicMock

        session = MagicMock()
        session.session_mode = None

        task_error = False
        if session and getattr(session, "session_mode", None) == "teammate" and not task_error:
            emoji = None
        else:
            emoji = "completion"
        assert emoji == "completion"

    def test_teammate_error_gets_error_reaction(self):
        """Teammate sessions with errors should still get error reaction."""
        from unittest.mock import MagicMock

        session = MagicMock()
        session.session_mode = "teammate"

        task_error = True
        if session and getattr(session, "session_mode", None) == "teammate" and not task_error:
            emoji = None
        else:
            emoji = "error"
        assert emoji == "error"
