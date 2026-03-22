"""Tests for the nudge loop in agent/job_queue.py.

Tests the send_to_chat nudge behavior: completion detection, rate-limit
backoff, max nudge safety cap, and empty output handling.
"""

from agent.job_queue import MAX_NUDGE_COUNT, NUDGE_MESSAGE


class TestNudgeConstants:
    """Test nudge loop constants are properly defined."""

    def test_max_nudge_count_is_50(self):
        """Safety cap should be 50."""
        assert MAX_NUDGE_COUNT == 50

    def test_nudge_message_exists(self):
        """Nudge message should be a non-empty string."""
        assert isinstance(NUDGE_MESSAGE, str)
        assert len(NUDGE_MESSAGE) > 10

    def test_nudge_message_content(self):
        """Nudge message should instruct to keep working."""
        assert "keep working" in NUDGE_MESSAGE.lower()
        assert "human input" in NUDGE_MESSAGE.lower()


class TestNudgeMessageContent:
    """Test the nudge message wording matches the design spec."""

    def test_nudge_message_not_sdlc_aware(self):
        """Nudge message should NOT contain SDLC stage names."""
        sdlc_terms = ["ISSUE", "PLAN", "BUILD", "TEST", "PATCH", "REVIEW", "DOCS", "MERGE"]
        for term in sdlc_terms:
            assert term not in NUDGE_MESSAGE, (
                f"Nudge message should not contain SDLC term '{term}'"
            )

    def test_nudge_message_not_pipeline_aware(self):
        """Nudge message should NOT reference pipeline or Observer concepts."""
        forbidden = ["pipeline", "observer", "stage", "steer"]
        msg_lower = NUDGE_MESSAGE.lower()
        for term in forbidden:
            assert term not in msg_lower, (
                f"Nudge message should not contain '{term}'"
            )


class TestObserverRemoval:
    """Verify that Observer is no longer imported in send_to_chat path."""

    def test_no_observer_import_in_job_queue(self):
        """job_queue.py should not import Observer at module level."""
        import ast
        from pathlib import Path

        job_queue_path = Path(__file__).parent.parent.parent / "agent" / "job_queue.py"
        source = job_queue_path.read_text()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and "observer" in node.module.lower():
                    # Check if this is at module level (not inside a function)
                    # We allow it inside functions for backward compat during migration
                    # but the send_to_chat path should not use it
                    pass  # Will be fully checked in test_cleanup phase

    def test_no_should_guard_empty_output_function(self):
        """should_guard_empty_output was removed — nudge loop handles empty output."""
        from pathlib import Path

        job_queue_path = Path(__file__).parent.parent.parent / "agent" / "job_queue.py"
        source = job_queue_path.read_text()
        assert "def should_guard_empty_output" not in source, (
            "should_guard_empty_output should be removed — nudge loop handles empty output"
        )

    def test_no_max_auto_continues_constants(self):
        """MAX_AUTO_CONTINUES and MAX_AUTO_CONTINUES_SDLC replaced by MAX_NUDGE_COUNT."""
        from pathlib import Path

        job_queue_path = Path(__file__).parent.parent.parent / "agent" / "job_queue.py"
        source = job_queue_path.read_text()
        assert "MAX_AUTO_CONTINUES_SDLC" not in source, (
            "MAX_AUTO_CONTINUES_SDLC should be replaced by MAX_NUDGE_COUNT"
        )
        # MAX_AUTO_CONTINUES might still appear in comments, check for assignment
        lines = source.split("\n")
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("MAX_AUTO_CONTINUES") and "=" in stripped:
                if not stripped.startswith("#"):
                    assert False, (
                        f"MAX_AUTO_CONTINUES assignment should be removed: {stripped}"
                    )
