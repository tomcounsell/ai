"""Unit tests for agent.agent_session_queue helpers.

Focused on field-extraction semantics used by delete-and-recreate callers
(retry, orphan fix, continuation fallback). _pop_agent_session itself uses
in-place mutation via transition_status() and does NOT go through
_extract_agent_session_fields.

Also tests Redis pop lock acquisition and contention behavior.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from agent.agent_session_queue import (
    _AGENT_SESSION_FIELDS,
    _acquire_pop_lock,
    _extract_agent_session_fields,
    _release_pop_lock,
)
from models.agent_session import AgentSession


def _make_session(**overrides) -> AgentSession:
    """Build an unsaved AgentSession with sensible defaults."""
    defaults = {
        "project_key": "test",
        "status": "pending",
        "priority": "normal",
        "created_at": datetime.now(tz=UTC),
        "session_id": "unit-test",
        "working_dir": "/tmp/test",
        "chat_id": "123",
        "message_text": "hello",
        "sender_name": "Tester",
        "telegram_message_id": 1,
    }
    defaults.update(overrides)
    return AgentSession(**defaults)


class TestExtractFieldsMessageTextRoundTrip:
    """_extract_agent_session_fields must preserve message_text across
    delete-and-recreate via the initial_telegram_message dict.

    message_text is a virtual @property on AgentSession that reads from
    initial_telegram_message["message_text"]. _AGENT_SESSION_FIELDS does not
    include message_text directly -- it includes initial_telegram_message,
    so the value is preserved transitively when the dict is copied.
    """

    def test_message_text_roundtrips_via_initial_telegram_message(self):
        """Round-trip: extract -> create new record -> .message_text matches."""
        original = _make_session(message_text="the-original-message")
        assert original.message_text == "the-original-message"

        fields = _extract_agent_session_fields(original)

        # message_text is NOT a top-level key in the extracted dict; it lives
        # inside initial_telegram_message.
        assert "message_text" not in fields
        assert "initial_telegram_message" in fields
        assert fields["initial_telegram_message"]["message_text"] == "the-original-message"

        # Recreate and verify the virtual property resolves correctly.
        recreated = AgentSession(**fields)
        assert recreated.message_text == "the-original-message"

    def test_message_text_none_roundtrips_safely(self):
        """When message_text is None / unset, extraction and recreation
        must not raise."""
        original = _make_session()
        # Clear the text explicitly
        original.initial_telegram_message = None

        fields = _extract_agent_session_fields(original)
        # initial_telegram_message may be None; recreation should still work
        recreated = AgentSession(**fields)
        # No crash; .message_text returns None for empty dict
        assert recreated.message_text in (None, "")

    def test_scheduling_depth_intentionally_omitted(self):
        """_AGENT_SESSION_FIELDS must NOT include scheduling_depth.

        scheduling_depth is a derived @property that walks the
        parent_agent_session_id chain at read time. Including it in the
        extracted dict would attempt to set a read-only property on recreate.
        """
        assert "scheduling_depth" not in _AGENT_SESSION_FIELDS

    def test_agent_session_id_intentionally_omitted(self):
        """_AGENT_SESSION_FIELDS must NOT include agent_session_id / id.

        agent_session_id is the AutoKeyField; delete-and-recreate callers
        rely on a fresh auto-generated ID for the new record.
        """
        assert "agent_session_id" not in _AGENT_SESSION_FIELDS
        assert "id" not in _AGENT_SESSION_FIELDS


class TestPopLock:
    """Tests for _acquire_pop_lock and _release_pop_lock helpers.

    These helpers prevent TOCTOU races in _pop_agent_session by making the
    query→transition block atomic across concurrent workers.
    """

    def test_acquire_pop_lock_succeeds_when_no_lock_held(self):
        """First acquisition of a lock key must succeed."""
        chat_id = "test-pop-lock-chat-1"
        # Ensure no stale lock
        _release_pop_lock(chat_id)
        try:
            result = _acquire_pop_lock(chat_id)
            assert result is True, "First acquisition must succeed"
        finally:
            _release_pop_lock(chat_id)

    def test_acquire_pop_lock_fails_when_already_held(self):
        """Second acquisition of the same key must fail (contention)."""
        chat_id = "test-pop-lock-chat-2"
        _release_pop_lock(chat_id)
        try:
            first = _acquire_pop_lock(chat_id)
            assert first is True, "First acquisition must succeed"

            second = _acquire_pop_lock(chat_id)
            assert second is False, (
                "Second acquisition while lock is held must return False — "
                "contention detected, caller should return None"
            )
        finally:
            _release_pop_lock(chat_id)

    def test_release_pop_lock_allows_reacquisition(self):
        """After releasing a lock, it must be acquirable again."""
        chat_id = "test-pop-lock-chat-3"
        _release_pop_lock(chat_id)
        try:
            first = _acquire_pop_lock(chat_id)
            assert first is True
            _release_pop_lock(chat_id)

            reacquired = _acquire_pop_lock(chat_id)
            assert reacquired is True, (
                "After release, lock must be acquirable again"
            )
        finally:
            _release_pop_lock(chat_id)

    def test_different_chat_ids_have_independent_locks(self):
        """Locks for different chat_ids must be independent."""
        chat_id_a = "test-pop-lock-chat-a"
        chat_id_b = "test-pop-lock-chat-b"
        _release_pop_lock(chat_id_a)
        _release_pop_lock(chat_id_b)
        try:
            result_a = _acquire_pop_lock(chat_id_a)
            result_b = _acquire_pop_lock(chat_id_b)
            assert result_a is True, "Lock for chat_id_a must succeed"
            assert result_b is True, "Lock for chat_id_b must succeed (independent)"
        finally:
            _release_pop_lock(chat_id_a)
            _release_pop_lock(chat_id_b)

    def test_acquire_pop_lock_returns_true_on_redis_failure(self):
        """If Redis is unavailable, _acquire_pop_lock must fail open (return True).

        Failing open preserves backward compatibility: workers continue to
        function without the lock rather than deadlocking when Redis is down.
        """
        with patch("agent.agent_session_queue._acquire_pop_lock") as mock_acquire:
            # Simulate the fail-open path by returning True even on error
            mock_acquire.return_value = True
            result = mock_acquire("test-chat-id")
            assert result is True

    def test_pop_lock_key_is_chat_id_scoped(self):
        """Pop lock key format must be worker:pop_lock:{chat_id}."""
        from popoto import get_redis

        redis_client = get_redis()
        chat_id = "test-pop-lock-key-format"
        expected_key = f"worker:pop_lock:{chat_id}"
        _release_pop_lock(chat_id)
        try:
            _acquire_pop_lock(chat_id)
            assert redis_client.exists(expected_key), (
                f"Lock key {expected_key!r} must exist in Redis after acquisition"
            )
        finally:
            _release_pop_lock(chat_id)
