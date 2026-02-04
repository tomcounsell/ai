"""Tests for the Telegram history tool."""

from datetime import datetime, timedelta

from tools.telegram_history import (
    get_chat_stats,
    get_link_by_url,
    get_link_stats,
    get_recent_messages,
    list_links,
    search_history,
    search_links,
    store_link,
    store_message,
    update_link,
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
            db_path=db_path,
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
            db_path=db_path,
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
                db_path=db_path,
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
        store_message(
            chat_id="chat1", content="Python for data science", db_path=db_path
        )

        result = search_history("Python", chat_id="chat1", db_path=db_path)

        assert "error" not in result
        assert result.get("total_matches", 0) >= 2
        assert all("python" in r["content"].lower() for r in result["results"])

    def test_search_respects_max_results(self, tmp_path):
        """Test that search respects max_results."""
        db_path = tmp_path / "test.db"

        # Store many messages
        for i in range(10):
            store_message(
                chat_id="chat1", content=f"Message {i} about Python", db_path=db_path
            )

        result = search_history(
            "Python", chat_id="chat1", max_results=3, db_path=db_path
        )

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
            db_path=db_path,
        )
        store_message(
            chat_id="chat1",
            content="Second message",
            timestamp=base_time - timedelta(hours=1),
            db_path=db_path,
        )
        store_message(
            chat_id="chat1",
            content="Third message",
            timestamp=base_time,
            db_path=db_path,
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

        store_message(
            chat_id="chat1", content="Message 1", sender="user1", db_path=db_path
        )
        store_message(
            chat_id="chat1", content="Message 2", sender="user2", db_path=db_path
        )
        store_message(
            chat_id="chat1", content="Message 3", sender="user1", db_path=db_path
        )

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


# =============================================================================
# Link Storage Tests
# =============================================================================


class TestStoreLink:
    """Test link storage."""

    def test_store_basic_link(self, tmp_path):
        """Test storing a basic link."""
        db_path = tmp_path / "test.db"
        result = store_link(
            url="https://example.com/article",
            sender="Tom",
            chat_id="chat1",
            db_path=db_path,
        )

        assert result.get("stored") is True
        assert result.get("url") == "https://example.com/article"
        assert result.get("domain") == "example.com"

    def test_store_link_extracts_domain(self, tmp_path):
        """Test that domain is extracted correctly."""
        db_path = tmp_path / "test.db"

        result = store_link(
            url="https://www.github.com/user/repo",
            sender="Tom",
            chat_id="chat1",
            db_path=db_path,
        )

        assert result.get("domain") == "github.com"

    def test_store_link_with_metadata(self, tmp_path):
        """Test storing a link with metadata."""
        db_path = tmp_path / "test.db"
        result = store_link(
            url="https://example.com/article",
            sender="Tom",
            chat_id="chat1",
            title="Test Article",
            description="A test article about testing",
            tags=["test", "article"],
            db_path=db_path,
        )

        assert result.get("stored") is True

    def test_store_duplicate_link_updates(self, tmp_path):
        """Test that duplicate links are updated, not duplicated."""
        db_path = tmp_path / "test.db"

        # Store initial link
        store_link(
            url="https://example.com/article",
            sender="Tom",
            chat_id="chat1",
            message_id=100,
            db_path=db_path,
        )

        # Store same link again with more metadata
        store_link(
            url="https://example.com/article",
            sender="Tom",
            chat_id="chat1",
            message_id=100,
            title="Updated Title",
            db_path=db_path,
        )

        # Should still only have one link
        result = list_links(db_path=db_path)
        assert result["total"] == 1


class TestSearchLinks:
    """Test link search."""

    def test_search_by_query(self, tmp_path):
        """Test searching links by text query."""
        db_path = tmp_path / "test.db"

        store_link(
            url="https://python.org",
            sender="Tom",
            chat_id="chat1",
            title="Python Programming",
            db_path=db_path,
        )
        store_link(
            url="https://javascript.info",
            sender="Tom",
            chat_id="chat1",
            title="JavaScript Tutorial",
            db_path=db_path,
        )

        result = search_links(query="Python", db_path=db_path)

        assert "error" not in result
        assert result["count"] == 1
        assert "python" in result["links"][0]["url"].lower()

    def test_search_by_domain(self, tmp_path):
        """Test filtering links by domain."""
        db_path = tmp_path / "test.db"

        store_link(
            url="https://github.com/repo1",
            sender="Tom",
            chat_id="chat1",
            db_path=db_path,
        )
        store_link(
            url="https://github.com/repo2",
            sender="Tom",
            chat_id="chat1",
            db_path=db_path,
        )
        store_link(
            url="https://gitlab.com/repo",
            sender="Tom",
            chat_id="chat1",
            db_path=db_path,
        )

        result = search_links(domain="github.com", db_path=db_path)

        assert "error" not in result
        assert result["count"] == 2
        assert all(link["domain"] == "github.com" for link in result["links"])

    def test_search_by_sender(self, tmp_path):
        """Test filtering links by sender."""
        db_path = tmp_path / "test.db"

        store_link(
            url="https://example.com/1", sender="Tom", chat_id="chat1", db_path=db_path
        )
        store_link(
            url="https://example.com/2",
            sender="Alice",
            chat_id="chat1",
            db_path=db_path,
        )

        result = search_links(sender="Tom", db_path=db_path)

        assert "error" not in result
        assert result["count"] == 1
        assert result["links"][0]["sender"] == "Tom"

    def test_search_by_status(self, tmp_path):
        """Test filtering links by status."""
        db_path = tmp_path / "test.db"

        store_link(
            url="https://example.com/1", sender="Tom", chat_id="chat1", db_path=db_path
        )
        store_link(
            url="https://example.com/2", sender="Tom", chat_id="chat1", db_path=db_path
        )

        # Update one to read status
        links = list_links(db_path=db_path)
        update_link(links["links"][0]["id"], status="read", db_path=db_path)

        result = search_links(status="unread", db_path=db_path)

        assert "error" not in result
        assert result["count"] == 1


class TestListLinks:
    """Test listing links."""

    def test_list_empty(self, tmp_path):
        """Test listing when no links exist."""
        db_path = tmp_path / "test.db"
        result = list_links(db_path=db_path)

        assert "error" not in result
        assert result["count"] == 0
        assert result["total"] == 0

    def test_list_with_pagination(self, tmp_path):
        """Test pagination of links."""
        db_path = tmp_path / "test.db"

        for i in range(10):
            store_link(
                url=f"https://example.com/{i}",
                sender="Tom",
                chat_id="chat1",
                db_path=db_path,
            )

        # First page
        result1 = list_links(limit=3, offset=0, db_path=db_path)
        assert result1["count"] == 3
        assert result1["total"] == 10
        assert result1["has_more"] is True

        # Second page
        result2 = list_links(limit=3, offset=3, db_path=db_path)
        assert result2["count"] == 3
        assert result2["has_more"] is True

        # Last page
        result3 = list_links(limit=3, offset=9, db_path=db_path)
        assert result3["count"] == 1
        assert result3["has_more"] is False


class TestUpdateLink:
    """Test updating links."""

    def test_update_status(self, tmp_path):
        """Test updating link status."""
        db_path = tmp_path / "test.db"

        store_link(
            url="https://example.com", sender="Tom", chat_id="chat1", db_path=db_path
        )
        links = list_links(db_path=db_path)
        link_id = links["links"][0]["id"]

        result = update_link(link_id, status="read", db_path=db_path)

        assert result.get("updated") is True

        # Verify the change
        updated = list_links(db_path=db_path)
        assert updated["links"][0]["status"] == "read"

    def test_update_tags(self, tmp_path):
        """Test updating link tags."""
        db_path = tmp_path / "test.db"

        store_link(
            url="https://example.com", sender="Tom", chat_id="chat1", db_path=db_path
        )
        links = list_links(db_path=db_path)
        link_id = links["links"][0]["id"]

        result = update_link(link_id, tags=["python", "tutorial"], db_path=db_path)

        assert result.get("updated") is True

        updated = list_links(db_path=db_path)
        assert updated["links"][0]["tags"] == ["python", "tutorial"]

    def test_update_notes(self, tmp_path):
        """Test updating link notes."""
        db_path = tmp_path / "test.db"

        store_link(
            url="https://example.com", sender="Tom", chat_id="chat1", db_path=db_path
        )
        links = list_links(db_path=db_path)
        link_id = links["links"][0]["id"]

        result = update_link(
            link_id, notes="Great article about testing", db_path=db_path
        )

        assert result.get("updated") is True

    def test_update_nonexistent_link(self, tmp_path):
        """Test updating a link that doesn't exist."""
        db_path = tmp_path / "test.db"
        result = update_link(9999, status="read", db_path=db_path)

        assert "error" in result
        assert "not found" in result["error"]


class TestGetLinkStats:
    """Test link statistics."""

    def test_stats_empty(self, tmp_path):
        """Test stats when no links exist."""
        db_path = tmp_path / "test.db"
        result = get_link_stats(db_path=db_path)

        assert "error" not in result
        assert result["total_links"] == 0

    def test_stats_with_links(self, tmp_path):
        """Test stats with links."""
        db_path = tmp_path / "test.db"

        store_link(
            url="https://github.com/repo1",
            sender="Tom",
            chat_id="chat1",
            db_path=db_path,
        )
        store_link(
            url="https://github.com/repo2",
            sender="Tom",
            chat_id="chat1",
            db_path=db_path,
        )
        store_link(
            url="https://python.org", sender="Alice", chat_id="chat1", db_path=db_path
        )

        result = get_link_stats(db_path=db_path)

        assert "error" not in result
        assert result["total_links"] == 3
        assert result["unique_domains"] == 2
        assert result["unique_senders"] == 2
        assert result["by_status"]["unread"] == 3

    def test_stats_top_domains(self, tmp_path):
        """Test that top domains are returned."""
        db_path = tmp_path / "test.db"

        for i in range(5):
            store_link(
                url=f"https://github.com/repo{i}",
                sender="Tom",
                chat_id="chat1",
                db_path=db_path,
            )
        for i in range(3):
            store_link(
                url=f"https://python.org/page{i}",
                sender="Tom",
                chat_id="chat1",
                db_path=db_path,
            )

        result = get_link_stats(db_path=db_path)

        assert "error" not in result
        assert len(result["top_domains"]) >= 2
        # github.com should be first with 5 links
        assert result["top_domains"][0]["domain"] == "github.com"
        assert result["top_domains"][0]["count"] == 5


class TestGetLinkByUrl:
    """Test link lookup by URL for caching."""

    def test_get_link_by_url_not_found(self, tmp_path):
        """Test that None is returned when link doesn't exist."""
        db_path = tmp_path / "test.db"
        result = get_link_by_url("https://example.com", db_path=db_path)

        assert result is None

    def test_get_link_by_url_no_summary(self, tmp_path):
        """Test that None is returned when link exists but has no summary."""
        db_path = tmp_path / "test.db"

        # Store link without summary
        store_link(
            url="https://example.com/article",
            sender="Tom",
            chat_id="chat1",
            db_path=db_path,
        )

        result = get_link_by_url("https://example.com/article", db_path=db_path)

        # Should return None because no ai_summary
        assert result is None

    def test_get_link_by_url_with_summary(self, tmp_path):
        """Test that link is returned when it has a summary."""
        db_path = tmp_path / "test.db"

        # Store link with summary
        store_link(
            url="https://example.com/article",
            sender="Tom",
            chat_id="chat1",
            ai_summary="This is a test article about Python programming.",
            db_path=db_path,
        )

        result = get_link_by_url("https://example.com/article", db_path=db_path)

        assert result is not None
        assert result["url"] == "https://example.com/article"
        assert (
            result["ai_summary"] == "This is a test article about Python programming."
        )

    def test_get_link_by_url_respects_max_age(self, tmp_path):
        """Test that max_age_hours filter works."""
        db_path = tmp_path / "test.db"

        # Store link with summary and old timestamp
        old_time = datetime.now() - timedelta(hours=48)
        store_link(
            url="https://example.com/article",
            sender="Tom",
            chat_id="chat1",
            timestamp=old_time,
            ai_summary="Old summary",
            db_path=db_path,
        )

        # With 24 hour max age, should not find the old link
        result = get_link_by_url(
            "https://example.com/article", max_age_hours=24, db_path=db_path
        )

        assert result is None

        # Without max age, should find it
        result_no_age = get_link_by_url("https://example.com/article", db_path=db_path)

        assert result_no_age is not None
        assert result_no_age["ai_summary"] == "Old summary"

    def test_get_link_by_url_returns_most_recent(self, tmp_path):
        """Test that the most recent link is returned when multiple exist."""
        db_path = tmp_path / "test.db"

        # Store same URL multiple times with different timestamps
        older_time = datetime.now() - timedelta(hours=5)
        newer_time = datetime.now() - timedelta(hours=1)

        store_link(
            url="https://example.com/article",
            sender="Tom",
            chat_id="chat1",
            message_id=100,
            timestamp=older_time,
            ai_summary="Older summary",
            db_path=db_path,
        )

        store_link(
            url="https://example.com/article",
            sender="Tom",
            chat_id="chat1",
            message_id=200,  # Different message ID to allow storing
            timestamp=newer_time,
            ai_summary="Newer summary",
            db_path=db_path,
        )

        result = get_link_by_url("https://example.com/article", db_path=db_path)

        assert result is not None
        assert result["ai_summary"] == "Newer summary"
