"""Integration tests for bridge/reconciler.py.

Simulates a gap scenario: dedup has messages 1-5, but the mock client returns
messages 1-7. The reconciler should detect messages 6 and 7 as missed, enqueue
them, and record them in dedup. A subsequent reconcile_once() should find no
new gaps.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bridge.reconciler import reconcile_once


def _make_message(msg_id, text=None, out=False, minutes_ago=2):
    """Create a mock Telegram message."""
    msg = MagicMock()
    msg.id = msg_id
    msg.text = f"Message {msg_id}" if text is None else text
    msg.out = out
    msg.date = datetime.now(UTC) - timedelta(minutes=minutes_ago)

    sender = MagicMock()
    sender.first_name = "Alice"
    sender.username = "alice"
    sender.id = 42
    msg.get_sender = AsyncMock(return_value=sender)
    return msg


def _make_dialog(chat_title, entity_id=100, chat_id=None):
    """Create a mock Telegram dialog.

    chat_id defaults to -100{entity_id} to match Telethon's supergroup format.
    """
    dialog = MagicMock()
    dialog.entity = MagicMock()
    dialog.entity.title = chat_title
    dialog.entity.id = entity_id
    dialog.id = chat_id if chat_id is not None else -(1000000000000 + entity_id)
    return dialog


class TestReconcilerGapDetection:
    """Integration test: gap detection and recovery flow."""

    @pytest.mark.asyncio
    async def test_gap_detection_and_recovery(self):
        """Simulate a gap where messages 6 and 7 were missed.

        Setup:
        - Dedup has messages 1-5 recorded as processed
        - Client returns messages 1-7
        - Messages 6 and 7 should be detected as missed and enqueued

        Then run reconcile again and verify no new gaps are found.
        """
        entity_id = 500
        dialog = _make_dialog("Agent Builders Chat", entity_id=entity_id)

        # Create messages 1-7 (ordered recent first as Telegram returns them)
        messages = [_make_message(i, minutes_ago=8 - i) for i in range(7, 0, -1)]

        client = AsyncMock()
        client.get_dialogs = AsyncMock(return_value=[dialog])
        client.get_messages = AsyncMock(return_value=messages)

        # Track which messages get enqueued and recorded
        enqueued = []
        recorded_dedup = set()

        async def mock_enqueue(**kwargs):
            enqueued.append(kwargs)

        async def mock_is_duplicate(chat_id, message_id):
            # Messages 1-5 are already in dedup; 6 and 7 are not
            if message_id <= 5:
                return True
            return message_id in recorded_dedup

        async def mock_record(chat_id, message_id):
            recorded_dedup.add(message_id)

        should_respond_fn = AsyncMock(return_value=(True, False))
        project = {"_key": "builders", "working_directory": "/tmp/builders"}

        with (
            patch("bridge.reconciler.is_duplicate_message", side_effect=mock_is_duplicate),
            patch("bridge.reconciler.record_message_processed", side_effect=mock_record),
            patch("bridge.reconciler.record_last_processed", new_callable=AsyncMock),
        ):
            # First scan: should detect messages 6 and 7 as missed
            result = await reconcile_once(
                client=client,
                monitored_groups=["agent builders chat"],
                should_respond_fn=should_respond_fn,
                enqueue_agent_session_fn=mock_enqueue,
                find_project_fn=MagicMock(return_value=project),
            )

            assert result == 2, f"Expected 2 recovered messages, got {result}"
            assert len(enqueued) == 2

            # Verify the enqueued messages are 6 and 7
            enqueued_ids = {e["telegram_message_id"] for e in enqueued}
            assert enqueued_ids == {6, 7}

            # Verify they were recorded in dedup
            assert 6 in recorded_dedup
            assert 7 in recorded_dedup

            # Verify enqueue parameters
            for session in enqueued:
                assert session["project_key"] == "builders"
                assert session["priority"] == "low"
                assert session["chat_id"] == str(-(1000000000000 + entity_id))
                assert session["sender_name"] == "Alice"

            # Second scan: should find no new gaps (6 and 7 now in dedup)
            enqueued.clear()
            result2 = await reconcile_once(
                client=client,
                monitored_groups=["agent builders chat"],
                should_respond_fn=should_respond_fn,
                enqueue_agent_session_fn=mock_enqueue,
                find_project_fn=MagicMock(return_value=project),
            )

            assert result2 == 0, f"Expected 0 on second scan, got {result2}"
            assert len(enqueued) == 0

    @pytest.mark.asyncio
    async def test_mixed_message_types_in_gap(self):
        """Verify that outgoing and empty messages in the gap are skipped.

        Setup:
        - Messages 1-3 are in dedup
        - Client returns messages 1-6
        - Message 4 is outgoing (our own), message 5 has no text
        - Only message 6 should be recovered
        """
        dialog = _make_dialog("Test Group", entity_id=600)

        msg4 = _make_message(4, out=True, minutes_ago=4)
        msg5 = _make_message(5, text="", minutes_ago=3)
        msg6 = _make_message(6, text="Real missed message", minutes_ago=2)
        # Messages 1-3 are in dedup, include them too
        msg1 = _make_message(1, minutes_ago=7)
        msg2 = _make_message(2, minutes_ago=6)
        msg3 = _make_message(3, minutes_ago=5)

        # Ordered recent first
        messages = [msg6, msg5, msg4, msg3, msg2, msg1]

        client = AsyncMock()
        client.get_dialogs = AsyncMock(return_value=[dialog])
        client.get_messages = AsyncMock(return_value=messages)

        enqueued = []

        async def mock_enqueue(**kwargs):
            enqueued.append(kwargs)

        async def mock_is_duplicate(chat_id, message_id):
            return message_id <= 3

        should_respond_fn = AsyncMock(return_value=(True, False))
        project = {"_key": "test", "working_directory": "/tmp/test"}

        with (
            patch("bridge.reconciler.is_duplicate_message", side_effect=mock_is_duplicate),
            patch("bridge.reconciler.record_message_processed", new_callable=AsyncMock),
            patch("bridge.reconciler.record_last_processed", new_callable=AsyncMock),
        ):
            result = await reconcile_once(
                client=client,
                monitored_groups=["test group"],
                should_respond_fn=should_respond_fn,
                enqueue_agent_session_fn=mock_enqueue,
                find_project_fn=MagicMock(return_value=project),
            )

        assert result == 1
        assert enqueued[0]["telegram_message_id"] == 6
        assert enqueued[0]["message_text"] == "Real missed message"


class TestReconcilerExtendedLookback:
    """Issue #1408: the 30-minute lookback recovers older messages.

    A message 20 minutes old would have aged out of the old 10-minute window
    before the reconciler could act (the documented dead-zone scenario). With
    the extended 30-minute lookback it must be recovered.
    """

    @pytest.mark.asyncio
    async def test_constants_extended(self):
        """Lookback is 30 minutes and the per-scan limit is 30 messages."""
        from bridge.reconciler import RECONCILE_LOOKBACK_MINUTES, RECONCILE_MESSAGE_LIMIT

        assert RECONCILE_LOOKBACK_MINUTES == 30
        assert RECONCILE_MESSAGE_LIMIT == 30

    @pytest.mark.asyncio
    async def test_twenty_minute_old_message_recovered(self):
        """A 20-minute-old message is recovered (would fail under the old 10-min window)."""
        dialog = _make_dialog("Cyndra Dev", entity_id=700)
        # 20 minutes old: outside the old 10-min window, inside the new 30-min window.
        missed = _make_message(900, text="please review this", minutes_ago=20)

        client = AsyncMock()
        client.get_dialogs = AsyncMock(return_value=[dialog])
        client.get_messages = AsyncMock(return_value=[missed])

        enqueued = []

        async def mock_enqueue(**kwargs):
            enqueued.append(kwargs)

        should_respond_fn = AsyncMock(return_value=(True, False))
        project = {"_key": "cyndra", "working_directory": "/tmp/cyndra"}

        with (
            patch(
                "bridge.reconciler.is_duplicate_message",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch("bridge.reconciler.record_message_processed", new_callable=AsyncMock),
            patch("bridge.reconciler.record_last_processed", new_callable=AsyncMock),
        ):
            result = await reconcile_once(
                client=client,
                monitored_groups=["cyndra dev"],
                should_respond_fn=should_respond_fn,
                enqueue_agent_session_fn=mock_enqueue,
                find_project_fn=MagicMock(return_value=project),
            )

        assert result == 1
        assert len(enqueued) == 1
        assert enqueued[0]["telegram_message_id"] == 900


class TestReconcilerPersonaResolution:
    """Reconciler must resolve persona for parity with the live handler.

    Finding 2 bug: reconciler enqueued every chat with the eng default, so a
    teammate-configured chat wrongly ran as an eng PM<->Dev loop.
    """

    @pytest.mark.asyncio
    async def test_teammate_chat_enqueues_teammate_session(self):
        """A teammate-configured chat enqueues session_type=teammate + project_config."""
        from config.enums import SessionType

        dialog = _make_dialog("Cyndra Dev Team", entity_id=800)
        missed = _make_message(901, text="@valor look here", minutes_ago=5)

        client = AsyncMock()
        client.get_dialogs = AsyncMock(return_value=[dialog])
        client.get_messages = AsyncMock(return_value=[missed])

        enqueued = []

        async def mock_enqueue(**kwargs):
            enqueued.append(kwargs)

        project = {
            "_key": "cyndra",
            "working_directory": "/tmp/cyndra",
            "telegram": {"groups": {"Cyndra Dev Team": {"persona": "teammate"}}},
        }

        with (
            patch(
                "bridge.reconciler.is_duplicate_message",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch("bridge.reconciler.record_message_processed", new_callable=AsyncMock),
            patch("bridge.reconciler.record_last_processed", new_callable=AsyncMock),
        ):
            result = await reconcile_once(
                client=client,
                monitored_groups=["cyndra dev team"],
                should_respond_fn=AsyncMock(return_value=(True, False)),
                enqueue_agent_session_fn=mock_enqueue,
                find_project_fn=MagicMock(return_value=project),
            )

        assert result == 1
        assert enqueued[0]["session_type"] == SessionType.TEAMMATE
        assert enqueued[0]["project_config"] is project

    @pytest.mark.asyncio
    async def test_eng_chat_enqueues_eng_session(self):
        """A chat with no teammate persona still enqueues an eng session."""
        from config.enums import SessionType

        dialog = _make_dialog("Dev: Popoto", entity_id=810)
        missed = _make_message(902, text="fix the build", minutes_ago=5)

        client = AsyncMock()
        client.get_dialogs = AsyncMock(return_value=[dialog])
        client.get_messages = AsyncMock(return_value=[missed])

        enqueued = []

        async def mock_enqueue(**kwargs):
            enqueued.append(kwargs)

        project = {"_key": "popoto", "working_directory": "/tmp/popoto"}

        with (
            patch(
                "bridge.reconciler.is_duplicate_message",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch("bridge.reconciler.record_message_processed", new_callable=AsyncMock),
            patch("bridge.reconciler.record_last_processed", new_callable=AsyncMock),
        ):
            result = await reconcile_once(
                client=client,
                monitored_groups=["dev: popoto"],
                should_respond_fn=AsyncMock(return_value=(True, False)),
                enqueue_agent_session_fn=mock_enqueue,
                find_project_fn=MagicMock(return_value=project),
            )

        assert result == 1
        assert enqueued[0]["session_type"] == SessionType.ENG

    @pytest.mark.asyncio
    async def test_persona_failure_warns_and_continues_with_eng(self, caplog):
        """A persona-resolution exception falls back to eng and the scan continues."""
        import logging

        from config.enums import SessionType

        dialog = _make_dialog("Dev: Popoto", entity_id=820)
        missed = _make_message(903, text="status?", minutes_ago=5)

        client = AsyncMock()
        client.get_dialogs = AsyncMock(return_value=[dialog])
        client.get_messages = AsyncMock(return_value=[missed])

        enqueued = []

        async def mock_enqueue(**kwargs):
            enqueued.append(kwargs)

        project = {"_key": "popoto", "working_directory": "/tmp/popoto"}

        with (
            patch(
                "bridge.reconciler.is_duplicate_message",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch("bridge.reconciler.record_message_processed", new_callable=AsyncMock),
            patch("bridge.reconciler.record_last_processed", new_callable=AsyncMock),
            patch("bridge.reconciler.resolve_persona", side_effect=RuntimeError("boom")),
            caplog.at_level(logging.WARNING),
        ):
            result = await reconcile_once(
                client=client,
                monitored_groups=["dev: popoto"],
                should_respond_fn=AsyncMock(return_value=(True, False)),
                enqueue_agent_session_fn=mock_enqueue,
                find_project_fn=MagicMock(return_value=project),
            )

        assert result == 1
        assert enqueued[0]["session_type"] == SessionType.ENG
        assert any(
            "[reconciler] persona resolution failed" in r.getMessage() for r in caplog.records
        )
