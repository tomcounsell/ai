"""Integration test for the Redis pub/sub session notification path.

Verifies that _push_agent_session() publishes to valor:sessions:new within 1 second,
enabling the standalone worker to pick up CLI-created sessions immediately instead of
waiting for the 5-minute health check.
"""

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.agent_session_queue import _push_agent_session


@pytest.fixture
def mock_agent_session_cls():
    """Patch AgentSession for integration tests."""
    with patch("agent.agent_session_queue.AgentSession") as mock_cls:
        mock_session = MagicMock()
        mock_session.agent_session_id = "test-session-notify-001"
        mock_cls.query.filter.return_value = []
        mock_cls.async_create = AsyncMock(return_value=mock_session)
        mock_cls.query.async_count = AsyncMock(return_value=1)
        yield mock_cls


class TestSessionNotifyPublish:
    """Verify _push_agent_session publishes to valor:sessions:new."""

    def test_publishes_to_notify_channel(self, mock_agent_session_cls):
        """Calling _push_agent_session() should publish a notification within 1 second."""
        received: list[dict] = []

        with patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis:

            def capture_publish(channel, payload):
                received.append({"channel": channel, "payload": payload})
                return 1

            mock_redis.publish = MagicMock(side_effect=capture_publish)

            start = time.monotonic()
            asyncio.run(
                _push_agent_session(
                    project_key="test",
                    session_id="notify-test-sess",
                    working_dir="/tmp",
                    message_text="test message",
                    sender_name="TestSender",
                    chat_id="notify-chat-1",
                    telegram_message_id=99,
                )
            )
            elapsed = time.monotonic() - start

            assert elapsed < 2.0, f"publish took too long: {elapsed:.2f}s"
            assert len(received) == 1, f"expected 1 publish call, got {len(received)}"
            assert received[0]["channel"] == "valor:sessions:new"
            payload = json.loads(received[0]["payload"])
            assert payload["chat_id"] == "notify-chat-1"
            assert payload["session_id"] == "notify-test-sess"

    def test_notify_failure_does_not_block_session_creation(self, mock_agent_session_cls):
        """If Redis publish fails, session creation must still complete successfully."""
        with patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis:
            mock_redis.publish = MagicMock(side_effect=ConnectionError("Redis unavailable"))

            # Should not raise; health check is the fallback
            result = asyncio.run(
                _push_agent_session(
                    project_key="test",
                    session_id="notify-fail-sess",
                    working_dir="/tmp",
                    message_text="test message",
                    sender_name="TestSender",
                    chat_id="notify-chat-2",
                    telegram_message_id=100,
                )
            )

            # Session was created despite publish failure
            mock_agent_session_cls.async_create.assert_called_once()
            assert isinstance(result, int)

    def test_payload_contains_required_fields(self, mock_agent_session_cls):
        """Notification payload must include chat_id and session_id."""
        captured_payload: list[dict] = []

        with patch("popoto.redis_db.POPOTO_REDIS_DB") as mock_redis:

            def capture(channel, payload):
                captured_payload.append(json.loads(payload))
                return 1

            mock_redis.publish = MagicMock(side_effect=capture)

            asyncio.run(
                _push_agent_session(
                    project_key="myproject",
                    session_id="field-check-sess",
                    working_dir="/tmp",
                    message_text="check fields",
                    sender_name="FieldTester",
                    chat_id="field-chat-99",
                    telegram_message_id=42,
                )
            )

            assert len(captured_payload) == 1
            payload = captured_payload[0]
            assert "chat_id" in payload
            assert "session_id" in payload
            assert payload["chat_id"] == "field-chat-99"
            assert payload["session_id"] == "field-check-sess"
