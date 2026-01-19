"""
Integration tests for telegram-history tool.

Run with: pytest tools/telegram-history/tests/ -v
"""

import tempfile
import pytest
from pathlib import Path
from datetime import datetime, timedelta

from tools.telegram_history import (
    search_history,
    store_message,
    get_recent_messages,
    get_chat_stats,
)


class TestTelegramHistoryInstallation:
    """Verify tool is properly configured."""

    def test_import(self):
        """Tool can be imported."""
        from tools.telegram_history import search_history
        assert callable(search_history)


class TestTelegramHistoryValidation:
    """Test input validation."""

    def test_empty_query(self):
        """Empty query returns error."""
        result = search_history("", "chat123")
        assert "error" in result

    def test_empty_chat_id(self):
        """Empty chat_id returns error."""
        result = search_history("test", "")
        assert "error" in result


class TestStoreMessage:
    """Test message storage."""

    @pytest.fixture
    def test_db(self):
        """Create temporary test database."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            yield db_path

    def test_store_message(self, test_db):
        """Store message successfully."""
        result = store_message(
            chat_id="test_chat",
            content="Hello, world!",
            sender="test_user",
            db_path=test_db,
        )

        assert result["stored"] is True
        assert result["chat_id"] == "test_chat"

    def test_store_with_timestamp(self, test_db):
        """Store message with custom timestamp."""
        custom_time = datetime(2024, 1, 15, 10, 30, 0)
        result = store_message(
            chat_id="test_chat",
            content="Time test",
            timestamp=custom_time,
            db_path=test_db,
        )

        assert result["stored"] is True


class TestSearchHistory:
    """Test history search."""

    @pytest.fixture
    def populated_db(self):
        """Create and populate test database."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"

            # Store test messages
            messages = [
                ("Python is great", "user1", datetime.now() - timedelta(days=1)),
                ("I love programming", "user2", datetime.now() - timedelta(days=2)),
                ("Python and JavaScript", "user1", datetime.now() - timedelta(days=3)),
                ("Old message", "user3", datetime.now() - timedelta(days=60)),
            ]

            for content, sender, timestamp in messages:
                store_message(
                    chat_id="test_chat",
                    content=content,
                    sender=sender,
                    timestamp=timestamp,
                    db_path=db_path,
                )

            yield db_path

    def test_basic_search(self, populated_db):
        """Basic search finds matching messages."""
        result = search_history("Python", "test_chat", db_path=populated_db)

        assert "error" not in result
        assert result["total_matches"] > 0
        assert all("python" in r["content"].lower() for r in result["results"])

    def test_no_results(self, populated_db):
        """Search with no matches returns empty."""
        result = search_history("nonexistent", "test_chat", db_path=populated_db)

        assert "error" not in result
        assert result["total_matches"] == 0

    def test_time_window(self, populated_db):
        """Time window filters old messages."""
        result = search_history(
            "message",
            "test_chat",
            max_age_days=30,
            db_path=populated_db,
        )

        # Should not find the 60-day old message
        assert "error" not in result

    def test_relevance_scoring(self, populated_db):
        """Results are scored by relevance."""
        result = search_history("Python", "test_chat", db_path=populated_db)

        assert "error" not in result
        if len(result["results"]) > 1:
            # Results should be sorted by relevance
            scores = [r["relevance_score"] for r in result["results"]]
            assert scores == sorted(scores, reverse=True)


class TestGetRecentMessages:
    """Test recent messages retrieval."""

    @pytest.fixture
    def populated_db(self):
        """Create and populate test database."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"

            for i in range(5):
                store_message(
                    chat_id="test_chat",
                    content=f"Message {i}",
                    sender="user",
                    db_path=db_path,
                )

            yield db_path

    def test_get_recent(self, populated_db):
        """Get recent messages."""
        result = get_recent_messages("test_chat", limit=3, db_path=populated_db)

        assert "error" not in result
        assert result["count"] == 3


class TestGetChatStats:
    """Test chat statistics."""

    @pytest.fixture
    def populated_db(self):
        """Create and populate test database."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"

            for i in range(5):
                store_message(
                    chat_id="test_chat",
                    content=f"Message {i}",
                    sender=f"user{i % 2}",
                    db_path=db_path,
                )

            yield db_path

    def test_get_stats(self, populated_db):
        """Get chat statistics."""
        result = get_chat_stats("test_chat", db_path=populated_db)

        assert "error" not in result
        assert result["total_messages"] == 5
        assert result["unique_senders"] == 2
