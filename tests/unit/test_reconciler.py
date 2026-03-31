"""Unit tests for bridge/reconciler.py.

Tests the reconcile_once() function with mocked dependencies:
client, dedup, routing, and enqueue functions.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bridge.reconciler import RECONCILE_LOOKBACK_MINUTES, reconcile_once


def _make_message(msg_id, text="hello", out=False, minutes_ago=1):
    """Create a mock Telegram message."""
    msg = MagicMock()
    msg.id = msg_id
    msg.text = text
    msg.out = out
    msg.date = datetime.now(UTC) - timedelta(minutes=minutes_ago)

    sender = MagicMock()
    sender.first_name = "TestUser"
    sender.username = "testuser"
    sender.id = 12345
    msg.get_sender = AsyncMock(return_value=sender)
    return msg


def _make_dialog(chat_title, entity_id=100, chat_id=None):
    """Create a mock Telegram dialog.

    chat_id defaults to -100{entity_id} to match Telethon's supergroup format.
    The event handler uses dialog.id (negative), while dialog.entity.id is the
    raw entity ID (positive). The reconciler must use dialog.id.
    """
    dialog = MagicMock()
    dialog.entity = MagicMock()
    dialog.entity.title = chat_title
    dialog.entity.id = entity_id
    dialog.id = chat_id if chat_id is not None else -(1000000000000 + entity_id)
    return dialog


def _make_project(key="testproj", working_dir="/tmp/test"):
    """Create a mock project config."""
    return {"_key": key, "working_directory": working_dir}


class TestReconcileOnce:
    """Tests for reconcile_once()."""

    @pytest.mark.asyncio
    async def test_empty_monitored_groups(self):
        """Empty monitored_groups list results in no-op."""
        client = AsyncMock()
        result = await reconcile_once(
            client=client,
            monitored_groups=[],
            should_respond_fn=AsyncMock(),
            enqueue_agent_session_fn=AsyncMock(),
            find_project_fn=MagicMock(),
        )
        assert result == 0
        client.get_dialogs.assert_not_called()

    @pytest.mark.asyncio
    async def test_message_already_in_dedup_is_skipped(self):
        """Messages already in dedup are not re-dispatched."""
        dialog = _make_dialog("Test Group")
        msg = _make_message(100, text="already seen")

        client = AsyncMock()
        client.get_dialogs = AsyncMock(return_value=[dialog])
        client.get_messages = AsyncMock(return_value=[msg])

        enqueue_fn = AsyncMock()

        with (
            patch(
                "bridge.reconciler.is_duplicate_message", new_callable=AsyncMock, return_value=True
            ),
            patch("bridge.reconciler.record_message_processed", new_callable=AsyncMock),
        ):
            result = await reconcile_once(
                client=client,
                monitored_groups=["test group"],
                should_respond_fn=AsyncMock(),
                enqueue_agent_session_fn=enqueue_fn,
                find_project_fn=MagicMock(return_value=_make_project()),
            )

        assert result == 0
        enqueue_fn.assert_not_called()

    @pytest.mark.asyncio
    async def test_outgoing_message_is_skipped(self):
        """Outgoing messages (our own) are skipped."""
        dialog = _make_dialog("Test Group")
        msg = _make_message(100, text="my message", out=True)

        client = AsyncMock()
        client.get_dialogs = AsyncMock(return_value=[dialog])
        client.get_messages = AsyncMock(return_value=[msg])

        enqueue_fn = AsyncMock()

        with (
            patch(
                "bridge.reconciler.is_duplicate_message", new_callable=AsyncMock, return_value=False
            ),
            patch("bridge.reconciler.record_message_processed", new_callable=AsyncMock),
        ):
            result = await reconcile_once(
                client=client,
                monitored_groups=["test group"],
                should_respond_fn=AsyncMock(),
                enqueue_agent_session_fn=enqueue_fn,
                find_project_fn=MagicMock(return_value=_make_project()),
            )

        assert result == 0
        enqueue_fn.assert_not_called()

    @pytest.mark.asyncio
    async def test_message_without_text_is_skipped(self):
        """Messages with no text are skipped."""
        dialog = _make_dialog("Test Group")
        msg = _make_message(100, text="", out=False)

        client = AsyncMock()
        client.get_dialogs = AsyncMock(return_value=[dialog])
        client.get_messages = AsyncMock(return_value=[msg])

        enqueue_fn = AsyncMock()

        with (
            patch(
                "bridge.reconciler.is_duplicate_message", new_callable=AsyncMock, return_value=False
            ),
            patch("bridge.reconciler.record_message_processed", new_callable=AsyncMock),
        ):
            result = await reconcile_once(
                client=client,
                monitored_groups=["test group"],
                should_respond_fn=AsyncMock(),
                enqueue_agent_session_fn=enqueue_fn,
                find_project_fn=MagicMock(return_value=_make_project()),
            )

        assert result == 0
        enqueue_fn.assert_not_called()

    @pytest.mark.asyncio
    async def test_message_whitespace_only_is_skipped(self):
        """Messages with only whitespace are skipped."""
        dialog = _make_dialog("Test Group")
        msg = _make_message(100, text="   \n  ", out=False)

        client = AsyncMock()
        client.get_dialogs = AsyncMock(return_value=[dialog])
        client.get_messages = AsyncMock(return_value=[msg])

        enqueue_fn = AsyncMock()

        with (
            patch(
                "bridge.reconciler.is_duplicate_message", new_callable=AsyncMock, return_value=False
            ),
            patch("bridge.reconciler.record_message_processed", new_callable=AsyncMock),
        ):
            result = await reconcile_once(
                client=client,
                monitored_groups=["test group"],
                should_respond_fn=AsyncMock(),
                enqueue_agent_session_fn=enqueue_fn,
                find_project_fn=MagicMock(return_value=_make_project()),
            )

        assert result == 0
        enqueue_fn.assert_not_called()

    @pytest.mark.asyncio
    async def test_message_fails_routing_is_skipped(self):
        """Messages where should_respond returns False are skipped."""
        dialog = _make_dialog("Test Group")
        msg = _make_message(100, text="some message")

        client = AsyncMock()
        client.get_dialogs = AsyncMock(return_value=[dialog])
        client.get_messages = AsyncMock(return_value=[msg])

        should_respond_fn = AsyncMock(return_value=(False, False))
        enqueue_fn = AsyncMock()

        with (
            patch(
                "bridge.reconciler.is_duplicate_message", new_callable=AsyncMock, return_value=False
            ),
            patch("bridge.reconciler.record_message_processed", new_callable=AsyncMock),
        ):
            result = await reconcile_once(
                client=client,
                monitored_groups=["test group"],
                should_respond_fn=should_respond_fn,
                enqueue_agent_session_fn=enqueue_fn,
                find_project_fn=MagicMock(return_value=_make_project()),
            )

        assert result == 0
        enqueue_fn.assert_not_called()

    @pytest.mark.asyncio
    async def test_missed_message_is_enqueued_and_recorded(self):
        """A qualifying missed message is enqueued and recorded in dedup."""
        dialog = _make_dialog("Test Group", entity_id=200)
        msg = _make_message(555, text="missed message")

        client = AsyncMock()
        client.get_dialogs = AsyncMock(return_value=[dialog])
        client.get_messages = AsyncMock(return_value=[msg])

        should_respond_fn = AsyncMock(return_value=(True, False))
        enqueue_fn = AsyncMock()
        record_fn = AsyncMock()

        with (
            patch(
                "bridge.reconciler.is_duplicate_message", new_callable=AsyncMock, return_value=False
            ),
            patch("bridge.reconciler.record_message_processed", record_fn),
        ):
            result = await reconcile_once(
                client=client,
                monitored_groups=["test group"],
                should_respond_fn=should_respond_fn,
                enqueue_agent_session_fn=enqueue_fn,
                find_project_fn=MagicMock(return_value=_make_project()),
            )

        assert result == 1
        enqueue_fn.assert_called_once()
        call_kwargs = enqueue_fn.call_args[1]
        assert call_kwargs["project_key"] == "testproj"
        assert call_kwargs["message_text"] == "missed message"
        assert call_kwargs["priority"] == "low"
        assert call_kwargs["telegram_message_id"] == 555
        expected_chat_id = -(1000000000000 + 200)
        assert call_kwargs["chat_id"] == str(expected_chat_id)
        record_fn.assert_called_once_with(expected_chat_id, 555)

    @pytest.mark.asyncio
    async def test_old_message_outside_lookback_is_skipped(self):
        """Messages older than the lookback window are not processed."""
        dialog = _make_dialog("Test Group")
        # Message from 20 minutes ago, beyond the 10-min lookback
        msg = _make_message(100, text="old message", minutes_ago=RECONCILE_LOOKBACK_MINUTES + 10)

        client = AsyncMock()
        client.get_dialogs = AsyncMock(return_value=[dialog])
        client.get_messages = AsyncMock(return_value=[msg])

        enqueue_fn = AsyncMock()

        with (
            patch(
                "bridge.reconciler.is_duplicate_message", new_callable=AsyncMock, return_value=False
            ),
            patch("bridge.reconciler.record_message_processed", new_callable=AsyncMock),
        ):
            result = await reconcile_once(
                client=client,
                monitored_groups=["test group"],
                should_respond_fn=AsyncMock(return_value=(True, False)),
                enqueue_agent_session_fn=enqueue_fn,
                find_project_fn=MagicMock(return_value=_make_project()),
            )

        assert result == 0
        enqueue_fn.assert_not_called()

    @pytest.mark.asyncio
    async def test_per_group_error_does_not_stop_scan(self):
        """An error scanning one group does not prevent scanning other groups."""
        dialog_ok = _make_dialog("Good Group", entity_id=100)
        dialog_bad = _make_dialog("Bad Group", entity_id=200)
        msg = _make_message(999, text="found message")

        client = AsyncMock()
        client.get_dialogs = AsyncMock(return_value=[dialog_bad, dialog_ok])

        # First call (bad group) raises, second call (good group) returns a message
        client.get_messages = AsyncMock(side_effect=[Exception("API error"), [msg]])

        should_respond_fn = AsyncMock(return_value=(True, False))
        enqueue_fn = AsyncMock()

        def find_project(title):
            return _make_project(key=title.lower().replace(" ", "_"))

        with (
            patch(
                "bridge.reconciler.is_duplicate_message", new_callable=AsyncMock, return_value=False
            ),
            patch("bridge.reconciler.record_message_processed", new_callable=AsyncMock),
        ):
            result = await reconcile_once(
                client=client,
                monitored_groups=["bad group", "good group"],
                should_respond_fn=should_respond_fn,
                enqueue_agent_session_fn=enqueue_fn,
                find_project_fn=find_project,
            )

        assert result == 1
        enqueue_fn.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_project_config_skips_group(self):
        """Groups with no project config are skipped."""
        dialog = _make_dialog("Unknown Group")

        client = AsyncMock()
        client.get_dialogs = AsyncMock(return_value=[dialog])

        enqueue_fn = AsyncMock()

        result = await reconcile_once(
            client=client,
            monitored_groups=["unknown group"],
            should_respond_fn=AsyncMock(),
            enqueue_agent_session_fn=enqueue_fn,
            find_project_fn=MagicMock(return_value=None),
        )

        assert result == 0
        client.get_messages.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_monitored_group_is_skipped(self):
        """Dialogs for non-monitored groups are skipped entirely."""
        dialog = _make_dialog("Random Chat")

        client = AsyncMock()
        client.get_dialogs = AsyncMock(return_value=[dialog])

        enqueue_fn = AsyncMock()

        result = await reconcile_once(
            client=client,
            monitored_groups=["some other group"],
            should_respond_fn=AsyncMock(),
            enqueue_agent_session_fn=enqueue_fn,
            find_project_fn=MagicMock(),
        )

        assert result == 0
        client.get_messages.assert_not_called()

    @pytest.mark.asyncio
    async def test_multiple_missed_messages_all_enqueued(self):
        """Multiple missed messages in the same group are all enqueued."""
        dialog = _make_dialog("Test Group", entity_id=300)
        msg1 = _make_message(10, text="missed one", minutes_ago=2)
        msg2 = _make_message(11, text="missed two", minutes_ago=1)

        client = AsyncMock()
        client.get_dialogs = AsyncMock(return_value=[dialog])
        client.get_messages = AsyncMock(return_value=[msg1, msg2])

        should_respond_fn = AsyncMock(return_value=(True, False))
        enqueue_fn = AsyncMock()

        with (
            patch(
                "bridge.reconciler.is_duplicate_message", new_callable=AsyncMock, return_value=False
            ),
            patch("bridge.reconciler.record_message_processed", new_callable=AsyncMock),
        ):
            result = await reconcile_once(
                client=client,
                monitored_groups=["test group"],
                should_respond_fn=should_respond_fn,
                enqueue_agent_session_fn=enqueue_fn,
                find_project_fn=MagicMock(return_value=_make_project()),
            )

        assert result == 2
        assert enqueue_fn.call_count == 2
