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
            patch("bridge.dedup.record_message_processed", new_callable=AsyncMock),
        ):
            mock_dedup.return_value = False

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
            patch("bridge.dedup.record_message_processed", new_callable=AsyncMock),
        ):
            mock_dedup.return_value = False

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


class TestCatchupPersonaResolution:
    """Catchup must resolve persona for parity with the live handler.

    Finding 2 bug: catchup enqueued every chat with the eng default, so a
    teammate-configured chat wrongly ran as an eng PM<->Dev loop.
    """

    @staticmethod
    def _teammate_project():
        return {
            "_key": "cyndra",
            "working_directory": "/tmp/cyndra",
            "telegram": {
                "groups": {"Cyndra Dev Team": {"persona": "teammate"}},
            },
        }

    @staticmethod
    def _eng_project():
        return {"_key": "popoto", "working_directory": "/tmp/popoto"}

    @pytest.mark.asyncio
    async def test_teammate_chat_enqueues_teammate_session(self):
        """A teammate-configured chat enqueues session_type=teammate + project_config."""
        from config.enums import SessionType

        client = AsyncMock()
        dialog = _make_dialog(chat_id=300, title="Cyndra Dev Team")
        client.get_dialogs.return_value = [dialog]
        message = _make_message(msg_id=7, text="@valor please look")
        client.get_messages.return_value = [message]

        enqueue_agent_session_fn = AsyncMock()
        project = self._teammate_project()
        find_project_fn = MagicMock(return_value=project)

        with (
            patch("bridge.dedup.is_duplicate_message", new_callable=AsyncMock) as mock_dedup,
            patch("bridge.dedup.record_message_processed", new_callable=AsyncMock),
            patch("bridge.dedup.record_last_processed", new_callable=AsyncMock),
        ):
            mock_dedup.return_value = False

            queued = await scan_for_missed_messages(
                client=client,
                monitored_groups=["cyndra dev team"],
                projects_config={},
                should_respond_fn=AsyncMock(return_value=(True, False)),
                enqueue_agent_session_fn=enqueue_agent_session_fn,
                find_project_fn=find_project_fn,
            )

        assert queued == 1
        call_kwargs = enqueue_agent_session_fn.call_args[1]
        assert call_kwargs["session_type"] == SessionType.TEAMMATE
        assert call_kwargs["project_config"] is project

    @pytest.mark.asyncio
    async def test_eng_chat_enqueues_eng_session(self):
        """A chat with no teammate persona still enqueues an eng session."""
        from config.enums import SessionType

        client = AsyncMock()
        dialog = _make_dialog(chat_id=400, title="Dev: Popoto")
        client.get_dialogs.return_value = [dialog]
        message = _make_message(msg_id=8, text="fix the build")
        client.get_messages.return_value = [message]

        enqueue_agent_session_fn = AsyncMock()
        find_project_fn = MagicMock(return_value=self._eng_project())

        with (
            patch("bridge.dedup.is_duplicate_message", new_callable=AsyncMock) as mock_dedup,
            patch("bridge.dedup.record_message_processed", new_callable=AsyncMock),
            patch("bridge.dedup.record_last_processed", new_callable=AsyncMock),
        ):
            mock_dedup.return_value = False

            queued = await scan_for_missed_messages(
                client=client,
                monitored_groups=["dev: popoto"],
                projects_config={},
                should_respond_fn=AsyncMock(return_value=(True, False)),
                enqueue_agent_session_fn=enqueue_agent_session_fn,
                find_project_fn=find_project_fn,
            )

        assert queued == 1
        call_kwargs = enqueue_agent_session_fn.call_args[1]
        assert call_kwargs["session_type"] == SessionType.ENG

    @pytest.mark.asyncio
    async def test_persona_failure_warns_and_continues_with_eng(self, caplog):
        """A persona-resolution exception falls back to eng and the scan continues."""
        import logging

        from config.enums import SessionType

        client = AsyncMock()
        dialog = _make_dialog(chat_id=500, title="Dev: Popoto")
        client.get_dialogs.return_value = [dialog]
        message = _make_message(msg_id=9, text="status?")
        client.get_messages.return_value = [message]

        enqueue_agent_session_fn = AsyncMock()
        find_project_fn = MagicMock(return_value=self._eng_project())

        with (
            patch("bridge.dedup.is_duplicate_message", new_callable=AsyncMock) as mock_dedup,
            patch("bridge.dedup.record_message_processed", new_callable=AsyncMock),
            patch("bridge.dedup.record_last_processed", new_callable=AsyncMock),
            patch("bridge.catchup.resolve_persona", side_effect=RuntimeError("boom")),
            caplog.at_level(logging.WARNING),
        ):
            mock_dedup.return_value = False

            queued = await scan_for_missed_messages(
                client=client,
                monitored_groups=["dev: popoto"],
                projects_config={},
                should_respond_fn=AsyncMock(return_value=(True, False)),
                enqueue_agent_session_fn=enqueue_agent_session_fn,
                find_project_fn=find_project_fn,
            )

        assert queued == 1
        call_kwargs = enqueue_agent_session_fn.call_args[1]
        assert call_kwargs["session_type"] == SessionType.ENG
        assert any("[catchup] persona resolution failed" in r.getMessage() for r in caplog.records)


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
            patch("bridge.dedup.record_message_processed", new_callable=AsyncMock),
            # claim_message mocked True: this test reuses chat_id=100/msg_id=42
            # from test_enqueue_called_without_workflow_id above; the real
            # per-message claim (issue #1817) is a short-TTL Redis SETNX
            # unrelated to what's under test here (lookback windowing), so an
            # unmocked real claim would collide within the claim TTL.
            patch("bridge.dedup.claim_message", new_callable=AsyncMock, return_value=True),
        ):
            mock_dedup.return_value = False

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
            patch("bridge.dedup.record_message_processed", new_callable=AsyncMock),
        ):
            mock_dedup.return_value = False

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
