"""Issue #2477: catchup's fetch must not truncate the cursor-extended lookback.

The reconciler got the paged/bounded/loud fetch in the #2476 fix, but catchup
kept a single ``get_messages(limit=50)`` call. Since catchup's per-chat cutoff
extends back to the last-dispatched cursor (potentially days), a chat with more
than one page of messages in the gap silently lost every older missed message.
These tests pin the ported fix and the scanner-parity relationship so the two
recovery scanners cannot drift apart again.

Mocking follows tests/unit/test_catchup_claim.py: dedup functions are patched
at ``bridge.dedup`` because bridge/catchup.py imports them locally per call.
"""

import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bridge.catchup import (
    CATCHUP_MAX_MESSAGES_PER_CHAT,
    CATCHUP_MESSAGE_LIMIT,
    scan_for_missed_messages,
)


def _make_message(msg_id, text="hello", out=False, minutes_ago=1):
    msg = MagicMock()
    msg.id = msg_id
    msg.text = text
    msg.out = out
    msg.date = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    msg.reply_to_msg_id = None

    sender = MagicMock()
    sender.first_name = "TestUser"
    sender.username = "testuser"
    sender.id = 12345
    msg.get_sender = AsyncMock(return_value=sender)
    return msg


def _make_dialog(chat_title, entity_id=100):
    dialog = MagicMock()
    dialog.entity = MagicMock()
    dialog.entity.title = chat_title
    dialog.entity.id = entity_id
    dialog.id = -(1000000000000 + entity_id)
    return dialog


def _paging_client(dialog, messages):
    """A client whose get_messages honors offset_id, like Telethon's."""
    newest_first = sorted(messages, key=lambda m: m.id, reverse=True)

    async def get_messages(_entity, limit=None, offset_id=0, **_kwargs):
        pool = [m for m in newest_first if offset_id == 0 or m.id < offset_id]
        return pool[:limit] if limit else pool

    client = AsyncMock()
    client.get_dialogs = AsyncMock(return_value=[dialog])
    client.get_messages = get_messages
    return client


def _dedup_patches(cursor=None):
    return (
        patch("bridge.catchup.catchup_disabled", return_value=False),
        patch("bridge.dedup.is_duplicate_message", new_callable=AsyncMock, return_value=False),
        patch("bridge.dedup.get_last_processed", new_callable=AsyncMock, return_value=cursor),
        patch("bridge.dedup.claim_message", new_callable=AsyncMock, return_value=True),
        patch("bridge.dedup.release_message_claim", new_callable=AsyncMock),
        patch("bridge.dedup.record_message_processed", new_callable=AsyncMock),
        patch("bridge.dedup.record_last_processed", new_callable=AsyncMock),
    )


async def _run_scan(client, enqueue_fn, group="Busy Group"):
    return await scan_for_missed_messages(
        client=client,
        monitored_groups=[group.lower()],
        projects_config={},
        should_respond_fn=AsyncMock(return_value=(True, False)),
        enqueue_agent_session_fn=enqueue_fn,
        find_project_fn=MagicMock(
            return_value={"_key": "testproj", "working_directory": "/tmp/test"}
        ),
    )


class TestCatchupDeepFetchPaging:
    """Catchup pages backwards to the cutoff instead of one fixed-size fetch."""

    @pytest.mark.asyncio
    async def test_recovers_messages_beyond_the_first_page(self):
        """All in-window messages are recovered, not just the newest page."""
        dialog = _make_dialog("Busy Group")
        count = CATCHUP_MESSAGE_LIMIT + CATCHUP_MESSAGE_LIMIT // 2
        messages = [_make_message(1000 + i, text=f"msg {i}") for i in range(count)]
        client = _paging_client(dialog, messages)
        enqueue_fn = AsyncMock()

        p1, p2, p3, p4, p5, p6, p7 = _dedup_patches()
        with p1, p2, p3, p4, p5, p6, p7:
            queued = await _run_scan(client, enqueue_fn)

        assert queued == count
        recovered_ids = {c[1]["telegram_message_id"] for c in enqueue_fn.call_args_list}
        assert recovered_ids == {m.id for m in messages}

    @pytest.mark.asyncio
    async def test_paging_stops_at_the_cutoff(self):
        """Paging stops once the cutoff is crossed; stale messages are untouched."""
        from bridge.catchup import CATCHUP_LOOKBACK_MINUTES

        dialog = _make_dialog("Busy Group")
        in_window = [
            _make_message(2000 + i, text=f"recent {i}") for i in range(CATCHUP_MESSAGE_LIMIT + 5)
        ]
        stale = [
            _make_message(1000 + i, text=f"stale {i}", minutes_ago=CATCHUP_LOOKBACK_MINUTES + 90)
            for i in range(CATCHUP_MESSAGE_LIMIT)
        ]
        client = _paging_client(dialog, in_window + stale)
        enqueue_fn = AsyncMock()

        p1, p2, p3, p4, p5, p6, p7 = _dedup_patches()
        with p1, p2, p3, p4, p5, p6, p7:
            queued = await _run_scan(client, enqueue_fn)

        assert queued == len(in_window)
        recovered_ids = {c[1]["telegram_message_id"] for c in enqueue_fn.call_args_list}
        assert recovered_ids == {m.id for m in in_window}

    @pytest.mark.asyncio
    async def test_fetch_is_bounded_and_truncation_is_logged(self, caplog):
        """The per-chat ceiling binds, and hitting it emits a loud WARNING.

        A recovery scan that stops short must be distinguishable from one that
        found nothing -- that ambiguity is what let the reconciler's twin of
        this bug survive unnoticed (#2476).
        """
        dialog = _make_dialog("Firehose")
        count = CATCHUP_MAX_MESSAGES_PER_CHAT + 25
        messages = [_make_message(5000 + i, text=f"msg {i}") for i in range(count)]
        client = _paging_client(dialog, messages)
        enqueue_fn = AsyncMock()

        p1, p2, p3, p4, p5, p6, p7 = _dedup_patches()
        with caplog.at_level(logging.WARNING), p1, p2, p3, p4, p5, p6, p7:
            queued = await _run_scan(client, enqueue_fn, group="Firehose")

        assert queued == CATCHUP_MAX_MESSAGES_PER_CHAT
        assert "TRUNCATED" in caplog.text
        assert "[catchup]" in caplog.text


class TestScannerParityPins:
    """The two recovery scanners must not drift apart again (#2477 scope 3-4)."""

    def test_ceilings_are_one_policy(self):
        """Recovery depth is a single decision, not two accidents.

        If these ever need to differ, that divergence is a policy change:
        make it deliberately and update this pin with the rationale.
        """
        from bridge.reconciler import RECONCILE_MAX_MESSAGES_PER_CHAT

        assert CATCHUP_MAX_MESSAGES_PER_CHAT == RECONCILE_MAX_MESSAGES_PER_CHAT

    def test_scanners_share_the_paged_fetch_implementation(self):
        """Both scanners call the SAME fetch function, so the paged/bounded/loud
        mechanism cannot be fixed in one sibling and left broken in the other."""
        import bridge.catchup as catchup
        import bridge.history_fetch as history_fetch
        import bridge.reconciler as reconciler

        assert catchup.fetch_messages_back_to is history_fetch.fetch_messages_back_to
        assert reconciler.fetch_messages_back_to is history_fetch.fetch_messages_back_to

    def test_dedup_window_covers_catchup_ceiling(self):
        """#2477 scope 4: the dedup-ordering invariant for the catchup ceiling.

        DedupRecord retains only its most recent _MAX_IDS ids per chat; a scan
        reaching past that window loses its "already handled" guard.
        tests/unit/test_dedup.py pins the max() over both scanners; this pins
        the catchup side explicitly.
        """
        from models.dedup import DedupRecord

        assert DedupRecord._MAX_IDS >= CATCHUP_MAX_MESSAGES_PER_CHAT
