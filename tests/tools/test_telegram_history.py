"""Tests for the Telegram history tool (Redis/Popoto backend).

All tests use the redis_test_db fixture (autouse, db=1) for isolation.
The db_path parameter is ignored by the new Redis-backed implementation;
it is kept in function signatures for backward-compatibility only.
"""

import time
from datetime import datetime, timedelta

import pytest

from tools.telegram_history import (
    get_chat_stats,
    get_link_by_url,
    get_link_stats,
    get_recent_messages,
    list_chats,
    list_links,
    register_chat,
    resolve_chat_id,
    search_all_chats,
    search_history,
    search_links,
    store_link,
    store_message,
    update_link,
)


class TestStoreMessage:
    """Test message storage via Popoto/Redis."""

    def test_store_basic_message(self):
        result = store_message(
            chat_id="test_chat",
            content="Hello, world!",
            sender="user1",
        )
        assert result.get("stored") is True
        assert result.get("chat_id") == "test_chat"
        assert result.get("id") is not None

    def test_store_message_with_timestamp(self):
        timestamp = datetime(2024, 1, 15, 10, 30, 0)
        result = store_message(
            chat_id="test_chat",
            content="Test message",
            timestamp=timestamp,
        )
        assert result.get("stored") is True

    @pytest.mark.parametrize("msg_type", ["text", "photo", "document"])
    def test_store_message_types(self, msg_type):
        result = store_message(
            chat_id="test_chat",
            content=f"Content for {msg_type}",
            message_type=msg_type,
        )
        assert result.get("stored") is True


class TestSearchHistory:
    """Test history search via Popoto/Redis."""

    def test_search_empty_query_returns_error(self):
        result = search_history("", chat_id="test")
        assert "error" in result
        assert "empty" in result["error"].lower()

    def test_search_missing_chat_id_returns_error(self):
        result = search_history("query", chat_id="")
        assert "error" in result

    def test_search_finds_matching_messages(self):
        store_message(chat_id="chat1", content="Hello Python world")
        store_message(chat_id="chat1", content="JavaScript is great")
        store_message(chat_id="chat1", content="Python for data science")

        result = search_history("Python", chat_id="chat1")

        assert "error" not in result
        assert result.get("total_matches", 0) >= 2
        assert all("python" in r["content"].lower() for r in result["results"])

    def test_search_respects_max_results(self):
        for i in range(10):
            store_message(chat_id="chat1", content=f"Message {i} about Python")

        result = search_history("Python", chat_id="chat1", max_results=3)

        assert "error" not in result
        assert len(result["results"]) <= 3

    def test_search_scores_relevance(self):
        store_message(chat_id="chat1", content="Python is great")

        result = search_history("Python", chat_id="chat1")

        if result.get("results"):
            assert "relevance_score" in result["results"][0]
            assert result["results"][0]["relevance_score"] > 0


class TestGetRecentMessages:
    """Test getting recent messages via Popoto/Redis."""

    def test_get_recent_empty_chat(self):
        result = get_recent_messages("nonexistent_chat_xyz")
        assert "error" not in result
        assert result.get("count") == 0
        assert result["messages"] == []

    def test_get_recent_missing_chat_id_returns_error(self):
        result = get_recent_messages("")
        assert "error" in result

    def test_get_recent_messages_ordered_by_time(self):
        base_time = time.time()
        store_message(
            chat_id="chat1",
            content="First message",
            timestamp=datetime.fromtimestamp(base_time - 7200),
        )
        store_message(
            chat_id="chat1",
            content="Second message",
            timestamp=datetime.fromtimestamp(base_time - 3600),
        )
        store_message(
            chat_id="chat1",
            content="Third message",
            timestamp=datetime.fromtimestamp(base_time),
        )

        result = get_recent_messages("chat1", limit=3)

        assert "error" not in result
        assert result["count"] == 3
        # Most recent should be first
        assert "Third" in result["messages"][0]["content"]

    def test_get_recent_respects_limit(self):
        for i in range(10):
            store_message(chat_id="chat1", content=f"Message {i}")

        result = get_recent_messages("chat1", limit=5)

        assert "error" not in result
        assert len(result["messages"]) == 5


class TestGetChatStats:
    """Test chat statistics via Popoto/Redis."""

    def test_get_stats_empty_chat(self):
        result = get_chat_stats("nonexistent_chat_xyz")
        assert "error" not in result
        assert result["total_messages"] == 0

    def test_get_stats_with_messages(self):
        store_message(chat_id="chat1", content="Message 1", sender="user1")
        store_message(chat_id="chat1", content="Message 2", sender="user2")
        store_message(chat_id="chat1", content="Message 3", sender="user1")

        result = get_chat_stats("chat1")

        assert "error" not in result
        assert result["total_messages"] == 3
        assert result["unique_senders"] == 2


class TestChatIsolation:
    """Test that different chats are isolated."""

    def test_search_only_returns_target_chat(self):
        store_message(chat_id="chat1", content="Python in chat1")
        store_message(chat_id="chat2", content="Python in chat2")

        result = search_history("Python", chat_id="chat1")

        assert "error" not in result
        assert result["total_matches"] == 1
        assert result["results"][0]["content"] == "Python in chat1"


# =============================================================================
# Link Storage Tests
# =============================================================================


class TestStoreLink:
    """Test link storage via Popoto/Redis."""

    def test_store_basic_link(self):
        result = store_link(
            url="https://example.com/article",
            sender="Tom",
            chat_id="chat1",
        )
        assert result.get("stored") is True
        assert result.get("url") == "https://example.com/article"
        assert result.get("domain") == "example.com"

    def test_store_link_extracts_domain(self):
        result = store_link(
            url="https://www.github.com/user/repo",
            sender="Tom",
            chat_id="chat1",
        )
        assert result.get("domain") == "github.com"

    def test_store_link_with_metadata(self):
        result = store_link(
            url="https://example.com/article",
            sender="Tom",
            chat_id="chat1",
            title="Test Article",
            description="A test article about testing",
            tags=["test", "article"],
        )
        assert result.get("stored") is True

    def test_store_duplicate_link_updates(self):
        # Store initial link
        store_link(
            url="https://example.com/article",
            sender="Tom",
            chat_id="chat1",
            message_id=100,
        )

        # Store same url+chat_id — should update, not duplicate
        store_link(
            url="https://example.com/article",
            sender="Tom",
            chat_id="chat1",
            message_id=100,
            title="Updated Title",
        )

        # Should still only have one link
        result = list_links()
        assert result["total"] == 1


class TestSearchLinks:
    """Test link search via Popoto/Redis."""

    def test_search_by_query(self):
        store_link(
            url="https://python.org",
            sender="Tom",
            chat_id="chat1",
            title="Python Programming",
        )
        store_link(
            url="https://javascript.info",
            sender="Tom",
            chat_id="chat1",
            title="JavaScript Tutorial",
        )

        result = search_links(query="Python")

        assert "error" not in result
        assert result["count"] == 1
        assert "python" in result["links"][0]["url"].lower()

    def test_search_by_domain(self):
        store_link(url="https://github.com/repo1", sender="Tom", chat_id="chat1")
        store_link(url="https://github.com/repo2", sender="Tom", chat_id="chat1")
        store_link(url="https://gitlab.com/repo", sender="Tom", chat_id="chat1")

        result = search_links(domain="github.com")

        assert "error" not in result
        assert result["count"] == 2
        assert all(link["domain"] == "github.com" for link in result["links"])

    def test_search_by_sender(self):
        store_link(url="https://example.com/1", sender="Tom", chat_id="chat1")
        store_link(url="https://example.com/2", sender="Alice", chat_id="chat1")

        result = search_links(sender="Tom")

        assert "error" not in result
        assert result["count"] == 1
        assert result["links"][0]["sender"] == "Tom"

    def test_search_by_status(self):
        store_link(url="https://example.com/1", sender="Tom", chat_id="chat1")
        store_link(url="https://example.com/2", sender="Tom", chat_id="chat1")

        # Update one to read status
        links = list_links()
        update_link(links["links"][0]["id"], status="read")

        result = search_links(status="unread")

        assert "error" not in result
        assert result["count"] == 1


class TestListLinks:
    """Test listing links via Popoto/Redis."""

    def test_list_empty(self):
        result = list_links()
        assert "error" not in result
        assert result["count"] == 0
        assert result["total"] == 0

    def test_list_with_pagination(self):
        for i in range(10):
            store_link(
                url=f"https://example.com/{i}",
                sender="Tom",
                chat_id="chat1",
            )

        result1 = list_links(limit=3, offset=0)
        assert result1["count"] == 3
        assert result1["total"] == 10
        assert result1["has_more"] is True

        result2 = list_links(limit=3, offset=3)
        assert result2["count"] == 3
        assert result2["has_more"] is True

        result3 = list_links(limit=3, offset=9)
        assert result3["count"] == 1
        assert result3["has_more"] is False


class TestUpdateLink:
    """Test updating links via Popoto/Redis."""

    def test_update_status(self):
        store_link(url="https://example.com", sender="Tom", chat_id="chat1")
        links = list_links()
        link_id = links["links"][0]["id"]

        result = update_link(link_id, status="read")

        assert result.get("updated") is True

        # Verify the change
        updated = list_links()
        assert updated["links"][0]["status"] == "read"

    def test_update_tags(self):
        store_link(url="https://example.com", sender="Tom", chat_id="chat1")
        links = list_links()
        link_id = links["links"][0]["id"]

        result = update_link(link_id, tags=["python", "tutorial"])

        assert result.get("updated") is True

        updated = list_links()
        assert updated["links"][0]["tags"] == ["python", "tutorial"]

    def test_update_notes(self):
        store_link(url="https://example.com", sender="Tom", chat_id="chat1")
        links = list_links()
        link_id = links["links"][0]["id"]

        result = update_link(link_id, notes="Great article about testing")

        assert result.get("updated") is True

    def test_update_nonexistent_link(self):
        result = update_link("nonexistent_link_id_xyz", status="read")
        assert "error" in result
        assert "not found" in result["error"]


class TestGetLinkStats:
    """Test link statistics via Popoto/Redis."""

    def test_stats_empty(self):
        result = get_link_stats()
        assert "error" not in result
        assert result["total_links"] == 0

    def test_stats_with_links(self):
        store_link(url="https://github.com/repo1", sender="Tom", chat_id="chat1")
        store_link(url="https://github.com/repo2", sender="Tom", chat_id="chat1")
        store_link(url="https://python.org", sender="Alice", chat_id="chat1")

        result = get_link_stats()

        assert "error" not in result
        assert result["total_links"] == 3
        assert result["unique_domains"] == 2
        assert result["unique_senders"] == 2
        assert result["by_status"]["unread"] == 3

    def test_stats_top_domains(self):
        for i in range(5):
            store_link(
                url=f"https://github.com/repo{i}",
                sender="Tom",
                chat_id="chat1",
            )
        for i in range(3):
            store_link(
                url=f"https://python.org/page{i}",
                sender="Tom",
                chat_id="chat1",
            )

        result = get_link_stats()

        assert "error" not in result
        assert len(result["top_domains"]) >= 2
        # github.com should be first with 5 links
        assert result["top_domains"][0]["domain"] == "github.com"
        assert result["top_domains"][0]["count"] == 5


class TestGetLinkByUrl:
    """Test link lookup by URL for caching."""

    def test_get_link_by_url_not_found(self):
        result = get_link_by_url("https://nonexistent-url-xyz.com")
        assert result is None

    def test_get_link_by_url_no_summary(self):
        store_link(
            url="https://example.com/article",
            sender="Tom",
            chat_id="chat1",
        )
        result = get_link_by_url("https://example.com/article")
        # Should return None because no ai_summary
        assert result is None

    def test_get_link_by_url_with_summary(self):
        store_link(
            url="https://example.com/article",
            sender="Tom",
            chat_id="chat1",
            ai_summary="This is a test article about Python programming.",
        )

        result = get_link_by_url("https://example.com/article")

        assert result is not None
        assert result["url"] == "https://example.com/article"
        assert result["ai_summary"] == "This is a test article about Python programming."

    def test_get_link_by_url_respects_max_age(self):
        old_time = datetime.now() - timedelta(hours=48)
        store_link(
            url="https://example.com/article",
            sender="Tom",
            chat_id="chat1",
            timestamp=old_time,
            ai_summary="Old summary",
        )

        # With 24 hour max age, should not find the old link
        result = get_link_by_url("https://example.com/article", max_age_hours=24)
        assert result is None

        # Without max age, should find it
        result_no_age = get_link_by_url("https://example.com/article")
        assert result_no_age is not None
        assert result_no_age["ai_summary"] == "Old summary"

    def test_get_link_by_url_returns_most_recent(self):
        base = time.time()
        store_link(
            url="https://example.com/article",
            sender="Tom",
            chat_id="chat1",
            timestamp=datetime.fromtimestamp(base - 18000),
            ai_summary="Older summary",
        )
        store_link(
            url="https://example.com/article",
            sender="Tom",
            chat_id="chat2",  # Different chat_id to allow second record
            timestamp=datetime.fromtimestamp(base - 3600),
            ai_summary="Newer summary",
        )

        result = get_link_by_url("https://example.com/article")

        assert result is not None
        assert result["ai_summary"] == "Newer summary"


class TestRegisterChat:
    """Test chat registration via Popoto/Redis."""

    def test_register_new_chat(self):
        result = register_chat(chat_id="12345", chat_name="Dev: Valor", chat_type="group")
        assert result.get("registered") is True
        assert result.get("chat_id") == "12345"
        assert result.get("chat_name") == "Dev: Valor"

    def test_register_chat_idempotent(self):
        # Register twice — should not error
        register_chat(chat_id="99999", chat_name="Test Chat")
        result = register_chat(chat_id="99999", chat_name="Test Chat Updated")
        assert result.get("registered") is True


class TestListChats:
    """Test chat listing via Popoto/Redis."""

    def test_list_chats_empty(self):
        result = list_chats()
        assert "error" not in result
        assert result["count"] == 0

    def test_list_chats_with_data(self):
        register_chat(chat_id="111", chat_name="Chat A")
        register_chat(chat_id="222", chat_name="Chat B")

        result = list_chats()

        assert "error" not in result
        assert result["count"] == 2


class TestResolveChatId:
    """Test chat_id resolution by name."""

    def test_resolve_exact_match(self):
        register_chat(chat_id="123", chat_name="Dev: Valor")
        resolved = resolve_chat_id("Dev: Valor")
        assert resolved == "123"

    def test_resolve_case_insensitive(self):
        register_chat(chat_id="456", chat_name="Dev: Valor")
        resolved = resolve_chat_id("dev: valor")
        assert resolved == "456"

    def test_resolve_partial_match(self):
        register_chat(chat_id="789", chat_name="Dev: Valor Project")
        resolved = resolve_chat_id("Valor")
        assert resolved == "789"

    def test_resolve_not_found(self):
        resolved = resolve_chat_id("NonexistentChatXYZ")
        assert resolved is None


class TestSearchAllChats:
    """Test cross-chat search via Popoto/Redis."""

    def test_search_all_chats_empty_query(self):
        result = search_all_chats("")
        assert "error" in result

    def test_search_all_chats_finds_results(self):
        store_message(chat_id="chat1", content="Python is great in chat1")
        store_message(chat_id="chat2", content="Python rocks in chat2")

        result = search_all_chats("Python")

        assert "error" not in result
        assert result["total_matches"] == 2
