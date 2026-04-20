"""Regression tests for duplicate message delivery (issue #193).

Tests the three fixes:
1. Catchup scanner checks Redis dedup before enqueuing
2. Catchup scanner records processed messages in Redis dedup
3. Auto-continue skips when session is already completed
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestCatchupRedisDedup:
    """Fix 1: Catchup scanner checks Redis dedup set."""

    @pytest.mark.asyncio
    async def test_catchup_skips_already_processed_message(self):
        """Messages in Redis dedup set are skipped by catchup scanner."""
        from bridge.catchup import scan_for_missed_messages

        # Mock Telegram client
        mock_client = AsyncMock()

        # Create a mock dialog with a monitored group
        mock_entity = MagicMock()
        mock_entity.title = "Dev: Valor"
        mock_entity.id = -5051653062

        mock_dialog = MagicMock()
        mock_dialog.entity = mock_entity

        mock_client.get_dialogs = AsyncMock(return_value=[mock_dialog])

        # Create a mock message that looks unhandled in Telegram
        from datetime import UTC, datetime

        mock_message = MagicMock()
        mock_message.id = 5920
        mock_message.date = datetime.now(UTC)
        mock_message.out = False
        mock_message.text = "Merge"
        mock_sender = MagicMock()
        mock_sender.first_name = "Tom"
        mock_sender.username = "tomcounsell"
        mock_sender.id = 12345
        mock_message.get_sender = AsyncMock(return_value=mock_sender)

        mock_client.get_messages = AsyncMock(return_value=[mock_message])

        # Mock project config
        mock_project = {"_key": "valor", "working_directory": "/src/ai"}
        find_project = MagicMock(return_value=mock_project)

        # Mock enqueue function
        enqueue_fn = AsyncMock()

        # KEY: is_duplicate_message returns True (already in Redis)
        with patch("bridge.dedup.is_duplicate_message", new_callable=AsyncMock) as mock_dedup:
            mock_dedup.return_value = True

            queued = await scan_for_missed_messages(
                client=mock_client,
                monitored_groups=["dev: valor"],
                projects_config={},
                should_respond_fn=AsyncMock(return_value=(True, False)),
                enqueue_agent_session_fn=enqueue_fn,
                find_project_fn=find_project,
            )

        # Should NOT have enqueued the message
        enqueue_fn.assert_not_called()
        assert queued == 0

    @pytest.mark.asyncio
    async def test_catchup_processes_non_duplicate_message(self):
        """Messages NOT in Redis dedup set are processed normally."""
        from bridge.catchup import scan_for_missed_messages

        mock_client = AsyncMock()

        mock_entity = MagicMock()
        mock_entity.title = "Dev: Valor"
        mock_entity.id = -5051653062

        mock_dialog = MagicMock()
        mock_dialog.entity = mock_entity
        mock_dialog.id = -5051653062

        mock_client.get_dialogs = AsyncMock(return_value=[mock_dialog])

        from datetime import UTC, datetime

        mock_message = MagicMock()
        mock_message.id = 5920
        mock_message.date = datetime.now(UTC)
        mock_message.out = False
        mock_message.text = "Merge"
        mock_sender = MagicMock()
        mock_sender.first_name = "Tom"
        mock_sender.username = "tomcounsell"
        mock_sender.id = 12345
        mock_message.get_sender = AsyncMock(return_value=mock_sender)

        mock_client.get_messages = AsyncMock(return_value=[mock_message])

        mock_project = {"_key": "valor", "working_directory": "/src/ai"}
        find_project = MagicMock(return_value=mock_project)
        enqueue_fn = AsyncMock()

        with (
            patch("bridge.dedup.is_duplicate_message", new_callable=AsyncMock) as mock_dedup,
            patch("bridge.dedup.record_message_processed", new_callable=AsyncMock) as mock_record,
            patch("bridge.catchup._check_if_handled", new_callable=AsyncMock) as mock_handled,
        ):
            mock_dedup.return_value = False  # Not in Redis
            mock_handled.return_value = False  # No Telegram reply either
            mock_should_respond = AsyncMock(return_value=(True, False))

            queued = await scan_for_missed_messages(
                client=mock_client,
                monitored_groups=["dev: valor"],
                projects_config={},
                should_respond_fn=mock_should_respond,
                enqueue_agent_session_fn=enqueue_fn,
                find_project_fn=find_project,
            )

        # Should have enqueued AND recorded in dedup
        enqueue_fn.assert_called_once()
        mock_record.assert_called_once_with(-5051653062, 5920)
        assert queued == 1


class TestCatchupRecordsDedup:
    """Fix 2: Catchup scanner records processed messages in Redis."""

    @pytest.mark.asyncio
    async def test_catchup_records_message_after_enqueue(self):
        """After enqueuing, message ID is recorded in Redis dedup set."""
        # This is tested as part of test_catchup_processes_non_duplicate_message
        # above. The mock_record.assert_called_once_with() validates Fix 2.
        pass  # Covered by TestCatchupRedisDedup.test_catchup_processes_non_duplicate_message


class TestCompletedSessionGuard:
    """Fix 3: Auto-continue skips when session is already completed."""

    def test_completed_session_skips_auto_continue(self):
        """When session_status is terminal, the router returns deliver_already_completed
        (not a nudge action) so the executor delivers to chat and skips auto-continue."""
        from agent.output_router import determine_delivery_action
        from models.session_lifecycle import TERMINAL_STATUSES

        # Every terminal status must short-circuit to deliver_already_completed,
        # regardless of output content or stop_reason.
        for status in TERMINAL_STATUSES:
            action = determine_delivery_action(
                msg="some agent output",
                stop_reason="end_turn",
                auto_continue_count=0,
                max_nudge_count=50,
                session_status=status,
            )
            assert action == "deliver_already_completed", (
                f"status={status!r} should short-circuit to deliver_already_completed, "
                f"got {action!r}"
            )

        # Also holds for empty output — must not emit a nudge action.
        empty_action = determine_delivery_action(
            msg="",
            stop_reason="end_turn",
            auto_continue_count=0,
            max_nudge_count=50,
            session_status=next(iter(TERMINAL_STATUSES)),
        )
        assert empty_action == "deliver_already_completed"
        assert "nudge" not in empty_action

    def test_guard_is_before_nudge_routing(self):
        """The completed-session guard must win over every nudge branch.

        Behavioral check: for every (stop_reason, msg) permutation that would
        otherwise drive a nudge action, a terminal session_status must still
        return deliver_already_completed — proving the guard runs first.
        """
        from agent.output_router import determine_delivery_action
        from models.session_lifecycle import TERMINAL_STATUSES

        terminal = next(iter(TERMINAL_STATUSES))

        # These inputs would produce a nudge for a non-terminal session.
        nudge_inducing_cases = [
            {"msg": "", "stop_reason": "end_turn"},  # nudge_empty
            {"msg": "work", "stop_reason": "rate_limited"},  # nudge_rate_limited
            {
                "msg": "work",
                "stop_reason": "end_turn",
                "session_type": "pm",
                "classification_type": "sdlc",
            },  # nudge_continue
        ]

        for case in nudge_inducing_cases:
            action = determine_delivery_action(
                auto_continue_count=0,
                max_nudge_count=50,
                session_status=terminal,
                **case,
            )
            assert action == "deliver_already_completed", (
                f"terminal session with nudge-inducing inputs {case!r} must still return "
                f"deliver_already_completed, got {action!r} — guard is not firing first"
            )


class TestCatchupCodeStructure:
    """Verify the catchup.py dedup integration is correct."""

    def test_dedup_check_before_sender_lookup(self):
        """Redis dedup check should be before the expensive sender lookup."""
        from pathlib import Path

        catchup_code = Path("bridge/catchup.py").read_text()

        dedup_pos = catchup_code.find("is_duplicate_message")
        sender_pos = catchup_code.find("message.get_sender()")

        assert dedup_pos < sender_pos, (
            "Redis dedup check should be before sender lookup (save API calls)"
        )

    def test_dedup_record_after_enqueue(self):
        """Redis dedup record should be after successful enqueue."""
        from pathlib import Path

        catchup_code = Path("bridge/catchup.py").read_text()

        enqueue_pos = catchup_code.find("await enqueue_agent_session_fn(")
        record_pos = catchup_code.find("record_message_processed")

        assert record_pos > enqueue_pos, (
            "Dedup record should be after enqueue (don't record if enqueue fails)"
        )

    def test_seen_chat_ids_guard_exists(self):
        """Catchup must have a seen_chat_ids dedup to handle Telethon returning
        the same supergroup twice (once as channel, once as linked discussion group)."""
        from pathlib import Path

        catchup_code = Path("bridge/catchup.py").read_text()
        assert "seen_chat_ids" in catchup_code, (
            "seen_chat_ids guard missing from catchup.py — Telethon can return the same "
            "supergroup twice, causing duplicate messages without this guard"
        )
        assert "dialog.id in seen_chat_ids" in catchup_code, (
            "seen_chat_ids guard must check dialog.id before scanning each group"
        )
        assert "seen_chat_ids.add(dialog.id)" in catchup_code, (
            "seen_chat_ids guard must record dialog.id after first scan to block duplicates"
        )


class TestTelethonDuplicateDialogDedup:
    """Regression test: Telethon returns same supergroup twice in get_dialogs()."""

    @pytest.mark.asyncio
    async def test_catchup_skips_duplicate_dialog_id(self):
        """When get_dialogs() returns the same chat twice (channel + linked discussion),
        catchup must only scan it once — not queue messages twice."""
        from bridge.catchup import scan_for_missed_messages

        mock_client = AsyncMock()

        # Simulate Telethon returning PM: Valor twice with the same underlying id
        mock_entity = MagicMock()
        mock_entity.title = "PM: Valor"
        mock_entity.id = -1003449100931

        mock_dialog_a = MagicMock()
        mock_dialog_a.entity = mock_entity
        mock_dialog_a.id = -1003449100931

        mock_dialog_b = MagicMock()
        mock_dialog_b.entity = mock_entity
        mock_dialog_b.id = -1003449100931  # same id — duplicate

        mock_client.get_dialogs = AsyncMock(return_value=[mock_dialog_a, mock_dialog_b])

        from datetime import UTC, datetime

        mock_message = MagicMock()
        mock_message.id = 999
        mock_message.date = datetime.now(UTC)
        mock_message.out = False
        mock_message.text = "Hello"
        mock_sender = MagicMock()
        mock_sender.first_name = "Valor"
        mock_sender.username = "valorengels"
        mock_sender.id = 179144806
        mock_message.get_sender = AsyncMock(return_value=mock_sender)

        mock_client.get_messages = AsyncMock(return_value=[mock_message])

        mock_project = {"_key": "valor", "working_directory": "/src/ai"}
        find_project = MagicMock(return_value=mock_project)
        enqueue_fn = AsyncMock()

        with (
            patch("bridge.dedup.is_duplicate_message", new_callable=AsyncMock) as mock_dedup,
            patch("bridge.dedup.record_message_processed", new_callable=AsyncMock),
            patch("bridge.catchup._check_if_handled", new_callable=AsyncMock) as mock_handled,
        ):
            mock_dedup.return_value = False
            mock_handled.return_value = False

            await scan_for_missed_messages(
                client=mock_client,
                monitored_groups=["pm: valor"],
                projects_config={},
                should_respond_fn=AsyncMock(return_value=(True, False)),
                enqueue_agent_session_fn=enqueue_fn,
                find_project_fn=find_project,
            )

        # get_messages should only be called ONCE despite two dialogs with same id
        assert mock_client.get_messages.call_count == 1, (
            f"get_messages called {mock_client.get_messages.call_count} times — "
            "duplicate dialog dedup not working; Telethon returned same group twice"
        )
        # Message should be enqueued exactly once
        assert enqueue_fn.call_count == 1, (
            f"enqueue called {enqueue_fn.call_count} times — same message queued multiple times"
        )
