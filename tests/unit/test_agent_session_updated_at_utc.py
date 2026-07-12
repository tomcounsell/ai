"""Unit tests for AgentSession.save() UTC timestamp stamping and _heal_future_updated_at.

Covers bug #1645: popoto auto_now minted naive local time; fix stamps UTC via save() override.

Tests are pure-logic (no Redis) — they patch save() to avoid touching Redis.
"""

import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

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
# Tests: save() liveness-field omission allowlist (DEBUG vs WARNING)
# ---------------------------------------------------------------------------


class TestSaveUpdatedAtOmissionAllowlist:
    """Liveness/detector-state partial saves log DEBUG; genuine omissions WARN.

    The allowlist (``_UPDATED_AT_OMISSION_OK_FIELDS``) downgrades the
    "missing 'updated_at'" log to DEBUG for high-frequency liveness/PID/wedge-
    detector fields whose freshness is carried elsewhere — eliminating the
    worker-log flood (especially from granite-container sessions) while keeping
    the WARNING for genuine accidental omissions.
    """

    @staticmethod
    def _save_and_capture(update_fields, caplog):
        """Run save() with super() stubbed; return captured records at DEBUG+."""
        s = _make_session_no_save()
        sentinel = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
        s.updated_at = sentinel

        with caplog.at_level(logging.DEBUG, logger="models.agent_session"):
            with patch.object(AgentSession.__bases__[0], "save", lambda self, *a, **kw: None):
                AgentSession.save(s, update_fields=update_fields)

        # The guard must never advance updated_at when the stamp is omitted —
        # the memory/Redis desync guarantee holds regardless of log level.
        assert s.updated_at == sentinel, (
            "updated_at must NOT be mutated when update_fields omits it "
            f"(update_fields={update_fields!r})"
        )
        return caplog.records

    @pytest.mark.parametrize(
        "update_fields",
        [
            ["last_turn_at"],
            ["last_stdout_at"],
            ["last_heartbeat_at"],
            ["last_sdk_heartbeat_at"],
            ["claude_pid"],
            ["harness_pid"],
            ["pm_pid"],
            ["current_tool_name", "last_tool_use_at"],
        ],
    )
    def test_allowlisted_liveness_fields_log_debug_not_warning(self, update_fields, caplog):
        """An all-allowlisted partial save logs at DEBUG, never WARNING."""
        records = self._save_and_capture(update_fields, caplog)

        assert not any(r.levelno >= logging.WARNING for r in records), (
            f"update_fields={update_fields!r} (all-allowlisted) must NOT emit a WARNING; "
            f"got {[(r.levelname, r.message) for r in records]}"
        )
        assert any(r.levelno == logging.DEBUG and "updated_at" in r.message for r in records), (
            f"Expected a DEBUG log about the omitted 'updated_at' for {update_fields!r}"
        )

    @pytest.mark.parametrize(
        "update_fields",
        [
            ["status"],
            ["turn_count"],
            ["reprieve_count"],
            ["exit_returncode"],
            # Mixed: one allowlisted + one not → still a genuine omission, WARN.
            ["last_turn_at", "status"],
            ["pm_pid", "status"],
        ],
    )
    def test_non_allowlisted_omission_still_warns(self, update_fields, caplog):
        """A non-allowlisted (or mixed) omission keeps the WARNING."""
        records = self._save_and_capture(update_fields, caplog)

        assert any(r.levelno == logging.WARNING and "updated_at" in r.message for r in records), (
            f"update_fields={update_fields!r} must still emit a WARNING about missing "
            f"'updated_at'; got {[(r.levelname, r.message) for r in records]}"
        )


# ---------------------------------------------------------------------------
# Tests: _heal_future_updated_at
# ---------------------------------------------------------------------------


class TestHealFutureUpdatedAt:
    """_heal_future_updated_at() DETECTS future-dated records (C2, #1817).

    It no longer clamps or re-saves anything -- re-saving reshuffled the
    created_at-based sorted index on every heal. See the function's
    docstring in models/agent_session.py for the full rationale.
    """

    def test_heal_detects_future_record_without_saving(self):
        """A session with updated_at 7h in the future is counted as
        detected, but save() is never called and updated_at is left
        untouched (still future-dated)."""
        now = datetime.now(UTC)
        future = now + timedelta(hours=7)
        past = now - timedelta(minutes=30)

        session = _make_session_no_save()
        session.updated_at = future
        session.created_at = past
        session.id = "future-session-1"

        save_calls = []

        def mock_save(self, *args, **kwargs):
            save_calls.append(True)

        with (
            patch.object(AgentSession, "query") as mock_query,
            patch.object(AgentSession, "save", mock_save),
        ):
            mock_query.all.return_value = [session]
            count = AgentSession._heal_future_updated_at()

        assert count == 1, f"Expected 1 future-dated record detected, got {count}"
        assert len(save_calls) == 0, "save() must never be called by the heal"
        assert session.updated_at == future, (
            "updated_at must be left untouched -- no clamp, no re-save "
            f"(got {session.updated_at!r})"
        )

    def test_heal_skips_sane_records(self):
        """Sessions with updated_at in the past must not be counted or touched."""
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

        assert count == 0, "Sane record must not be counted as detected"
        assert len(save_calls) == 0, "save() must not be called for a sane record"
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

    def test_heal_is_idempotent_repeated_calls_agree(self):
        """Since nothing is mutated, repeated calls against an unchanged
        future-dated record report the SAME detection count every time --
        the new idempotency contract (no first-call-fixes-it / second-call-
        finds-nothing-left behavior, since nothing is ever fixed)."""
        now = datetime.now(UTC)
        future = now + timedelta(hours=7)

        session = _make_session_no_save()
        session.updated_at = future
        session.created_at = now - timedelta(minutes=10)
        session.id = "idempotent-session"

        save_calls = []

        def mock_save(self, *args, **kwargs):
            save_calls.append(True)

        with (
            patch.object(AgentSession, "query") as mock_query,
            patch.object(AgentSession, "save", mock_save),
        ):
            mock_query.all.return_value = [session]

            count1 = AgentSession._heal_future_updated_at()
            count2 = AgentSession._heal_future_updated_at()

        assert count1 == 1
        assert count2 == 1, "Repeated detection of an unmutated future record must agree"
        assert len(save_calls) == 0

    def test_heal_returns_zero_on_query_failure(self, caplog):
        """If query.all() raises, heal must return 0 (fail-soft)."""
        with patch.object(AgentSession, "query") as mock_query:
            mock_query.all.side_effect = RuntimeError("Redis connection error")
            with caplog.at_level(logging.WARNING, logger="models.agent_session"):
                count = AgentSession._heal_future_updated_at()

        assert count == 0
        assert any("could not fetch sessions" in r.message for r in caplog.records)

    def test_heal_skips_record_on_per_record_error(self, caplog):
        """If accessing a single record's fields raises, heal skips that
        record and continues to the next -- one bad record must not abort
        the whole detection pass."""
        now = datetime.now(UTC)
        future = now + timedelta(hours=7)

        class _BoomOnAccess:
            """A record whose updated_at raises when read (simulates a
            corrupted/partial hash) instead of a normal save() failure,
            since save() is no longer called by the heal at all."""

            id = "bad-record"

            @property
            def updated_at(self):
                raise RuntimeError("simulated corrupt field read")

        bad = _BoomOnAccess()

        good = _make_session_no_save()
        good.updated_at = future
        good.created_at = now - timedelta(minutes=5)
        good.id = "good-record"

        with patch.object(AgentSession, "query") as mock_query:
            mock_query.all.return_value = [bad, good]
            with caplog.at_level(logging.WARNING, logger="models.agent_session"):
                count = AgentSession._heal_future_updated_at()

        # Only the good record was successfully detected.
        assert count == 1
        assert any("skipped" in r.message for r in caplog.records)
