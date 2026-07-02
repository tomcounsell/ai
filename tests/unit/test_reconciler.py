"""Unit tests for bridge/reconciler.py.

Tests the reconcile_once() function with mocked dependencies:
client, dedup, routing, and enqueue functions.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bridge.reconciler import RECONCILE_LOOKBACK_MINUTES, reconcile_once
from bridge.silent_stream import SILENCE_THRESHOLD_SECONDS, SilentStreamState


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
            patch("bridge.reconciler.claim_message", new_callable=AsyncMock, return_value=True),
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
            patch("bridge.reconciler.claim_message", new_callable=AsyncMock, return_value=True),
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
            patch("bridge.reconciler.claim_message", new_callable=AsyncMock, return_value=True),
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
            patch("bridge.reconciler.claim_message", new_callable=AsyncMock, return_value=True),
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
        cursor_fn = AsyncMock()

        with (
            patch(
                "bridge.reconciler.is_duplicate_message", new_callable=AsyncMock, return_value=False
            ),
            patch("bridge.reconciler.claim_message", new_callable=AsyncMock, return_value=True),
            patch("bridge.reconciler.record_message_processed", record_fn),
            patch("bridge.reconciler.record_last_processed", cursor_fn),
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
        # Per-chat cursor is advanced alongside dedup (issue #1408)
        cursor_fn.assert_called_once_with(expected_chat_id, 555, msg.date)

    @pytest.mark.asyncio
    async def test_lost_claim_skips_enqueue_and_leaves_no_dedup(self):
        """A lost message claim skips enqueue AND leaves no durable dedup (BLOCKER).

        Issue #1817 B1, round-4 BLOCKER: this is the exact scenario where a
        peer producer (the live handler, or catchup) already won the SAME
        message. The loser must not call record_message_processed (it does
        not double-record), so if the winner dies before enqueue the next
        reconciler scan re-picks the never-enqueued message instead of
        silently dropping it forever.
        """
        dialog = _make_dialog("Test Group", entity_id=250)
        msg = _make_message(556, text="raced message")

        client = AsyncMock()
        client.get_dialogs = AsyncMock(return_value=[dialog])
        client.get_messages = AsyncMock(return_value=[msg])

        should_respond_fn = AsyncMock(return_value=(True, False))
        enqueue_fn = AsyncMock()
        record_fn = AsyncMock()
        cursor_fn = AsyncMock()
        release_fn = AsyncMock()

        with (
            patch(
                "bridge.reconciler.is_duplicate_message", new_callable=AsyncMock, return_value=False
            ),
            patch("bridge.reconciler.claim_message", new_callable=AsyncMock, return_value=False),
            patch("bridge.reconciler.release_message_claim", release_fn),
            patch("bridge.reconciler.record_message_processed", record_fn),
            patch("bridge.reconciler.record_last_processed", cursor_fn),
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
        record_fn.assert_not_called()
        cursor_fn.assert_not_called()
        # Nothing to release -- the claim was never won by this caller.
        release_fn.assert_not_called()

    @pytest.mark.asyncio
    async def test_enqueue_exception_releases_claim_no_orphan(self):
        """A fault-injected enqueue exception releases the claim before
        propagating, preserving the propagate-and-retry contract (dedup
        stays unrecorded so the message is re-enqueueable).
        """
        dialog = _make_dialog("Test Group", entity_id=251)
        msg = _make_message(557, text="doomed message")

        client = AsyncMock()
        client.get_dialogs = AsyncMock(return_value=[dialog])
        client.get_messages = AsyncMock(return_value=[msg])

        should_respond_fn = AsyncMock(return_value=(True, False))
        enqueue_fn = AsyncMock(side_effect=RuntimeError("enqueue boom"))
        record_fn = AsyncMock()
        release_fn = AsyncMock()

        with (
            patch(
                "bridge.reconciler.is_duplicate_message", new_callable=AsyncMock, return_value=False
            ),
            patch("bridge.reconciler.claim_message", new_callable=AsyncMock, return_value=True),
            patch("bridge.reconciler.release_message_claim", release_fn),
            patch("bridge.reconciler.record_message_processed", record_fn),
            patch("bridge.reconciler.record_last_processed", new_callable=AsyncMock),
        ):
            # reconcile_once wraps the per-group body in a broad try/except
            # that logs and continues -- the exception does not propagate
            # out of reconcile_once, but the claim release must have
            # happened before it was swallowed.
            result = await reconcile_once(
                client=client,
                monitored_groups=["test group"],
                should_respond_fn=should_respond_fn,
                enqueue_agent_session_fn=enqueue_fn,
                find_project_fn=MagicMock(return_value=_make_project()),
            )

        assert result == 0
        release_fn.assert_called_once_with(dialog.id, 557)
        record_fn.assert_not_called()

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
            patch("bridge.reconciler.claim_message", new_callable=AsyncMock, return_value=True),
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
            patch("bridge.reconciler.claim_message", new_callable=AsyncMock, return_value=True),
            patch("bridge.reconciler.record_message_processed", new_callable=AsyncMock),
            patch("bridge.reconciler.record_last_processed", new_callable=AsyncMock),
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
            patch("bridge.reconciler.claim_message", new_callable=AsyncMock, return_value=True),
            patch("bridge.reconciler.record_message_processed", new_callable=AsyncMock),
            patch("bridge.reconciler.record_last_processed", new_callable=AsyncMock),
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


class TestReconcileOnceSilentStream:
    """The silent-gap check (issue #1408) rides the reconciler's dialog pass.

    The reconciler already fetches dialogs every pass; the silent-gap check
    reuses them rather than running a separate loop with its own get_dialogs().
    """

    @pytest.mark.asyncio
    async def test_silent_check_runs_on_existing_dialog_pass(self):
        """When state is provided, a silent monitored chat warns using the same dialogs."""
        import time

        dialog = _make_dialog("Cyndra Dev", entity_id=900)
        client = AsyncMock()
        client.get_dialogs = AsyncMock(return_value=[dialog])
        client.get_messages = AsyncMock(return_value=[])

        # Bridge up long ago; chat silent well past the threshold.
        now = time.time()
        state = SilentStreamState(bridge_start_ts=now - 10 * 3600)
        project = {
            "_key": "cyndra",
            "working_directory": "/tmp/cyndra",
            "telegram": {"respond_to_unaddressed": True},
        }

        with patch(
            "bridge.silent_stream.get_last_event_ts",
            new_callable=AsyncMock,
            return_value=now - (SILENCE_THRESHOLD_SECONDS + 60),
        ):
            result = await reconcile_once(
                client=client,
                monitored_groups=["cyndra dev"],
                should_respond_fn=AsyncMock(return_value=(True, False)),
                enqueue_agent_session_fn=AsyncMock(),
                find_project_fn=MagicMock(return_value=project),
                silent_stream_state=state,
            )

        # No messages to recover, but the silent-gap warning was recorded.
        assert result == 0
        assert dialog.id in state.warned_chats
        # The reconciler fetched dialogs exactly once for both jobs.
        client.get_dialogs.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_silent_state_is_a_noop(self):
        """Without silent_stream_state the reconciler behaves exactly as before."""
        dialog = _make_dialog("Test Group")
        msg = _make_message(100, text="missed")
        client = AsyncMock()
        client.get_dialogs = AsyncMock(return_value=[dialog])
        client.get_messages = AsyncMock(return_value=[msg])

        with (
            patch(
                "bridge.reconciler.is_duplicate_message",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch("bridge.reconciler.claim_message", new_callable=AsyncMock, return_value=True),
            patch("bridge.reconciler.record_message_processed", new_callable=AsyncMock),
            patch("bridge.reconciler.record_last_processed", new_callable=AsyncMock),
            patch("bridge.silent_stream.get_last_event_ts", new_callable=AsyncMock) as evt,
        ):
            result = await reconcile_once(
                client=client,
                monitored_groups=["test group"],
                should_respond_fn=AsyncMock(return_value=(True, False)),
                enqueue_agent_session_fn=AsyncMock(),
                find_project_fn=MagicMock(return_value=_make_project()),
            )

        assert result == 1
        # Silent-gap check never ran (no state), so it never touched Redis.
        evt.assert_not_called()

    @pytest.mark.asyncio
    async def test_silent_check_failure_does_not_break_recovery(self):
        """A failing silent-gap check must not stop message recovery."""
        import time

        dialog = _make_dialog("Cyndra Dev", entity_id=950)
        msg = _make_message(123, text="recover me")
        client = AsyncMock()
        client.get_dialogs = AsyncMock(return_value=[dialog])
        client.get_messages = AsyncMock(return_value=[msg])

        state = SilentStreamState(bridge_start_ts=time.time() - 10 * 3600)
        project = {
            "_key": "cyndra",
            "working_directory": "/tmp/cyndra",
            "telegram": {"respond_to_unaddressed": True},
        }
        enqueue_fn = AsyncMock()

        with (
            patch(
                "bridge.reconciler.is_duplicate_message",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch("bridge.reconciler.claim_message", new_callable=AsyncMock, return_value=True),
            patch("bridge.reconciler.record_message_processed", new_callable=AsyncMock),
            patch("bridge.reconciler.record_last_processed", new_callable=AsyncMock),
            patch(
                "bridge.silent_stream.get_last_event_ts",
                new_callable=AsyncMock,
                side_effect=RuntimeError("redis down"),
            ),
        ):
            result = await reconcile_once(
                client=client,
                monitored_groups=["cyndra dev"],
                should_respond_fn=AsyncMock(return_value=(True, False)),
                enqueue_agent_session_fn=enqueue_fn,
                find_project_fn=MagicMock(return_value=project),
                silent_stream_state=state,
            )

        # Recovery still succeeded despite the silent-gap check raising.
        assert result == 1
        enqueue_fn.assert_called_once()


class TestReconcilePersonaSessionType:
    """Reconciler resolves persona -> session_type for parity with the live handler."""

    @pytest.mark.asyncio
    async def test_teammate_persona_enqueues_teammate(self):
        """A teammate-configured chat enqueues session_type=teammate + project_config."""
        from config.enums import SessionType

        dialog = _make_dialog("Cyndra Dev Team", entity_id=210)
        msg = _make_message(601, text="@valor please look")

        client = AsyncMock()
        client.get_dialogs = AsyncMock(return_value=[dialog])
        client.get_messages = AsyncMock(return_value=[msg])

        enqueue_fn = AsyncMock()
        project = {
            "_key": "cyndra",
            "working_directory": "/tmp/cyndra",
            "telegram": {"groups": {"Cyndra Dev Team": {"persona": "teammate"}}},
        }

        with (
            patch(
                "bridge.reconciler.is_duplicate_message", new_callable=AsyncMock, return_value=False
            ),
            patch("bridge.reconciler.claim_message", new_callable=AsyncMock, return_value=True),
            patch("bridge.reconciler.record_message_processed", new_callable=AsyncMock),
            patch("bridge.reconciler.record_last_processed", new_callable=AsyncMock),
        ):
            result = await reconcile_once(
                client=client,
                monitored_groups=["cyndra dev team"],
                should_respond_fn=AsyncMock(return_value=(True, False)),
                enqueue_agent_session_fn=enqueue_fn,
                find_project_fn=MagicMock(return_value=project),
            )

        assert result == 1
        call_kwargs = enqueue_fn.call_args[1]
        assert call_kwargs["session_type"] == SessionType.TEAMMATE
        assert call_kwargs["project_config"] is project

    @pytest.mark.asyncio
    async def test_default_persona_enqueues_eng(self):
        """A chat with no teammate persona enqueues an eng session."""
        from config.enums import SessionType

        dialog = _make_dialog("Test Group", entity_id=220)
        msg = _make_message(602, text="fix the build")

        client = AsyncMock()
        client.get_dialogs = AsyncMock(return_value=[dialog])
        client.get_messages = AsyncMock(return_value=[msg])

        enqueue_fn = AsyncMock()

        with (
            patch(
                "bridge.reconciler.is_duplicate_message", new_callable=AsyncMock, return_value=False
            ),
            patch("bridge.reconciler.claim_message", new_callable=AsyncMock, return_value=True),
            patch("bridge.reconciler.record_message_processed", new_callable=AsyncMock),
            patch("bridge.reconciler.record_last_processed", new_callable=AsyncMock),
        ):
            result = await reconcile_once(
                client=client,
                monitored_groups=["test group"],
                should_respond_fn=AsyncMock(return_value=(True, False)),
                enqueue_agent_session_fn=enqueue_fn,
                find_project_fn=MagicMock(return_value=_make_project()),
            )

        assert result == 1
        assert enqueue_fn.call_args[1]["session_type"] == SessionType.ENG
