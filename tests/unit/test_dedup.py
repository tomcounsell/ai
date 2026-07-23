"""Unit tests for bridge/dedup.py and models/dedup.py.

Tests duplicate detection, recording, TTL behavior, trimming, and error handling.
"""

from unittest.mock import patch

import pytest

from models.dedup import DedupRecord


class TestDedupRecord:
    """Tests for the DedupRecord Popoto model."""

    def setup_method(self):
        """Clean up test records before each test."""
        for record in DedupRecord.query.all():
            if str(record.chat_id).startswith("test_"):
                record.delete()

    def teardown_method(self):
        """Clean up test records after each test."""
        for record in DedupRecord.query.all():
            if str(record.chat_id).startswith("test_"):
                record.delete()

    def test_get_or_create_new(self):
        """get_or_create returns a new record for unseen chat_id."""
        record = DedupRecord.get_or_create("test_new_chat")
        assert record.chat_id == "test_new_chat"
        assert record.message_ids == set()

    def test_get_or_create_existing(self):
        """get_or_create returns existing record for known chat_id."""
        DedupRecord.create(chat_id="test_existing", message_ids={"100", "200"})
        record = DedupRecord.get_or_create("test_existing")
        assert "100" in record.message_ids
        assert "200" in record.message_ids

    def test_has_message(self):
        """has_message correctly checks for message ID presence."""
        record = DedupRecord.create(chat_id="test_has", message_ids={"42", "99"})
        assert record.has_message(42) is True
        assert record.has_message(99) is True
        assert record.has_message(1) is False

    def test_add_message(self):
        """add_message adds an ID and persists it."""
        record = DedupRecord.create(chat_id="test_add", message_ids=set())
        record.add_message(123)
        # Reload to verify persistence
        reloaded = DedupRecord.get_or_create("test_add")
        assert reloaded.has_message(123) is True

    def test_add_message_trimming(self):
        """add_message trims to MAX_IDS when exceeding 2x threshold."""
        record = DedupRecord.create(chat_id="test_trim", message_ids=set())
        # Add more than MAX_IDS * 2 messages
        for i in range(DedupRecord._MAX_IDS * 2 + 5):
            record.message_ids.add(str(i))
        # Trigger trim via add_message
        record.add_message(99999)
        # Should be trimmed to MAX_IDS, keeping the highest IDs
        assert len(record.message_ids) == DedupRecord._MAX_IDS
        assert record.has_message(99999) is True
        # Lowest IDs should have been removed
        assert record.has_message(0) is False

    def test_ttl_is_set(self):
        """DedupRecord's TTL is settings-backed and coupled to the cursor TTL.

        The old hardcoded 7200s (2h) TTL was shorter than the startup-catchup
        scan window (cursor-extended, up to ~30 days), which is the root
        cause of catchup-rehandles-handled-messages. The TTL must now equal
        the settings value, which defaults to last_processed_ttl_s.
        """
        from config.settings import settings

        assert DedupRecord._meta.ttl == settings.timeouts.dedup_record_ttl_s
        assert DedupRecord._meta.ttl == settings.timeouts.last_processed_ttl_s

    def test_max_ids_covers_scanner_fetch_limits(self):
        """_MAX_IDS must cover the largest scanner fetch limit.

        DedupRecord retains only the most-recent _MAX_IDS inbound ids after
        trimming (see add_message). If a scanner's fetch limit ever exceeds
        _MAX_IDS, that scanner could fetch a message older than what dedup
        retained, silently reopening the re-handling bug. This pins the
        invariant instead of speculatively bumping _MAX_IDS.
        """
        from bridge.catchup import MAX_MESSAGES_PER_CHAT
        from bridge.reconciler import RECONCILE_MESSAGE_LIMIT

        assert DedupRecord._MAX_IDS >= max(MAX_MESSAGES_PER_CHAT, RECONCILE_MESSAGE_LIMIT)


class TestDedupFunctions:
    """Tests for bridge/dedup.py public API functions."""

    def setup_method(self):
        for record in DedupRecord.query.all():
            if str(record.chat_id).startswith("test_"):
                record.delete()

    def teardown_method(self):
        for record in DedupRecord.query.all():
            if str(record.chat_id).startswith("test_"):
                record.delete()

    @pytest.mark.asyncio
    async def test_is_duplicate_message_false(self):
        """Returns False for unseen messages."""
        from bridge.dedup import is_duplicate_message

        result = await is_duplicate_message("test_dup_false", 12345)
        assert result is False

    @pytest.mark.asyncio
    async def test_record_then_check_duplicate(self):
        """After recording, the message is detected as duplicate."""
        from bridge.dedup import is_duplicate_message, record_message_processed

        await record_message_processed("test_dup_check", 42)
        result = await is_duplicate_message("test_dup_check", 42)
        assert result is True

    @pytest.mark.asyncio
    async def test_different_chats_independent(self):
        """Messages in different chats don't interfere."""
        from bridge.dedup import is_duplicate_message, record_message_processed

        await record_message_processed("test_chat_a", 100)
        result = await is_duplicate_message("test_chat_b", 100)
        assert result is False

    @pytest.mark.asyncio
    async def test_error_handling_is_duplicate(self):
        """is_duplicate_message returns False on error."""
        from bridge.dedup import is_duplicate_message

        with patch("models.dedup.DedupRecord.get_or_create", side_effect=RuntimeError("boom")):
            result = await is_duplicate_message("test_err", 1)
            assert result is False

    @pytest.mark.asyncio
    async def test_error_handling_record(self):
        """record_message_processed does not raise on error."""
        from bridge.dedup import record_message_processed

        with patch("models.dedup.DedupRecord.get_or_create", side_effect=RuntimeError("boom")):
            # Should not raise
            await record_message_processed("test_err", 1)


class TestMessageClaim:
    """Tests for the atomic per-message producer claim (issue #1817 B1).

    ``claim_message``/``release_message_claim`` are backed by a plain
    (non-Popoto-managed) Redis SETNX key, distinct from the DedupRecord
    membership set above. Tests use the real Redis client via
    ``bridge.dedup._get_redis()`` and clean up their own keys.
    """

    def _cleanup(self, chat_id, message_id):
        from bridge.dedup import _MSG_CLAIM_KEY_PREFIX, _get_redis

        _get_redis().delete(f"{_MSG_CLAIM_KEY_PREFIX}{chat_id}:{message_id}")

    def teardown_method(self):
        self._cleanup("test_claim_chat", 1)
        self._cleanup("test_claim_chat", 2)
        self._cleanup("test_claim_release", 1)

    @pytest.mark.asyncio
    async def test_claim_fresh_id_succeeds(self):
        """claim_message on a fresh (chat_id, message_id) returns True."""
        from bridge.dedup import claim_message

        result = await claim_message("test_claim_chat", 1)
        assert result is True

    @pytest.mark.asyncio
    async def test_claim_already_claimed_fails(self):
        """A second claim on the same (chat_id, message_id) returns False."""
        from bridge.dedup import claim_message

        first = await claim_message("test_claim_chat", 2)
        second = await claim_message("test_claim_chat", 2)
        assert first is True
        assert second is False

    @pytest.mark.asyncio
    async def test_release_message_claim_deletes_key(self):
        """release_message_claim deletes the key so a retry can re-acquire it."""
        from bridge.dedup import claim_message, release_message_claim

        assert await claim_message("test_claim_release", 1) is True
        await release_message_claim("test_claim_release", 1)
        # Re-acquisition succeeds only if the key was actually deleted.
        assert await claim_message("test_claim_release", 1) is True

    def test_claim_ttl_is_seconds_scoped_and_short(self):
        """CLAIM_TTL_SECONDS must be short (cross-actor skew), decoupled from
        the durable cursor-coupled DedupRecord membership TTL used for
        startup-catchup replay coverage.
        """
        from bridge.dedup import CLAIM_TTL_SECONDS
        from models.dedup import DedupRecord

        assert isinstance(CLAIM_TTL_SECONDS, int)
        # "Short" per the plan: sized to cross-actor processing skew
        # (seconds), not the ~1h sync-lag window. Generous upper bound of
        # 5 minutes still rules out anything resembling the durable window.
        assert 0 < CLAIM_TTL_SECONDS <= 300
        assert CLAIM_TTL_SECONDS != DedupRecord._meta.ttl
