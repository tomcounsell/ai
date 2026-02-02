"""Tests for the steering queue module.

Tests use Redis db=1 via the autouse redis_test_db fixture in conftest.py.
"""

import time
from unittest.mock import AsyncMock, patch

import pytest

from agent.steering import (
    ABORT_KEYWORDS,
    clear_steering_queue,
    has_steering_messages,
    pop_all_steering_messages,
    pop_steering_message,
    push_steering_message,
)


class TestSteeringQueue:
    """Tests for push/pop/clear operations on the steering Redis queue."""

    def test_push_and_pop_single_message(self):
        session_id = "test_session_001"
        push_steering_message(session_id, "focus on OAuth", "Tom")

        msg = pop_steering_message(session_id)
        assert msg is not None
        assert msg["text"] == "focus on OAuth"
        assert msg["sender"] == "Tom"
        assert msg["is_abort"] is False
        assert "timestamp" in msg

        # Queue should be empty now
        assert pop_steering_message(session_id) is None

    def test_push_and_pop_fifo_order(self):
        session_id = "test_session_fifo"
        push_steering_message(session_id, "first", "Tom")
        push_steering_message(session_id, "second", "Tom")
        push_steering_message(session_id, "third", "Tom")

        msg1 = pop_steering_message(session_id)
        msg2 = pop_steering_message(session_id)
        msg3 = pop_steering_message(session_id)

        assert msg1["text"] == "first"
        assert msg2["text"] == "second"
        assert msg3["text"] == "third"
        assert pop_steering_message(session_id) is None

    def test_pop_all_drains_queue(self):
        session_id = "test_session_popall"
        push_steering_message(session_id, "msg1", "Tom")
        push_steering_message(session_id, "msg2", "Tom")
        push_steering_message(session_id, "msg3", "Tom")

        messages = pop_all_steering_messages(session_id)
        assert len(messages) == 3
        assert messages[0]["text"] == "msg1"
        assert messages[1]["text"] == "msg2"
        assert messages[2]["text"] == "msg3"

        # Queue should be empty
        assert pop_all_steering_messages(session_id) == []

    def test_pop_all_empty_queue(self):
        assert pop_all_steering_messages("nonexistent_session") == []

    def test_clear_steering_queue(self):
        session_id = "test_session_clear"
        push_steering_message(session_id, "msg1", "Tom")
        push_steering_message(session_id, "msg2", "Tom")

        count = clear_steering_queue(session_id)
        assert count == 2
        assert pop_steering_message(session_id) is None

    def test_clear_empty_queue(self):
        count = clear_steering_queue("nonexistent_session")
        assert count == 0

    def test_has_steering_messages(self):
        session_id = "test_session_has"
        assert has_steering_messages(session_id) is False

        push_steering_message(session_id, "hello", "Tom")
        assert has_steering_messages(session_id) is True

        pop_steering_message(session_id)
        assert has_steering_messages(session_id) is False

    def test_explicit_abort_flag(self):
        session_id = "test_session_abort_explicit"
        push_steering_message(session_id, "stop everything", "Tom", is_abort=True)

        msg = pop_steering_message(session_id)
        assert msg["is_abort"] is True
        assert msg["text"] == "stop everything"

    @pytest.mark.parametrize("keyword", list(ABORT_KEYWORDS))
    def test_auto_detect_abort_keywords(self, keyword):
        session_id = f"test_session_abort_{keyword}"
        push_steering_message(session_id, keyword, "Tom")

        msg = pop_steering_message(session_id)
        assert msg["is_abort"] is True

    def test_non_abort_message(self):
        session_id = "test_session_noabort"
        push_steering_message(session_id, "focus on the login page", "Tom")

        msg = pop_steering_message(session_id)
        assert msg["is_abort"] is False

    def test_abort_keyword_case_insensitive(self):
        session_id = "test_session_abort_case"
        push_steering_message(session_id, "STOP", "Tom")

        msg = pop_steering_message(session_id)
        assert msg["is_abort"] is True

    def test_abort_keyword_with_whitespace(self):
        session_id = "test_session_abort_ws"
        push_steering_message(session_id, "  cancel  ", "Tom")

        msg = pop_steering_message(session_id)
        assert msg["is_abort"] is True

    def test_isolation_between_sessions(self):
        push_steering_message("session_a", "msg for a", "Tom")
        push_steering_message("session_b", "msg for b", "Tom")

        msg_a = pop_steering_message("session_a")
        msg_b = pop_steering_message("session_b")

        assert msg_a["text"] == "msg for a"
        assert msg_b["text"] == "msg for b"

    def test_timestamp_is_recent(self):
        session_id = "test_session_ts"
        before = time.time()
        push_steering_message(session_id, "test", "Tom")
        after = time.time()

        msg = pop_steering_message(session_id)
        assert before <= msg["timestamp"] <= after


class TestClientRegistry:
    """Tests for the SDK client registry in sdk_client.py."""

    def test_get_active_client_empty(self):
        from agent.sdk_client import get_active_client

        assert get_active_client("nonexistent") is None

    def test_get_all_active_sessions_empty(self):
        from agent.sdk_client import get_all_active_sessions

        # May have entries from other tests, but should be a dict
        result = get_all_active_sessions()
        assert isinstance(result, dict)

    def test_registry_is_dict(self):
        from agent.sdk_client import _active_clients

        assert isinstance(_active_clients, dict)


class TestWatchdogSteering:
    """Tests for steering integration in the watchdog hook."""

    @pytest.mark.asyncio
    async def test_watchdog_returns_continue_when_no_steering(self):
        """Watchdog should return continue when steering queue is empty."""
        from agent.health_check import _handle_steering

        result = await _handle_steering("empty_session_xyz")
        assert result is None

    @pytest.mark.asyncio
    async def test_watchdog_handles_abort(self):
        """Watchdog should return block when abort signal is in queue."""
        from agent.health_check import _handle_steering

        session_id = "test_watchdog_abort"
        push_steering_message(session_id, "stop", "Tom", is_abort=True)

        result = await _handle_steering(session_id)
        assert result is not None
        assert result["continue_"] is False
        assert "Aborted" in result.get("stopReason", "")

    @pytest.mark.asyncio
    async def test_watchdog_injects_message(self):
        """Watchdog should call interrupt+query when steering message exists."""
        from agent.health_check import _handle_steering

        session_id = "test_watchdog_inject"
        push_steering_message(session_id, "focus on OAuth", "Tom")

        mock_client = AsyncMock()
        with patch("agent.sdk_client.get_active_client", return_value=mock_client):
            result = await _handle_steering(session_id)

        assert result is not None
        assert result["continue_"] is True
        # Verify interrupt+query were actually called
        mock_client.interrupt.assert_awaited_once()
        mock_client.query.assert_awaited_once()
        # Verify the query contained the steering text
        query_arg = mock_client.query.call_args[0][0]
        assert "focus on OAuth" in query_arg
        assert "STEERING MESSAGE" in query_arg

    @pytest.mark.asyncio
    async def test_watchdog_handles_missing_client(self):
        """If no active client, messages should be re-pushed."""
        from agent.health_check import _handle_steering

        session_id = "test_watchdog_noclient"
        push_steering_message(session_id, "focus on OAuth", "Tom")

        with patch("agent.sdk_client.get_active_client", return_value=None):
            result = await _handle_steering(session_id)

        assert result is not None
        assert result["continue_"] is True

        # Message should have been re-pushed
        msg = pop_steering_message(session_id)
        assert msg is not None
        assert msg["text"] == "focus on OAuth"

    @pytest.mark.asyncio
    async def test_watchdog_repushes_on_injection_failure(self):
        """If interrupt succeeds but query throws, messages should be re-pushed."""
        from agent.health_check import _handle_steering

        session_id = "test_watchdog_inject_fail"
        push_steering_message(session_id, "update the tests", "Tom")

        mock_client = AsyncMock()
        mock_client.query.side_effect = RuntimeError("connection lost")
        with patch("agent.sdk_client.get_active_client", return_value=mock_client):
            result = await _handle_steering(session_id)

        assert result is not None
        assert result["continue_"] is True

        # Message should have been re-pushed after failure
        msg = pop_steering_message(session_id)
        assert msg is not None
        assert msg["text"] == "update the tests"
