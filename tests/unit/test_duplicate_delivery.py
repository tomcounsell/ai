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
        """When agent_session.status == 'completed', output is delivered without nudge."""
        # Verify the guard exists in the code
        from pathlib import Path

        queue_code = Path("agent/agent_session_queue.py").read_text()

        # The guard should check session_status == "completed" (in determine_delivery_action)
        assert 'session_status == "completed"' in queue_code
        # It should deliver to chat without nudge
        assert "delivering without nudge" in queue_code

    def test_guard_is_before_nudge_routing(self):
        """The completed-session guard must come before the nudge routing logic."""
        from pathlib import Path

        queue_code = Path("agent/agent_session_queue.py").read_text()

        # Find positions
        guard_pos = queue_code.find("Session already completed")
        nudge_pos = queue_code.find("await _enqueue_nudge(")

        # Guard should be BEFORE the nudge call site
        assert guard_pos > 0, "completed-session guard not found in agent_session_queue.py"
        assert nudge_pos > 0, "await _enqueue_nudge() call not found in agent_session_queue.py"
        assert guard_pos < nudge_pos, "completed-session guard should be before nudge call"


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
