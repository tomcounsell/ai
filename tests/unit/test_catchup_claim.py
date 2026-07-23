"""Unit tests for the atomic per-message claim in bridge/catchup.py (issue #1817 B1).

``scan_for_missed_messages`` is the startup recovery scanner. It intentionally
bypasses ``bridge/dispatch.py``'s wrapper function but shares the SAME
``claim_message``/``release_message_claim`` gate (same Redis key shape) so a
peer producer (the live handler, or the periodic reconciler) racing on the
SAME message loses cleanly instead of double-enqueueing.

These tests mock the Telethon client, routing, and enqueue functions; they
patch ``bridge.dedup.claim_message`` / ``bridge.dedup.release_message_claim``
directly since ``bridge/catchup.py`` imports them with a local (in-function)
``from bridge.dedup import ...`` -- patching the source module's attribute is
what actually takes effect for a fresh local import on each call.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bridge.catchup import scan_for_missed_messages


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


def _make_dialog(chat_title, entity_id=100, chat_id=None):
    dialog = MagicMock()
    dialog.entity = MagicMock()
    dialog.entity.title = chat_title
    dialog.entity.id = entity_id
    dialog.id = chat_id if chat_id is not None else -(1000000000000 + entity_id)
    return dialog


def _make_project(key="testproj", working_dir="/tmp/test"):
    return {"_key": key, "working_directory": working_dir}


def _client_for(dialog, message):
    """A client whose get_messages returns the message for the main scan.

    The dedup set (``is_duplicate_message``) is now the sole "already
    handled" guard, so ``get_messages`` is never called with ``min_id`` by
    ``scan_for_missed_messages`` -- the ``min_id`` branch below is kept only
    as defensive scaffolding in case a future caller reintroduces such a
    call.
    """
    client = AsyncMock()
    client.get_dialogs = AsyncMock(return_value=[dialog])

    async def _get_messages(entity, limit=None, min_id=None):
        if min_id is not None:
            return []
        return [message]

    client.get_messages = AsyncMock(side_effect=_get_messages)
    return client


_PATCH_TARGETS = (
    "bridge.dedup.is_duplicate_message",
    "bridge.dedup.get_last_processed",
    "bridge.dedup.record_message_processed",
    "bridge.dedup.record_last_processed",
    "bridge.dedup.claim_message",
    "bridge.dedup.release_message_claim",
)


class TestCatchupClaimGate:
    @pytest.mark.asyncio
    async def test_lost_claim_skips_enqueue_and_dedup(self):
        """A lost claim skips enqueue AND leaves no durable dedup (BLOCKER).

        This is the exact round-4 scenario: a peer producer already won this
        message. The loser must not call record_message_processed (it does
        not double-record), so if the winner dies before enqueue a re-scan
        self-heals instead of the message being silently dropped forever.
        """
        dialog = _make_dialog("Test Group", entity_id=400)
        msg = _make_message(700, text="missed message")
        client = _client_for(dialog, msg)
        enqueue_fn = AsyncMock()

        with (
            patch("bridge.dedup.is_duplicate_message", new_callable=AsyncMock, return_value=False),
            patch("bridge.dedup.get_last_processed", new_callable=AsyncMock, return_value=None),
            patch("bridge.dedup.record_message_processed", new_callable=AsyncMock) as rec_processed,
            patch("bridge.dedup.record_last_processed", new_callable=AsyncMock) as rec_last,
            patch("bridge.dedup.claim_message", new_callable=AsyncMock, return_value=False),
            patch("bridge.dedup.release_message_claim", new_callable=AsyncMock) as release,
        ):
            queued = await scan_for_missed_messages(
                client=client,
                monitored_groups=["test group"],
                projects_config={},
                should_respond_fn=AsyncMock(return_value=(True, False)),
                enqueue_agent_session_fn=enqueue_fn,
                find_project_fn=MagicMock(return_value=_make_project()),
            )

        assert queued == 0
        enqueue_fn.assert_not_called()
        rec_processed.assert_not_called()
        rec_last.assert_not_called()
        # Nothing to release -- the claim was never won by this caller.
        release.assert_not_called()

    @pytest.mark.asyncio
    async def test_won_claim_enqueues_and_records_dedup(self):
        """A won claim proceeds to enqueue and records durable dedup as before."""
        dialog = _make_dialog("Test Group", entity_id=401)
        msg = _make_message(701, text="missed message")
        client = _client_for(dialog, msg)
        enqueue_fn = AsyncMock()

        with (
            patch("bridge.dedup.is_duplicate_message", new_callable=AsyncMock, return_value=False),
            patch("bridge.dedup.get_last_processed", new_callable=AsyncMock, return_value=None),
            patch("bridge.dedup.record_message_processed", new_callable=AsyncMock) as rec_processed,
            patch("bridge.dedup.record_last_processed", new_callable=AsyncMock) as rec_last,
            patch("bridge.dedup.claim_message", new_callable=AsyncMock, return_value=True),
            patch("bridge.dedup.release_message_claim", new_callable=AsyncMock) as release,
        ):
            queued = await scan_for_missed_messages(
                client=client,
                monitored_groups=["test group"],
                projects_config={},
                should_respond_fn=AsyncMock(return_value=(True, False)),
                enqueue_agent_session_fn=enqueue_fn,
                find_project_fn=MagicMock(return_value=_make_project()),
            )

        assert queued == 1
        enqueue_fn.assert_called_once()
        rec_processed.assert_called_once_with(dialog.id, 701)
        rec_last.assert_called_once_with(dialog.id, 701, msg.date)
        release.assert_not_called()

    @pytest.mark.asyncio
    async def test_enqueue_exception_releases_claim_no_orphan(self):
        """A fault-injected enqueue exception releases the claim before the
        per-group except/continue swallows it, and dedup stays unrecorded.
        """
        dialog = _make_dialog("Test Group", entity_id=402)
        msg = _make_message(702, text="missed message")
        client = _client_for(dialog, msg)
        enqueue_fn = AsyncMock(side_effect=RuntimeError("enqueue boom"))

        with (
            patch("bridge.dedup.is_duplicate_message", new_callable=AsyncMock, return_value=False),
            patch("bridge.dedup.get_last_processed", new_callable=AsyncMock, return_value=None),
            patch("bridge.dedup.record_message_processed", new_callable=AsyncMock) as rec_processed,
            patch("bridge.dedup.record_last_processed", new_callable=AsyncMock),
            patch("bridge.dedup.claim_message", new_callable=AsyncMock, return_value=True),
            patch("bridge.dedup.release_message_claim", new_callable=AsyncMock) as release,
        ):
            # scan_for_missed_messages wraps the per-group body in a broad
            # try/except that logs and continues to the next group -- the
            # exception does not propagate out of the function, but the
            # claim release must have happened before it was swallowed.
            queued = await scan_for_missed_messages(
                client=client,
                monitored_groups=["test group"],
                projects_config={},
                should_respond_fn=AsyncMock(return_value=(True, False)),
                enqueue_agent_session_fn=enqueue_fn,
                find_project_fn=MagicMock(return_value=_make_project()),
            )

        assert queued == 0
        release.assert_called_once_with(dialog.id, 702)
        rec_processed.assert_not_called()
