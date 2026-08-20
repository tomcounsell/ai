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


class TestDeepFetchPaging:
    """Issue #2476: the fetch must not truncate the cursor-extended lookback.

    5d9515671 extended the per-chat cutoff back to the last-dispatched cursor
    (potentially days), but the fetch stayed a single fixed-size get_messages()
    call. A chat busier than one page lost every missed message older than the
    newest page -- the same fixed-window bug the patch set out to remove, still
    live inside the patch.
    """

    @staticmethod
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

    @pytest.mark.asyncio
    async def test_recovers_messages_beyond_the_first_page(self):
        """All in-window messages are recovered, not just the newest page."""
        from bridge.reconciler import RECONCILE_MESSAGE_LIMIT

        dialog = _make_dialog("Busy Group", entity_id=880)
        # One and a half pages, every message inside the lookback window.
        count = RECONCILE_MESSAGE_LIMIT + RECONCILE_MESSAGE_LIMIT // 2
        messages = [_make_message(1000 + i, text=f"msg {i}", minutes_ago=1) for i in range(count)]

        client = self._paging_client(dialog, messages)
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
                monitored_groups=["busy group"],
                should_respond_fn=AsyncMock(return_value=(True, False)),
                enqueue_agent_session_fn=enqueue_fn,
                find_project_fn=MagicMock(return_value=_make_project()),
            )

        assert result == count
        recovered_ids = {c[1]["telegram_message_id"] for c in enqueue_fn.call_args_list}
        assert recovered_ids == {m.id for m in messages}

    @pytest.mark.asyncio
    async def test_paging_stops_at_the_cutoff(self):
        """Paging stops once the cutoff is crossed; older messages are untouched."""
        from bridge.reconciler import RECONCILE_LOOKBACK_MINUTES, RECONCILE_MESSAGE_LIMIT

        dialog = _make_dialog("Mixed Group", entity_id=881)
        in_window = [
            _make_message(2000 + i, text=f"recent {i}", minutes_ago=1)
            for i in range(RECONCILE_MESSAGE_LIMIT + 5)
        ]
        stale = [
            _make_message(1000 + i, text=f"stale {i}", minutes_ago=RECONCILE_LOOKBACK_MINUTES + 60)
            for i in range(RECONCILE_MESSAGE_LIMIT)
        ]

        client = self._paging_client(dialog, in_window + stale)
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
                monitored_groups=["mixed group"],
                should_respond_fn=AsyncMock(return_value=(True, False)),
                enqueue_agent_session_fn=enqueue_fn,
                find_project_fn=MagicMock(return_value=_make_project()),
            )

        assert result == len(in_window)
        recovered_ids = {c[1]["telegram_message_id"] for c in enqueue_fn.call_args_list}
        assert recovered_ids == {m.id for m in in_window}

    @pytest.mark.asyncio
    async def test_fetch_is_bounded_and_truncation_is_logged(self, caplog):
        """The per-chat ceiling binds, and hitting it emits a loud WARNING.

        A recovery scan that stops short must be distinguishable from one that
        found nothing -- that ambiguity is what let the original bug survive.
        """
        import logging

        from bridge.reconciler import RECONCILE_MAX_MESSAGES_PER_CHAT

        dialog = _make_dialog("Firehose", entity_id=882)
        # More in-window messages than the ceiling allows.
        count = RECONCILE_MAX_MESSAGES_PER_CHAT + 25
        messages = [_make_message(5000 + i, text=f"msg {i}", minutes_ago=1) for i in range(count)]

        client = self._paging_client(dialog, messages)
        enqueue_fn = AsyncMock()

        with (
            caplog.at_level(logging.WARNING, logger="bridge.reconciler"),
            patch(
                "bridge.reconciler.is_duplicate_message", new_callable=AsyncMock, return_value=False
            ),
            patch("bridge.reconciler.claim_message", new_callable=AsyncMock, return_value=True),
            patch("bridge.reconciler.record_message_processed", new_callable=AsyncMock),
            patch("bridge.reconciler.record_last_processed", new_callable=AsyncMock),
        ):
            result = await reconcile_once(
                client=client,
                monitored_groups=["firehose"],
                should_respond_fn=AsyncMock(return_value=(True, False)),
                enqueue_agent_session_fn=enqueue_fn,
                find_project_fn=MagicMock(return_value=_make_project()),
            )

        assert result == RECONCILE_MAX_MESSAGES_PER_CHAT
        assert "TRUNCATED" in caplog.text

    @pytest.mark.asyncio
    async def test_repeated_page_does_not_loop_or_double_process(self):
        """A client that ignores offset_id terminates after one page.

        Defends the paging loop against an API (or test double) that returns
        the same page forever.
        """
        dialog = _make_dialog("Repeater", entity_id=883)
        msg = _make_message(7000, text="only message", minutes_ago=1)

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
                monitored_groups=["repeater"],
                should_respond_fn=AsyncMock(return_value=(True, False)),
                enqueue_agent_session_fn=enqueue_fn,
                find_project_fn=MagicMock(return_value=_make_project()),
            )

        assert result == 1
        assert enqueue_fn.call_count == 1


class TestPerChatCursorCutoff:
    """Retro-tests for hotfix 5d9515671 (issue #2478).

    The hotfix extended reconcile_once's fixed `now - RECONCILE_LOOKBACK_MINUTES`
    cutoff back to the per-chat last-dispatched cursor, so a message missed
    during a long update-loop wedge cannot age out of a blind rolling window.
    It shipped with zero tests; these pin the observed behavior.

    `get_last_processed` is imported locally inside reconcile_once
    (`from bridge.dedup import get_last_processed`), so it is patched at the
    source module `bridge.dedup`, matching tests/unit/test_catchup_claim.py.
    """

    @staticmethod
    def _base_patches():
        return (
            patch(
                "bridge.reconciler.is_duplicate_message", new_callable=AsyncMock, return_value=False
            ),
            patch("bridge.reconciler.claim_message", new_callable=AsyncMock, return_value=True),
            patch("bridge.reconciler.record_message_processed", new_callable=AsyncMock),
            patch("bridge.reconciler.record_last_processed", new_callable=AsyncMock),
        )

    @staticmethod
    async def _run(client, enqueue_fn, group="test group"):
        return await reconcile_once(
            client=client,
            monitored_groups=[group],
            should_respond_fn=AsyncMock(return_value=(True, False)),
            enqueue_agent_session_fn=enqueue_fn,
            find_project_fn=MagicMock(return_value=_make_project()),
        )

    @pytest.mark.asyncio
    async def test_cursor_extends_cutoff_to_last_dispatched(self):
        """A message far older than the rolling window is recovered when the
        per-chat cursor (last dispatched message) is older still."""
        dialog = _make_dialog("Test Group", entity_id=700)
        # Missed 2 days ago -- far outside the 30-minute rolling window.
        msg = _make_message(801, text="wedge casualty", minutes_ago=2 * 24 * 60)
        cursor_dt = datetime.now(UTC) - timedelta(days=3)

        client = AsyncMock()
        client.get_dialogs = AsyncMock(return_value=[dialog])
        client.get_messages = AsyncMock(return_value=[msg])
        enqueue_fn = AsyncMock()

        p1, p2, p3, p4 = self._base_patches()
        with (
            p1,
            p2,
            p3,
            p4,
            patch(
                "bridge.dedup.get_last_processed",
                new_callable=AsyncMock,
                return_value=(800, cursor_dt),
            ) as cursor_read,
        ):
            result = await self._run(client, enqueue_fn)

        assert result == 1
        enqueue_fn.assert_called_once()
        assert enqueue_fn.call_args[1]["telegram_message_id"] == 801
        # The cursor is consulted for THIS chat's id (dialog.id, -100 form).
        cursor_read.assert_awaited_once_with(dialog.id)

    @pytest.mark.asyncio
    async def test_no_cursor_first_run_falls_back_to_rolling_window(self):
        """First run with no cursor: fallback is the global rolling window --
        recent messages recovered, old ones not (sane, bounded, not epoch-0)."""
        dialog = _make_dialog("Test Group", entity_id=701)
        old_msg = _make_message(900, text="too old", minutes_ago=RECONCILE_LOOKBACK_MINUTES + 30)
        recent_msg = _make_message(901, text="recent", minutes_ago=1)

        client = AsyncMock()
        client.get_dialogs = AsyncMock(return_value=[dialog])
        # Newest-first, as Telethon returns them.
        client.get_messages = AsyncMock(return_value=[recent_msg, old_msg])
        enqueue_fn = AsyncMock()

        p1, p2, p3, p4 = self._base_patches()
        with (
            p1,
            p2,
            p3,
            p4,
            patch("bridge.dedup.get_last_processed", new_callable=AsyncMock, return_value=None),
        ):
            result = await self._run(client, enqueue_fn)

        assert result == 1
        assert enqueue_fn.call_args[1]["telegram_message_id"] == 901

    @pytest.mark.asyncio
    async def test_cursor_survives_restart_via_durable_store(self):
        """The cursor written by one scan extends a later scan's reach.

        The reconciler holds no in-process cursor state: every scan re-reads
        the durable store (Redis via bridge.dedup). Simulated here with a
        shared store dict spanning two independent reconcile_once calls --
        the 'restart' is that nothing but the store carries over.
        """
        store: dict = {}

        async def fake_record(chat_id, msg_id, msg_dt):
            store[chat_id] = (msg_id, msg_dt)

        async def fake_get(chat_id):
            return store.get(chat_id)

        dialog = _make_dialog("Test Group", entity_id=702)
        # Scan 1: cursor pre-seeded 4 days back (long wedge); recovers a
        # 3-day-old message and advances the cursor to it.
        store[dialog.id] = (1000, datetime.now(UTC) - timedelta(days=4))
        msg_a = _make_message(1001, text="first recovery", minutes_ago=3 * 24 * 60)

        client = AsyncMock()
        client.get_dialogs = AsyncMock(return_value=[dialog])
        client.get_messages = AsyncMock(return_value=[msg_a])
        enqueue_fn = AsyncMock()

        p1, p2, p3, _p4 = self._base_patches()
        with (
            p1,
            p2,
            p3,
            patch("bridge.reconciler.record_last_processed", side_effect=fake_record),
            patch("bridge.dedup.get_last_processed", side_effect=fake_get),
        ):
            assert await self._run(client, enqueue_fn) == 1
            assert store[dialog.id][0] == 1001  # cursor advanced to msg_a

            # 'Restart': a fresh scan, fresh mocks, only the store persists.
            msg_b = _make_message(1002, text="second recovery", minutes_ago=2 * 24 * 60)
            client2 = AsyncMock()
            client2.get_dialogs = AsyncMock(return_value=[dialog])
            client2.get_messages = AsyncMock(return_value=[msg_b])
            enqueue_fn2 = AsyncMock()

            assert await self._run(client2, enqueue_fn2) == 1
            assert enqueue_fn2.call_args[1]["telegram_message_id"] == 1002

    @pytest.mark.asyncio
    async def test_cursor_extension_beyond_ceiling_truncates_loudly(self, caplog):
        """The >limit interaction (#2478 item 4): a cursor-extended window with
        more messages than the per-chat ceiling recovers exactly the ceiling
        and WARNs about the truncation.

        The issue predicted this test 'should fail today' against the
        single-fetch reconciler; the #2476 paged fetch has since landed, so it
        now passes -- pinning that fix stays fixed.
        """
        import logging as _logging

        from bridge.reconciler import RECONCILE_MAX_MESSAGES_PER_CHAT

        dialog = _make_dialog("Wedged Firehose", entity_id=703)
        cursor_dt = datetime.now(UTC) - timedelta(days=3)
        count = RECONCILE_MAX_MESSAGES_PER_CHAT + 10
        messages = [
            _make_message(2000 + i, text=f"missed {i}", minutes_ago=2 * 24 * 60)
            for i in range(count)
        ]
        client = TestDeepFetchPaging._paging_client(dialog, messages)
        enqueue_fn = AsyncMock()

        p1, p2, p3, p4 = self._base_patches()
        with (
            caplog.at_level(_logging.WARNING),
            p1,
            p2,
            p3,
            p4,
            patch(
                "bridge.dedup.get_last_processed",
                new_callable=AsyncMock,
                return_value=(1999, cursor_dt),
            ),
        ):
            result = await self._run(client, enqueue_fn, group="wedged firehose")

        assert result == RECONCILE_MAX_MESSAGES_PER_CHAT
        assert "TRUNCATED" in caplog.text

    @pytest.mark.asyncio
    async def test_cursor_read_failure_falls_back_to_rolling_window(self, caplog):
        """A cursor read failure degrades to the global cutoff, never aborts."""
        import logging as _logging

        dialog = _make_dialog("Test Group", entity_id=704)
        recent_msg = _make_message(3001, text="still recovered", minutes_ago=1)

        client = AsyncMock()
        client.get_dialogs = AsyncMock(return_value=[dialog])
        client.get_messages = AsyncMock(return_value=[recent_msg])
        enqueue_fn = AsyncMock()

        p1, p2, p3, p4 = self._base_patches()
        with (
            caplog.at_level(_logging.WARNING),
            p1,
            p2,
            p3,
            p4,
            patch(
                "bridge.dedup.get_last_processed",
                new_callable=AsyncMock,
                side_effect=RuntimeError("redis down"),
            ),
        ):
            result = await self._run(client, enqueue_fn)

        assert result == 1
        assert "falling back to global cutoff" in caplog.text

    @pytest.mark.asyncio
    async def test_naive_cursor_datetime_degrades_to_rolling_window(self, caplog):
        """Timezone correctness (#2478 item 5, naive-datetime history).

        get_last_processed contractually returns tz-aware UTC (it builds the
        datetime with tz=UTC from a unix timestamp). If a regression ever hands
        back a NAIVE datetime, the aware-vs-naive min() comparison raises
        TypeError -- observed behavior is that the per-chat try/except swallows
        it and falls back to the global cutoff rather than crashing the scan.
        """
        import logging as _logging

        dialog = _make_dialog("Test Group", entity_id=705)
        recent_msg = _make_message(4001, text="survives tz bug", minutes_ago=1)
        naive_cursor = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=2)

        client = AsyncMock()
        client.get_dialogs = AsyncMock(return_value=[dialog])
        client.get_messages = AsyncMock(return_value=[recent_msg])
        enqueue_fn = AsyncMock()

        p1, p2, p3, p4 = self._base_patches()
        with (
            caplog.at_level(_logging.WARNING),
            p1,
            p2,
            p3,
            p4,
            patch(
                "bridge.dedup.get_last_processed",
                new_callable=AsyncMock,
                return_value=(4000, naive_cursor),
            ),
        ):
            result = await self._run(client, enqueue_fn)

        assert result == 1
        assert "falling back to global cutoff" in caplog.text

    @pytest.mark.asyncio
    async def test_future_cursor_never_shrinks_the_window(self):
        """Clock-skew guard: a cursor in the FUTURE must not shrink the window
        below the rolling floor (min(), never max()), and the cutoff math can
        never go negative/absurd -- pinned via bridge.utc.to_unix_ts."""
        from utils.utc import to_unix_ts

        dialog = _make_dialog("Test Group", entity_id=706)
        recent_msg = _make_message(5001, text="inside rolling window", minutes_ago=5)
        future_cursor = datetime.now(UTC) + timedelta(hours=2)

        client = AsyncMock()
        client.get_dialogs = AsyncMock(return_value=[dialog])
        client.get_messages = AsyncMock(return_value=[recent_msg])
        enqueue_fn = AsyncMock()

        p1, p2, p3, p4 = self._base_patches()
        with (
            p1,
            p2,
            p3,
            p4,
            patch(
                "bridge.dedup.get_last_processed",
                new_callable=AsyncMock,
                return_value=(5000, future_cursor),
            ),
        ):
            result = await self._run(client, enqueue_fn)

        # min(cutoff, future-60s) == cutoff: the rolling window still applies
        # and the in-window message is recovered.
        assert result == 1

        # Sanity on the window arithmetic itself: the effective cutoff is a
        # tz-aware UTC instant in the past -- never negative, never absurd.
        cutoff = datetime.now(UTC) - timedelta(minutes=RECONCILE_LOOKBACK_MINUTES)
        cutoff_ts = to_unix_ts(cutoff)
        assert cutoff_ts is not None and cutoff_ts > 0
        assert to_unix_ts(datetime.now(UTC)) - cutoff_ts == pytest.approx(
            RECONCILE_LOOKBACK_MINUTES * 60, abs=5
        )
