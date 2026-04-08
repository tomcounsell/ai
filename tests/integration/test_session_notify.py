"""Integration test for the Redis pub/sub session notification path.

Verifies that _push_agent_session() publishes to valor:sessions:new within 1 second,
enabling the standalone worker to pick up CLI-created sessions immediately instead of
waiting for the 5-minute health check.

Also verifies that _listen_in_thread (inside _session_notify_listener) creates a
dedicated Redis connection with socket_timeout=None, preventing spurious
"Timeout reading from socket" exceptions during idle periods.
"""

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.agent_session_queue import _push_agent_session, _session_notify_listener


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


class TestSessionNotifyListener:
    """Verify _session_notify_listener uses a socket_timeout=None Redis connection."""

    def test_notify_listener_uses_no_socket_timeout(self):
        """_listen_in_thread must create redis.Redis with socket_timeout=None.

        This prevents the global POPOTO_REDIS_DB socket_timeout=5 from being
        inherited by the pub/sub connection, which would cause spurious
        "Timeout reading from socket" exceptions and a 10-second reconnect
        cycle that drops notifications published during the dead window.
        """
        captured_kwargs: list[dict] = []

        # pubsub that raises StopIteration immediately so the thread exits cleanly
        mock_pubsub = MagicMock()
        mock_pubsub.listen.return_value = iter([])  # empty iterator → thread exits

        mock_conn = MagicMock()
        mock_conn.pubsub.return_value = mock_pubsub

        def fake_redis_constructor(**kwargs):
            captured_kwargs.append(kwargs)
            return mock_conn

        mock_popoto_redis = MagicMock()
        mock_popoto_redis.connection_pool.connection_kwargs = {
            "host": "localhost",
            "port": 6379,
            "db": 0,
        }

        async def run_one_cycle():
            # Patch redis.Redis at the import site inside agent_session_queue
            with (
                patch("agent.agent_session_queue.json", wraps=json),
                patch("popoto.redis_db.POPOTO_REDIS_DB", mock_popoto_redis),
            ):
                # We need to patch redis.Redis inside the function's local scope.
                # The function does `import redis as _redis` and then calls
                # `_redis.Redis(...)`, so we patch the module-level redis.Redis.
                import redis as _redis_module

                with patch.object(_redis_module, "Redis", side_effect=fake_redis_constructor):
                    # Run the listener coroutine but cancel it after the first
                    # reconnect sleep so it only executes one cycle.
                    task = asyncio.create_task(_session_notify_listener())
                    # Give the thread time to run and the coroutine to process
                    await asyncio.sleep(0.3)
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

        asyncio.run(run_one_cycle())

        assert len(captured_kwargs) >= 1, "redis.Redis was never called — fix not applied"
        first_call = captured_kwargs[0]
        assert first_call.get("socket_timeout") is None, (
            f"Expected socket_timeout=None, got {first_call.get('socket_timeout')!r}. "
            "The listener must use a dedicated connection with socket_timeout=None "
            "to prevent spurious idle timeouts."
        )
        assert first_call.get("socket_connect_timeout") is None, (
            f"Expected socket_connect_timeout=None, got "
            f"{first_call.get('socket_connect_timeout')!r}."
        )
