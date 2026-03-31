"""Tests for abandoned session revival via bridge/catchup.py.

Covers the gap identified in issue #471: after PR #470 removed workflow_id,
no test verified that scan_for_missed_messages still re-enqueues correctly
without the workflow_id kwarg.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bridge.catchup import scan_for_missed_messages


def _make_entity(chat_id: int, title: str):
    """Create a minimal Telegram entity-like object."""
    entity = SimpleNamespace(id=chat_id, title=title)
    return entity


def _make_dialog(chat_id: int, title: str):
    """Create a minimal Telegram dialog-like object."""
    entity = _make_entity(chat_id, title)
    return SimpleNamespace(id=chat_id, entity=entity)


def _make_message(msg_id: int, text: str, out: bool = False, minutes_ago: int = 5):
    """Create a minimal Telegram message-like object."""
    date = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    sender = SimpleNamespace(first_name="TestUser", username="testuser", id=12345)
    msg = MagicMock()
    msg.id = msg_id
    msg.text = text
    msg.out = out
    msg.date = date
    msg.reply_to_msg_id = None
    msg.get_sender = AsyncMock(return_value=sender)
    return msg


class TestCatchupRevival:
    """Verify scan_for_missed_messages re-enqueues abandoned messages correctly."""

    @pytest.mark.asyncio
    async def test_enqueue_called_without_workflow_id(self):
        """Revival path should call enqueue_agent_session_fn without workflow_id kwarg.

        After PR #470 removed workflow_id from enqueue_agent_session, the catchup path
        must not pass it. This test verifies the correct kwargs are used.
        """
        # Set up mocks
        client = AsyncMock()
        dialog = _make_dialog(chat_id=100, title="Dev: Popoto")
        client.get_dialogs.return_value = [dialog]

        message = _make_message(msg_id=42, text="Please fix the bug")
        client.get_messages.return_value = [message]

        should_respond_fn = AsyncMock(return_value=(True, False))
        enqueue_agent_session_fn = AsyncMock()
        find_project_fn = MagicMock(
            return_value={"_key": "popoto", "working_directory": "/tmp/popoto"}
        )

        monitored_groups = ["dev: popoto"]

        with (
            patch("bridge.dedup.is_duplicate_message", new_callable=AsyncMock) as mock_dedup,
            patch("bridge.catchup._check_if_handled", new_callable=AsyncMock) as mock_handled,
            patch("bridge.dedup.record_message_processed", new_callable=AsyncMock),
        ):
            mock_dedup.return_value = False
            mock_handled.return_value = False

            queued = await scan_for_missed_messages(
                client=client,
                monitored_groups=monitored_groups,
                projects_config={},
                should_respond_fn=should_respond_fn,
                enqueue_agent_session_fn=enqueue_agent_session_fn,
                find_project_fn=find_project_fn,
            )

        assert queued == 1
        enqueue_agent_session_fn.assert_called_once()

        # Verify workflow_id is NOT in the kwargs (it was removed in PR #470)
        call_kwargs = enqueue_agent_session_fn.call_args[1]
        assert "workflow_id" not in call_kwargs, (
            "workflow_id was removed in PR #470 and must not be passed to enqueue_agent_session_fn"
        )

    @pytest.mark.asyncio
    async def test_enqueue_called_with_correct_project_key(self):
        """Revival should pass the correct project_key from find_project_fn."""
        client = AsyncMock()
        dialog = _make_dialog(chat_id=200, title="Dev: Valor")
        client.get_dialogs.return_value = [dialog]

        message = _make_message(msg_id=99, text="What is the status?")
        client.get_messages.return_value = [message]

        should_respond_fn = AsyncMock(return_value=(True, False))
        enqueue_agent_session_fn = AsyncMock()
        find_project_fn = MagicMock(
            return_value={"_key": "valor", "working_directory": "/tmp/valor"}
        )

        with (
            patch("bridge.dedup.is_duplicate_message", new_callable=AsyncMock) as mock_dedup,
            patch("bridge.catchup._check_if_handled", new_callable=AsyncMock) as mock_handled,
            patch("bridge.dedup.record_message_processed", new_callable=AsyncMock),
        ):
            mock_dedup.return_value = False
            mock_handled.return_value = False

            queued = await scan_for_missed_messages(
                client=client,
                monitored_groups=["dev: valor"],
                projects_config={},
                should_respond_fn=should_respond_fn,
                enqueue_agent_session_fn=enqueue_agent_session_fn,
                find_project_fn=find_project_fn,
            )

        assert queued == 1
        call_kwargs = enqueue_agent_session_fn.call_args[1]
        assert call_kwargs["project_key"] == "valor"
        assert call_kwargs["chat_id"] == "200"
        assert call_kwargs["telegram_message_id"] == 99
        assert call_kwargs["priority"] == "low"

    @pytest.mark.asyncio
    async def test_skips_outgoing_messages(self):
        """Revival should skip messages sent by us (out=True)."""
        client = AsyncMock()
        dialog = _make_dialog(chat_id=100, title="Dev: Popoto")
        client.get_dialogs.return_value = [dialog]

        our_msg = _make_message(msg_id=50, text="I will fix it", out=True)
        client.get_messages.return_value = [our_msg]

        enqueue_agent_session_fn = AsyncMock()
        find_project_fn = MagicMock(
            return_value={"_key": "popoto", "working_directory": "/tmp/popoto"}
        )

        queued = await scan_for_missed_messages(
            client=client,
            monitored_groups=["dev: popoto"],
            projects_config={},
            should_respond_fn=AsyncMock(return_value=(True, False)),
            enqueue_agent_session_fn=enqueue_agent_session_fn,
            find_project_fn=find_project_fn,
        )

        assert queued == 0
        enqueue_agent_session_fn.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_already_deduplicated_messages(self):
        """Revival should skip messages already in Redis dedup."""
        client = AsyncMock()
        dialog = _make_dialog(chat_id=100, title="Dev: Popoto")
        client.get_dialogs.return_value = [dialog]

        message = _make_message(msg_id=55, text="Fix the tests")
        client.get_messages.return_value = [message]

        enqueue_agent_session_fn = AsyncMock()
        find_project_fn = MagicMock(
            return_value={"_key": "popoto", "working_directory": "/tmp/popoto"}
        )

        with patch("bridge.dedup.is_duplicate_message", new_callable=AsyncMock) as mock_dedup:
            mock_dedup.return_value = True  # Already processed

            queued = await scan_for_missed_messages(
                client=client,
                monitored_groups=["dev: popoto"],
                projects_config={},
                should_respond_fn=AsyncMock(return_value=(True, False)),
                enqueue_agent_session_fn=enqueue_agent_session_fn,
                find_project_fn=find_project_fn,
            )

        assert queued == 0
        enqueue_agent_session_fn.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_project_config_skips_group(self):
        """If find_project_fn returns None, the group should be skipped."""
        client = AsyncMock()
        dialog = _make_dialog(chat_id=100, title="Dev: Unknown")
        client.get_dialogs.return_value = [dialog]

        enqueue_agent_session_fn = AsyncMock()
        find_project_fn = MagicMock(return_value=None)

        queued = await scan_for_missed_messages(
            client=client,
            monitored_groups=["dev: unknown"],
            projects_config={},
            should_respond_fn=AsyncMock(return_value=(True, False)),
            enqueue_agent_session_fn=enqueue_agent_session_fn,
            find_project_fn=find_project_fn,
        )

        assert queued == 0
        enqueue_agent_session_fn.assert_not_called()


class TestCatchupLookbackOverride:
    """Verify the lookback_override parameter controls the catchup window."""

    @pytest.mark.asyncio
    async def test_lookback_override_extends_window(self):
        """With lookback_override, messages older than default 60 min should be found."""
        client = AsyncMock()
        dialog = _make_dialog(chat_id=100, title="Dev: Popoto")
        client.get_dialogs.return_value = [dialog]

        # Message from 3 hours ago (would be skipped with default 60 min)
        message = _make_message(msg_id=42, text="Fix the bug", minutes_ago=180)
        client.get_messages.return_value = [message]

        should_respond_fn = AsyncMock(return_value=(True, False))
        enqueue_agent_session_fn = AsyncMock()
        find_project_fn = MagicMock(
            return_value={"_key": "popoto", "working_directory": "/tmp/popoto"}
        )

        with (
            patch("bridge.dedup.is_duplicate_message", new_callable=AsyncMock) as mock_dedup,
            patch("bridge.catchup._check_if_handled", new_callable=AsyncMock) as mock_handled,
            patch("bridge.dedup.record_message_processed", new_callable=AsyncMock),
        ):
            mock_dedup.return_value = False
            mock_handled.return_value = False

            # Use a 4-hour lookback override
            queued = await scan_for_missed_messages(
                client=client,
                monitored_groups=["dev: popoto"],
                projects_config={},
                should_respond_fn=should_respond_fn,
                enqueue_agent_session_fn=enqueue_agent_session_fn,
                find_project_fn=find_project_fn,
                lookback_override=timedelta(hours=4),
            )

        assert queued == 1
        enqueue_agent_session_fn.assert_called_once()

    @pytest.mark.asyncio
    async def test_lookback_override_capped_at_24h(self):
        """lookback_override should be capped at 24 hours."""
        client = AsyncMock()
        dialog = _make_dialog(chat_id=100, title="Dev: Popoto")
        client.get_dialogs.return_value = [dialog]

        # Message from 30 hours ago (beyond 24h cap)
        message = _make_message(msg_id=42, text="Old message", minutes_ago=30 * 60)
        client.get_messages.return_value = [message]

        should_respond_fn = AsyncMock(return_value=(True, False))
        enqueue_agent_session_fn = AsyncMock()
        find_project_fn = MagicMock(
            return_value={"_key": "popoto", "working_directory": "/tmp/popoto"}
        )

        with (
            patch("bridge.dedup.is_duplicate_message", new_callable=AsyncMock) as mock_dedup,
            patch("bridge.catchup._check_if_handled", new_callable=AsyncMock) as mock_handled,
            patch("bridge.dedup.record_message_processed", new_callable=AsyncMock),
        ):
            mock_dedup.return_value = False
            mock_handled.return_value = False

            # Use a 48-hour lookback override (should be capped to 24h)
            queued = await scan_for_missed_messages(
                client=client,
                monitored_groups=["dev: popoto"],
                projects_config={},
                should_respond_fn=should_respond_fn,
                enqueue_agent_session_fn=enqueue_agent_session_fn,
                find_project_fn=find_project_fn,
                lookback_override=timedelta(hours=48),
            )

        # Message at 30h ago is beyond the 24h cap, so it should be skipped
        assert queued == 0

    @pytest.mark.asyncio
    async def test_default_lookback_without_override(self):
        """Without lookback_override, default 60 min window should apply."""
        client = AsyncMock()
        dialog = _make_dialog(chat_id=100, title="Dev: Popoto")
        client.get_dialogs.return_value = [dialog]

        # Message from 90 minutes ago (beyond default 60 min)
        message = _make_message(msg_id=42, text="Missed message", minutes_ago=90)
        client.get_messages.return_value = [message]

        enqueue_agent_session_fn = AsyncMock()
        find_project_fn = MagicMock(
            return_value={"_key": "popoto", "working_directory": "/tmp/popoto"}
        )

        queued = await scan_for_missed_messages(
            client=client,
            monitored_groups=["dev: popoto"],
            projects_config={},
            should_respond_fn=AsyncMock(return_value=(True, False)),
            enqueue_agent_session_fn=enqueue_agent_session_fn,
            find_project_fn=find_project_fn,
            # No lookback_override
        )

        # Message at 90 min ago is beyond default 60 min window
        assert queued == 0
