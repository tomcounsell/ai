"""Integration tests for AgentSession updated_at UTC heal (bug #1645, revised C2 #1817).

Seeds future-dated updated_at values directly into Redis (bypassing the ORM,
which now stamps correct UTC via the save() override) and verifies that
_heal_future_updated_at() DETECTS them without mutating or persisting a clamp.

C2 (#1817): the heal used to clamp a future-dated updated_at down to now and
re-save() it. That re-save reshuffled the created_at-based sorted index on
every heal -- a real hazard, not a cure. The heal is now detection-only:
it logs and counts future-dated records but never mutates Redis. Health
staleness reads no longer depend on this heal having run -- see
agent/session_health.py's _trusted_utc_now() (Redis TIME, not local
wall-clock), which makes a still-future-dated (skew-written) updated_at
harmless to read even though it's never clamped.

Why bypass the ORM for seeding?
    The normal save() path stamps utc_now() for updated_at, so future values
    can no longer be created through the ORM. To reproduce the pre-fix
    condition (popoto auto_now writing naive local time on UTC+7 hosts), we
    write the raw Redis hash directly.

Seed mechanism (CONCERN — Skeptic/Adversary):
    Popoto encodes datetime fields using msgpack with the schema:
        {'__datetime__': True, 'as_encodable': '20260613T00:03:13.103209'}
    and stores them in the Redis hash key at `session._redis_key`.
    The seed step writes this encoded value directly to Redis; the heal and
    all assertions still go through the ORM.

Isolation:
    All tests depend on the `redis_test_db` fixture (autouse=True in conftest.py),
    which redirects Popoto to a per-worker Redis test database and flushes it
    before/after each test. No production data is touched.
"""

import uuid
from datetime import UTC, datetime, timedelta

import msgpack
import pytest

from models.agent_session import AgentSession

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _as_utc(dt: datetime) -> datetime:
    """Attach UTC tzinfo to a naive datetime (popoto strips tzinfo on load).

    This is the same logic used by bridge/utc.py::to_unix_ts and the
    AgentSession read-back normalization: naive == UTC.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _popoto_encode_datetime(dt: datetime) -> bytes:
    """Encode a datetime as popoto's msgpack wire format.

    Popoto's DatetimeField stores datetimes as:
        msgpack({'__datetime__': True, 'as_encodable': '20260613T00:03:13.103209'})
    with tzinfo stripped (the strftime format drops tz).
    """
    dt_str = dt.strftime("%Y%m%dT%H:%M:%S.%f")
    return msgpack.packb({"__datetime__": True, "as_encodable": dt_str})


def _seed_future_updated_at(session: AgentSession, offset_hours: int = 7) -> datetime:
    """Directly write a future updated_at into the Redis hash for the given session.

    Returns the future datetime that was seeded (tz-aware UTC).

    This bypasses the ORM save() path (which now stamps correct UTC) to
    reproduce the pre-fix condition where popoto auto_now wrote naive local time.

    Uses:
    - session._redis_key  — the full composite Redis hash key
    - msgpack encoding    — the wire format popoto's DatetimeField uses
    """
    import popoto.redis_db as rdb

    future_dt = datetime.now(UTC) + timedelta(hours=offset_hours)
    encoded = _popoto_encode_datetime(future_dt)

    rdb.POPOTO_REDIS_DB.hset(session._redis_key, "updated_at", encoded)
    return future_dt


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def future_session(redis_test_db):
    """Create an AgentSession and seed its updated_at ~7h in the future."""
    uid = uuid.uuid4().hex[:8]
    session = AgentSession.create(
        session_id=f"heal-integration-{uid}",
        project_key="test-heal",
        status="completed",
        chat_id=f"heal-chat-{uid}",
        working_dir="/tmp/test-heal",
    )
    _seed_future_updated_at(session, offset_hours=7)
    return session


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestHealIntegration:
    """Integration tests: seed future-dated Redis records, run detection,
    assert nothing gets mutated or persisted."""

    def test_heal_detects_future_updated_at_without_persisting_a_clamp(
        self, future_session, redis_test_db
    ):
        """Heal counts the future-dated record, but its persisted updated_at
        in Redis is left completely untouched -- still future-dated."""
        reloaded = AgentSession.get_by_id(future_session.id)
        assert reloaded is not None

        now_before = datetime.now(UTC)
        reloaded_updated_at = _as_utc(reloaded.updated_at)
        assert reloaded_updated_at > now_before, (
            "Seed did not produce a future updated_at — test setup is broken. "
            f"updated_at={reloaded_updated_at!r}, now={now_before!r}"
        )

        count = AgentSession._heal_future_updated_at()
        assert count >= 1, f"Expected at least 1 detected record, got {count}"

        # Reload again — the persisted value must be UNCHANGED (still future).
        after_heal = AgentSession.get_by_id(future_session.id)
        assert after_heal is not None
        after_heal_updated_at = _as_utc(after_heal.updated_at)
        assert abs((after_heal_updated_at - reloaded_updated_at).total_seconds()) < 1, (
            "The persisted updated_at must not change -- heal is detection-only "
            f"(before={reloaded_updated_at!r}, after={after_heal_updated_at!r})"
        )
        assert after_heal_updated_at > datetime.now(UTC), (
            "The record must STILL be future-dated after heal (no clamp/re-save)"
        )

    def test_heal_does_not_touch_created_at(self, future_session, redis_test_db):
        """created_at is never read or mutated by the detection-only heal."""
        before = AgentSession.get_by_id(future_session.id)
        original_created_at = _as_utc(before.created_at)

        AgentSession._heal_future_updated_at()

        after = AgentSession.get_by_id(future_session.id)
        after_created_at = _as_utc(after.created_at)
        assert after_created_at == original_created_at

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
        original_updated_at = _as_utc(session.updated_at)

        count = AgentSession._heal_future_updated_at()
        assert count == 0, f"Sane session must not be detected, got count={count}"

        reloaded = AgentSession.get_by_id(session.id)
        assert reloaded is not None
        if original_updated_at is not None:
            reloaded_updated_at = _as_utc(reloaded.updated_at)
            assert abs((reloaded_updated_at - original_updated_at).total_seconds()) < 5, (
                "Sane session's updated_at should not have been rewritten by heal"
            )
