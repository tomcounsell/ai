"""
Integration tests for session_type parameter flow through the session queue.

Validates that async_create(session_type=...) and the factory method
create_eng produce AgentSession instances with the correct
session_type, and that session_type survives a Redis round-trip.

Requires: Redis running (autouse redis_test_db fixture handles isolation).
"""

import time
from datetime import UTC, datetime

import pytest

from models.agent_session import SESSION_TYPE_ENG, SESSION_TYPE_TEAMMATE, AgentSession

# ---------------------------------------------------------------------------
# async_create vs factory method equivalence
# ---------------------------------------------------------------------------


class TestAsyncCreateMatchesFactoryMethods:
    """Sessions from async_create(session_type=...) must match factory-method results."""

    @pytest.mark.asyncio
    async def test_async_create_eng_matches_create_eng(self):
        """async_create(session_type='eng') has same flags as create_eng()."""
        shared = {
            "project_key": "test-equiv",
            "session_id": f"equiv-eng-{time.time_ns()}",
            "working_dir": "/tmp/test-equiv",
            "message_text": "equivalence test",
        }

        via_direct = await AgentSession.async_create(
            session_type="eng",
            status="pending",
            priority="normal",
            created_at=datetime.now(tz=UTC),
            sender_name="Test",
            chat_id=str(-time.time_ns() % 999_000),
            telegram_message_id=1,
            **shared,
        )
        via_factory = AgentSession.create_eng(
            chat_id=str(-time.time_ns() % 999_000),
            telegram_message_id=2,
            sender_name="Test",
            **shared,
        )

        assert via_direct.session_type == via_factory.session_type == SESSION_TYPE_ENG
        assert via_direct.is_eng == via_factory.is_eng is True
        assert via_direct.is_teammate == via_factory.is_teammate is False

    @pytest.mark.asyncio
    async def test_async_create_teammate(self):
        """async_create(session_type='teammate') produces a teammate session."""
        from config.enums import SessionType

        shared = {
            "project_key": "test-equiv",
            "session_id": f"equiv-teammate-{time.time_ns()}",
            "working_dir": "/tmp/test-equiv",
            "message_text": "teammate test",
            "sender_name": "Test",
            "chat_id": str(-time.time_ns() % 999_000),
            "telegram_message_id": 1,
        }

        session = await AgentSession.async_create(
            session_type="teammate",
            status="pending",
            priority="normal",
            created_at=datetime.now(tz=UTC),
            **shared,
        )

        assert session.session_type == SessionType.TEAMMATE
        assert session.is_teammate is True
        assert session.is_eng is False


# ---------------------------------------------------------------------------
# Redis round-trip persistence
# ---------------------------------------------------------------------------


class TestSessionTypeRoundTrip:
    """session_type must survive Redis write -> read cycle."""

    def test_eng_session_type_survives_roundtrip(self):
        """Create with session_type='eng', re-fetch -> still 'eng'."""
        session = AgentSession.create(
            project_key="test-rt",
            status="pending",
            priority="normal",
            created_at=datetime.now(tz=UTC),
            session_id=f"rt-eng-{time.time_ns()}",
            working_dir="/tmp/test",
            message_text="roundtrip eng",
            sender_name="Test",
            chat_id=str(-time.time_ns() % 999_000),
            telegram_message_id=1,
            session_type="eng",
        )

        fetched = AgentSession.query.filter(session_id=session.session_id)
        results = list(fetched)
        assert len(results) >= 1
        assert results[0].session_type == "eng"
        assert results[0].is_eng is True

    def test_teammate_session_type_survives_roundtrip(self):
        """Create with session_type='teammate', re-fetch -> still 'teammate'."""
        session = AgentSession.create(
            project_key="test-rt",
            status="pending",
            priority="normal",
            created_at=datetime.now(tz=UTC),
            session_id=f"rt-teammate-{time.time_ns()}",
            working_dir="/tmp/test",
            message_text="roundtrip teammate",
            sender_name="Test",
            chat_id=str(-time.time_ns() % 999_000),
            telegram_message_id=1,
            session_type="teammate",
        )

        fetched = AgentSession.query.filter(session_id=session.session_id)
        results = list(fetched)
        assert len(results) >= 1
        assert results[0].session_type == "teammate"
        assert results[0].is_teammate is True


# ---------------------------------------------------------------------------
# Only valid session types
# ---------------------------------------------------------------------------


class TestValidSessionTypes:
    """Document and enforce the allowed session_type values."""

    def test_valid_types_are_eng_and_teammate(self):
        """The module constants define the session types."""
        assert SESSION_TYPE_ENG == "eng"
        assert SESSION_TYPE_TEAMMATE == "teammate"

    def test_is_eng_true_for_eng_session(self):
        """is_eng property is True when session_type='eng'."""
        session = AgentSession.create(
            project_key="test",
            status="pending",
            priority="normal",
            created_at=datetime.now(tz=UTC),
            session_id=f"type-check-eng-{time.time_ns()}",
            working_dir="/tmp/test",
            message_text="test",
            sender_name="Test",
            chat_id="999",
            telegram_message_id=1,
            session_type="eng",
        )
        assert session.is_eng is True
        assert session.is_teammate is False

    def test_is_eng_false_for_teammate(self):
        """is_eng property is False when session_type='teammate'."""
        session = AgentSession.create(
            project_key="test",
            status="pending",
            priority="normal",
            created_at=datetime.now(tz=UTC),
            session_id=f"type-check-tm-{time.time_ns()}",
            working_dir="/tmp/test",
            message_text="test",
            sender_name="Test",
            chat_id="999",
            telegram_message_id=1,
            session_type="teammate",
        )
        assert session.is_eng is False
        assert session.is_teammate is True

    def test_is_teammate(self):
        """is_teammate property is True when session_type='teammate'."""
        session = AgentSession.create(
            project_key="test",
            status="pending",
            priority="normal",
            created_at=datetime.now(tz=UTC),
            session_id=f"type-check-{time.time_ns()}",
            working_dir="/tmp/test",
            message_text="test",
            sender_name="Test",
            chat_id="999",
            telegram_message_id=1,
            session_type="teammate",
        )
        assert session.is_teammate is True
        assert session.is_eng is False
