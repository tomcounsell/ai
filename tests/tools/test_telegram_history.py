"""Tests for the Telegram history tool."""

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tools.telegram_history import (
    store_message,
    search_history,
    get_recent_messages,
    get_chat_stats,
)


class TestStoreMessage:
    """Test message storage."""

    def test_store_basic_message(self, tmp_path):
        """Test storing a basic message."""
        db_path = tmp_path / "test.db"
        result = store_message(
            chat_id="test_chat",
            content="Hello, world!",
            sender="user1",
            db_path=db_path
        )

        assert result.get("stored") is True
        assert result.get("chat_id") == "test_chat"
        assert result.get("id") is not None

    def test_store_message_with_timestamp(self, tmp_path):
        """Test storing a message with custom timestamp."""
        db_path = tmp_path / "test.db"
        timestamp = datetime(2024, 1, 15, 10, 30, 0)
        result = store_message(
            chat_id="test_chat",
            content="Test message",
            timestamp=timestamp,
            db_path=db_path
        )

        assert result.get("stored") is True

    def test_store_message_types(self, tmp_path):
        """Test storing different message types."""
        db_path = tmp_path / "test.db"

        for msg_type in ["text", "photo", "document"]:
            result = store_message(
                chat_id="test_chat",
                content=f"Content for {msg_type}",
                message_type=msg_type,
                db_path=db_path
            )
            assert result.get("stored") is True


class TestSearchHistory:
    """Test history search."""

    def test_search_empty_query_returns_error(self, tmp_path):
        """Test that empty query returns error."""
        result = search_history("", chat_id="test", db_path=tmp_path / "test.db")
        assert "error" in result
        assert "empty" in result["error"].lower()

    def test_search_missing_chat_id_returns_error(self, tmp_path):
        """Test that missing chat ID returns error."""
        result = search_history("query", chat_id="", db_path=tmp_path / "test.db")
        assert "error" in result

    def test_search_finds_matching_messages(self, tmp_path):
        """Test that search finds matching messages."""
        db_path = tmp_path / "test.db"

        # Store some messages
        store_message(chat_id="chat1", content="Hello Python world", db_path=db_path)
        store_message(chat_id="chat1", content="JavaScript is great", db_path=db_path)
        store_message(chat_id="chat1", content="Python for data science", db_path=db_path)

        result = search_history("Python", chat_id="chat1", db_path=db_path)

        assert "error" not in result
        assert result.get("total_matches", 0) >= 2
        assert all("python" in r["content"].lower() for r in result["results"])

    def test_search_respects_max_results(self, tmp_path):
        """Test that search respects max_results."""
        db_path = tmp_path / "test.db"

        # Store many messages
        for i in range(10):
            store_message(chat_id="chat1", content=f"Message {i} about Python", db_path=db_path)

        result = search_history("Python", chat_id="chat1", max_results=3, db_path=db_path)

        assert "error" not in result
        assert len(result["results"]) <= 3

    def test_search_scores_relevance(self, tmp_path):
        """Test that search scores relevance."""
        db_path = tmp_path / "test.db"

        store_message(chat_id="chat1", content="Python is great", db_path=db_path)

        result = search_history("Python", chat_id="chat1", db_path=db_path)

        if result.get("results"):
            assert "relevance_score" in result["results"][0]
            assert result["results"][0]["relevance_score"] > 0


class TestGetRecentMessages:
    """Test getting recent messages."""

    def test_get_recent_empty_chat(self, tmp_path):
        """Test getting messages from empty chat."""
        db_path = tmp_path / "test.db"
        result = get_recent_messages("chat1", db_path=db_path)

        assert "error" not in result
        assert result.get("count") == 0
        assert result["messages"] == []

    def test_get_recent_missing_chat_id_returns_error(self, tmp_path):
        """Test that missing chat ID returns error."""
        result = get_recent_messages("", db_path=tmp_path / "test.db")
        assert "error" in result

    def test_get_recent_messages_ordered_by_time(self, tmp_path):
        """Test that messages are ordered by time."""
        db_path = tmp_path / "test.db"

        # Store messages with different timestamps
        base_time = datetime.now()
        store_message(
            chat_id="chat1",
            content="First message",
            timestamp=base_time - timedelta(hours=2),
            db_path=db_path
        )
        store_message(
            chat_id="chat1",
            content="Second message",
            timestamp=base_time - timedelta(hours=1),
            db_path=db_path
        )
        store_message(
            chat_id="chat1",
            content="Third message",
            timestamp=base_time,
            db_path=db_path
        )

        result = get_recent_messages("chat1", limit=3, db_path=db_path)

        assert "error" not in result
        assert result["count"] == 3
        # Most recent should be first
        assert "Third" in result["messages"][0]["content"]

    def test_get_recent_respects_limit(self, tmp_path):
        """Test that limit is respected."""
        db_path = tmp_path / "test.db"

        for i in range(10):
            store_message(chat_id="chat1", content=f"Message {i}", db_path=db_path)

        result = get_recent_messages("chat1", limit=5, db_path=db_path)

        assert "error" not in result
        assert len(result["messages"]) == 5


class TestGetChatStats:
    """Test chat statistics."""

    def test_get_stats_empty_chat(self, tmp_path):
        """Test stats for empty chat."""
        db_path = tmp_path / "test.db"
        result = get_chat_stats("chat1", db_path=db_path)

        assert "error" not in result
        assert result["total_messages"] == 0

    def test_get_stats_with_messages(self, tmp_path):
        """Test stats with messages."""
        db_path = tmp_path / "test.db"

        store_message(chat_id="chat1", content="Message 1", sender="user1", db_path=db_path)
        store_message(chat_id="chat1", content="Message 2", sender="user2", db_path=db_path)
        store_message(chat_id="chat1", content="Message 3", sender="user1", db_path=db_path)

        result = get_chat_stats("chat1", db_path=db_path)

        assert "error" not in result
        assert result["total_messages"] == 3
        assert result["unique_senders"] == 2


class TestDatabaseIsolation:
    """Test that different chats are isolated."""

    def test_search_only_returns_target_chat(self, tmp_path):
        """Test that search only returns messages from target chat."""
        db_path = tmp_path / "test.db"

        store_message(chat_id="chat1", content="Python in chat1", db_path=db_path)
        store_message(chat_id="chat2", content="Python in chat2", db_path=db_path)

        result = search_history("Python", chat_id="chat1", db_path=db_path)

        assert "error" not in result
        assert result["total_matches"] == 1
        assert result["results"][0]["content"] == "Python in chat1"
