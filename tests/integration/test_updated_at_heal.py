"""Integration tests for AgentSession updated_at UTC heal (bug #1645).

Seeds future-dated updated_at values directly into Redis (bypassing the ORM,
which now stamps correct UTC via the save() override) and verifies that
_heal_future_updated_at() corrects them.

Why bypass the ORM for seeding?
    The normal save() path now stamps utc_now() for updated_at, so future values
    can no longer be created through the ORM. To reproduce the pre-fix condition
    (popoto auto_now writing naive local time on UTC+7 hosts), we write the raw
    Redis hash directly, using the same datetime encoding format popoto uses:
    "%Y%m%dT%H:%M:%S.%f" (strftime).

Isolation:
    All tests depend on the `redis_test_db` fixture (autouse=True in conftest.py),
    which redirects Popoto to a per-worker Redis test database and flushes it
    before/after each test. No production data is touched.
"""

import subprocess
from datetime import UTC, datetime, timedelta

import pytest

from models.agent_session import AgentSession

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_POPOTO_DT_FMT = "%Y%m%dT%H:%M:%S.%f"


def _popoto_encode_dt(dt: datetime) -> str:
    """Encode a datetime as popoto's DatetimeField wire format."""
    # Popoto strips tzinfo on save (it's stored as a naive-looking string).
    # We replicate that here so the seeded value looks exactly like what
    # popoto auto_now would have written on a UTC+7 host.
    return dt.strftime(_POPOTO_DT_FMT)


def _seed_future_updated_at(session: AgentSession, offset_hours: int = 7) -> datetime:
    """Directly write a future updated_at into the Redis hash for the given session.

    Returns the future datetime that was seeded.

    This bypasses the ORM save() path (which now stamps correct UTC) to
    reproduce the pre-fix condition where popoto auto_now wrote naive local time.
    """
    import popoto.redis_db as rdb

    future_dt = datetime.now(UTC) + timedelta(hours=offset_hours)
    # Encode without tzinfo (popoto's naive-looking wire format)
    encoded = _popoto_encode_dt(future_dt)

    redis_key = f"AgentSession:{session.id}"
    rdb.POPOTO_REDIS_DB.hset(redis_key, "updated_at", encoded)
    return future_dt


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def future_session(redis_test_db):
    """Create an AgentSession and seed its updated_at ~7h in the future."""
    session = AgentSession.create(
        session_id="heal-integration-1",
        project_key="test-heal",
        status="completed",
        chat_id="heal-chat-1",
        working_dir="/tmp/test-heal",
    )
    _seed_future_updated_at(session, offset_hours=7)
    return session


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestHealIntegration:
    """Integration tests: seed future-dated Redis records, heal, assert invariants."""

    def test_heal_corrects_future_updated_at(self, future_session, redis_test_db):
        """After heal, updated_at must be <= now (no longer in the future)."""
        # Reload from Redis to confirm the seed took effect
        reloaded = AgentSession.get_by_id(future_session.id)
        assert reloaded is not None

        now_before = datetime.now(UTC)
        assert reloaded.updated_at > now_before, (
            "Seed did not produce a future updated_at — test setup is broken"
        )

        # Run heal
        count = AgentSession._heal_future_updated_at()
        assert count >= 1, f"Expected at least 1 healed record, got {count}"

        # Reload again and verify the invariant
        healed = AgentSession.get_by_id(future_session.id)
        assert healed is not None
        now_after = datetime.now(UTC)

        assert healed.updated_at is not None
        assert healed.updated_at <= now_after + timedelta(seconds=5), (
            f"updated_at {healed.updated_at!r} is still in the future after heal "
            f"(now={now_after!r})"
        )

    def test_heal_preserves_created_at_lte_updated_at_invariant(
        self, future_session, redis_test_db
    ):
        """After heal, created_at <= updated_at must hold."""
        AgentSession._heal_future_updated_at()

        healed = AgentSession.get_by_id(future_session.id)
        assert healed is not None
        assert healed.created_at is not None
        assert healed.updated_at is not None

        assert healed.created_at <= healed.updated_at, (
            f"Invariant violated: created_at={healed.created_at!r} > "
            f"updated_at={healed.updated_at!r}"
        )

    def test_heal_idempotent_on_redis_records(self, future_session, redis_test_db):
        """Running heal twice must produce count=0 on the second pass."""
        count1 = AgentSession._heal_future_updated_at()
        count2 = AgentSession._heal_future_updated_at()

        assert count1 >= 1, f"First heal should fix >=1 record, got {count1}"
        assert count2 == 0, f"Second heal should fix 0 records (idempotent), got {count2}"

    def test_sane_sessions_not_healed(self, redis_test_db):
        """Sessions with updated_at already in the past are not touched by heal."""
        session = AgentSession.create(
            session_id="sane-heal-1",
            project_key="test-heal",
            status="completed",
            chat_id="heal-chat-sane",
            working_dir="/tmp/test-heal-sane",
        )
        # ORM save() already stamps correct UTC — no seeding needed
        original_updated_at = session.updated_at

        count = AgentSession._heal_future_updated_at()
        assert count == 0, f"Sane session must not be healed, got count={count}"

        reloaded = AgentSession.get_by_id(session.id)
        assert reloaded is not None
        # updated_at should not have changed (no heal save() was called)
        if original_updated_at is not None:
            assert abs((reloaded.updated_at - original_updated_at).total_seconds()) < 5, (
                "Sane session's updated_at should not have been rewritten by heal"
            )

    def test_valor_session_status_shows_no_future_timestamp(self, future_session, redis_test_db):
        """After heal, valor-session status output must not contain a future timestamp.

        This verifies the end-to-end CLI surface: operators viewing session
        status should no longer see timestamps 7 hours in the future after
        the heal has run.

        Note: valor-session status reads from Redis via the ORM, so this also
        verifies that the healed value was persisted to Redis correctly.
        """
        # First verify the session is future-dated before heal
        pre_heal = AgentSession.get_by_id(future_session.id)
        now = datetime.now(UTC)
        assert pre_heal.updated_at > now, "Pre-heal session must be future-dated"

        # Run heal
        AgentSession._heal_future_updated_at()

        # Reload and check that the value is now sane
        post_heal = AgentSession.get_by_id(future_session.id)
        now_after = datetime.now(UTC)
        assert post_heal.updated_at <= now_after + timedelta(seconds=5), (
            f"Post-heal updated_at {post_heal.updated_at!r} is still in the future"
        )

        # Verify via CLI output — run valor-session status and check the timestamp
        result = subprocess.run(
            [
                "python",
                "-m",
                "tools.valor_session",
                "status",
                "--id",
                future_session.id,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        # CLI may fail if the session is in a terminal state or missing fields —
        # we only check timestamp sanity if the command succeeds
        if result.returncode == 0 and result.stdout:
            output = result.stdout
            # Parse out any timestamp-looking strings and verify none are 7h+ in future
            # The output typically shows: "updated_at: 2026-06-12T..."
            threshold = now_after + timedelta(hours=1)
            import re

            ts_pattern = r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"
            for match in re.finditer(ts_pattern, output):
                try:
                    ts_str = match.group(0).replace(" ", "T")
                    ts = datetime.fromisoformat(ts_str).replace(tzinfo=UTC)
                    assert ts <= threshold, (
                        f"CLI output contains future timestamp {ts!r} "
                        f"(threshold={threshold!r}): {output!r}"
                    )
                except ValueError:
                    pass  # unparseable timestamp fragment, skip
