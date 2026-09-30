"""Unit tests for the one-time per-chat dedup-seeding pass (bridge/dedup_seed.py).

docs/plans/catchup-rehandles-handled-messages.md: the rollout regression fix
(critique BLOCKER) -- re-seeds the durable dedup set from a live Telethon read
so the first post-fix scan does not re-enqueue the entire already-handled
historical backlog. Guarded by PER-CHAT markers, never a single global flag
(the re-critique BLOCKER on the prior revision).
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bridge.dedup import is_duplicate_message
from bridge.dedup_seed import (
    _seed_marker_path,
    is_chat_seeded,
    seed_dedup_for_chat,
    seed_dedup_for_chats,
)
from models.dedup import DedupRecord

TEST_CHAT_A = "test_seed_chat_a"
TEST_CHAT_B = "test_seed_chat_b"


def _cleanup_chat(chat_id):
    for record in DedupRecord.query.all():
        if str(record.chat_id) == str(chat_id):
            record.delete()
    marker = _seed_marker_path(chat_id)
    if marker.exists():
        marker.unlink()


def _make_message(msg_id, out=False):
    msg = MagicMock()
    msg.id = msg_id
    msg.out = out
    return msg


class TestSeedDedupForChat:
    """Unit tests for the single-chat seed helper."""

    def setup_method(self):
        _cleanup_chat(TEST_CHAT_A)

    def teardown_method(self):
        _cleanup_chat(TEST_CHAT_A)

    @pytest.mark.asyncio
    async def test_seeds_only_inbound_ids_at_or_below_cursor(self):
        """Only inbound (not out) messages with id <= cursor are seeded."""
        cursor_id = 100
        client = AsyncMock()
        client.get_messages = AsyncMock(
            return_value=[
                _make_message(105),  # above cursor -- not seeded
                _make_message(100),  # at cursor -- seeded
                _make_message(90),  # below cursor -- seeded
                _make_message(80, out=True),  # our own outgoing msg -- not seeded
            ]
        )

        with patch(
            "bridge.dedup_seed.get_last_processed",
            new_callable=AsyncMock,
            return_value=(cursor_id, datetime.now(UTC)),
        ):
            count, marker_written = await seed_dedup_for_chat(
                client, TEST_CHAT_A, entity=MagicMock(), max_messages=50
            )

        assert count == 2
        assert marker_written is True
        assert await is_duplicate_message(TEST_CHAT_A, 100) is True
        assert await is_duplicate_message(TEST_CHAT_A, 90) is True
        assert await is_duplicate_message(TEST_CHAT_A, 105) is False
        assert await is_duplicate_message(TEST_CHAT_A, 80) is False

    @pytest.mark.asyncio
    async def test_no_cursor_seeds_nothing_but_marks_success(self):
        """A chat with no LastProcessedRecord cursor seeds nothing (no evidence
        of dispatch), but this is a legitimate outcome, not a failure."""
        client = AsyncMock()
        client.get_messages = AsyncMock(return_value=[_make_message(50)])

        with patch(
            "bridge.dedup_seed.get_last_processed", new_callable=AsyncMock, return_value=None
        ):
            count, marker_written = await seed_dedup_for_chat(
                client, TEST_CHAT_A, entity=MagicMock(), max_messages=50
            )

        assert count == 0
        assert marker_written is True
        client.get_messages.assert_not_called()
        assert await is_duplicate_message(TEST_CHAT_A, 50) is False

    @pytest.mark.asyncio
    async def test_already_seeded_chat_is_a_noop(self):
        """A chat whose marker already exists is skipped without a Telethon read."""
        from bridge.dedup_seed import _write_seed_marker

        _write_seed_marker(TEST_CHAT_A)
        client = AsyncMock()

        count, marker_written = await seed_dedup_for_chat(
            client, TEST_CHAT_A, entity=MagicMock(), max_messages=50
        )

        assert count == 0
        assert marker_written is True
        client.get_messages.assert_not_called()

    @pytest.mark.asyncio
    async def test_telethon_failure_does_not_write_marker(self):
        """A Telethon read failure for this chat leaves the marker ABSENT so
        the chat retries on the next restart (BLOCKER-fix: never silently
        stamp success on a partial failure)."""
        with patch(
            "bridge.dedup_seed.get_last_processed",
            new_callable=AsyncMock,
            return_value=(100, datetime.now(UTC)),
        ):
            client = AsyncMock()
            client.get_messages = AsyncMock(side_effect=RuntimeError("flood wait"))

            count, marker_written = await seed_dedup_for_chat(
                client, TEST_CHAT_A, entity=MagicMock(), max_messages=50
            )

        assert count == 0
        assert marker_written is False
        assert is_chat_seeded(TEST_CHAT_A) is False


class TestSeedDedupForChats:
    """Unit tests for the multi-chat orchestration pass."""

    def setup_method(self):
        _cleanup_chat(TEST_CHAT_A)
        _cleanup_chat(TEST_CHAT_B)

    def teardown_method(self):
        _cleanup_chat(TEST_CHAT_A)
        _cleanup_chat(TEST_CHAT_B)

    def _dialog(self, chat_id, title):
        entity = MagicMock()
        entity.title = title
        dialog = MagicMock()
        dialog.id = chat_id
        dialog.entity = entity
        return dialog

    @pytest.mark.asyncio
    async def test_per_chat_failure_isolated_sibling_marker_written(self):
        """A per-chat Telethon failure for one chat does NOT abort seeding for
        a sibling chat -- the BLOCKER-fix regression test. A single global
        marker would incorrectly mark BOTH chats done (or neither); per-chat
        markers must leave exactly the failed chat unmarked.
        """
        dialog_a = self._dialog(TEST_CHAT_A, "Seed Chat A")
        dialog_b = self._dialog(TEST_CHAT_B, "Seed Chat B")

        client = AsyncMock()
        client.get_dialogs = AsyncMock(return_value=[dialog_a, dialog_b])

        async def _get_messages(entity, limit=None):
            if entity is dialog_a.entity:
                raise RuntimeError("flood wait for chat A")
            return [_make_message(10)]

        client.get_messages = AsyncMock(side_effect=_get_messages)

        find_project_fn = MagicMock(return_value={"_key": "test"})

        async def _get_last_processed(chat_id):
            return (10, datetime.now(UTC))

        with patch(
            "bridge.dedup_seed.get_last_processed",
            new_callable=AsyncMock,
            side_effect=_get_last_processed,
        ):
            summary = await seed_dedup_for_chats(
                client=client,
                monitored_groups=["seed chat a", "seed chat b"],
                find_project_fn=find_project_fn,
                max_messages=50,
            )

        assert is_chat_seeded(TEST_CHAT_A) is False, "failed chat must NOT have a marker"
        assert is_chat_seeded(TEST_CHAT_B) is True, "sibling chat must still be marked done"
        assert summary[TEST_CHAT_A]["marker_written"] is False
        assert summary[TEST_CHAT_B]["marker_written"] is True
        assert summary[TEST_CHAT_B]["count"] == 1

    @pytest.mark.asyncio
    async def test_get_dialogs_failure_skips_pass_without_crashing(self):
        """A get_dialogs() failure at the top of the pass logs and returns an
        empty summary -- it must never crash bridge startup."""
        client = AsyncMock()
        client.get_dialogs = AsyncMock(side_effect=RuntimeError("connection reset"))

        summary = await seed_dedup_for_chats(
            client=client,
            monitored_groups=["seed chat a"],
            find_project_fn=MagicMock(),
            max_messages=50,
        )

        assert summary == {}

    @pytest.mark.asyncio
    async def test_unmonitored_and_unowned_chats_skipped(self):
        """Chats not in monitored_groups, or with no project config, are skipped."""
        dialog_unmonitored = self._dialog(TEST_CHAT_A, "Not Monitored")
        dialog_unowned = self._dialog(TEST_CHAT_B, "Seed Chat B")

        client = AsyncMock()
        client.get_dialogs = AsyncMock(return_value=[dialog_unmonitored, dialog_unowned])
        client.get_messages = AsyncMock(return_value=[])

        # find_project_fn returns None for the unowned chat.
        find_project_fn = MagicMock(return_value=None)

        summary = await seed_dedup_for_chats(
            client=client,
            monitored_groups=["seed chat b"],  # "Not Monitored" excluded by title
            find_project_fn=find_project_fn,
            max_messages=50,
        )

        assert summary == {}
        assert is_chat_seeded(TEST_CHAT_A) is False
        assert is_chat_seeded(TEST_CHAT_B) is False
