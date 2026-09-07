"""Unit tests for bridge/dispatch.py's atomic per-message claim (issue #1817 B1).

Covers the live-handler wrapper's claim-before-enqueue gate and the
delete-on-exception fail-safe. These are the BLOCKER-level behaviors flagged
in the plan's critique rounds:

1. A lost claim (a peer producer already won) must skip enqueue entirely.
2. If ``enqueue_agent_session`` raises after the claim was won, the claim
   key must be released (no orphan) BEFORE the exception propagates, and
   dedup must stay unrecorded so the message remains re-enqueueable.
"""

from unittest.mock import AsyncMock, patch

import pytest

from bridge.dispatch import dispatch_telegram_session


def _base_kwargs(**overrides):
    kwargs = dict(
        project_key="test-project",
        session_id="test-session-1",
        working_dir="/tmp/test",
        message_text="hello",
        sender_name="Tom",
        chat_id="test-chat-1",
        telegram_message_id=101,
    )
    kwargs.update(overrides)
    return kwargs


class TestDispatchClaimGate:
    @pytest.mark.asyncio
    async def test_lost_claim_skips_enqueue(self):
        """A lost claim must skip enqueue and return None without recording dedup."""
        with (
            patch("bridge.dispatch.claim_message", new=AsyncMock(return_value=False)),
            patch("bridge.dispatch.enqueue_agent_session", new=AsyncMock()) as mock_enqueue,
            patch("bridge.dispatch.record_message_processed", new=AsyncMock()) as mock_record,
            patch("bridge.dispatch.record_last_processed", new=AsyncMock()),
            patch("bridge.dispatch.release_message_claim", new=AsyncMock()) as mock_release,
        ):
            result = await dispatch_telegram_session(**_base_kwargs())

        assert result is None
        mock_enqueue.assert_not_called()
        mock_record.assert_not_called()
        # Nothing to release -- the claim was never won by this caller.
        mock_release.assert_not_called()

    @pytest.mark.asyncio
    async def test_won_claim_proceeds_to_enqueue(self):
        """A won claim must proceed to enqueue and record dedup as before."""
        with (
            patch("bridge.dispatch.claim_message", new=AsyncMock(return_value=True)),
            patch(
                "bridge.dispatch.enqueue_agent_session", new=AsyncMock(return_value=3)
            ) as mock_enqueue,
            patch("bridge.dispatch.record_message_processed", new=AsyncMock()) as mock_record,
            patch("bridge.dispatch.record_last_processed", new=AsyncMock()),
            patch("bridge.dispatch._append_inbound_chat_log"),
        ):
            result = await dispatch_telegram_session(**_base_kwargs())

        assert result == 3
        mock_enqueue.assert_awaited_once()
        mock_record.assert_awaited_once_with("test-chat-1", 101)

    @pytest.mark.asyncio
    async def test_enqueue_exception_releases_claim_no_orphan(self):
        """A fault-injected enqueue exception must release the claim (no
        orphan) before propagating, and dedup must stay unrecorded so the
        message remains re-enqueueable (BLOCKER, issue #1817).
        """
        boom = RuntimeError("enqueue boom")
        with (
            patch("bridge.dispatch.claim_message", new=AsyncMock(return_value=True)),
            patch("bridge.dispatch.enqueue_agent_session", new=AsyncMock(side_effect=boom)),
            patch("bridge.dispatch.record_message_processed", new=AsyncMock()) as mock_record,
            patch("bridge.dispatch.record_last_processed", new=AsyncMock()) as mock_cursor,
            patch("bridge.dispatch.release_message_claim", new=AsyncMock()) as mock_release,
        ):
            with pytest.raises(RuntimeError, match="enqueue boom"):
                await dispatch_telegram_session(**_base_kwargs())

        mock_release.assert_awaited_once_with("test-chat-1", 101)
        mock_record.assert_not_called()
        mock_cursor.assert_not_called()


class TestRecordTelegramMessageHandledClaimGate:
    @pytest.mark.asyncio
    async def test_lost_claim_skips_record(self):
        from bridge.dispatch import record_telegram_message_handled

        with (
            patch("bridge.dispatch.claim_message", new=AsyncMock(return_value=False)),
            patch("bridge.dispatch.record_message_processed", new=AsyncMock()) as mock_record,
        ):
            await record_telegram_message_handled("test-chat-1", 202)

        mock_record.assert_not_called()

    @pytest.mark.asyncio
    async def test_won_claim_records(self):
        from bridge.dispatch import record_telegram_message_handled

        with (
            patch("bridge.dispatch.claim_message", new=AsyncMock(return_value=True)),
            patch("bridge.dispatch.record_message_processed", new=AsyncMock()) as mock_record,
        ):
            await record_telegram_message_handled("test-chat-1", 202)

        mock_record.assert_awaited_once_with("test-chat-1", 202)
