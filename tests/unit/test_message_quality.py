"""Tests for bridge.message_quality — narration detection and delivery quality filters."""

from bridge.message_quality import (
    NARRATION_NUDGE_FEEDBACK,
    NARRATION_FALLBACK_MESSAGE,
    PROCESS_NARRATION_PATTERNS,
    is_narration_only,
)


class TestIsNarrationOnly:
    """Test the is_narration_only() heuristic."""

    # --- True cases: pure narration ---

    def test_single_narration_line(self):
        """Single 'Let me check' line is narration-only."""
        assert is_narration_only("Let me check how the observer is configured.")

    def test_multiple_narration_lines(self):
        """Multiple narration lines are still narration-only."""
        text = "Let me check the code.\nNow let me examine the tests."
        assert is_narration_only(text)

    def test_looking_at_pattern(self):
        """'Looking at the...' is narration."""
        assert is_narration_only("Looking at the configuration file.")

    def test_ill_start_pattern(self):
        """'I'll start by...' is narration."""
        assert is_narration_only("I'll start by reading the config.")

    def test_plan_sharing(self):
        """Plan-sharing narration like 'Here's my plan' is narration-only."""
        assert is_narration_only("Here's my plan:")

    def test_approach_narration(self):
        """'My approach is...' is narration-only."""
        assert is_narration_only("My approach is to check the logs first.")

    def test_step_narration(self):
        """'Step 1: ...' is narration-only."""
        assert is_narration_only("Step 1: Check the configuration")

    def test_good_period(self):
        """Bare 'Good.' is narration."""
        assert is_narration_only("Good.")

    def test_alright_pattern(self):
        """'Alright, let me' is narration."""
        assert is_narration_only("Alright, let me check the logs.")

    def test_ok_pattern(self):
        """'OK, let me' is narration."""
        assert is_narration_only("OK, let me look at the tests.")

    # --- False cases: substantive content ---

    def test_empty_string(self):
        """Empty string is NOT narration-only (it's nothing)."""
        assert not is_narration_only("")

    def test_whitespace_only(self):
        """Whitespace-only is NOT narration-only."""
        assert not is_narration_only("   \n  \t  ")

    def test_none_input(self):
        """None-ish input is NOT narration-only."""
        assert not is_narration_only("")

    def test_narration_with_findings(self):
        """Narration + substantive findings (file path) is NOT narration-only."""
        text = "Let me check the config. Found the issue in bridge/observer.py line 42."
        assert not is_narration_only(text)

    def test_narration_with_code_fence(self):
        """Narration + code fence is NOT narration-only."""
        text = "Let me look at the logs.\n```\nERROR: connection refused\n```"
        assert not is_narration_only(text)

    def test_narration_with_url(self):
        """Narration + URL is NOT narration-only."""
        text = "Let me check the issue at https://github.com/org/repo/issues/42"
        assert not is_narration_only(text)

    def test_narration_with_traceback(self):
        """Narration + traceback is NOT narration-only."""
        text = 'Let me look.\nTraceback (most recent call last):\n  File "test.py"'
        assert not is_narration_only(text)

    def test_long_output_not_narration(self):
        """Output over 500 chars is NOT narration-only (length gate)."""
        text = "Let me check. " * 50  # ~700 chars
        assert not is_narration_only(text)

    def test_substantive_response(self):
        """A normal substantive response is NOT narration-only."""
        text = "The issue is caused by a race condition in the observer module."
        assert not is_narration_only(text)

    def test_mixed_narration_and_substance(self):
        """Lines that don't all match narration patterns are NOT narration-only."""
        text = "Let me check the code.\nThe problem is in the routing logic."
        assert not is_narration_only(text)

    def test_narration_with_file_path(self):
        """Narration containing a file path is NOT narration-only."""
        text = "Let me check agent/job_queue.py for the issue."
        assert not is_narration_only(text)

    # --- Edge cases ---

    def test_multiline_with_blank_lines(self):
        """Blank lines between narration lines are ignored."""
        text = "Let me check the code.\n\nNow let me examine the tests.\n"
        assert is_narration_only(text)

    def test_numbered_steps_only(self):
        """Numbered step narration is narration-only."""
        text = "1. First check the config\n2. Then look at the tests"
        assert is_narration_only(text)


class TestNarrationPatterns:
    """Verify the shared patterns are importable and functional."""

    def test_patterns_list_not_empty(self):
        """PROCESS_NARRATION_PATTERNS should have entries."""
        assert len(PROCESS_NARRATION_PATTERNS) > 0

    def test_let_me_check_matches(self):
        """'Let me check' should match a pattern."""
        assert any(p.match("Let me check the code") for p in PROCESS_NARRATION_PATTERNS)

    def test_normal_text_no_match(self):
        """Normal text should not match any pattern."""
        assert not any(p.match("The bug is in line 42") for p in PROCESS_NARRATION_PATTERNS)


class TestConstants:
    """Verify the exported constants are sensible."""

    def test_fallback_message_not_empty(self):
        assert len(NARRATION_FALLBACK_MESSAGE) > 0

    def test_nudge_feedback_not_empty(self):
        assert len(NARRATION_NUDGE_FEEDBACK) > 0

    def test_fallback_message_is_user_facing(self):
        """Fallback should not contain internal jargon."""
        assert "observer" not in NARRATION_FALLBACK_MESSAGE.lower()
        assert "narration" not in NARRATION_FALLBACK_MESSAGE.lower()

    def test_nudge_feedback_instructs_continuation(self):
        """Coaching message should instruct the worker to continue."""
        assert "continue" in NARRATION_NUDGE_FEEDBACK.lower()
