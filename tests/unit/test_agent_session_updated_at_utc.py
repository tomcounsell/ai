"""Unit tests for AgentSession.save() UTC timestamp stamping and _heal_future_updated_at.

Covers bug #1645: popoto auto_now minted naive local time; fix stamps UTC via save() override.

Tests are pure-logic (no Redis) — they patch save() to avoid touching Redis.
"""

import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from models.agent_session import AgentSession

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session_no_save(**kwargs):
    """Instantiate an AgentSession without touching Redis.

    Patches save() at the class level during construction so test sessions
    are pure in-memory objects.
    """
    defaults = {
        "project_key": "test-utc",
        "chat_id": "chat-utc-1",
        "session_id": "sid-utc-1",
        "working_dir": "/tmp/test-utc",
    }
    defaults.update(kwargs)
    original_save = AgentSession.save
    AgentSession.save = lambda self, *a, **kw: None
    try:
        s = AgentSession(**defaults)
    finally:
        AgentSession.save = original_save
    return s


# ---------------------------------------------------------------------------
# Tests: save() stamps UTC
# ---------------------------------------------------------------------------


class TestSaveStampsUTC:
    """save() override always stamps a tz-aware UTC datetime into updated_at."""

    def test_save_stamps_utc_aware_datetime(self):
        """save() must set updated_at to a tz-aware UTC datetime (not naive local)."""
        s = _make_session_no_save()
        save_calls = []

        def fake_super_save(self, *args, update_fields=None, **kwargs):
            save_calls.append((args, update_fields, kwargs))

        with patch.object(AgentSession.__bases__[0], "save", fake_super_save, create=True):
            # Directly invoke our override (super() is patched, no Redis call)
            before = datetime.now(UTC)
            AgentSession.save(s)
            after = datetime.now(UTC)

        assert s.updated_at is not None, "updated_at must not be None after save()"
        assert s.updated_at.tzinfo is not None, (
            "updated_at must be tz-aware (was naive — the bug this test catches)"
        )
        assert s.updated_at.tzinfo == UTC, (
            f"updated_at must be UTC, got tzinfo={s.updated_at.tzinfo!r}"
        )
        assert before <= s.updated_at <= after, (
            "updated_at must be close to wall-clock now, "
            f"got {s.updated_at!r} outside [{before!r}, {after!r}]"
        )

    def test_save_stamps_updated_at_not_naive_local(self):
        """updated_at after save() must be tz-aware, ruling out naive datetime.now()."""
        s = _make_session_no_save()
        s.updated_at = None

        with patch.object(AgentSession.__bases__[0], "save", lambda self, *a, **kw: None):
            AgentSession.save(s)

        # The critical invariant: tzinfo is set (not naive)
        assert s.updated_at is not None
        assert s.updated_at.tzinfo is not None, (
            "Bug #1645: popoto auto_now minted naive datetime.now(); "
            "our override must mint tz-aware UTC instead"
        )

    def test_save_updates_updated_at_monotonically(self):
        """Each successive save() call must produce an equal-or-later updated_at."""
        s = _make_session_no_save()

        timestamps = []

        def capture_save(self, *args, update_fields=None, **kwargs):
            timestamps.append(self.updated_at)

        with patch.object(AgentSession.__bases__[0], "save", capture_save):
            AgentSession.save(s)
            t1 = s.updated_at
            AgentSession.save(s)
            t2 = s.updated_at

        assert t2 >= t1, f"Second save must not go backwards: {t1!r} → {t2!r}"

    def test_save_delegates_to_super(self):
        """save() must call super().save() exactly once with the same positional args."""
        s = _make_session_no_save()
        super_calls = []

        def recording_super_save(self, *args, update_fields=None, **kwargs):
            super_calls.append({"args": args, "update_fields": update_fields, "kwargs": kwargs})

        with patch.object(AgentSession.__bases__[0], "save", recording_super_save):
            AgentSession.save(s, update_fields=["status", "updated_at"])

        assert len(super_calls) == 1, f"Expected 1 super().save() call, got {len(super_calls)}"
        assert super_calls[0]["update_fields"] == ["status", "updated_at"]


# ---------------------------------------------------------------------------
# Tests: save() update_fields guard
# ---------------------------------------------------------------------------


class TestSaveUpdateFieldsGuard:
    """save() must NOT mutate updated_at when update_fields omits it."""

    def test_save_skips_stamp_when_updated_at_not_in_update_fields(self, caplog):
        """If update_fields omits 'updated_at', in-memory value must NOT be overwritten."""
        s = _make_session_no_save()
        # Set a known sentinel value
        sentinel = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
        s.updated_at = sentinel

        super_save_called = []

        def recording_super_save(self, *args, update_fields=None, **kwargs):
            super_save_called.append(update_fields)

        with caplog.at_level(logging.WARNING, logger="models.agent_session"):
            with patch.object(AgentSession.__bases__[0], "save", recording_super_save):
                AgentSession.save(s, update_fields=["status"])

        # updated_at must NOT have been overwritten
        assert s.updated_at == sentinel, (
            f"updated_at was mutated to {s.updated_at!r} even though "
            "update_fields=['status'] did not include 'updated_at'"
        )
        # A warning must be logged
        assert any("updated_at" in record.message for record in caplog.records), (
            "Expected a warning about missing 'updated_at' in update_fields"
        )
        # super().save() still called once (partial save still happens)
        assert len(super_save_called) == 1

    def test_save_stamps_when_updated_at_in_update_fields(self):
        """If update_fields includes 'updated_at', stamp must happen."""
        s = _make_session_no_save()
        old_ts = datetime(2020, 1, 1, tzinfo=UTC)
        s.updated_at = old_ts

        with patch.object(AgentSession.__bases__[0], "save", lambda self, *a, **kw: None):
            AgentSession.save(s, update_fields=["status", "updated_at"])

        assert s.updated_at > old_ts, (
            f"updated_at should have been stamped forward from {old_ts!r}, got {s.updated_at!r}"
        )

    def test_save_stamps_when_update_fields_is_none(self):
        """update_fields=None (the default full-save path) must stamp updated_at."""
        s = _make_session_no_save()
        old_ts = datetime(2020, 1, 1, tzinfo=UTC)
        s.updated_at = old_ts

        with patch.object(AgentSession.__bases__[0], "save", lambda self, *a, **kw: None):
            AgentSession.save(s)  # update_fields defaults to None

        assert s.updated_at > old_ts, "Full save (update_fields=None) must stamp updated_at"


# ---------------------------------------------------------------------------
# Tests: _heal_future_updated_at
# ---------------------------------------------------------------------------


class TestHealFutureUpdatedAt:
    """_heal_future_updated_at() must clamp future-dated records to now."""

    def _build_records(self, records_spec):
        """Build in-memory AgentSession instances from a list of (updated_at, created_at) pairs."""
        sessions = []
        for spec in records_spec:
            s = _make_session_no_save()
            s.updated_at = spec.get("updated_at")
            s.created_at = spec.get("created_at")
            s.id = spec.get("id", f"test-{id(s)}")
            sessions.append(s)
        return sessions

    def test_heal_clamps_future_record(self):
        """A session with updated_at 7h in the future must be healed."""
        now = datetime.now(UTC)
        future = now + timedelta(hours=7)
        past = now - timedelta(minutes=30)

        session = _make_session_no_save()
        session.updated_at = future
        session.created_at = past
        session.id = "future-session-1"

        save_calls = []

        def mock_save(self, *args, update_fields=None, **kwargs):
            # Simulate what our real save() does: stamp utc_now()
            from bridge.utc import utc_now

            if update_fields is None or "updated_at" in update_fields:
                self.updated_at = utc_now()
            save_calls.append(update_fields)

        with (
            patch.object(AgentSession, "query") as mock_query,
            patch.object(AgentSession, "save", mock_save),
        ):
            mock_query.all.return_value = [session]
            count = AgentSession._heal_future_updated_at()

        assert count == 1, f"Expected 1 healed record, got {count}"
        assert len(save_calls) == 1, "save() must be called once for the healed record"
        assert session.updated_at <= datetime.now(UTC) + timedelta(seconds=2), (
            f"updated_at should be near-now after heal, got {session.updated_at!r}"
        )

    def test_heal_skips_sane_records(self):
        """Sessions with updated_at in the past must not be touched."""
        now = datetime.now(UTC)
        past = now - timedelta(hours=2)

        session = _make_session_no_save()
        session.updated_at = past
        session.created_at = past - timedelta(minutes=5)
        session.id = "sane-session"

        save_calls = []

        def mock_save(self, *args, **kwargs):
            save_calls.append(True)

        with (
            patch.object(AgentSession, "query") as mock_query,
            patch.object(AgentSession, "save", mock_save),
        ):
            mock_query.all.return_value = [session]
            count = AgentSession._heal_future_updated_at()

        assert count == 0, "Sane record must not be counted as healed"
        assert len(save_calls) == 0, "save() must not be called for a sane record"
        # updated_at must be unchanged
        assert session.updated_at == past

    def test_heal_skips_none_updated_at(self):
        """Sessions with updated_at=None must be left untouched."""
        session = _make_session_no_save()
        session.updated_at = None
        session.created_at = datetime.now(UTC) - timedelta(hours=1)
        session.id = "none-ts-session"

        save_calls = []

        def mock_save(self, *args, **kwargs):
            save_calls.append(True)

        with (
            patch.object(AgentSession, "query") as mock_query,
            patch.object(AgentSession, "save", mock_save),
        ):
            mock_query.all.return_value = [session]
            count = AgentSession._heal_future_updated_at()

        assert count == 0
        assert len(save_calls) == 0
        assert session.updated_at is None

    def test_heal_is_idempotent(self):
        """Running heal twice must produce count=0 on the second run."""
        now = datetime.now(UTC)
        future = now + timedelta(hours=7)

        session = _make_session_no_save()
        session.updated_at = future
        session.created_at = now - timedelta(minutes=10)
        session.id = "idempotent-session"

        def mock_save(self, *args, update_fields=None, **kwargs):
            from bridge.utc import utc_now

            if update_fields is None or "updated_at" in update_fields:
                self.updated_at = utc_now()

        with (
            patch.object(AgentSession, "query") as mock_query,
            patch.object(AgentSession, "save", mock_save),
        ):
            mock_query.all.return_value = [session]

            count1 = AgentSession._heal_future_updated_at()
            # After first heal, updated_at is now near-current
            # Second call should see updated_at <= now, skip it
            count2 = AgentSession._heal_future_updated_at()

        assert count1 == 1, f"First heal should fix 1 record, got {count1}"
        assert count2 == 0, f"Second heal should fix 0 records (idempotent), got {count2}"

    def test_heal_handles_created_at_none_with_future_updated_at(self):
        """created_at=None with future updated_at — heal must clamp to now."""
        now = datetime.now(UTC)
        future = now + timedelta(hours=5)

        session = _make_session_no_save()
        session.updated_at = future
        session.created_at = None
        session.id = "no-created-at-session"

        def mock_save(self, *args, update_fields=None, **kwargs):
            from bridge.utc import utc_now

            if update_fields is None or "updated_at" in update_fields:
                self.updated_at = utc_now()

        with (
            patch.object(AgentSession, "query") as mock_query,
            patch.object(AgentSession, "save", mock_save),
        ):
            mock_query.all.return_value = [session]
            count = AgentSession._heal_future_updated_at()

        assert count == 1
        assert session.updated_at <= datetime.now(UTC) + timedelta(seconds=2)

    def test_heal_dual_future_case_invariant(self):
        """Dual-future: both created_at and updated_at in the future.

        After heal the invariant created_at <= updated_at <= now must hold.
        """
        now = datetime.now(UTC)
        future_updated = now + timedelta(hours=7)
        future_created = now + timedelta(hours=7)  # same offset — both naive-local stamped

        session = _make_session_no_save()
        session.updated_at = future_updated
        session.created_at = future_created
        session.id = "dual-future-session"

        def mock_save(self, *args, update_fields=None, **kwargs):
            from bridge.utc import utc_now

            current_now = utc_now()
            if update_fields is None or "updated_at" in update_fields:
                self.updated_at = current_now
            # created_at clamping is done before save() is called in the heal loop

        with (
            patch.object(AgentSession, "query") as mock_query,
            patch.object(AgentSession, "save", mock_save),
        ):
            mock_query.all.return_value = [session]
            count = AgentSession._heal_future_updated_at()

        assert count == 1
        # created_at was clamped to now by the heal loop before save()
        real_now = datetime.now(UTC)
        assert session.created_at <= real_now + timedelta(seconds=2), (
            f"created_at should be clamped to ~now, got {session.created_at!r}"
        )
        assert session.updated_at <= real_now + timedelta(seconds=2), (
            f"updated_at should be clamped to ~now, got {session.updated_at!r}"
        )

    def test_heal_returns_zero_on_query_failure(self, caplog):
        """If query.all() raises, heal must return 0 (fail-soft)."""
        with patch.object(AgentSession, "query") as mock_query:
            mock_query.all.side_effect = RuntimeError("Redis connection error")
            with caplog.at_level(logging.WARNING, logger="models.agent_session"):
                count = AgentSession._heal_future_updated_at()

        assert count == 0
        assert any("could not fetch sessions" in r.message for r in caplog.records)

    def test_heal_skips_record_on_save_error(self, caplog):
        """If a single record's save() raises, heal skips that record and continues."""
        now = datetime.now(UTC)
        future = now + timedelta(hours=7)

        bad = _make_session_no_save()
        bad.updated_at = future
        bad.created_at = now - timedelta(minutes=5)
        bad.id = "bad-save-session"

        good = _make_session_no_save()
        good.updated_at = future
        good.created_at = now - timedelta(minutes=5)
        good.id = "good-save-session"

        call_count = [0]

        def mock_save(self, *args, update_fields=None, **kwargs):
            call_count[0] += 1
            if self.id == "bad-save-session":
                raise RuntimeError("Redis write error")
            from bridge.utc import utc_now

            if update_fields is None or "updated_at" in update_fields:
                self.updated_at = utc_now()

        with (
            patch.object(AgentSession, "query") as mock_query,
            patch.object(AgentSession, "save", mock_save),
        ):
            mock_query.all.return_value = [bad, good]
            with caplog.at_level(logging.WARNING, logger="models.agent_session"):
                count = AgentSession._heal_future_updated_at()

        # Only the good record was successfully healed
        assert count == 1
        assert any("skipped" in r.message for r in caplog.records)
