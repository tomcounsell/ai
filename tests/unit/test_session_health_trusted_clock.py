"""Tests for C2 (#1817): monotonic/relative freshness, stop healing-by-clamp.

Two things are covered:

1. ``models.agent_session.AgentSession._heal_future_updated_at`` no longer
   persists (re-saves) a clamped ``updated_at`` -- the re-save reshuffled the
   ``created_at``-based sorted index on every heal. Detection-only now.
2. ``agent.session_health._has_progress`` sources "now" from Redis's own
   clock (``_trusted_utc_now()``) rather than the reader's local wall-clock,
   so a reader whose clock is skewed ahead of the writer's does not flag a
   genuinely fresh session as stale.
"""

import inspect
from datetime import UTC, datetime, timedelta

from agent.session_health import _has_progress
from models.agent_session import AgentSession


class TestHealNoLongerReSaves:
    """_heal_future_updated_at() must be detection-only -- no record.save()."""

    def test_heal_function_source_contains_no_save_call(self):
        """Function-scoped source-code assertion (mirrors the anti-criterion
        grep used in CI): the body of _heal_future_updated_at must not
        contain a `record.save()` call, anywhere, as plain text."""
        source = inspect.getsource(AgentSession._heal_future_updated_at)
        assert "record.save()" not in source, (
            "_heal_future_updated_at must not persist a clamped updated_at -- "
            "re-saving reshuffles the created_at-based index (C2, #1817)"
        )

    @staticmethod
    def _seed_future_updated_at(session: AgentSession, offset_hours: int = 7) -> datetime:
        """Directly write a future updated_at into the Redis hash for the
        given session, bypassing save()'s utc_now() stamping -- the same
        seed technique used by tests/integration/test_updated_at_heal.py,
        needed because there is no other way to reproduce the pre-fix
        future-dated condition through the ORM."""
        import msgpack
        import popoto.redis_db as rdb

        future_dt = datetime.now(UTC) + timedelta(hours=offset_hours)
        encoded = msgpack.packb(
            {"__datetime__": True, "as_encodable": future_dt.strftime("%Y%m%dT%H:%M:%S.%f")}
        )
        rdb.POPOTO_REDIS_DB.hset(session._redis_key, "updated_at", encoded)
        return future_dt

    def test_heal_detects_but_does_not_persist_clamp(self, redis_test_db):
        """A future-dated record is detected (counted) but its persisted
        updated_at in Redis is left completely untouched -- no clamp,
        no re-save, no index reshuffle."""
        session = AgentSession(
            session_id="c2-heal-future-1",
            project_key="test-c2",
            status="completed",
        )
        session.save()
        future_dt = self._seed_future_updated_at(session)

        count = AgentSession._heal_future_updated_at()
        assert count >= 1, f"Expected the seeded future record to be detected, got count={count}"

        reloaded = AgentSession.get_by_id(session.id)
        reloaded_updated_at = reloaded.updated_at
        if reloaded_updated_at.tzinfo is None:
            reloaded_updated_at = reloaded_updated_at.replace(tzinfo=UTC)

        assert reloaded_updated_at > datetime.now(UTC), (
            "The persisted updated_at must STILL be future-dated after heal -- "
            "the heal must not clamp/re-save it (C2, #1817)"
        )
        # Within a second of the originally-seeded value (round-trip precision).
        assert abs((reloaded_updated_at - future_dt).total_seconds()) < 1


class TestHasProgressTrustedClock:
    """_has_progress must not false-flag a fresh session as stale when the
    READER's local clock is skewed ahead of the writer's.

    ``datetime.datetime`` is an immutable C type -- ``.now`` cannot be
    monkeypatched directly. Instead we swap the module-level ``datetime``
    name in ``agent.session_health`` for a subclass whose ``.now()`` is
    skewed; every timestamp handed to ``_has_progress`` is constructed as an
    instance of that SAME subclass (via ``_skewed_instant``) so the
    module's ``isinstance(x, datetime)`` gates keep matching -- only the
    ``.now()`` classmethod's return value is actually skewed, not the
    identity/type semantics of the values already stored on the entry.
    """

    class _SkewedDatetime(datetime):
        """A datetime subclass whose .now() is skewed ahead by a fixed
        offset; every other datetime behavior (isinstance, arithmetic,
        construction) is inherited unchanged from the real class."""

        _skew_seconds = 0

        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(seconds=cls._skew_seconds)

    @classmethod
    def _skewed_instant(cls, dt: datetime):
        """Re-wrap a plain datetime value as an instance of
        ``_SkewedDatetime`` (same wall-clock value, just the subclass) so it
        satisfies isinstance checks against the patched module-level name."""
        return cls._SkewedDatetime(
            dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second, dt.microsecond, dt.tzinfo
        )

    @classmethod
    def _make_entry(cls, **overrides):
        from types import SimpleNamespace

        defaults = {
            "turn_count": 0,
            "log_path": "",
            "claude_session_uuid": None,
            "last_tool_use_at": None,
            "last_turn_at": None,
            "last_heartbeat_at": None,
            "last_sdk_heartbeat_at": None,
            "last_stdout_at": None,
            "started_at": None,
            "created_at": None,
        }
        defaults.update(overrides)
        for key, value in defaults.items():
            if isinstance(value, datetime):
                defaults[key] = cls._skewed_instant(value)
        entry = SimpleNamespace(**defaults)
        entry.get_children = lambda: []
        return entry

    def _patch_skew(self, monkeypatch, seconds: int):
        import agent.session_health as session_health_module

        self._SkewedDatetime._skew_seconds = seconds
        monkeypatch.setattr(session_health_module, "datetime", self._SkewedDatetime)

    def test_fresh_heartbeat_not_flagged_stale_under_90s_reader_skew(
        self, monkeypatch, redis_test_db
    ):
        """Reader's local clock (every ``datetime.now()`` call inside the
        module, including the fallback path of ``_trusted_utc_now`` and
        ``_never_started_past_grace``) is skewed 90s ahead of real time --
        matching the HEARTBEAT_FRESHNESS_WINDOW itself. A heartbeat written
        20s ago by real wall-clock time is genuinely fresh and must still
        register as progress: with Redis reachable, ``_trusted_utc_now()``
        sources "now" from Redis TIME (unaffected by the skew), not from the
        reader's skewed ``datetime.now()``.
        """
        self._patch_skew(monkeypatch, seconds=90)

        real_now = datetime.now(UTC)  # unpatched (this test file's own import) — true time
        entry = self._make_entry(
            last_heartbeat_at=real_now - timedelta(seconds=20),
            started_at=real_now - timedelta(seconds=10),
        )

        assert _has_progress(entry) is True, (
            "A genuinely fresh (20s-old) heartbeat must register as progress "
            "even when the reader's local clock is skewed 90s ahead -- "
            "_has_progress must source 'now' from Redis TIME, not local "
            "datetime.now()"
        )

    def test_control_without_skew_still_fresh(self, monkeypatch, redis_test_db):
        """Control: with zero skew, the same fresh heartbeat is progress too
        (proves the assertion above isn't vacuously true)."""
        self._patch_skew(monkeypatch, seconds=0)

        real_now = datetime.now(UTC)
        entry = self._make_entry(
            last_heartbeat_at=real_now - timedelta(seconds=20),
            started_at=real_now - timedelta(seconds=10),
        )

        assert _has_progress(entry) is True

    def test_genuinely_stale_heartbeat_still_flagged_without_skew(self, monkeypatch, redis_test_db):
        """Control: a heartbeat that is ACTUALLY stale (no skew involved)
        must still be treated as no-progress by sub-check B -- the
        trusted-clock fix must not make staleness detection permissive."""
        self._patch_skew(monkeypatch, seconds=0)

        real_now = datetime.now(UTC)
        entry = self._make_entry(
            last_heartbeat_at=real_now - timedelta(seconds=200),  # > 90s window
            started_at=real_now - timedelta(seconds=5000),  # well past budget
        )

        assert _has_progress(entry) is False


class TestTrustedUtcNowHelper:
    """Direct tests for the _trusted_utc_now() helper itself."""

    def test_returns_aware_utc_datetime_close_to_real_time(self, redis_test_db):
        from agent.session_health import _trusted_utc_now

        before = datetime.now(UTC)
        result = _trusted_utc_now()
        after = datetime.now(UTC)

        assert result.tzinfo is not None
        # Redis TIME and local wall-clock should agree within a couple of
        # seconds on the same machine (no injected skew in this test).
        assert before - timedelta(seconds=5) <= result <= after + timedelta(seconds=5)

    def test_falls_back_to_local_clock_on_redis_error(self, monkeypatch):
        """A Redis TIME failure must not raise -- falls back to local now()."""
        import agent.session_health as session_health_module

        class _BoomRedis:
            def time(self):
                raise ConnectionError("simulated Redis outage")

        monkeypatch.setattr(
            "popoto.redis_db.POPOTO_REDIS_DB",
            _BoomRedis(),
        )

        result = session_health_module._trusted_utc_now()
        assert isinstance(result, datetime)
        assert abs((result - datetime.now(UTC)).total_seconds()) < 5
