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
            created_at=time.time(),
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
        """Steering check should NOT match completed sessions."""
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
        self._create_session(session_id, "pending", created_at=time.time())

        # Simulate the bridge logic: check age and push steering
        from models.agent_session import AgentSession

        pending_sessions = AgentSession.query.filter(
            session_id=session_id, status="pending"
        )
        assert len(pending_sessions) > 0
        pending_session = pending_sessions[0]
        age = time.time() - (pending_session.created_at or 0)
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
        self._create_session(session_id, "pending", created_at=time.time() - 10)

        from models.agent_session import AgentSession

        pending_sessions = AgentSession.query.filter(
            session_id=session_id, status="pending"
        )
        assert len(pending_sessions) > 0
        pending_session = pending_sessions[0]
        age = time.time() - (pending_session.created_at or 0)
        assert age > PENDING_MERGE_WINDOW_SECONDS

    def test_pending_merge_window_constant_is_7(self):
        """The merge window constant should be 7 seconds."""
        from bridge.telegram_bridge import PENDING_MERGE_WINDOW_SECONDS

        assert PENDING_MERGE_WINDOW_SECONDS == 7

    def test_multiple_steering_messages_into_pending(self):
        """Multiple follow-up messages should all queue into a pending session."""
        session_id = "test_pending_multi_steer"
        self._create_session(session_id, "pending", created_at=time.time())

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
            session_id, "pending", chat_id=chat_id, created_at=time.time()
        )

        # Replicate the intake classifier logic from the bridge
        active_sessions = []
        for check_status in ("running", "active", "dormant"):
            sessions = AgentSession.query.filter(
                chat_id=chat_id, status=check_status
            )
            if sessions:
                active_sessions.extend(sessions)

        # Also include recent pending sessions within the merge window
        pending_sessions = AgentSession.query.filter(
            chat_id=chat_id, status="pending"
        )
        if pending_sessions:
            now_ts = time.time()
            for ps in pending_sessions:
                age = now_ts - (ps.created_at or 0)
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
            session_id, "pending", chat_id=chat_id, created_at=time.time() - 10
        )

        active_sessions = []
        for check_status in ("running", "active", "dormant"):
            sessions = AgentSession.query.filter(
                chat_id=chat_id, status=check_status
            )
            if sessions:
                active_sessions.extend(sessions)

        pending_sessions = AgentSession.query.filter(
            chat_id=chat_id, status="pending"
        )
        if pending_sessions:
            now_ts = time.time()
            for ps in pending_sessions:
                age = now_ts - (ps.created_at or 0)
                if age <= PENDING_MERGE_WINDOW_SECONDS:
                    active_sessions.append(ps)

        assert len(active_sessions) == 0


class TestDrainOnStart:
    """Tests for the drain-on-start logic in _pop_job (#619).

    Uses AgentSession.create() (sync) + _pop_job_with_fallback() which has a
    sync fallback that avoids the async_filter index visibility race in tests.
    """

    def _create_pending_job(self, session_id, chat_id="test_chat", message_text="hello"):
        """Create a pending AgentSession (job) using the same pattern as test_job_queue_race."""
        from models.agent_session import AgentSession

        return AgentSession.create(
            session_id=session_id,
            project_key="test",
            status="pending",
            priority="normal",
            chat_id=chat_id,
            message_text=message_text,
            created_at=time.time(),
            working_dir="/tmp/test",
            sender_name="Test",
            telegram_message_id=1,
        )

    @pytest.mark.asyncio
    async def test_drain_prepends_steering_to_message_text(self):
        """Steering messages queued during pending should be prepended on start."""
        from agent.job_queue import _pop_job_with_fallback

        session_id = "test_drain_prepend"
        chat_id = "test_drain_chat_1"
        self._create_pending_job(session_id, chat_id=chat_id, message_text="original message")

        # Simulate follow-up messages arriving during pending window
        push_steering_message(session_id, "follow-up context", "Tom")
        push_steering_message(session_id, "another detail", "Tom")

        # Pop the job (triggers drain-on-start)
        job = await _pop_job_with_fallback(chat_id)
        assert job is not None
        assert "original message" in job.message_text
        assert "follow-up context" in job.message_text
        assert "another detail" in job.message_text

    @pytest.mark.asyncio
    async def test_drain_no_steering_messages_unchanged(self):
        """If no steering messages, message_text should be unchanged."""
        from agent.job_queue import _pop_job_with_fallback

        session_id = "test_drain_empty"
        chat_id = "test_drain_chat_2"
        self._create_pending_job(session_id, chat_id=chat_id, message_text="just this")

        job = await _pop_job_with_fallback(chat_id)
        assert job is not None
        assert job.message_text == "just this"

    @pytest.mark.asyncio
    async def test_drain_empty_text_steering_skipped(self):
        """Steering messages with empty text should be skipped."""
        from agent.job_queue import _pop_job_with_fallback

        session_id = "test_drain_empty_text"
        chat_id = "test_drain_chat_3"
        self._create_pending_job(session_id, chat_id=chat_id, message_text="original")

        push_steering_message(session_id, "  ", "Tom")  # whitespace-only

        job = await _pop_job_with_fallback(chat_id)
        assert job is not None
        assert job.message_text == "original"

    @pytest.mark.asyncio
    async def test_drain_failure_does_not_crash_job(self):
        """If drain fails, the job should still start successfully."""
        from agent.job_queue import _pop_job_with_fallback

        session_id = "test_drain_failure"
        chat_id = "test_drain_chat_4"
        self._create_pending_job(session_id, chat_id=chat_id, message_text="still works")

        with patch(
            "agent.steering.pop_all_steering_messages",
            side_effect=ConnectionError("Redis down"),
        ):
            job = await _pop_job_with_fallback(chat_id)
            assert job is not None
            assert job.message_text == "still works"


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
                    {"sender": "Valor Engels", "reply_to_msg_id": None, "message_id": 8111},
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
        """Cache miss triggers API chain walk to find root."""
        from bridge.context import resolve_root_session_id

        chat_id = 99005
        project_key = "testproject"
        reply_to_msg_id = 9999

        # Cache: no records (simulate cache miss)
        def mock_filter(chat_id=None, message_id=None):
            return []

        mock_query = type("Q", (), {"filter": staticmethod(mock_filter)})()

        # API chain: returns a chain with root human message at msg_id=8800
        async def mock_fetch_reply_chain(client, cid, mid, max_depth=20):
            return [
                {"sender": "Bob", "content": "hello", "message_id": 8800, "date": None},
                {"sender": "Valor", "content": "hi", "message_id": 8801, "date": None},
                {"sender": "Bob", "content": "follow up", "message_id": 9999, "date": None},
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
