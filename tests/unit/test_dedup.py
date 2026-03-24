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
        """DedupRecord should have a 2-hour TTL."""
        assert DedupRecord._meta.ttl == 7200


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
