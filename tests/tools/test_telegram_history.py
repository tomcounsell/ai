"""Tests for the Telegram history tool (Redis/Popoto backend).

All tests use the redis_test_db fixture (autouse, db=1) for isolation.
The db_path parameter is ignored by the new Redis-backed implementation;
it is kept in function signatures for backward-compatibility only.
"""

import time
from datetime import datetime, timedelta

import pytest

from tools.telegram_history import (
    AmbiguousChatError,
    ChatCandidate,
    _normalize_chat_name,
    get_chat_stats,
    get_link_by_url,
    get_link_stats,
    get_recent_messages,
    list_chats,
    list_links,
    register_chat,
    resolve_chat_candidates,
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


class TestNormalizeChatName:
    """Test the `_normalize_chat_name` helper (issue #1163)."""

    def test_empty_string(self):
        assert _normalize_chat_name("") == ""

    def test_whitespace_only(self):
        assert _normalize_chat_name("   ") == ""

    def test_all_punctuation(self):
        assert _normalize_chat_name(":::") == ""
        assert _normalize_chat_name("---") == ""
        assert _normalize_chat_name("|||") == ""

    def test_lowercase(self):
        assert _normalize_chat_name("DEV VALOR") == "dev valor"

    def test_collapse_whitespace(self):
        assert _normalize_chat_name("dev   valor") == "dev valor"
        assert _normalize_chat_name("dev\tvalor") == "dev valor"
        assert _normalize_chat_name("dev\n\nvalor") == "dev valor"

    def test_strip_colon_and_dash(self):
        assert _normalize_chat_name("PM: PsyOptimal") == "pm psyoptimal"
        assert _normalize_chat_name("PM PsyOptimal") == "pm psyoptimal"
        assert _normalize_chat_name("dev-valor") == "dev valor"

    def test_strip_pipe(self):
        assert _normalize_chat_name("dev|valor") == "dev valor"

    def test_strip_underscore(self):
        # Q2 policy: `_` IS stripped. The ambiguity detector is the safety net
        # for the rare `dev_valor` vs `dev valor` collision case.
        assert _normalize_chat_name("dev_valor") == "dev valor"

    def test_underscore_vs_space_collide(self):
        # Q2 decision: dev_valor and dev valor MUST normalize equal.
        assert _normalize_chat_name("dev_valor") == _normalize_chat_name("dev valor")

    def test_preserves_emoji(self):
        # Emoji and non-ASCII must survive normalization.
        assert _normalize_chat_name("Project 🚀") == "project 🚀"
        assert _normalize_chat_name("café: mocha") == "café mocha"

    def test_preserves_non_ascii(self):
        assert _normalize_chat_name("日本語") == "日本語"

    def test_symmetric(self):
        # Applying normalization to an already-normalized string is a no-op.
        once = _normalize_chat_name("PM: Psy_Optimal")
        twice = _normalize_chat_name(once)
        assert once == twice

    def test_very_long(self):
        # >200 chars should not crash.
        long = "a" * 500
        result = _normalize_chat_name(long)
        assert result == long


class TestResolveChatCandidates:
    """Test `resolve_chat_candidates` — the candidate-collection resolver."""

    def test_empty_query_returns_empty_list(self):
        assert resolve_chat_candidates("") == []
        assert resolve_chat_candidates("   ") == []

    def test_zero_candidates(self):
        # No chats registered → empty list.
        assert resolve_chat_candidates("AnythingXYZ") == []

    def test_single_exact_candidate(self):
        register_chat(chat_id="100", chat_name="Alpha Team")
        result = resolve_chat_candidates("Alpha Team")
        assert len(result) == 1
        assert isinstance(result[0], ChatCandidate)
        assert result[0].chat_id == "100"
        assert result[0].chat_name == "Alpha Team"

    def test_ambiguous_substring(self):
        register_chat(chat_id="201", chat_name="PsyOptimal")
        register_chat(chat_id="202", chat_name="PM: PsyOptimal")
        result = resolve_chat_candidates("PsyOptimal")
        # Exact match on "PsyOptimal" wins stage 1 (substring PM: PsyOptimal doesn't
        # exact-match); stage 1 returns just the exact hit.
        ids = {c.chat_id for c in result}
        assert "201" in ids

    def test_ambiguous_at_substring_stage(self):
        # Two chats that both contain "Psy" but neither is an exact match.
        register_chat(chat_id="301", chat_name="Psy Team A")
        register_chat(chat_id="302", chat_name="Psy Team B")
        result = resolve_chat_candidates("Psy")
        ids = {c.chat_id for c in result}
        assert ids == {"301", "302"}

    def test_ordering_by_recency(self):
        # Older first, then newer — resolver must sort newer first.
        register_chat(chat_id="401", chat_name="Psy Older")
        time.sleep(0.01)
        register_chat(chat_id="402", chat_name="Psy Newer")
        result = resolve_chat_candidates("Psy")
        assert [c.chat_id for c in result] == ["402", "401"]

    def test_normalization_matches_missing_colon(self):
        register_chat(chat_id="501", chat_name="PM: PsyOptimal")
        # Query missing colon should still match via stage 3 (normalized substring).
        result = resolve_chat_candidates("PM PsyOptimal")
        assert len(result) == 1
        assert result[0].chat_id == "501"

    def test_stage_cascade_prefers_exact_over_substring(self):
        # If both an exact AND substring match exist, stage 1 wins.
        register_chat(chat_id="601", chat_name="foo")
        register_chat(chat_id="602", chat_name="foobar")
        result = resolve_chat_candidates("foo")
        assert len(result) == 1
        assert result[0].chat_id == "601"

    def test_candidate_is_dataclass_not_model(self):
        # The returned candidate must be a ChatCandidate instance (dataclass),
        # not a Popoto Chat model — prevents model-field churn leaks.
        register_chat(chat_id="701", chat_name="Canary")
        result = resolve_chat_candidates("Canary")
        from models.chat import Chat

        assert isinstance(result[0], ChatCandidate)
        assert not isinstance(result[0], Chat)


class TestResolveChatIdAmbiguity:
    """Test `resolve_chat_id` ambiguity handling (issue #1163).

    Semantics under the hotfixed plan (Q2 = pick-most-recent-with-warning):
      - Default (strict=False): >1 candidates → returns most-recent chat_id
        and emits a logger.warning listing all candidates.
      - strict=True: >1 candidates → raises AmbiguousChatError.
      - Defensive invariant: regardless of strict, if the chosen candidate
        is NOT the max-last_activity_ts one, raises AmbiguousChatError.
    """

    def test_default_returns_most_recent_and_warns(self, caplog):
        """Default (non-strict) path picks most-recent + emits logger.warning."""
        register_chat(chat_id="801", chat_name="Psy Older")
        time.sleep(0.01)
        register_chat(chat_id="802", chat_name="Psy Newer")

        with caplog.at_level("WARNING", logger="tools.telegram_history"):
            resolved = resolve_chat_id("Psy")

        assert resolved == "802"  # Most-recent winner.
        # Warning must name both candidates for audit.
        messages = [r.message for r in caplog.records]
        combined = " ".join(messages)
        assert "ambiguous" in combined.lower()
        assert "801" in combined
        assert "802" in combined

    def test_strict_raises_on_ambiguous(self):
        """strict=True re-enables the hard-error path for scripted callers."""
        register_chat(chat_id="811", chat_name="Psy Team A")
        register_chat(chat_id="812", chat_name="Psy Team B")
        with pytest.raises(AmbiguousChatError) as exc_info:
            resolve_chat_id("Psy", strict=True)
        err = exc_info.value
        assert len(err.candidates) == 2
        ids = {c.chat_id for c in err.candidates}
        assert ids == {"811", "812"}

    def test_strict_candidates_sorted_by_recency(self):
        register_chat(chat_id="901", chat_name="Psy First")
        time.sleep(0.01)
        register_chat(chat_id="902", chat_name="Psy Second")
        with pytest.raises(AmbiguousChatError) as exc_info:
            resolve_chat_id("Psy", strict=True)
        candidates = exc_info.value.candidates
        # Most recent first.
        assert candidates[0].chat_id == "902"
        assert candidates[1].chat_id == "901"

    def test_deterministic_tiebreak_on_chat_id(self, caplog, monkeypatch):
        """When last_activity_ts ties, tiebreak is deterministic on chat_id.

        Two candidates with identical timestamps must always pick the same
        winner across test runs. Tiebreak is chat_id ascending (lexicographic
        string compare) so `"100"` wins over `"200"`.
        """
        # Freeze two chats at the same updated_at by monkeypatching time.time
        # during registration. This is more deterministic than sleeping.
        import tools.telegram_history as _th

        frozen_ts = 1_700_000_500.0
        monkeypatch.setattr(_th.time, "time", lambda: frozen_ts)
        register_chat(chat_id="200", chat_name="Psy Two Hundred")
        register_chat(chat_id="100", chat_name="Psy One Hundred")
        monkeypatch.undo()

        with caplog.at_level("WARNING", logger="tools.telegram_history"):
            resolved = resolve_chat_id("Psy")
        # "100" < "200" lexicographically, so 100 wins on tiebreak.
        assert resolved == "100"

    def test_default_three_candidate_ambiguity(self, caplog):
        """Three-way ambiguity under default path picks newest, warns."""
        register_chat(chat_id="1101", chat_name="Psy Alpha")
        time.sleep(0.005)
        register_chat(chat_id="1102", chat_name="Psy Beta")
        time.sleep(0.005)
        register_chat(chat_id="1103", chat_name="Psy Gamma")
        with caplog.at_level("WARNING", logger="tools.telegram_history"):
            resolved = resolve_chat_id("Psy")
        assert resolved == "1103"  # Most recent of the three.
        # Warning must list all three candidates.
        combined = " ".join(r.message for r in caplog.records)
        assert "1101" in combined
        assert "1102" in combined
        assert "1103" in combined

    def test_strict_three_candidate_ambiguity(self):
        register_chat(chat_id="1111", chat_name="Psy Alpha")
        register_chat(chat_id="1112", chat_name="Psy Beta")
        register_chat(chat_id="1113", chat_name="Psy Gamma")
        with pytest.raises(AmbiguousChatError) as exc_info:
            resolve_chat_id("Psy", strict=True)
        assert len(exc_info.value.candidates) == 3

    def test_invariant_guard_raises_regardless_of_strict(self, monkeypatch):
        """If the selection logic picks a non-max candidate, raise unconditionally.

        This is a defensive assertion against a broken sort or a race.
        strict=False would normally swallow ambiguity with a warning, but the
        invariant guard fires BEFORE the warning path — regardless of strict.
        """
        import tools.telegram_history as _th
        from tools.telegram_history import ChatCandidate

        # Inject a candidate list where the first element is NOT the max-ts
        # one. resolve_chat_id must detect this and raise.
        broken = [
            ChatCandidate(chat_id="B", chat_name="Broken Winner", last_activity_ts=1000.0),
            ChatCandidate(chat_id="A", chat_name="Real Winner", last_activity_ts=9999.0),
        ]
        monkeypatch.setattr(_th, "resolve_chat_candidates", lambda name: broken)

        with pytest.raises(AmbiguousChatError):
            resolve_chat_id("anything")  # Default path — still raises.

        with pytest.raises(AmbiguousChatError):
            resolve_chat_id("anything", strict=True)  # Strict also raises.

    def test_ambiguous_chat_error_is_exception_subclass(self):
        # Ensures the exception is catchable as Exception by surrounding code.
        assert issubclass(AmbiguousChatError, Exception)
        err = AmbiguousChatError([])
        assert hasattr(err, "candidates")
        assert err.candidates == []

    def test_narrow_exception_handling_returns_empty_list(self, monkeypatch):
        # Simulate a Redis connectivity failure deep in Popoto.
        import redis

        class _FakeQuery:
            def filter(self, **kwargs):
                raise redis.RedisError("connection refused")

            def all(self):
                raise redis.RedisError("connection refused")

        from models.chat import Chat

        monkeypatch.setattr(Chat, "query", _FakeQuery())
        # resolve_chat_candidates returns [] on failure (logged).
        result = resolve_chat_candidates("anything")
        assert result == []


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
