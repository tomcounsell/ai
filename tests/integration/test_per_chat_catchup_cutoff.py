"""Integration test for the per-chat catchup cutoff (issue #1408).

Reproduces the "catchup dead zone": the global cutoff (derived from
``last_connected``) advances on every heartbeat, so a message sent inside the
connection window but silently dropped by Telethon falls BEFORE the cutoff on
restart and is excluded from catchup.

These tests exercise the real ``LastProcessedRecord`` Popoto model (no mock of
the cursor store) to prove that when the per-chat cursor predates the global
cutoff, catchup extends its lookback and recovers the message in the gap.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bridge.catchup import scan_for_missed_messages
from bridge.dedup import record_last_processed
from models.last_processed import LastProcessedRecord

TEST_CHAT_ID = -1009999000001


def _cleanup():
    for record in LastProcessedRecord.query.all():
        if str(record.chat_id) == str(TEST_CHAT_ID):
            record.delete()


def _make_dialog(chat_id: int, title: str):
    entity = SimpleNamespace(id=chat_id, title=title)
    return SimpleNamespace(id=chat_id, entity=entity)


def _make_message(msg_id: int, text: str, minutes_ago: float):
    date = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    sender = SimpleNamespace(first_name="Tom", username="tom", id=555)
    msg = MagicMock()
    msg.id = msg_id
    msg.text = text
    msg.out = False
    msg.date = date
    msg.reply_to_msg_id = None
    msg.get_sender = AsyncMock(return_value=sender)
    return msg


class TestPerChatCatchupCutoff:
    def setup_method(self):
        _cleanup()

    def teardown_method(self):
        _cleanup()

    @pytest.mark.asyncio
    async def test_message_in_gap_recovered_when_cursor_predates_global_cutoff(self):
        """A message 8 min old is recovered when the cursor predates the 3-min global cutoff.

        Global cutoff = now - 3 min (simulating a recent heartbeat-advanced
        last_connected). The cursor records a message dispatched 10 minutes ago,
        so per-chat cutoff becomes ~10 min ago. A message sent 8 minutes ago —
        inside the gap, EXCLUDED by the global cutoff — must be recovered.
        """
        # Seed the per-chat cursor: last dispatched message was 10 min ago.
        cursor_dt = datetime.now(UTC) - timedelta(minutes=10)
        await record_last_processed(TEST_CHAT_ID, 1000, cursor_dt)

        dialog = _make_dialog(TEST_CHAT_ID, "Cyndra Dev")
        client = AsyncMock()
        client.get_dialogs.return_value = [dialog]

        # The missed message: 8 minutes old, inside the gap.
        missed = _make_message(1005, "hey can you look at this", minutes_ago=8)
        client.get_messages.return_value = [missed]

        enqueue_fn = AsyncMock()
        project = {"_key": "cyndra", "working_directory": "/tmp/cyndra"}

        with (
            patch("bridge.catchup._check_if_handled", new_callable=AsyncMock, return_value=False),
            patch("bridge.dedup.is_duplicate_message", new_callable=AsyncMock, return_value=False),
        ):
            queued = await scan_for_missed_messages(
                client=client,
                monitored_groups=["cyndra dev"],
                projects_config={},
                should_respond_fn=AsyncMock(return_value=(True, False)),
                enqueue_agent_session_fn=enqueue_fn,
                find_project_fn=MagicMock(return_value=project),
                # Global lookback of 3 minutes simulates a recently-advanced
                # last_connected (the dead-zone condition).
                lookback_override=timedelta(minutes=3),
            )

        assert queued == 1
        enqueue_fn.assert_called_once()
        assert enqueue_fn.call_args[1]["telegram_message_id"] == 1005

    @pytest.mark.asyncio
    async def test_message_in_gap_excluded_without_cursor(self):
        """Without a cursor, the same 8-min-old message is excluded by the global cutoff.

        This is the regression baseline: it proves the gap exists and that the
        cursor (not some other code path) is what closes it.
        """
        # No cursor seeded.
        dialog = _make_dialog(TEST_CHAT_ID, "Cyndra Dev")
        client = AsyncMock()
        client.get_dialogs.return_value = [dialog]

        missed = _make_message(1005, "hey can you look at this", minutes_ago=8)
        client.get_messages.return_value = [missed]

        enqueue_fn = AsyncMock()
        project = {"_key": "cyndra", "working_directory": "/tmp/cyndra"}

        with (
            patch("bridge.catchup._check_if_handled", new_callable=AsyncMock, return_value=False),
            patch("bridge.dedup.is_duplicate_message", new_callable=AsyncMock, return_value=False),
        ):
            queued = await scan_for_missed_messages(
                client=client,
                monitored_groups=["cyndra dev"],
                projects_config={},
                should_respond_fn=AsyncMock(return_value=(True, False)),
                enqueue_agent_session_fn=enqueue_fn,
                find_project_fn=MagicMock(return_value=project),
                lookback_override=timedelta(minutes=3),
            )

        assert queued == 0
        enqueue_fn.assert_not_called()

    @pytest.mark.asyncio
    async def test_dedup_still_prevents_duplicate(self):
        """No regression: a message already in dedup is not re-enqueued even with the cursor."""
        cursor_dt = datetime.now(UTC) - timedelta(minutes=10)
        await record_last_processed(TEST_CHAT_ID, 1000, cursor_dt)

        dialog = _make_dialog(TEST_CHAT_ID, "Cyndra Dev")
        client = AsyncMock()
        client.get_dialogs.return_value = [dialog]
        missed = _make_message(1005, "already handled", minutes_ago=8)
        client.get_messages.return_value = [missed]

        enqueue_fn = AsyncMock()
        project = {"_key": "cyndra", "working_directory": "/tmp/cyndra"}

        with (
            patch("bridge.catchup._check_if_handled", new_callable=AsyncMock, return_value=False),
            patch("bridge.dedup.is_duplicate_message", new_callable=AsyncMock, return_value=True),
        ):
            queued = await scan_for_missed_messages(
                client=client,
                monitored_groups=["cyndra dev"],
                projects_config={},
                should_respond_fn=AsyncMock(return_value=(True, False)),
                enqueue_agent_session_fn=enqueue_fn,
                find_project_fn=MagicMock(return_value=project),
                lookback_override=timedelta(minutes=3),
            )

        assert queued == 0
        enqueue_fn.assert_not_called()

    @pytest.mark.asyncio
    async def test_teammate_chat_recovered_with_teammate_session_type(self):
        """A recovered message in a teammate-configured chat enqueues teammate + project_config.

        Finding 2: the per-chat catchup path must resolve persona like the live
        handler. A teammate chat recovered here must NOT default to an eng
        PM<->Dev loop.
        """
        from config.enums import SessionType

        cursor_dt = datetime.now(UTC) - timedelta(minutes=10)
        await record_last_processed(TEST_CHAT_ID, 1000, cursor_dt)

        dialog = _make_dialog(TEST_CHAT_ID, "Cyndra Dev Team")
        client = AsyncMock()
        client.get_dialogs.return_value = [dialog]
        missed = _make_message(1005, "@valor please look", minutes_ago=8)
        client.get_messages.return_value = [missed]

        enqueue_fn = AsyncMock()
        project = {
            "_key": "cyndra",
            "working_directory": "/tmp/cyndra",
            "telegram": {"groups": {"Cyndra Dev Team": {"persona": "teammate"}}},
        }

        with (
            patch("bridge.catchup._check_if_handled", new_callable=AsyncMock, return_value=False),
            patch("bridge.dedup.is_duplicate_message", new_callable=AsyncMock, return_value=False),
        ):
            queued = await scan_for_missed_messages(
                client=client,
                monitored_groups=["cyndra dev team"],
                projects_config={},
                should_respond_fn=AsyncMock(return_value=(True, False)),
                enqueue_agent_session_fn=enqueue_fn,
                find_project_fn=MagicMock(return_value=project),
                lookback_override=timedelta(minutes=3),
            )

        assert queued == 1
        call_kwargs = enqueue_fn.call_args[1]
        assert call_kwargs["session_type"] == SessionType.TEAMMATE
        assert call_kwargs["project_config"] is project

    @pytest.mark.asyncio
    async def test_default_chat_recovered_with_eng_session_type(self):
        """A recovered message in a non-teammate chat still enqueues an eng session."""
        from config.enums import SessionType

        cursor_dt = datetime.now(UTC) - timedelta(minutes=10)
        await record_last_processed(TEST_CHAT_ID, 1000, cursor_dt)

        dialog = _make_dialog(TEST_CHAT_ID, "Cyndra Dev")
        client = AsyncMock()
        client.get_dialogs.return_value = [dialog]
        missed = _make_message(1005, "fix the build", minutes_ago=8)
        client.get_messages.return_value = [missed]

        enqueue_fn = AsyncMock()
        project = {"_key": "cyndra", "working_directory": "/tmp/cyndra"}

        with (
            patch("bridge.catchup._check_if_handled", new_callable=AsyncMock, return_value=False),
            patch("bridge.dedup.is_duplicate_message", new_callable=AsyncMock, return_value=False),
        ):
            queued = await scan_for_missed_messages(
                client=client,
                monitored_groups=["cyndra dev"],
                projects_config={},
                should_respond_fn=AsyncMock(return_value=(True, False)),
                enqueue_agent_session_fn=enqueue_fn,
                find_project_fn=MagicMock(return_value=project),
                lookback_override=timedelta(minutes=3),
            )

        assert queued == 1
        call_kwargs = enqueue_fn.call_args[1]
        assert call_kwargs["session_type"] == SessionType.ENG
        assert call_kwargs["project_config"] is project
