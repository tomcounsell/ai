"""
Integration tests for session_type parameter flow through the session queue.

Validates that async_create(session_type=...) and the factory methods
(create_pm, create_dev) produce equivalent AgentSession instances, and
that session_type survives a Redis round-trip.

Requires: Redis running (autouse redis_test_db fixture handles isolation).
"""

import time
from datetime import UTC, datetime

import pytest

from models.agent_session import SESSION_TYPE_DEV, SESSION_TYPE_PM, AgentSession

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
            created_at=datetime.now(tz=UTC),
            sender_name="Test",
            chat_id=str(-time.time_ns() % 999_000),
            telegram_message_id=1,
            **shared,
        )
        via_factory = AgentSession.create_dev(
            parent_session_id="parent-123",
            chat_id=str(-time.time_ns() % 999_000),
            telegram_message_id=2,
            **shared,
        )

        assert via_direct.session_type == via_factory.session_type == SESSION_TYPE_DEV
        assert via_direct.is_dev == via_factory.is_dev is True
        assert via_direct.is_pm == via_factory.is_pm is False

    @pytest.mark.asyncio
    async def test_async_create_pm_matches_create_pm(self):
        """async_create(session_type='pm') has same flags as create_pm()."""
        shared = {
            "project_key": "test-equiv",
            "session_id": f"equiv-pm-{time.time_ns()}",
            "working_dir": "/tmp/test-equiv",
            "message_text": "equivalence test",
            "sender_name": "Test",
            "chat_id": str(-time.time_ns() % 999_000),
            "telegram_message_id": 1,
        }

        via_direct = await AgentSession.async_create(
            session_type="pm",
            status="pending",
            priority="normal",
            created_at=datetime.now(tz=UTC),
            **shared,
        )
        via_factory = AgentSession.create_pm(**shared)

        assert via_direct.session_type == via_factory.session_type == SESSION_TYPE_PM
        assert via_direct.is_pm == via_factory.is_pm is True
        assert via_direct.is_dev == via_factory.is_dev is False

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
        assert session.is_pm is False
        assert session.is_dev is False


# ---------------------------------------------------------------------------
# Redis round-trip persistence
# ---------------------------------------------------------------------------


class TestSessionTypeRoundTrip:
    """session_type must survive Redis write -> read cycle."""

    def test_pm_session_type_survives_roundtrip(self):
        """Create with session_type='pm', re-fetch -> still 'pm'."""
        session = AgentSession.create(
            project_key="test-rt",
            status="pending",
            priority="normal",
            created_at=datetime.now(tz=UTC),
            session_id=f"rt-pm-{time.time_ns()}",
            working_dir="/tmp/test",
            message_text="roundtrip pm",
            sender_name="Test",
            chat_id=str(-time.time_ns() % 999_000),
            telegram_message_id=1,
            session_type="pm",
        )

        fetched = AgentSession.query.filter(session_id=session.session_id)
        results = list(fetched)
        assert len(results) >= 1
        assert results[0].session_type == "pm"
        assert results[0].is_pm is True

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

    def test_dev_session_type_survives_roundtrip(self):
        """Create with session_type='dev', re-fetch -> still 'dev'."""
        session = AgentSession.create(
            project_key="test-rt",
            status="pending",
            priority="normal",
            created_at=datetime.now(tz=UTC),
            session_id=f"rt-dev-{time.time_ns()}",
            working_dir="/tmp/test",
            message_text="roundtrip dev",
            sender_name="Test",
            chat_id=str(-time.time_ns() % 999_000),
            telegram_message_id=1,
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

    def test_valid_types_are_pm_teammate_and_dev(self):
        """The module constants define the session types."""
        assert SESSION_TYPE_PM == "pm"
        assert SESSION_TYPE_DEV == "dev"

    def test_is_pm_false_for_dev(self):
        """is_pm property is False when session_type='dev'."""
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
            session_type="dev",
        )
        assert session.is_pm is False
        assert session.is_dev is True

    def test_is_dev_false_for_pm(self):
        """is_dev property is False when session_type='pm'."""
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
            session_type="pm",
        )
        assert session.is_dev is False
        assert session.is_pm is True

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
        assert session.is_pm is False
        assert session.is_dev is False
