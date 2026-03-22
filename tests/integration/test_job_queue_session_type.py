"""
Integration tests for session_type parameter flow through the job queue.

Validates that async_create(session_type=...) and the factory methods
(create_chat, create_dev) produce equivalent AgentSession instances, and
that session_type survives a Redis round-trip.

Requires: Redis running (autouse redis_test_db fixture handles isolation).
"""

import time

import pytest

from models.agent_session import SESSION_TYPE_CHAT, SESSION_TYPE_DEV, AgentSession

# ---------------------------------------------------------------------------
# async_create vs factory method equivalence
# ---------------------------------------------------------------------------


class TestAsyncCreateMatchesFactoryMethods:
    """Sessions from async_create(session_type=...) must match factory-method results."""

    @pytest.mark.asyncio
    async def test_async_create_dev_matches_create_dev(self):
        """async_create(session_type='dev') has same flags as create_dev()."""
        shared = {
            "project_key": "test-equiv",
            "session_id": f"equiv-dev-{time.time_ns()}",
            "working_dir": "/tmp/test-equiv",
            "message_text": "equivalence test",
        }

        via_direct = await AgentSession.async_create(
            session_type="dev",
            status="pending",
            priority="normal",
            created_at=time.time(),
            sender_name="Test",
            chat_id=str(-time.time_ns() % 999_000),
            message_id=1,
            **shared,
        )
        via_factory = AgentSession.create_dev(
            parent_chat_session_id="parent-123",
            chat_id=str(-time.time_ns() % 999_000),
            message_id=2,
            **shared,
        )

        assert via_direct.session_type == via_factory.session_type == SESSION_TYPE_DEV
        assert via_direct.is_dev == via_factory.is_dev is True
        assert via_direct.is_chat == via_factory.is_chat is False

    @pytest.mark.asyncio
    async def test_async_create_chat_matches_create_chat(self):
        """async_create(session_type='chat') has same flags as create_chat()."""
        shared = {
            "project_key": "test-equiv",
            "session_id": f"equiv-chat-{time.time_ns()}",
            "working_dir": "/tmp/test-equiv",
            "message_text": "equivalence test",
            "sender_name": "Test",
            "chat_id": str(-time.time_ns() % 999_000),
            "message_id": 1,
        }

        via_direct = await AgentSession.async_create(
            session_type="chat",
            status="pending",
            priority="normal",
            created_at=time.time(),
            **shared,
        )
        via_factory = AgentSession.create_chat(**shared)

        assert via_direct.session_type == via_factory.session_type == SESSION_TYPE_CHAT
        assert via_direct.is_chat == via_factory.is_chat is True
        assert via_direct.is_dev == via_factory.is_dev is False


# ---------------------------------------------------------------------------
# Redis round-trip persistence
# ---------------------------------------------------------------------------


class TestSessionTypeRoundTrip:
    """session_type must survive Redis write → read cycle."""

    def test_chat_session_type_survives_roundtrip(self):
        """Create with session_type='chat', re-fetch → still 'chat'."""
        session = AgentSession.create(
            project_key="test-rt",
            status="pending",
            priority="normal",
            created_at=time.time(),
            session_id=f"rt-chat-{time.time_ns()}",
            working_dir="/tmp/test",
            message_text="roundtrip chat",
            sender_name="Test",
            chat_id=str(-time.time_ns() % 999_000),
            message_id=1,
            session_type="chat",
        )

        fetched = AgentSession.query.filter(session_id=session.session_id)
        results = list(fetched)
        assert len(results) >= 1
        assert results[0].session_type == "chat"
        assert results[0].is_chat is True

    def test_dev_session_type_survives_roundtrip(self):
        """Create with session_type='dev', re-fetch → still 'dev'."""
        session = AgentSession.create(
            project_key="test-rt",
            status="pending",
            priority="normal",
            created_at=time.time(),
            session_id=f"rt-dev-{time.time_ns()}",
            working_dir="/tmp/test",
            message_text="roundtrip dev",
            sender_name="Test",
            chat_id=str(-time.time_ns() % 999_000),
            message_id=1,
            session_type="dev",
        )

        fetched = AgentSession.query.filter(session_id=session.session_id)
        results = list(fetched)
        assert len(results) >= 1
        assert results[0].session_type == "dev"
        assert results[0].is_dev is True


# ---------------------------------------------------------------------------
# Only valid session types
# ---------------------------------------------------------------------------


class TestValidSessionTypes:
    """Document and enforce the allowed session_type values."""

    def test_valid_types_are_chat_and_dev_only(self):
        """The module constants define exactly two session types."""
        assert SESSION_TYPE_CHAT == "chat"
        assert SESSION_TYPE_DEV == "dev"

    def test_is_chat_false_for_dev(self):
        """is_chat property is False when session_type='dev'."""
        session = AgentSession.create(
            project_key="test",
            status="pending",
            priority="normal",
            created_at=time.time(),
            session_id=f"type-check-{time.time_ns()}",
            working_dir="/tmp/test",
            message_text="test",
            sender_name="Test",
            chat_id="999",
            message_id=1,
            session_type="dev",
        )
        assert session.is_chat is False
        assert session.is_dev is True

    def test_is_dev_false_for_chat(self):
        """is_dev property is False when session_type='chat'."""
        session = AgentSession.create(
            project_key="test",
            status="pending",
            priority="normal",
            created_at=time.time(),
            session_id=f"type-check-{time.time_ns()}",
            working_dir="/tmp/test",
            message_text="test",
            sender_name="Test",
            chat_id="999",
            message_id=1,
            session_type="chat",
        )
        assert session.is_dev is False
        assert session.is_chat is True
