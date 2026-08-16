"""Tests for bridge/dead_letters.py -- dead-letter queue replay.

Focuses on defect 3 from #1749: the replay guard was narrowed from <= 0 to == 0
so that legitimate negative chat_ids (supergroups/channels) are replayed instead
of silently deleted.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestReplayDeadLetters:
    """Test replay_dead_letters with the narrowed chat_id guard."""

    @pytest.mark.asyncio
    async def test_replay_dead_letter_survives_negative_chat_id(self):
        """A dead letter with a negative chat_id must be replayed, not deleted.

        This is the critical regression test for #1749 defect 3.
        Before the fix, negative chat_ids (groups/supergroups) were caught by
        the `<= 0` guard and async_delete()'d on bridge startup — a silent no-op trap.
        After the fix, only chat_id == 0 is rejected; negative IDs are replayed normally.
        """
        from bridge.dead_letters import replay_dead_letters

        mock_letter = MagicMock()
        mock_letter.chat_id = "-1003900483201"
        mock_letter.text = "important group message"
        mock_letter.reply_to = None
        mock_letter.async_delete = AsyncMock()
        mock_letter.async_save = AsyncMock()
        mock_letter.attempts = 0

        mock_client = MagicMock()
        mock_client.send_message = AsyncMock()

        with patch("bridge.dead_letters.DeadLetter") as mock_dead_letter_cls:
            mock_dead_letter_cls.query.async_all = AsyncMock(return_value=[mock_letter])
            replayed = await replay_dead_letters(mock_client)

        # Must attempt send to the negative chat_id
        mock_client.send_message.assert_called_once_with(
            -1003900483201,
            "important group message",
            reply_to=None,
        )
        # Must delete after successful send
        mock_letter.async_delete.assert_called_once()
        assert replayed == 1

    @pytest.mark.asyncio
    async def test_replay_dead_letter_discards_zero_chat_id(self):
        """A dead letter with chat_id == 0 must be deleted without sending.

        chat_id=0 is not a valid Telegram peer and would cause PeerIdInvalidError
        in a loop, so stale records with this value are cleaned up on replay.
        """
        from bridge.dead_letters import replay_dead_letters

        mock_letter = MagicMock()
        mock_letter.chat_id = "0"
        mock_letter.text = "orphaned invalid record"
        mock_letter.reply_to = None
        mock_letter.async_delete = AsyncMock()
        mock_letter.async_save = AsyncMock()
        mock_letter.attempts = 0

        mock_client = MagicMock()
        mock_client.send_message = AsyncMock()

        with patch("bridge.dead_letters.DeadLetter") as mock_dead_letter_cls:
            mock_dead_letter_cls.query.async_all = AsyncMock(return_value=[mock_letter])
            replayed = await replay_dead_letters(mock_client)

        # Must NOT attempt send
        mock_client.send_message.assert_not_called()
        # Must delete the invalid record
        mock_letter.async_delete.assert_called_once()
        assert replayed == 0

    @pytest.mark.asyncio
    async def test_replay_empty_queue_returns_zero(self):
        """Should return 0 and do nothing when no dead letters exist."""
        from bridge.dead_letters import replay_dead_letters

        mock_client = MagicMock()
        mock_client.send_message = AsyncMock()

        with patch("bridge.dead_letters.DeadLetter") as mock_dead_letter_cls:
            mock_dead_letter_cls.query.async_all = AsyncMock(return_value=[])
            replayed = await replay_dead_letters(mock_client)

        assert replayed == 0
        mock_client.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_replay_increments_attempts_on_send_failure(self):
        """A failed send must increment attempts and save, not delete the record."""
        from bridge.dead_letters import replay_dead_letters

        mock_letter = MagicMock()
        mock_letter.chat_id = "12345"
        mock_letter.text = "will fail"
        mock_letter.reply_to = None
        mock_letter.async_delete = AsyncMock()
        mock_letter.async_save = AsyncMock()
        mock_letter.attempts = 1

        mock_client = MagicMock()
        mock_client.send_message = AsyncMock(side_effect=Exception("Network error"))

        with patch("bridge.dead_letters.DeadLetter") as mock_dead_letter_cls:
            mock_dead_letter_cls.query.async_all = AsyncMock(return_value=[mock_letter])
            replayed = await replay_dead_letters(mock_client)

        assert replayed == 0
        mock_letter.async_delete.assert_not_called()
        mock_letter.async_save.assert_called_once()
        assert mock_letter.attempts == 2


class TestReplayPeerParseAgreesWithSendPaths:
    """Replay must judge a peer the same way every send path does (#2644).

    This path was the fifth hand-rolled copy of the parse. Its local
    ``int()`` / ``except -> 0`` pair disagreed with ``utils.peer`` on the same
    stored value: ``int("+5")`` is 5, so a record with ``chat_id="+5"`` was
    replayed to peer 5, while the relay's send paths drop it. Only legacy rows
    can carry odd forms — the persist side now rejects them on the way in — so
    replay is exactly where such a record surfaces.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("odd_chat_id", ["+5", "5.9", "--5", "not-a-peer", "0"])
    async def test_replay_discards_peers_the_send_paths_would_drop(self, odd_chat_id):
        from bridge.dead_letters import replay_dead_letters
        from utils.peer import deliverable_telegram_peer

        assert not deliverable_telegram_peer(odd_chat_id), (
            f"test premise broken: {odd_chat_id!r} should be undeliverable"
        )

        mock_letter = MagicMock()
        mock_letter.chat_id = odd_chat_id
        mock_letter.text = "legacy record with an odd peer"
        mock_letter.reply_to = None
        mock_letter.async_delete = AsyncMock()
        mock_letter.async_save = AsyncMock()
        mock_letter.attempts = 0

        mock_client = MagicMock()
        mock_client.send_message = AsyncMock()

        with patch("bridge.dead_letters.DeadLetter") as mock_dead_letter_cls:
            mock_dead_letter_cls.query.async_all = AsyncMock(return_value=[mock_letter])
            replayed = await replay_dead_letters(mock_client)

        mock_client.send_message.assert_not_called()
        mock_letter.async_delete.assert_called_once()
        assert replayed == 0

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "good_chat_id,expected", [("-1003900483201", -1003900483201), ("12345", 12345)]
    )
    async def test_replay_still_sends_for_deliverable_peers(self, good_chat_id, expected):
        """The stricter parse must not start discarding ordinary records."""
        from bridge.dead_letters import replay_dead_letters
        from utils.peer import deliverable_telegram_peer

        assert deliverable_telegram_peer(good_chat_id)

        mock_letter = MagicMock()
        mock_letter.chat_id = good_chat_id
        mock_letter.text = "ordinary record"
        mock_letter.reply_to = None
        mock_letter.async_delete = AsyncMock()
        mock_letter.async_save = AsyncMock()
        mock_letter.attempts = 0

        mock_client = MagicMock()
        mock_client.send_message = AsyncMock()

        with patch("bridge.dead_letters.DeadLetter") as mock_dead_letter_cls:
            mock_dead_letter_cls.query.async_all = AsyncMock(return_value=[mock_letter])
            replayed = await replay_dead_letters(mock_client)

        mock_client.send_message.assert_called_once_with(expected, "ordinary record", reply_to=None)
        assert replayed == 1
