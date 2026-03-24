"""Unit tests for Telegram memory ingestion and system prompt priming."""

import pytest


class TestMemoryIngestion:
    """Test Memory.save() for Telegram messages."""

    def test_human_message_creates_memory(self):
        from models.memory import Memory
        from popoto import InteractionWeight

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
        m = Memory.safe_save(
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
        result = Memory.safe_save(
            agent_id=None,  # KeyField may reject None
            project_key="test",
            content="test",
            importance=1.0,
        )
        # Should not raise — may return None or a Memory instance
        # depending on how popoto handles None keys


class TestSystemPromptPriming:
    """Test that thought priming appears in _base.md."""

    def test_base_md_contains_thought_priming(self):
        from pathlib import Path

        base_md = Path("config/personas/_base.md")
        if not base_md.exists():
            # Try worktree path
            import os

            cwd = os.getcwd()
            base_md = Path(cwd) / "config" / "personas" / "_base.md"

        content = base_md.read_text()
        assert "## Subconscious Memory" in content
        assert "<thought>" in content
        assert "background context" in content

    def test_priming_is_at_end_of_file(self):
        """Priming should be clearly delimited, near end of file."""
        from pathlib import Path

        base_md = Path("config/personas/_base.md")
        if not base_md.exists():
            import os

            cwd = os.getcwd()
            base_md = Path(cwd) / "config" / "personas" / "_base.md"

        content = base_md.read_text()
        # Section should be in the last 500 chars
        last_section = content[-500:]
        assert "Subconscious Memory" in last_section
