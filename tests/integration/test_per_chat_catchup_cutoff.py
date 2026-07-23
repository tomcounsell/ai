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
from bridge.dedup import record_last_processed, record_message_processed
from models.dedup import DedupRecord
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

        # claim_message is mocked True: several tests in this class reuse
        # TEST_CHAT_ID/msg_id 1005, and the real per-message claim (issue
        # #1817) is a short-TTL Redis SETNX unrelated to what's under test
        # here (the per-chat cutoff), so an unmocked real claim would
        # collide across tests within the claim TTL.
        with (
            patch("bridge.dedup.is_duplicate_message", new_callable=AsyncMock, return_value=False),
            patch("bridge.dedup.claim_message", new_callable=AsyncMock, return_value=True),
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

        with patch("bridge.dedup.is_duplicate_message", new_callable=AsyncMock, return_value=False):
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

        with patch("bridge.dedup.is_duplicate_message", new_callable=AsyncMock, return_value=True):
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
            patch("bridge.dedup.is_duplicate_message", new_callable=AsyncMock, return_value=False),
            patch("bridge.dedup.claim_message", new_callable=AsyncMock, return_value=True),
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
            patch("bridge.dedup.is_duplicate_message", new_callable=AsyncMock, return_value=False),
            patch("bridge.dedup.claim_message", new_callable=AsyncMock, return_value=True),
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


class TestDurableDedupAuthoritativeOverFullScanWindow:
    """The durable (cursor-coupled) dedup set is now the SOLE "already handled"
    guard for startup catchup, across the full cursor-bounded lookback window
    -- not just the old 2h TTL window.

    docs/plans/catchup-rehandles-handled-messages.md: before this fix, a
    message dispatched more than 2h before a restart had already aged out of
    the (then-hardcoded 7200s) DedupRecord TTL and fell through to the
    deleted reply-only handled-check heuristic, which missed reaction-only
    acks, non-reply answers, and deliberate no-reply judgments. These tests
    exercise the REAL dedup path (no mocking of is_duplicate_message /
    record_message_processed) to prove a message recorded well outside the
    old 2h window is still recognized and skipped -- because the TTL is now
    settings-backed and coupled to the cursor TTL (~30 days), not a fixed
    short window.
    """

    def setup_method(self):
        _cleanup()
        self._dedup_cleanup()

    def teardown_method(self):
        _cleanup()
        self._dedup_cleanup()

    def _dedup_cleanup(self):
        for record in DedupRecord.query.all():
            if str(record.chat_id) == str(TEST_CHAT_ID):
                record.delete()

    @pytest.mark.asyncio
    async def test_message_dispatched_beyond_old_2h_window_still_skipped(self):
        """A message dispatched well beyond the OLD 2h dedup TTL is skipped.

        Simulates the >2h-old-handled-after-restart case: the per-chat cursor
        reaches back 3 hours (extending the scan window well past the old
        7200s TTL), and the candidate message (2.5h old) is durably recorded
        in the REAL dedup set. Startup catchup must skip it via
        is_duplicate_message -- the exact guard that used to age out at 2h
        and silently fall through to the deleted reply-only heuristic.
        """
        cursor_dt = datetime.now(UTC) - timedelta(hours=3)
        await record_last_processed(TEST_CHAT_ID, 2000, cursor_dt)

        handled_msg_id = 2005
        # Real dedup write -- no mocking. Under the OLD hardcoded 7200s TTL
        # this write would already be gone by the time a >2h-later restart
        # scan reached it; under the new settings-backed TTL it survives.
        await record_message_processed(TEST_CHAT_ID, handled_msg_id)

        dialog = _make_dialog(TEST_CHAT_ID, "Cyndra Dev")
        client = AsyncMock()
        client.get_dialogs.return_value = [dialog]
        handled = _make_message(handled_msg_id, "already answered by reaction", minutes_ago=150)
        client.get_messages.return_value = [handled]

        enqueue_fn = AsyncMock()
        project = {"_key": "cyndra", "working_directory": "/tmp/cyndra"}

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
    async def test_fresh_chat_no_dedup_record_still_recovers_genuine_miss(self):
        """A fresh chat with no DedupRecord/cursor history still recovers a
        genuinely-missed message (regression: the dedup-authoritative guard
        must not accidentally suppress recovery for brand-new chats).
        """
        dialog = _make_dialog(TEST_CHAT_ID, "Cyndra Dev")
        client = AsyncMock()
        client.get_dialogs.return_value = [dialog]
        missed = _make_message(3005, "never seen before", minutes_ago=1)
        client.get_messages.return_value = [missed]

        enqueue_fn = AsyncMock()
        project = {"_key": "cyndra", "working_directory": "/tmp/cyndra"}

        queued = await scan_for_missed_messages(
            client=client,
            monitored_groups=["cyndra dev"],
            projects_config={},
            should_respond_fn=AsyncMock(return_value=(True, False)),
            enqueue_agent_session_fn=enqueue_fn,
            find_project_fn=MagicMock(return_value=project),
        )

        assert queued == 1
        enqueue_fn.assert_called_once()
