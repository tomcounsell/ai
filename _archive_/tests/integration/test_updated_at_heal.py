"""Integration tests for AgentSession updated_at UTC heal (bug #1645, revised C2 #1817).

_heal_future_updated_at() is detection-only: it logs and counts future-dated
`updated_at` records but never mutates or re-saves them. C2 (#1817): the heal
used to clamp a future-dated updated_at down to now and re-save() it, which
reshuffled the created_at-based sorted index on every heal -- a real hazard,
not a cure. Health staleness reads no longer depend on this heal having run --
see agent/session_health.py's _trusted_utc_now() (Redis TIME, not local
wall-clock), which makes a still-future-dated (skew-written) updated_at
harmless to read even though it's never clamped.

Fixtures write through the ORM and read back through the ORM:
`AgentSession.create(...)`, assign a future `updated_at`, then
`save(preserve_updated_at=True)` -- the flag is load-bearing, since a plain
`save()` re-stamps `updated_at` to `utc_now()` and the fixture would stop
being future-dated at all. popoto 1.9.0 decodes every stored datetime as
aware UTC, so the fixture never needs to seed raw Redis or reason about
legacy msgpack encoding.

Isolation:
    All tests depend on the `redis_test_db` fixture (autouse=True in conftest.py),
    which redirects Popoto to a per-worker Redis test database and flushes it
    before/after each test. No production data is touched.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from models.agent_session import AgentSession

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def future_session(redis_test_db):
    """Create an AgentSession and set its updated_at ~7h in the future, through the ORM."""
    uid = uuid.uuid4().hex[:8]
    session = AgentSession.create(
        session_id=f"heal-integration-{uid}",
        project_key="test-heal",
        status="completed",
        chat_id=f"heal-chat-{uid}",
        working_dir="/tmp/test-heal",
    )
    session.updated_at = datetime.now(UTC) + timedelta(hours=7)
    session.save(preserve_updated_at=True)
    return session


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestHealIntegration:
    """Integration tests: an ORM-seeded future-dated record is detected but
    never mutated or persisted with a clamp."""

    def test_heal_detects_future_updated_at_without_persisting_a_clamp(
        self, future_session, redis_test_db
    ):
        """Heal counts the future-dated record, but its persisted updated_at
        in Redis is left completely untouched -- still future-dated."""
        reloaded = AgentSession.get_by_id(future_session.id)
        assert reloaded is not None

        now_before = datetime.now(UTC)
        assert reloaded.updated_at > now_before, (
            "Seed did not produce a future updated_at — test setup is broken. "
            f"updated_at={reloaded.updated_at!r}, now={now_before!r}"
        )

        count = AgentSession._heal_future_updated_at()
        assert count == 1, f"Expected exactly 1 detected record, got {count}"

        # Reload again — the persisted value must be UNCHANGED (still future).
        after_heal = AgentSession.get_by_id(future_session.id)
        assert after_heal is not None
        assert abs((after_heal.updated_at - reloaded.updated_at).total_seconds()) < 1, (
            "The persisted updated_at must not change -- heal is detection-only "
            f"(before={reloaded.updated_at!r}, after={after_heal.updated_at!r})"
        )
        assert after_heal.updated_at > datetime.now(UTC), (
            "The record must STILL be future-dated after heal (no clamp/re-save)"
        )

    def test_heal_does_not_touch_created_at(self, future_session, redis_test_db):
        """created_at is never read or mutated by the detection-only heal."""
        before = AgentSession.get_by_id(future_session.id)
        original_created_at = before.created_at

        AgentSession._heal_future_updated_at()

        after = AgentSession.get_by_id(future_session.id)
        assert after.created_at == original_created_at

    def test_heal_repeated_calls_agree_on_redis_records(self, future_session, redis_test_db):
        """Since nothing is persisted, repeated heal calls against an
        unchanged future-dated record detect it every time (not just once)."""
        count1 = AgentSession._heal_future_updated_at()
        count2 = AgentSession._heal_future_updated_at()

        assert count1 >= 1, f"First heal should detect >=1 record, got {count1}"
        assert count2 == count1, (
            f"Repeated detection of an unmutated record must agree: {count1} != {count2}"
        )

    def test_sane_sessions_not_detected(self, redis_test_db):
        """Sessions with updated_at already in the past are not counted or touched."""
        uid = uuid.uuid4().hex[:8]
        session = AgentSession.create(
            session_id=f"sane-heal-{uid}",
            project_key="test-heal",
            status="completed",
            chat_id=f"heal-chat-sane-{uid}",
            working_dir="/tmp/test-heal-sane",
        )
        # ORM save() already stamps correct UTC — no seeding needed
        original_updated_at = session.updated_at

        count = AgentSession._heal_future_updated_at()
        assert count == 0, f"Sane session must not be detected, got count={count}"

        reloaded = AgentSession.get_by_id(session.id)
        assert reloaded is not None
        if original_updated_at is not None:
            assert abs((reloaded.updated_at - original_updated_at).total_seconds()) < 5, (
                "Sane session's updated_at should not have been rewritten by heal"
            )
