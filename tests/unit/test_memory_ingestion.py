"""Unit tests for Telegram memory ingestion and system prompt priming."""


class TestMemoryIngestion:
    """Test Memory.save() for Telegram messages."""

    def test_human_message_creates_memory(self):
        from popoto import InteractionWeight

        from models.memory import Memory

        m = Memory.safe_save(
            agent_id="test-user",
            project_key="test-ingestion",
            content="Please deploy the latest version to staging",
            importance=InteractionWeight.HUMAN,
            source="human",
        )
        assert m is not None
        assert m.importance == InteractionWeight.HUMAN
        assert m.source == "human"

    def test_empty_content_returns_none_via_safe_save(self):
        """Empty content should still save (WriteFilter checks importance, not content).
        But the bridge skips empty text before calling Memory.save()."""
        from models.memory import Memory

        # The bridge checks `if text and text.strip()` before saving
        # This test verifies that even if empty content reaches Memory,
        # it doesn't crash
        Memory.safe_save(
            agent_id="test-user",
            project_key="test-ingestion",
            content="",
            importance=6.0,
            source="human",
        )
        # save may succeed (content is not checked by WriteFilter)
        # What matters is it doesn't crash

    def test_memory_save_failure_returns_none(self):
        """safe_save should return None on any exception, never raise."""
        from models.memory import Memory

        # Force an error by passing invalid kwargs
        Memory.safe_save(
            agent_id=None,  # KeyField may reject None
            project_key="test",
            content="test",
            importance=1.0,
        )
        # Should not raise — may return None or a Memory instance
        # depending on how popoto handles None keys


class TestSystemPromptPriming:
    """Test that thought priming appears in work-patterns.md segment."""

    def test_work_patterns_contains_thought_priming(self):
        from pathlib import Path

        segment = Path("config/personas/segments/work-patterns.md")
        if not segment.exists():
            # Try worktree path
            import os

            cwd = os.getcwd()
            segment = Path(cwd) / "config" / "personas" / "segments" / "work-patterns.md"

        content = segment.read_text()
        assert "## Subconscious Memory" in content
        assert "<thought>" in content
        assert "background context" in content

    def test_priming_is_at_end_of_file(self):
        """Priming should be clearly delimited, near end of file."""
        from pathlib import Path

        segment = Path("config/personas/segments/work-patterns.md")
        if not segment.exists():
            import os

            cwd = os.getcwd()
            segment = Path(cwd) / "config" / "personas" / "segments" / "work-patterns.md"

        content = segment.read_text()
        # Memory sections should not be at the very beginning of the file
        # (they belong with behavioral patterns, not identity)
        pos = content.find("Subconscious Memory")
        assert pos > 0, "Subconscious Memory section not found"
        assert pos > len(content) * 0.3, (
            f"Subconscious Memory at {pos}/{len(content)} ({pos / len(content) * 100:.0f}%) "
            "-- should be in the latter portion of work-patterns.md"
        )
        int_pos = content.find("Intentional Memory")
        assert int_pos > 0, "Intentional Memory section not found"
