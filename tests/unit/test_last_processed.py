"""Unit tests for models/last_processed.py and the bridge/dedup.py cursor helpers.

Covers the per-chat last-processed cursor introduced in issue #1408:
get_or_create idempotency, monotonic advance, TTL, and the
record_last_processed / get_last_processed helpers (including Redis-failure
fallback to None).
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from models.last_processed import LastProcessedRecord


def _cleanup():
    for record in LastProcessedRecord.query.all():
        if str(record.chat_id).startswith("test_"):
            record.delete()


class TestLastProcessedRecord:
    """Tests for the LastProcessedRecord Popoto model."""

    def setup_method(self):
        _cleanup()

    def teardown_method(self):
        _cleanup()

    def test_get_or_create_new(self):
        """get_or_create returns a new zeroed record for an unseen chat_id."""
        record = LastProcessedRecord.get_or_create("test_new_chat")
        assert record.chat_id == "test_new_chat"
        assert record.last_message_id == 0
        assert record.last_message_ts == 0

    def test_get_or_create_existing(self):
        """get_or_create returns the existing record for a known chat_id."""
        LastProcessedRecord.create(
            chat_id="test_existing",
            last_message_id=42,
            last_message_ts=1000,
            updated_at=1000,
        )
        record = LastProcessedRecord.get_or_create("test_existing")
        assert record.last_message_id == 42

    def test_advance_moves_cursor_forward(self):
        """advance() updates and persists when the message ID is newer."""
        record = LastProcessedRecord.get_or_create("test_adv")
        advanced = record.advance(100, 1700000000)
        assert advanced is True

        reloaded = LastProcessedRecord.get_or_create("test_adv")
        assert reloaded.last_message_id == 100
        assert reloaded.last_message_ts == 1700000000
        assert reloaded.updated_at > 0

    def test_advance_is_monotonic(self):
        """advance() is a no-op when the message ID is not strictly greater."""
        record = LastProcessedRecord.get_or_create("test_mono")
        record.advance(100, 1700000000)

        record = LastProcessedRecord.get_or_create("test_mono")
        # Equal ID — no-op
        assert record.advance(100, 1700009999) is False
        # Older ID — no-op
        assert record.advance(50, 1700009999) is False

        reloaded = LastProcessedRecord.get_or_create("test_mono")
        assert reloaded.last_message_id == 100
        assert reloaded.last_message_ts == 1700000000

    def test_ttl_is_30_days(self):
        """The record carries a 30-day TTL."""
        assert LastProcessedRecord._meta.ttl == 2592000


class TestCursorHelpers:
    """Tests for record_last_processed / get_last_processed in bridge/dedup.py."""

    def setup_method(self):
        _cleanup()

    def teardown_method(self):
        _cleanup()

    @pytest.mark.asyncio
    async def test_get_last_processed_unknown_returns_none(self):
        """get_last_processed returns None for a chat with no record."""
        from bridge.dedup import get_last_processed

        result = await get_last_processed("test_unknown")
        assert result is None

    @pytest.mark.asyncio
    async def test_record_then_get_round_trips(self):
        """After record_last_processed, get_last_processed returns (id, datetime)."""
        from bridge.dedup import get_last_processed, record_last_processed

        ts = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        await record_last_processed("test_round", 777, ts)

        result = await get_last_processed("test_round")
        assert result is not None
        msg_id, dt = result
        assert msg_id == 777
        assert dt.tzinfo is not None
        # Round-trips to the same instant (second precision)
        assert abs((dt - ts).total_seconds()) < 1

    @pytest.mark.asyncio
    async def test_record_is_monotonic(self):
        """A second record with an older message ID does not regress the cursor."""
        from bridge.dedup import get_last_processed, record_last_processed

        now = datetime.now(UTC)
        await record_last_processed("test_mono_helper", 200, now)
        await record_last_processed("test_mono_helper", 100, now + timedelta(minutes=5))

        result = await get_last_processed("test_mono_helper")
        assert result is not None
        assert result[0] == 200

    @pytest.mark.asyncio
    async def test_record_coerces_none_timestamp(self):
        """record_last_processed accepts message_ts=None and coerces to now()."""
        from bridge.dedup import get_last_processed, record_last_processed

        await record_last_processed("test_none_ts", 5, None)
        result = await get_last_processed("test_none_ts")
        assert result is not None
        msg_id, dt = result
        assert msg_id == 5
        # Coerced timestamp should be close to now
        assert abs((dt - datetime.now(UTC)).total_seconds()) < 60

    @pytest.mark.asyncio
    async def test_record_failure_does_not_raise(self):
        """record_last_processed swallows Redis failures (logs WARNING, no raise)."""
        from bridge.dedup import record_last_processed

        with patch(
            "models.last_processed.LastProcessedRecord.get_or_create",
            side_effect=RuntimeError("boom"),
        ):
            await record_last_processed("test_fail", 1, datetime.now(UTC))  # no raise

    @pytest.mark.asyncio
    async def test_get_failure_returns_none(self):
        """get_last_processed returns None on Redis failure (callers fall back)."""
        from bridge.dedup import get_last_processed

        with patch("models.last_processed.LastProcessedRecord.query") as mock_query:
            mock_query.filter.side_effect = RuntimeError("boom")
            result = await get_last_processed("test_fail_get")
            assert result is None


class TestStaleReplayGuard:
    """The live-handler stale catch_up replay guard (telegram_bridge.py).

    The handler skips a replayed message when its id is at-or-below the
    durable per-chat last-processed cursor. These tests lock in the boundary
    semantics the guard relies on: a message id > cursor is fresh (process),
    id <= cursor is stale (skip), and a missing cursor never skips.
    """

    def setup_method(self):
        _cleanup()

    def teardown_method(self):
        _cleanup()

    @staticmethod
    async def _is_stale_replay(chat_id, message_id) -> bool:
        """Mirror the guard predicate in telegram_bridge.py's live handler."""
        from bridge.dedup import get_last_processed

        cursor = await get_last_processed(chat_id)
        return cursor is not None and message_id <= cursor[0]

    @pytest.mark.asyncio
    async def test_older_message_id_is_stale(self):
        """A replayed id below the cursor is stale (the day-old-message bug)."""
        from bridge.dedup import record_last_processed

        await record_last_processed("test_stale_old", 500, datetime.now(UTC))
        assert await self._is_stale_replay("test_stale_old", 300) is True

    @pytest.mark.asyncio
    async def test_equal_message_id_is_stale(self):
        """The exact message we last dispatched replays as stale."""
        from bridge.dedup import record_last_processed

        await record_last_processed("test_stale_eq", 500, datetime.now(UTC))
        assert await self._is_stale_replay("test_stale_eq", 500) is True

    @pytest.mark.asyncio
    async def test_newer_message_id_is_fresh(self):
        """A genuinely-new message (higher id) is never skipped by the guard."""
        from bridge.dedup import record_last_processed

        await record_last_processed("test_stale_new", 500, datetime.now(UTC))
        assert await self._is_stale_replay("test_stale_new", 501) is False

    @pytest.mark.asyncio
    async def test_no_cursor_never_skips(self):
        """With no cursor yet, nothing is treated as a stale replay."""
        assert await self._is_stale_replay("test_stale_missing", 1) is False
