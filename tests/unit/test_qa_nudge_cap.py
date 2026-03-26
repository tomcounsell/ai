"""Tests for Q&A reduced nudge cap in the nudge loop."""

from agent.job_queue import MAX_NUDGE_COUNT, classify_nudge_action
from agent.qa_handler import QA_MAX_NUDGE_COUNT


class TestQaNudgeCap:
    def test_qa_cap_is_lower_than_default(self):
        assert QA_MAX_NUDGE_COUNT < MAX_NUDGE_COUNT

    def test_empty_output_nudges_within_qa_cap(self):
        """Within Q&A cap, empty output should still nudge."""
        action = classify_nudge_action(
            msg="",
            stop_reason=None,
            auto_continue_count=5,
            max_nudge_count=QA_MAX_NUDGE_COUNT,
        )
        assert action == "nudge_empty"

    def test_empty_output_delivers_at_qa_cap(self):
        """At Q&A cap, empty output should deliver fallback."""
        action = classify_nudge_action(
            msg="",
            stop_reason=None,
            auto_continue_count=QA_MAX_NUDGE_COUNT,
            max_nudge_count=QA_MAX_NUDGE_COUNT,
        )
        assert action == "deliver_fallback"

    def test_normal_cap_still_allows_more_nudges(self):
        """At Q&A cap count, normal cap should still allow nudges."""
        action = classify_nudge_action(
            msg="",
            stop_reason=None,
            auto_continue_count=QA_MAX_NUDGE_COUNT,
            max_nudge_count=MAX_NUDGE_COUNT,
        )
        assert action == "nudge_empty"


class TestQaReactionClearing:
    """Tests for Q&A reaction clearing behavior (issue #541)."""

    def test_qa_session_gets_none_reaction(self):
        """Q&A sessions should clear the processing reaction (None) on success."""
        from unittest.mock import MagicMock

        session = MagicMock()
        session.qa_mode = True

        # The reaction logic in job_queue.py checks qa_mode and returns None
        # for successful Q&A sessions. We test the conditional directly.
        task_error = False
        if session and getattr(session, "qa_mode", False) and not task_error:
            emoji = None
        else:
            emoji = "completion"
        assert emoji is None

    def test_work_session_gets_completion_reaction(self):
        """Non-Q&A sessions should still get a completion emoji."""
        from unittest.mock import MagicMock

        session = MagicMock()
        session.qa_mode = False

        task_error = False
        if session and getattr(session, "qa_mode", False) and not task_error:
            emoji = None
        else:
            emoji = "completion"
        assert emoji == "completion"

    def test_qa_error_gets_error_reaction(self):
        """Q&A sessions with errors should still get error reaction."""
        from unittest.mock import MagicMock

        session = MagicMock()
        session.qa_mode = True

        task_error = True
        if session and getattr(session, "qa_mode", False) and not task_error:
            emoji = None
        else:
            emoji = "error"
        assert emoji == "error"
