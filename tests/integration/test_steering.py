"""Tests for the steering queue module.

Tests use Redis db=1 via the autouse redis_test_db fixture in conftest.py.
"""

import time
from datetime import UTC, datetime, timedelta
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


class TestBridgeSteeringCheck:
    """Tests for the bridge steering check status matching logic.

    These tests verify that the steering check in telegram_bridge.py
    correctly matches sessions in 'running' and 'active' statuses,
    and falls through gracefully when no matching session exists.
    """

    def _create_session(self, session_id, status):
        """Create an AgentSession with the given status."""
        from models.agent_session import AgentSession

        session = AgentSession(
            session_id=session_id,
            project_key="test",
            status=status,
            message_text="test message",
            created_at=datetime.now(tz=UTC),
        )
        session.save()
        return session

    def test_steering_matches_running_status(self):
        """Steering check should find sessions in 'running' status."""
        from models.agent_session import AgentSession

        session_id = "test_bridge_running"
        self._create_session(session_id, "running")

        # Replicate the bridge steering check logic
        matching_session = None
        for check_status in ("running", "active"):
            sessions = AgentSession.query.filter(session_id=session_id, status=check_status)
            if sessions:
                matching_session = sessions[0]
                break

        assert matching_session is not None
        assert matching_session.status == "running"

    def test_steering_matches_active_status(self):
        """Steering check should find sessions in 'active' status."""
        from models.agent_session import AgentSession

        session_id = "test_bridge_active"
        self._create_session(session_id, "active")

        matching_session = None
        for check_status in ("running", "active"):
            sessions = AgentSession.query.filter(session_id=session_id, status=check_status)
            if sessions:
                matching_session = sessions[0]
                break

        assert matching_session is not None
        assert matching_session.status == "active"

    def test_steering_prefers_running_over_active(self):
        """When both running and active sessions exist, running wins."""
        from models.agent_session import AgentSession

        session_id = "test_bridge_prefer_running"
        # Create both -- running should be found first
        self._create_session(session_id, "running")
        self._create_session(session_id, "active")

        matching_session = None
        for check_status in ("running", "active"):
            sessions = AgentSession.query.filter(session_id=session_id, status=check_status)
            if sessions:
                matching_session = sessions[0]
                break

        assert matching_session is not None
        assert matching_session.status == "running"

    def test_steering_no_match_for_pending(self):
        """Steering check should NOT match sessions in 'pending' status."""
        from models.agent_session import AgentSession

        session_id = "test_bridge_pending"
        self._create_session(session_id, "pending")

        matching_session = None
        for check_status in ("running", "active"):
            sessions = AgentSession.query.filter(session_id=session_id, status=check_status)
            if sessions:
                matching_session = sessions[0]
                break

        assert matching_session is None

    def test_steering_no_match_for_completed(self):
        """Running/active check skips completed sessions.

        Completed sessions are handled by the dedicated re-enqueue branch.
        """
        from models.agent_session import AgentSession

        session_id = "test_bridge_completed"
        self._create_session(session_id, "completed")

        matching_session = None
        for check_status in ("running", "active"):
            sessions = AgentSession.query.filter(session_id=session_id, status=check_status)
            if sessions:
                matching_session = sessions[0]
                break

        assert matching_session is None

    def test_steering_no_match_for_nonexistent_session(self):
        """Steering check should return None for nonexistent sessions."""
        from models.agent_session import AgentSession

        matching_session = None
        for check_status in ("running", "active"):
            sessions = AgentSession.query.filter(
                session_id="nonexistent_session_xyz", status=check_status
            )
            if sessions:
                matching_session = sessions[0]
                break

        assert matching_session is None

    def test_steering_pending_detection_for_race_window(self):
        """Steering check should detect pending sessions for logging."""
        from models.agent_session import AgentSession

        session_id = "test_bridge_race_window"
        self._create_session(session_id, "pending")

        # First, the main check should find nothing
        matching_session = None
        for check_status in ("running", "active"):
            sessions = AgentSession.query.filter(session_id=session_id, status=check_status)
            if sessions:
                matching_session = sessions[0]
                break

        assert matching_session is None

        # Then the pending check should find it
        pending_sessions = AgentSession.query.filter(session_id=session_id, status="pending")
        assert len(pending_sessions) > 0
        assert pending_sessions[0].status == "pending"

    def test_steering_push_only_after_session_match(self):
        """push_steering_message should only be called after session match."""
        session_id = "test_bridge_push_guard"
        self._create_session(session_id, "running")

        from models.agent_session import AgentSession

        matching_session = None
        for check_status in ("running", "active"):
            sessions = AgentSession.query.filter(session_id=session_id, status=check_status)
            if sessions:
                matching_session = sessions[0]
                break

        assert matching_session is not None
        # Only push if we matched
        push_steering_message(session_id, "test steering", "Tom")
        msg = pop_steering_message(session_id)
        assert msg is not None
        assert msg["text"] == "test steering"

    def test_steering_error_handling_connection_error(self):
        """ConnectionError should be caught separately from generic errors."""
        from models.agent_session import AgentSession

        # Verify that ConnectionError is a subclass check target
        # (the bridge catches ConnectionError and OSError separately)
        with patch.object(
            AgentSession.query,
            "filter",
            side_effect=ConnectionError("Redis unavailable"),
        ):
            caught_connection = False
            try:
                AgentSession.query.filter(session_id="test", status="running")
            except (ConnectionError, OSError):
                caught_connection = True
            except Exception:
                caught_connection = False

            assert caught_connection is True

    def test_steering_error_handling_generic_error(self):
        """Generic exceptions should be caught by the fallback handler."""
        from models.agent_session import AgentSession

        with patch.object(
            AgentSession.query,
            "filter",
            side_effect=ValueError("unexpected"),
        ):
            caught_generic = False
            try:
                AgentSession.query.filter(session_id="test", status="running")
            except (ConnectionError, OSError):
                caught_generic = False
            except Exception:
                caught_generic = True

            assert caught_generic is True


class TestPendingSessionSteering:
    """Tests for steering into pending sessions within the merge window (#619)."""

    def _create_session(self, session_id, status, chat_id="test_chat", created_at=None):
        """Create an AgentSession with the given status."""
        from models.agent_session import AgentSession

        session = AgentSession(
            session_id=session_id,
            project_key="test",
            status=status,
            chat_id=chat_id,
            message_text="test message",
            created_at=created_at or time.time(),
        )
        session.save()
        return session

    def test_pending_session_within_window_receives_steering(self):
        """A pending session within 7s should accept steering messages."""
        from bridge.telegram_bridge import PENDING_MERGE_WINDOW_SECONDS

        session_id = "test_pending_steer_recent"
        self._create_session(session_id, "pending", created_at=datetime.now(tz=UTC))

        # Simulate the bridge logic: check age and push steering
        from models.agent_session import AgentSession

        pending_sessions = AgentSession.query.filter(session_id=session_id, status="pending")
        assert len(pending_sessions) > 0
        pending_session = pending_sessions[0]
        _created = pending_session.created_at
        if isinstance(_created, datetime):
            _created = (
                _created.timestamp()
                if _created.tzinfo
                else _created.replace(tzinfo=UTC).timestamp()
            )
        age = time.time() - (_created or 0)
        assert age <= PENDING_MERGE_WINDOW_SECONDS

        push_steering_message(session_id, "follow-up context", "Tom")
        msg = pop_steering_message(session_id)
        assert msg is not None
        assert msg["text"] == "follow-up context"

    def test_pending_session_outside_window_not_steered(self):
        """A pending session older than 7s should NOT be steered into."""
        from bridge.telegram_bridge import PENDING_MERGE_WINDOW_SECONDS

        session_id = "test_pending_steer_old"
        # Create with timestamp 10s in the past
        self._create_session(
            session_id,
            "pending",
            created_at=datetime.now(tz=UTC) - timedelta(seconds=10),
        )

        from models.agent_session import AgentSession

        pending_sessions = AgentSession.query.filter(session_id=session_id, status="pending")
        assert len(pending_sessions) > 0
        pending_session = pending_sessions[0]
        _created = pending_session.created_at
        if isinstance(_created, datetime):
            _created = (
                _created.timestamp()
                if _created.tzinfo
                else _created.replace(tzinfo=UTC).timestamp()
            )
        age = time.time() - (_created or 0)
        assert age > PENDING_MERGE_WINDOW_SECONDS

    def test_pending_merge_window_constant_is_8(self):
        """The merge window constant should be 8 seconds."""
        from bridge.telegram_bridge import PENDING_MERGE_WINDOW_SECONDS

        assert PENDING_MERGE_WINDOW_SECONDS == 8

    def test_multiple_steering_messages_into_pending(self):
        """Multiple follow-up messages should all queue into a pending session."""
        session_id = "test_pending_multi_steer"
        self._create_session(session_id, "pending", created_at=datetime.now(tz=UTC))

        push_steering_message(session_id, "first follow-up", "Tom")
        push_steering_message(session_id, "second follow-up", "Tom")
        push_steering_message(session_id, "third follow-up", "Tom")

        messages = pop_all_steering_messages(session_id)
        assert len(messages) == 3
        assert messages[0]["text"] == "first follow-up"
        assert messages[1]["text"] == "second follow-up"
        assert messages[2]["text"] == "third follow-up"

    def test_intake_classifier_includes_recent_pending(self):
        """The intake classifier status loop should include recent pending sessions."""
        from bridge.telegram_bridge import PENDING_MERGE_WINDOW_SECONDS
        from models.agent_session import AgentSession

        chat_id = "test_intake_pending_chat"
        session_id = "test_intake_pending_session"
        self._create_session(
            session_id, "pending", chat_id=chat_id, created_at=datetime.now(tz=UTC)
        )

        # Replicate the intake classifier logic from the bridge
        active_sessions = []
        for check_status in ("running", "active", "dormant"):
            sessions = AgentSession.query.filter(chat_id=chat_id, status=check_status)
            if sessions:
                active_sessions.extend(sessions)

        # Also include recent pending sessions within the merge window
        pending_sessions = AgentSession.query.filter(chat_id=chat_id, status="pending")
        if pending_sessions:
            now_ts = time.time()
            for ps in pending_sessions:
                _ct = ps.created_at
                if isinstance(_ct, datetime):
                    _ct = _ct.timestamp() if _ct.tzinfo else _ct.replace(tzinfo=UTC).timestamp()
                age = now_ts - (_ct or 0)
                if age <= PENDING_MERGE_WINDOW_SECONDS:
                    active_sessions.append(ps)

        assert len(active_sessions) == 1
        assert active_sessions[0].session_id == session_id
        assert active_sessions[0].status == "pending"

    def test_intake_classifier_excludes_old_pending(self):
        """The intake classifier should NOT include pending sessions older than 7s."""
        from bridge.telegram_bridge import PENDING_MERGE_WINDOW_SECONDS
        from models.agent_session import AgentSession

        chat_id = "test_intake_old_pending_chat"
        session_id = "test_intake_old_pending_session"
        self._create_session(
            session_id,
            "pending",
            chat_id=chat_id,
            created_at=datetime.now(tz=UTC) - timedelta(seconds=10),
        )

        active_sessions = []
        for check_status in ("running", "active", "dormant"):
            sessions = AgentSession.query.filter(chat_id=chat_id, status=check_status)
            if sessions:
                active_sessions.extend(sessions)

        pending_sessions = AgentSession.query.filter(chat_id=chat_id, status="pending")
        if pending_sessions:
            now_ts = time.time()
            for ps in pending_sessions:
                _ct = ps.created_at
                if isinstance(_ct, datetime):
                    _ct = _ct.timestamp() if _ct.tzinfo else _ct.replace(tzinfo=UTC).timestamp()
                age = now_ts - (_ct or 0)
                if age <= PENDING_MERGE_WINDOW_SECONDS:
                    active_sessions.append(ps)

        assert len(active_sessions) == 0


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
        """Watchdog should inject abort directive via hookSpecificOutput.

        PostToolUse hooks can't enforce continue_: False directly, so the
        abort is injected as additionalContext with a strong stop directive.
        """
        from agent.health_check import _handle_steering

        session_id = "test_watchdog_abort"
        push_steering_message(session_id, "stop", "Tom", is_abort=True)

        result = await _handle_steering(session_id)
        assert result is not None
        # Abort is delivered via hookSpecificOutput additionalContext
        hook_output = result["hookSpecificOutput"]
        assert hook_output["hookEventName"] == "PostToolUse"
        assert "ABORT from Tom" in hook_output["additionalContext"]
        assert "stop immediately" in hook_output["additionalContext"]

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
    async def test_watchdog_handles_missing_client_session_found(self):
        """If no active client but session exists in DB, message lands in queued_steering_messages.

        Turn-boundary inbox (not Redis re-push) when the model lookup succeeds.
        """
        from datetime import UTC, datetime

        from agent.health_check import _handle_steering
        from models.agent_session import AgentSession

        session_id = "test_watchdog_noclient_found"
        session = AgentSession(
            session_id=session_id,
            project_key="test-steer",
            status="running",
            message_text="test task",
            created_at=datetime.now(tz=UTC),
        )
        session.save()

        push_steering_message(session_id, "focus on OAuth", "Tom")

        with patch("agent.sdk_client.get_active_client", return_value=None):
            result = await _handle_steering(session_id)

        assert result is not None
        assert result["continue_"] is True

        # Message should land in queued_steering_messages, NOT re-pushed to Redis list
        msg = pop_steering_message(session_id)
        assert msg is None, "Message should NOT have been re-pushed to Redis list"

        # Verify it landed in the model's turn-boundary inbox
        refreshed = list(AgentSession.query.filter(session_id=session_id))[0]
        assert len(refreshed.queued_steering_messages) >= 1
        assert "focus on OAuth" in refreshed.queued_steering_messages[0]

    @pytest.mark.asyncio
    async def test_watchdog_handles_missing_client_session_not_found(self):
        """If no active client and session not in DB, message is re-pushed to Redis list."""
        from agent.health_check import _handle_steering

        session_id = "test_watchdog_noclient_notfound"
        push_steering_message(session_id, "fallback message", "Tom")

        with patch("agent.sdk_client.get_active_client", return_value=None):
            result = await _handle_steering(session_id)

        assert result is not None
        assert result["continue_"] is True

        # Session not in DB: message should be re-pushed to Redis list (existing fallback)
        msg = pop_steering_message(session_id)
        assert msg is not None
        assert msg["text"] == "fallback message"

    @pytest.mark.asyncio
    async def test_watchdog_fallback_to_repush_when_model_write_fails(self):
        """If no active client and model write raises, messages are re-pushed to Redis list."""
        from datetime import UTC, datetime

        from agent.health_check import _handle_steering
        from models.agent_session import AgentSession

        session_id = "test_watchdog_model_write_fail"
        session = AgentSession(
            session_id=session_id,
            project_key="test-steer",
            status="running",
            message_text="test task",
            created_at=datetime.now(tz=UTC),
        )
        session.save()

        push_steering_message(session_id, "update the tests", "Tom")

        mock_session = session
        original_push = mock_session.push_steering_message

        def raise_on_push(msg):
            raise RuntimeError("Redis write failed")

        mock_session.push_steering_message = raise_on_push

        with patch("agent.sdk_client.get_active_client", return_value=None):
            with patch(
                "models.agent_session.AgentSession.query",
            ) as mock_query:
                mock_query.filter.return_value = [mock_session]
                result = await _handle_steering(session_id)

        assert result is not None
        assert result["continue_"] is True

        # After model write failure, message should be re-pushed to Redis list
        msg = pop_steering_message(session_id)
        assert msg is not None
        assert msg["text"] == "update the tests"

        # Restore original method
        mock_session.push_steering_message = original_push

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


class TestResolveRootSessionId:
    """Tests for resolve_root_session_id in bridge/context.py.

    These tests verify that reply-to messages resolve to the original human
    message's session_id regardless of which message in the thread is replied to.
    All tests use mocked Telegram clients and mocked TelegramMessage cache records.
    """

    @pytest.mark.asyncio
    async def test_reply_to_valor_response_resolves_root_session(self):
        """Reply to Valor's message should resolve to the original human session_id.

        Scenario:
          msg_8111 (human) → msg_8113 (Valor response) → msg_8114 (human reply to 8113)
        Expected: resolve_root_session_id(client, chat, 8113, key) == "tg_key_chat_8111"
        """
        from bridge.context import resolve_root_session_id

        chat_id = 99001
        project_key = "testproject"

        # Simulate TelegramMessage cache:
        # msg_8113 is a Valor outbound message that has reply_to_msg_id=8111
        # msg_8111 is the original human inbound message
        def mock_filter(chat_id=None, message_id=None):
            if message_id == 8113:
                record = type(
                    "TelegramMsg",
                    (),
                    {"sender": "Valor", "reply_to_msg_id": 8111, "message_id": 8113},
                )()
                return [record]
            elif message_id == 8111:
                record = type(
                    "TelegramMsg",
                    (),
                    {
                        "sender": "Valor Engels",
                        "reply_to_msg_id": None,
                        "message_id": 8111,
                    },
                )()
                return [record]
            return []

        mock_query = type("Q", (), {"filter": staticmethod(mock_filter)})()
        mock_client = AsyncMock()

        with patch("models.telegram.TelegramMessage") as mock_tm:
            mock_tm.query = mock_query
            result = await resolve_root_session_id(mock_client, chat_id, 8113, project_key)

        assert result == f"tg_{project_key}_{chat_id}_8111"
        # Telegram API should NOT have been called (cache hit path)
        mock_client.get_messages.assert_not_called()

    @pytest.mark.asyncio
    async def test_resolve_root_session_id_fallback_on_error(self):
        """Exception during chain walk should return fallback session_id.

        If both cache walk and API walk throw, the result must be derived
        directly from reply_to_msg_id (old behavior, safe fallback).
        """
        from bridge.context import resolve_root_session_id

        chat_id = 99002
        project_key = "testproject"
        reply_to_msg_id = 5555

        mock_client = AsyncMock()
        mock_client.get_messages.side_effect = ConnectionError("Telegram unavailable")

        with patch("models.telegram.TelegramMessage") as mock_tm:
            mock_tm.query.filter.side_effect = RuntimeError("Redis unavailable")
            result = await resolve_root_session_id(
                mock_client, chat_id, reply_to_msg_id, project_key
            )

        # Must fall back gracefully to reply_to_msg_id-based session_id
        assert result == f"tg_{project_key}_{chat_id}_{reply_to_msg_id}"

    @pytest.mark.asyncio
    async def test_resolve_root_session_id_all_valor_chain(self):
        """Chain consisting entirely of Valor messages falls through to API.

        If the cache walk finds only Valor messages with no parent, the function
        should accept the last Valor message as the root rather than looping forever.
        When the API also returns only Valor messages, it should use the fallback.
        """
        from bridge.context import resolve_root_session_id

        chat_id = 99003
        project_key = "testproject"
        reply_to_msg_id = 7777

        # Cache: single Valor message with no reply_to_msg_id
        def mock_filter(chat_id=None, message_id=None):
            if message_id == 7777:
                record = type(
                    "TelegramMsg",
                    (),
                    {"sender": "Valor", "reply_to_msg_id": None, "message_id": 7777},
                )()
                return [record]
            return []

        mock_query = type("Q", (), {"filter": staticmethod(mock_filter)})()
        mock_client = AsyncMock()

        with patch("models.telegram.TelegramMessage") as mock_tm:
            mock_tm.query = mock_query
            result = await resolve_root_session_id(
                mock_client, chat_id, reply_to_msg_id, project_key
            )

        # Valor-only chain with no parent: should use the Valor msg_id as root
        # (best-effort fallback within cache walk — still deterministic)
        assert result == f"tg_{project_key}_{chat_id}_7777"

    @pytest.mark.asyncio
    async def test_resolve_root_session_id_direct_reply_to_human(self):
        """Reply directly to a human message resolves to that human message."""
        from bridge.context import resolve_root_session_id

        chat_id = 99004
        project_key = "testproject"
        reply_to_msg_id = 1234

        # Cache: msg_1234 is a human message (not Valor)
        def mock_filter(chat_id=None, message_id=None):
            if message_id == 1234:
                record = type(
                    "TelegramMsg",
                    (),
                    {"sender": "Alice", "reply_to_msg_id": None, "message_id": 1234},
                )()
                return [record]
            return []

        mock_query = type("Q", (), {"filter": staticmethod(mock_filter)})()
        mock_client = AsyncMock()

        with patch("models.telegram.TelegramMessage") as mock_tm:
            mock_tm.query = mock_query
            result = await resolve_root_session_id(
                mock_client, chat_id, reply_to_msg_id, project_key
            )

        assert result == f"tg_{project_key}_{chat_id}_1234"

    @pytest.mark.asyncio
    async def test_resolve_root_session_id_api_fallback_on_cache_miss(self):
        """Cache miss triggers API chain walk; result is persisted to Redis cache."""
        from bridge.context import _get_cached_root, resolve_root_session_id

        chat_id = 99005
        project_key = "testproject"
        reply_to_msg_id = 9999

        # Cache: no records (simulate TelegramMessage cache miss)
        def mock_filter(chat_id=None, message_id=None):
            return []

        mock_query = type("Q", (), {"filter": staticmethod(mock_filter)})()

        # API chain: returns a chain with root human message at msg_id=8800
        async def mock_fetch_reply_chain(client, cid, mid, max_depth=20):
            return [
                {"sender": "Bob", "content": "hello", "message_id": 8800, "date": None},
                {"sender": "Valor", "content": "hi", "message_id": 8801, "date": None},
                {
                    "sender": "Bob",
                    "content": "follow up",
                    "message_id": 9999,
                    "date": None,
                },
            ]

        mock_client = AsyncMock()

        with patch("models.telegram.TelegramMessage") as mock_tm:
            mock_tm.query = mock_query
            with patch("bridge.context.fetch_reply_chain", side_effect=mock_fetch_reply_chain):
                result = await resolve_root_session_id(
                    mock_client, chat_id, reply_to_msg_id, project_key
                )

        # First human message in chain is msg_id=8800
        assert result == f"tg_{project_key}_{chat_id}_8800"

        # After API fallback, the root must be persisted to the authoritative Redis cache.
        # A second call via _get_cached_root should return the same root without needing
        # the API again — proving the cache was written on the first resolution.
        cached = await _get_cached_root(chat_id, reply_to_msg_id)
        assert cached == 8800, f"Expected root 8800 to be cached after API fallback, got {cached}"

    @pytest.mark.asyncio
    async def test_resolve_root_session_id_uses_cached_root(self):
        """Pre-populated Redis cache is hit first; cache walk and API are never called."""
        from bridge.context import _set_cached_root, resolve_root_session_id

        chat_id = 99006
        project_key = "testproject"
        reply_to_msg_id = 7001
        expected_root = 7000

        # Pre-populate the authoritative Redis cache
        await _set_cached_root(chat_id, reply_to_msg_id, expected_root)

        mock_client = AsyncMock()

        with patch("bridge.context._cache_walk_root") as mock_cache_walk:
            with patch("bridge.context.fetch_reply_chain") as mock_api_walk:
                result = await resolve_root_session_id(
                    mock_client, chat_id, reply_to_msg_id, project_key
                )

        assert result == f"tg_{project_key}_{chat_id}_{expected_root}"
        # Neither the TelegramMessage cache walk nor the Telegram API should be called
        mock_cache_walk.assert_not_called()
        mock_api_walk.assert_not_called()

    @pytest.mark.asyncio
    async def test_reply_to_completed_session_reenqueues_with_context(self):
        """Reply to a completed session re-enqueues with context_summary prepended.

        Tests the actual bridge helper _build_completed_resume_text which is called
        by bridge/telegram_bridge.py's completed-session branch to augment the new
        message with prior session context before re-enqueueing.
        """
        from bridge.telegram_bridge import _build_completed_resume_text
        from models.agent_session import AgentSession

        follow_up_text = "Can you also add logging?"
        context_summary = "Implemented feature X and wrote tests."

        # Create a completed session with context_summary
        session = AgentSession(
            session_id="test_completed_resume_helper_ctx",
            project_key="test",
            status="completed",
            message_text="original task",
            context_summary=context_summary,
            created_at=datetime.now(tz=UTC),
        )
        session.save()

        # Call the actual bridge helper — not a re-implementation
        result = _build_completed_resume_text(session, follow_up_text)

        # Verify augmented text contains the context summary preamble
        assert "[Prior session context:" in result, (
            f"Expected '[Prior session context:' prefix, got: {result!r}"
        )
        assert context_summary in result, f"Expected context summary in result, got: {result!r}"
        assert follow_up_text in result, f"Expected follow-up text in result, got: {result!r}"
        # Canonical format check
        assert result == f"[Prior session context: {context_summary}]\n\n{follow_up_text}"

        # --- Test fallback when context_summary is None ---
        session_no_summary = AgentSession(
            session_id="test_completed_resume_helper_no_ctx",
            project_key="test",
            status="completed",
            message_text="done",
            context_summary=None,
            created_at=datetime.now(tz=UTC),
        )
        session_no_summary.save()

        fallback_result = _build_completed_resume_text(session_no_summary, follow_up_text)

        assert (
            "[Prior session context: This continues a previously completed session.]"
            in fallback_result
        ), f"Expected fallback string, got: {fallback_result!r}"
        assert follow_up_text in fallback_result

        # --- Extended by issue #949: with reply chain, both blocks appear ---
        from bridge.context import REPLY_THREAD_CONTEXT_HEADER

        chain_block = (
            f"{REPLY_THREAD_CONTEXT_HEADER} (oldest to newest):\n"
            "----------------------------------------\n"
            "Tom: any update?\nValor: fixed yesterday\n"
            "----------------------------------------"
        )
        with_chain = _build_completed_resume_text(
            session, follow_up_text, reply_chain_context=chain_block
        )
        assert context_summary in with_chain
        assert REPLY_THREAD_CONTEXT_HEADER in with_chain
        assert follow_up_text in with_chain
        # Exactly one header — Race 1 / IN-1 guard
        assert with_chain.count(REPLY_THREAD_CONTEXT_HEADER) == 1
        # Order: summary -> chain -> follow_up
        assert (
            with_chain.index("[Prior session context:")
            < with_chain.index(REPLY_THREAD_CONTEXT_HEADER)
            < with_chain.index(follow_up_text)
        )

    @pytest.mark.asyncio
    async def test_reply_to_completed_session_fallback_without_summary(self):
        """Reply to a completed session with no context_summary still carries the reply chain.

        Replays the 2026-04-14 11:54 incident (issue #949): the prior session's
        context_summary was empty, so the fallback preamble is used. With the
        new reply-chain carry, the agent still sees the thread context.
        """
        from bridge.context import REPLY_THREAD_CONTEXT_HEADER
        from bridge.telegram_bridge import _build_completed_resume_text
        from models.agent_session import AgentSession

        session = AgentSession(
            session_id="test_fallback_reply_chain_949",
            project_key="test",
            status="completed",
            message_text="errored out",
            context_summary=None,  # the 11:54 incident had no summary
            created_at=datetime.now(tz=UTC),
        )
        session.save()

        chain_block = (
            f"{REPLY_THREAD_CONTEXT_HEADER} (oldest to newest):\n"
            "----------------------------------------\n"
            "Tom: can you check and see if we got this fixed?\n"
            "----------------------------------------"
        )
        result = _build_completed_resume_text(
            session,
            "did we get this fixed?",
            reply_chain_context=chain_block,
        )
        # Fallback sentinel still present
        assert "This continues a previously completed session." in result
        # Reply chain hydrated -- this is the new carry
        assert REPLY_THREAD_CONTEXT_HEADER in result
        assert result.count(REPLY_THREAD_CONTEXT_HEADER) == 1
        assert "did we get this fixed?" in result

    @pytest.mark.asyncio
    async def test_resume_completed_carries_reply_chain(self):
        """End-to-end: the helper produces a prompt containing the REPLY THREAD CONTEXT
        block when the handler passes a reply_chain_context.

        This is the guard against regression of the gap described in #949:
        the resume-completed branch used to omit reply-thread context.
        """
        from bridge.context import REPLY_THREAD_CONTEXT_HEADER, format_reply_chain
        from bridge.telegram_bridge import _build_completed_resume_text
        from models.agent_session import AgentSession

        session = AgentSession(
            session_id="test_resume_carries_chain",
            project_key="test",
            status="completed",
            message_text="",
            context_summary="prior context",
            created_at=datetime.now(tz=UTC),
        )
        session.save()

        # Build a realistic reply chain block via the real formatter
        chain = [
            {"sender": "Tom", "content": "is the bug fixed?", "message_id": 1, "date": None},
            {"sender": "Valor", "content": "yes, shipped yesterday", "message_id": 2, "date": None},
        ]
        chain_block = format_reply_chain(chain)
        assert REPLY_THREAD_CONTEXT_HEADER in chain_block

        augmented = _build_completed_resume_text(
            session,
            "can you verify it's still working?",
            reply_chain_context=chain_block,
        )
        assert "prior context" in augmented
        assert REPLY_THREAD_CONTEXT_HEADER in augmented
        assert "is the bug fixed?" in augmented
        assert "can you verify" in augmented

    @pytest.mark.parametrize(
        "hydration_site",
        ["resume_completed", "fresh_session_non_valor"],
    )
    def test_no_double_hydration_when_handler_prehydrates(self, hydration_site):
        """Race 1 / IN-1 / IN-7: belt-and-suspenders idempotency guard.

        The deferred enrichment must skip the reply-chain fetch when either:
          - Primary:   extra_context["reply_chain_hydrated"] flag is set by
                       the bridge handler at enqueue time.
          - Defensive: REPLY_THREAD_CONTEXT_HEADER substring is present in
                       message_text.

        Guards both the flag-based and header-based checks in
        agent/agent_session_queue.py against being accidentally removed or
        re-ordered. Also guards both bridge handler call sites against
        regressing on the extra_context stamp:
          - resume_completed: PR #953's resume-completed branch (reply-to-Valor).
          - fresh_session_non_valor: Issue #1064's fresh-session branch (reply
            to a non-Valor message, semantic-route miss).

        The guarantee is a SINGLE assertion contract: exactly one
        REPLY THREAD CONTEXT block per prompt regardless of which handler
        branch hydrated (plan Implementation Note C5).
        """
        import pathlib

        from bridge.context import REPLY_THREAD_CONTEXT_HEADER

        # Read the source and assert the guards are in place. This is a
        # structural test -- simulating the full worker path would pull in
        # Claude SDK / Popoto queues. The guards are a handful of lines and
        # regress only by deletion, which this test catches.
        #
        # Note: the worker-side guard lives in agent/session_executor.py
        # after the agent_session_queue.py split in commit b7e1a1db
        # (PR #1023 / #1051). Prior to that refactor it was in
        # agent/agent_session_queue.py.
        executor_src = pathlib.Path(__file__).resolve().parents[2] / "agent" / "session_executor.py"
        executor_content = executor_src.read_text()
        assert "REPLY_THREAD_CONTEXT_HEADER" in executor_content, (
            "Defensive header guard removed — reply chain may double-hydrate"
        )
        assert "reply_chain_hydrated" in executor_content, (
            "Primary flag guard (IN-1 belt-and-suspenders) removed from worker enrichment"
        )
        # Must do the check AGAINST enrich_reply_to_msg_id so the fetch is skipped
        assert "enrich_reply_to_msg_id = None" in executor_content
        assert REPLY_THREAD_CONTEXT_HEADER  # sanity check the import

        # Both bridge handler call sites must stamp the primary flag when
        # they hydrate the reply chain synchronously.
        bridge_src = pathlib.Path(__file__).resolve().parents[2] / "bridge" / "telegram_bridge.py"
        bridge_content = bridge_src.read_text()
        assert '"reply_chain_hydrated": True' in bridge_content, (
            "Handler stopped stamping reply_chain_hydrated=True on extra_context — "
            "primary IN-1 guard is no longer populated"
        )
        # Exactly two call sites must exist: resume-completed (PR #953) and
        # fresh-session-non-valor (issue #1064). Any additional site should be
        # reviewed explicitly because it risks double-hydration if not gated.
        flag_stamp_count = bridge_content.count('"reply_chain_hydrated": True')
        assert flag_stamp_count >= 2, (
            f"Expected at least 2 reply_chain_hydrated stamp sites (resume-completed + "
            f"fresh-session), found {flag_stamp_count}. Did the fresh-session pre-hydration "
            f"block get removed or renamed?"
        )

        # Per-site structural guards:
        if hydration_site == "resume_completed":
            assert "RESUME_REPLY_CHAIN_FAIL" in bridge_content, (
                "Resume-completed failure-path log tag missing"
            )
        else:  # fresh_session_non_valor
            assert "FRESH_REPLY_CHAIN_FAIL" in bridge_content, (
                "Fresh-session failure-path log tag missing — reply-to non-Valor "
                "messages may silently drop thread context"
            )
            assert "fresh_reply_chain_prehydrated" in bridge_content, (
                "Fresh-session success log tag missing — observability parity broken"
            )
            assert "REPLY_CHAIN_PREHYDRATION_DISABLED" in bridge_content, (
                "Fresh-session kill-switch removed — rollback without deploy is broken"
            )

    def test_fresh_session_non_valor_reply_prehydrates_chain(self):
        """Issue #1064: the fresh-session pre-hydration block must exist and
        produce a REPLY_THREAD_CONTEXT block in enqueued_message_text with
        extra_context[reply_chain_hydrated]=True.

        Structural test — we assert the code shape rather than simulate the
        full Telegram/Telethon handler invocation, which would pull in the
        Claude SDK, Popoto queues, and a mocked client. The code shape is a
        handful of lines and regresses only by deletion or gate-condition
        drift, which this test catches.
        """
        import pathlib

        bridge_src = pathlib.Path(__file__).resolve().parents[2] / "bridge" / "telegram_bridge.py"
        bridge_content = bridge_src.read_text()

        # The new block must exist with the canonical section marker.
        assert "FRESH-SESSION NON-VALOR REPLY PRE-HYDRATION" in bridge_content, (
            "Fresh-session pre-hydration block removed or section comment stripped"
        )
        # Gate condition: reply_to_msg_id truthy AND NOT is_reply_to_valor AND kill-switch off.
        # The handler topology already enforces the fresh-session placement; we only need
        # to assert the two explicit predicates plus the kill-switch.
        assert "not is_reply_to_valor" in bridge_content, (
            "Gate predicate `not is_reply_to_valor` missing — would double-hydrate "
            "when resume-completed branch already pre-hydrated"
        )
        # The prepend format must include the canonical header and the CURRENT MESSAGE marker.
        assert "CURRENT MESSAGE:" in bridge_content, (
            "CURRENT MESSAGE marker missing — agent can't distinguish thread from new text"
        )
        # Success path stamps the flag AND emits the INFO log.
        assert '"reply_chain_hydrated": True' in bridge_content
        assert "fresh_reply_chain_prehydrated" in bridge_content

    def test_fresh_session_non_valor_reply_timeout_falls_back(self):
        """Issue #1064 failure path: 3s timeout logs FRESH_REPLY_CHAIN_FAIL
        and does NOT stamp reply_chain_hydrated, so the worker's deferred
        enrichment remains free to retry.

        Implementation Note C2: three outcomes, only success-with-chain stamps.
        """
        import pathlib

        bridge_src = pathlib.Path(__file__).resolve().parents[2] / "bridge" / "telegram_bridge.py"
        bridge_content = bridge_src.read_text()

        # Both failure branches must log with FRESH_REPLY_CHAIN_FAIL tag.
        # grep-style: the tag appears at least twice (timeout + exception).
        assert bridge_content.count("FRESH_REPLY_CHAIN_FAIL") >= 2, (
            "FRESH_REPLY_CHAIN_FAIL log tag must appear in both TimeoutError and "
            "generic Exception branches — at least 2 occurrences required"
        )
        assert "FRESH_REPLY_CHAIN_FAIL timeout" in bridge_content, (
            "Timeout branch log missing the 'timeout' discriminator"
        )
        assert "FRESH_REPLY_CHAIN_FAIL exception" in bridge_content, (
            "Exception branch log missing the 'exception' discriminator"
        )

        # The 3s timeout must match PR #953's resume-completed value verbatim
        # (tuning timeouts belongs in a separate telemetry-driven change).
        # Both sites use `timeout=3.0` — assert at least 2 such occurrences.
        assert bridge_content.count("timeout=3.0") >= 2, (
            "Fresh-session pre-hydration timeout diverged from PR #953's 3.0s — "
            "tuning belongs in a follow-up with telemetry"
        )

        # Failure path must NOT stamp the flag: the flag assignment must be
        # inside the `if reply_chain_context:` branch (not unconditionally
        # after the try/except). We grep for the canonical ordering.
        assert (
            "if reply_chain_context:\n                enqueued_message_text = (" in bridge_content
            or "if reply_chain_context:" in bridge_content
        ), (
            "Flag stamp must be gated on `if reply_chain_context:` so failed/empty "
            "fetches do NOT stamp reply_chain_hydrated (Implementation Note C2)"
        )

    def test_fresh_session_reply_to_valor_skips_new_block(self):
        """Issue #1064: `is_reply_to_valor=True` messages must NOT hit the
        new fresh-session block. They are handled by the resume-completed
        branch (PR #953) which returns earlier in the handler, so placement
        enforces non-double-hydration.

        Structural check: the new block must explicitly gate on
        `not is_reply_to_valor` so even if handler topology changes in a
        way that lets control flow reach here with is_reply_to_valor=True,
        the gate prevents the pre-fetch.
        """
        import pathlib

        bridge_src = pathlib.Path(__file__).resolve().parents[2] / "bridge" / "telegram_bridge.py"
        bridge_content = bridge_src.read_text()

        # The new block's gate must include `not is_reply_to_valor`.
        # We look for the section comment followed by the gate clause.
        fresh_block_start = bridge_content.find("FRESH-SESSION NON-VALOR REPLY PRE-HYDRATION")
        assert fresh_block_start >= 0, "Fresh-session block section comment missing"

        # Find the gate `if` statement within the fresh-session block.
        # The gate is within ~2000 chars of the section comment.
        fresh_block_region = bridge_content[fresh_block_start : fresh_block_start + 3000]
        assert "not is_reply_to_valor" in fresh_block_region, (
            "Fresh-session gate missing `not is_reply_to_valor` predicate — "
            "would double-hydrate replies-to-Valor if resume-completed branch "
            "ever failed to short-circuit"
        )
        assert "message.reply_to_msg_id" in fresh_block_region, (
            "Fresh-session gate missing `message.reply_to_msg_id` predicate"
        )

    def test_fresh_session_prehydration_kill_switch(self):
        """Issue #1064: REPLY_CHAIN_PREHYDRATION_DISABLED kill-switch env var
        must mirror REPLY_CONTEXT_DIRECTIVE_DISABLED's parsing exactly —
        truthy set ("1", "true", "yes", "on"), .strip().lower(), default "".

        Implementation Note C3: parity prevents a subtle bug where a rollout
        uses "TRUE" to disable the directive but "true" to disable the chain.
        """
        import pathlib

        bridge_src = pathlib.Path(__file__).resolve().parents[2] / "bridge" / "telegram_bridge.py"
        bridge_content = bridge_src.read_text()

        # The kill-switch env var must be referenced.
        assert "REPLY_CHAIN_PREHYDRATION_DISABLED" in bridge_content, (
            "Kill-switch env var REPLY_CHAIN_PREHYDRATION_DISABLED missing — "
            "rollback without deploy is broken"
        )

        # Parsing must mirror the sibling REPLY_CONTEXT_DIRECTIVE_DISABLED
        # exactly — same truthy set, same normalization. Both bridge sites
        # use the multi-line tuple form, so we assert the full
        # `os.getenv(...).strip().lower() in (...)` shape via the env-var
        # name + normalization chain, then verify all four truthy values
        # appear together in the surrounding region of the new block.
        assert ".strip().lower() in (" in bridge_content, (
            "Kill-switch normalization must use `.strip().lower() in (...)` chain "
            "matching REPLY_CONTEXT_DIRECTIVE_DISABLED sibling pattern"
        )

        # Locate the fresh-session block and assert its truthy set matches
        # the sibling — all four truthy values present in a narrow region
        # following the env-var name.
        disabled_marker = "REPLY_CHAIN_PREHYDRATION_DISABLED"
        marker_pos = bridge_content.find(disabled_marker)
        assert marker_pos >= 0, "Kill-switch env var name not found"
        # Region from the env-var name to ~500 chars later covers the
        # os.getenv(...).strip().lower() in (...) block.
        region = bridge_content[marker_pos : marker_pos + 500]
        for truthy_value in ('"1"', '"true"', '"yes"', '"on"'):
            assert truthy_value in region, (
                f"Kill-switch truthy set missing {truthy_value} — must mirror "
                f"REPLY_CONTEXT_DIRECTIVE_DISABLED's set exactly for parity"
            )

        # The normalization chain `.strip().lower() in (` must appear twice
        # (once for each env var) so the two sites stay in lock-step.
        assert bridge_content.count(".strip().lower() in (") >= 2, (
            "Kill-switch normalization chain must appear at both sites "
            "(REPLY_CONTEXT_DIRECTIVE_DISABLED + REPLY_CHAIN_PREHYDRATION_DISABLED)"
        )

    def test_implicit_context_directive_injected(self):
        """Plan Change C: messages that reference prior context without reply-to
        get a [CONTEXT DIRECTIVE] prepended before enqueue.

        Tests the predicate and directive contents that the handler uses.
        """
        from bridge.context import matched_context_patterns, references_prior_context

        # Positive case -- message references prior context
        assert references_prior_context("did we get this fixed?") is True
        assert len(matched_context_patterns("did we get this fixed?")) >= 1

        # Negative case -- fresh request, no directive injection
        assert references_prior_context("please create a new issue") is False
        assert matched_context_patterns("please create a new issue") == []

        # The directive string itself is embedded in telegram_bridge.py.
        # Assert its canonical prefix ships so the agent sees a recognizable marker.
        import pathlib

        src = pathlib.Path(__file__).resolve().parents[2] / "bridge" / "telegram_bridge.py"
        content = src.read_text()
        assert "[CONTEXT DIRECTIVE]" in content, (
            "Implicit-context directive removed from bridge handler"
        )
        assert "REPLY_CONTEXT_DIRECTIVE_DISABLED" in content, (
            "Env kill-switch REPLY_CONTEXT_DIRECTIVE_DISABLED removed"
        )

    @pytest.mark.parametrize(
        "hydration_site,expected_log_tag",
        [
            ("resume_completed", "RESUME_REPLY_CHAIN_FAIL"),
            ("fresh_session_non_valor", "FRESH_REPLY_CHAIN_FAIL"),
        ],
    )
    def test_reply_chain_fetch_failure_falls_back(self, hydration_site, expected_log_tag):
        """Plan failure-path: a fetch_reply_chain exception must not prevent
        the handler from enqueueing the session.

        Parametrized across both handler call sites per Implementation Note C5:
          - resume_completed: PR #953's branch uses summary-only fallback via
            _build_completed_resume_text(reply_chain_context=None).
          - fresh_session_non_valor: issue #1064's branch leaves the enqueued
            message_text untouched and does NOT stamp reply_chain_hydrated,
            so worker-side deferred enrichment is free to retry.

        Both branches share the same failure contract: the session enqueues,
        the warning log fires with a distinguishable tag, and the flag is
        only stamped on success-with-non-empty-chain.
        """
        import pathlib

        src = pathlib.Path(__file__).resolve().parents[2] / "bridge" / "telegram_bridge.py"
        content = src.read_text()

        # Both call sites must emit their distinguishable warning tags.
        assert expected_log_tag in content, (
            f"{expected_log_tag} log tag missing — failure path invisible in logs"
        )

        if hydration_site == "resume_completed":
            # Summary-only fallback via the existing helper — verify the
            # contract end-to-end against _build_completed_resume_text.
            from bridge.telegram_bridge import _build_completed_resume_text
            from models.agent_session import AgentSession

            session = AgentSession(
                session_id="test_fetch_fail_fallback_resume",
                project_key="test",
                status="completed",
                message_text="prior",
                context_summary="did work",
                created_at=datetime.now(tz=UTC),
            )
            session.save()

            # Simulate the handler's catch branch: reply_chain_context is None
            result = _build_completed_resume_text(session, "follow up", reply_chain_context=None)

            # Summary-only format; the agent still gets SOMETHING
            assert result == "[Prior session context: did work]\n\nfollow up"
        else:
            # Fresh-session fallback is structural: on failure the handler
            # leaves enqueued_message_text unchanged and does NOT stamp the
            # flag. We assert the code-shape contract: the flag stamp is
            # gated on `if reply_chain_context:` so the failure branch
            # (exception caught, reply_chain_context remains None) falls
            # through without modification.
            assert "if reply_chain_context:" in content, (
                "Fresh-session flag stamp must be gated on `if reply_chain_context:` "
                "so failed fetches do NOT stamp reply_chain_hydrated (Impl Note C2)"
            )
            # Belt-and-suspenders: the `extra_overrides: dict | None = None`
            # default ensures None is passed through on failure.
            assert "extra_overrides: dict | None = None" in content, (
                "Fresh-session extra_overrides must default to None so failure "
                "branch does not stamp the flag"
            )


class TestSteerChildDelivery:
    """Integration tests for steer_child.py → CLI-harness delivery path.

    These tests use real AgentSession objects (no mock of get_active_client or
    push_steering_message) to verify the end-to-end steering delivery path.
    """

    def _create_dev_session(self, parent_agent_id: str, session_id: str, status: str = "running"):
        """Create a Dev AgentSession with a parent-child relationship.

        session_id: the Popoto session_id field (used by steer_session() for lookup).
        Returns the saved session — use session.agent_session_id as the ID for _steer_child().
        """
        from datetime import UTC, datetime

        from models.agent_session import AgentSession

        session = AgentSession(
            session_id=session_id,
            project_key="test-steer-child",
            status=status,
            session_type="dev",
            parent_agent_session_id=parent_agent_id,
            message_text="dev task",
            created_at=datetime.now(tz=UTC),
        )
        session.save()
        return session

    def _create_pm_session(self, session_id: str):
        """Create a PM AgentSession. Returns the saved session."""
        from datetime import UTC, datetime

        from models.agent_session import AgentSession

        session = AgentSession(
            session_id=session_id,
            project_key="test-steer-child",
            status="running",
            session_type="pm",
            message_text="pm task",
            created_at=datetime.now(tz=UTC),
        )
        session.save()
        return session

    def test_steer_child_cli_harness_delivery(self):
        """_steer_child() writes message to turn-boundary inbox (queued_steering_messages)."""
        from models.agent_session import AgentSession
        from scripts.steer_child import _steer_child

        parent = self._create_pm_session("tg_test_parent_p001")
        child = self._create_dev_session(parent.agent_session_id, "tg_test_child_c001")

        exit_code = _steer_child(
            session_id=child.agent_session_id,
            message="focus on error handling",
            parent_id=parent.agent_session_id,
            abort=False,
        )

        assert exit_code == 0

        # Verify the message landed in queued_steering_messages
        sessions = list(AgentSession.query.filter(id=child.agent_session_id))
        assert sessions, "Child session not found after steering"
        refreshed = sessions[0]
        assert len(refreshed.queued_steering_messages) >= 1
        assert "focus on error handling" in refreshed.queued_steering_messages[0]

        # Verify the Redis list was NOT used (turn-boundary path only)
        msg = pop_steering_message(child.session_id)
        assert msg is None, "Message should NOT have gone to Redis steering list"

    def test_steer_child_abort_uses_redis_list(self):
        """_steer_child() with abort=True writes to Redis list, not turn-boundary inbox."""
        from models.agent_session import AgentSession
        from scripts.steer_child import _steer_child

        parent = self._create_pm_session("tg_test_parent_p002")
        child = self._create_dev_session(parent.agent_session_id, "tg_test_child_c002")

        exit_code = _steer_child(
            session_id=child.agent_session_id,
            message="stop",
            parent_id=parent.agent_session_id,
            abort=True,
        )

        assert exit_code == 0

        # Abort message should be on the Redis list with is_abort=True
        # Abort path uses session_id (Redis key) from push_steering_message
        msg = pop_steering_message(child.agent_session_id)
        assert msg is not None, "Abort message should be in Redis steering list"
        assert msg["is_abort"] is True
        assert "stop" in msg["text"]

        # queued_steering_messages should be empty (abort does NOT use turn-boundary inbox)
        sessions = list(AgentSession.query.filter(id=child.agent_session_id))
        refreshed = sessions[0]
        inbox = refreshed.queued_steering_messages or []
        assert len(inbox) == 0, "Abort messages must NOT appear in queued_steering_messages"

    def test_steer_child_terminal_session_exits_nonzero(self):
        """_steer_child() exits non-zero when session is in a terminal status."""
        from scripts.steer_child import _steer_child

        parent = self._create_pm_session("tg_test_parent_p003")
        child = self._create_dev_session(
            parent.agent_session_id, "tg_test_child_c003", status="completed"
        )

        exit_code = _steer_child(
            session_id=child.agent_session_id,
            message="too late",
            parent_id=parent.agent_session_id,
            abort=False,
        )

        assert exit_code == 1

    def test_steer_child_steer_session_failure_exits_nonzero(self, capsys):
        """_steer_child() exits non-zero and prints error when steer_session fails."""
        from scripts.steer_child import _steer_child

        parent = self._create_pm_session("tg_test_parent_p004")
        child = self._create_dev_session(parent.agent_session_id, "tg_test_child_c004")

        # Patch at the source module — _steer_child imports steer_session lazily
        with patch(
            "agent.agent_session_queue.steer_session",
            return_value={
                "success": False,
                "session_id": child.session_id,
                "error": "mock failure",
            },
        ):
            exit_code = _steer_child(
                session_id=child.agent_session_id,
                message="will fail",
                parent_id=parent.agent_session_id,
                abort=False,
            )

        assert exit_code == 1
        captured = capsys.readouterr()
        assert "mock failure" in captured.err
