"""Tests for agent/session_archive.py -- the durable secondary SQLite store.

Covers the export/restore round-trip, the restore guard's four decision
branches, the restore-atomicity sentinel (partial-restore recovery, the
stuck-sentinel-never-clobbers invariant, poison-row quarantine + the wedged
cap), serialization fidelity (datetime, key/parent-child graph preservation),
the two-thread connection-safety model, per-row export fault isolation, the
on-loop tight busy-timeout, and get_archive_status()'s never-raises contract.

See docs/plans/session-archive-sqlite.md "Failure Path Test Strategy" for the
authoritative case list this file implements.
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from datetime import UTC, datetime

import pytest

import agent.session_archive as archive
from agent.constants import (
    SESSION_ARCHIVE_BUSY_TIMEOUT_MS,
    SESSION_ARCHIVE_ONLOOP_BUSY_TIMEOUT_MS,
)
from models.agent_session import AgentSession

pytestmark = pytest.mark.usefixtures("redis_test_db")


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def archive_db(tmp_path, monkeypatch):
    """Point the archive at an isolated per-test SQLite file."""
    db_path = tmp_path / "session_archive.db"
    monkeypatch.setenv("SESSION_ARCHIVE_DB_PATH", str(db_path))
    return db_path


def _make_session(status: str = "completed", **overrides) -> AgentSession:
    defaults = dict(
        session_id=f"tg_archive_test_{uuid.uuid4().hex[:8]}",
        project_key="test-session-archive",
        working_dir="/tmp",
        status=status,
    )
    defaults.update(overrides)
    session = AgentSession(**defaults)
    session.save()
    return session


def _read_meta_row(db_path) -> sqlite3.Row:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT restore_in_progress, restore_complete, expected_row_count, resume_attempts "
            "FROM _meta WHERE id=1"
        ).fetchone()
    finally:
        conn.close()


def _set_meta_sentinel(
    restore_in_progress: int, expected_row_count: int, resume_attempts: int = 0
) -> None:
    conn = archive._connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE _meta SET restore_in_progress=?, expected_row_count=?, "
            "resume_attempts=? WHERE id=1",
            (restore_in_progress, expected_row_count, resume_attempts),
        )
        conn.execute("COMMIT")
    finally:
        conn.close()


def _snapshot_fields(session: AgentSession) -> dict:
    return {name: getattr(session, name, None) for name in session._meta.fields}


# ---------------------------------------------------------------------------
# Basic export/restore round-trip
# ---------------------------------------------------------------------------


def test_export_and_restore_round_trip(archive_db):
    session = _make_session(status="completed")
    session_id = session.id
    archive.export_session(session)
    session.delete()
    assert len(list(AgentSession.query.all())) == 0

    result = archive.restore_if_empty()

    assert result == {"restored": 1, "skipped_reason": None, "resumed": False, "quarantined": 0}
    restored = AgentSession.query.get(id=session_id)
    assert restored is not None
    assert restored.id == session_id
    assert restored.status == "completed"
    assert restored.project_key == "test-session-archive"


def test_export_all_zero_sessions_does_not_crash(archive_db):
    assert len(list(AgentSession.query.all())) == 0

    archive.export_all()

    status = archive.get_archive_status()
    assert status["row_count"] == 0
    assert status["kind"] == "periodic"
    assert status["exists"] is True


def test_restore_if_empty_empty_archive_empty_redis(archive_db):
    result = archive.restore_if_empty()

    assert result["restored"] == 0
    assert result["skipped_reason"] is None
    assert result["quarantined"] == 0


# ---------------------------------------------------------------------------
# Serialization fidelity
# ---------------------------------------------------------------------------


def test_datetime_round_trip_fidelity(archive_db):
    now = datetime.now(UTC).replace(microsecond=123000)
    session = _make_session(created_at=now, completed_at=now, scheduled_at=now)
    session_id = session.id
    # Capture Popoto's own load-form (it strips tzinfo on read from Redis --
    # a pre-existing Popoto behavior unrelated to the archive). Comparing the
    # restored session against THIS baseline isolates round-trip fidelity of
    # the archive's own export/restore code from that unrelated quirk.
    baseline = AgentSession.query.get(id=session_id)

    archive.export_session(session)
    session.delete()
    result = archive.restore_if_empty()
    assert result["restored"] == 1

    restored = AgentSession.query.get(id=session_id)
    assert restored.created_at == baseline.created_at
    assert restored.completed_at == baseline.completed_at
    assert restored.scheduled_at == baseline.scheduled_at


def test_key_and_parent_child_graph_preservation(archive_db):
    parent = _make_session()
    child = _make_session(parent_agent_session_id=parent.id)
    parent_id, child_id = parent.id, child.id

    archive.export_session(parent)
    archive.export_session(child)
    parent.delete()
    child.delete()
    assert len(list(AgentSession.query.all())) == 0

    result = archive.restore_if_empty()
    assert result["restored"] == 2

    restored_parent = AgentSession.query.get(id=parent_id)
    restored_child = AgentSession.query.get(id=child_id)
    assert restored_parent is not None
    assert restored_child is not None
    # The archived id must equal the restored id -- not a regenerated UUID.
    assert restored_parent.id == parent_id
    assert restored_child.id == child_id
    # The child's parent link must still resolve to the restored parent.
    assert restored_child.parent_agent_session_id == parent_id
    resolved_parent = AgentSession.query.get(id=restored_child.parent_agent_session_id)
    assert resolved_parent is not None
    assert resolved_parent.id == parent_id


# ---------------------------------------------------------------------------
# Two-thread connection safety
# ---------------------------------------------------------------------------


def test_two_thread_connection_safety(archive_db):
    import threading

    session_main = _make_session()
    session_thread = _make_session()
    errors: list[Exception] = []
    main_id = session_main.id

    def _run_export_session():
        try:
            archive.export_session(session_thread)
        except Exception as exc:  # noqa: BLE001 -- capture for assertion, not swallow silently
            errors.append(exc)

    thread = threading.Thread(target=_run_export_session)
    thread.start()
    try:
        archive.export_all()
    except Exception as exc:  # noqa: BLE001
        errors.append(exc)
    thread.join(timeout=10)

    assert not thread.is_alive()
    assert not any(isinstance(exc, sqlite3.ProgrammingError) for exc in errors), errors
    assert errors == []

    status = archive.get_archive_status()
    assert status["row_count"] >= 2
    conn = archive._connect()
    try:
        ids = {row["id"] for row in conn.execute("SELECT id FROM sessions").fetchall()}
    finally:
        conn.close()
    assert main_id in ids
    assert session_thread.id in ids


# ---------------------------------------------------------------------------
# export_all() per-row serialization isolation
# ---------------------------------------------------------------------------


def test_export_all_per_row_serialization_isolation(archive_db, monkeypatch):
    sessions = [_make_session() for _ in range(4)]
    bad_id = sessions[2].id
    original_serialize = archive._serialize_session

    def _flaky_serialize(session):
        if session.id == bad_id:
            raise ValueError("simulated serialization failure")
        return original_serialize(session)

    monkeypatch.setattr(archive, "_serialize_session", _flaky_serialize)

    archive.export_all()  # must not raise

    conn = archive._connect()
    try:
        ids = {row["id"] for row in conn.execute("SELECT id FROM sessions").fetchall()}
    finally:
        conn.close()
    assert bad_id not in ids
    assert len(ids) == 3

    status = archive.get_archive_status()
    assert status["row_count"] == 3


# ---------------------------------------------------------------------------
# On-loop tight busy-timeout
# ---------------------------------------------------------------------------


def test_connect_sets_distinct_busy_timeouts_by_on_loop(archive_db):
    conn_tight = archive._connect(on_loop=True)
    try:
        tight_value = conn_tight.execute("PRAGMA busy_timeout").fetchone()[0]
    finally:
        conn_tight.close()

    conn_loose = archive._connect(on_loop=False)
    try:
        loose_value = conn_loose.execute("PRAGMA busy_timeout").fetchone()[0]
    finally:
        conn_loose.close()

    assert tight_value == SESSION_ARCHIVE_ONLOOP_BUSY_TIMEOUT_MS
    assert loose_value == SESSION_ARCHIVE_BUSY_TIMEOUT_MS
    assert tight_value < loose_value


def test_export_session_uses_onloop_connection_export_all_does_not(archive_db, monkeypatch):
    captured: list[bool] = []
    original_connect = archive._connect

    def _spy_connect(on_loop: bool = False):
        captured.append(on_loop)
        return original_connect(on_loop=on_loop)

    monkeypatch.setattr(archive, "_connect", _spy_connect)

    session = _make_session()
    archive.export_session(session)
    assert captured == [True]

    captured.clear()
    archive.export_all()
    assert captured == [False]


# ---------------------------------------------------------------------------
# Restore guard (the load-bearing case)
# ---------------------------------------------------------------------------


def test_restore_guard_empty_redis_runs(archive_db):
    sessions = [_make_session() for _ in range(3)]
    for session in sessions:
        archive.export_session(session)
    ids = [s.id for s in sessions]
    for session in sessions:
        session.delete()
    assert len(list(AgentSession.query.all())) == 0

    result = archive.restore_if_empty()

    assert result["restored"] == 3
    assert result["skipped_reason"] is None
    for session_id in ids:
        assert AgentSession.query.get(id=session_id) is not None


def test_restore_guard_partial_redis_does_not_run(archive_db):
    archived_sessions = [_make_session() for _ in range(2)]
    for session in archived_sessions:
        archive.export_session(session)
    for session in archived_sessions:
        session.delete()

    live_session = _make_session(status="running", extra_context={"marker": "keep-me"})
    # Reload (not the in-memory object) so the baseline matches Popoto's own
    # load-form (it strips tzinfo on read -- a pre-existing Popoto quirk
    # unrelated to the archive; comparing reload-to-reload isolates whether
    # the guard itself touched anything).
    before = _snapshot_fields(AgentSession.query.get(id=live_session.id))

    result = archive.restore_if_empty()

    assert result == {
        "restored": 0,
        "skipped_reason": "redis_has_records",
        "resumed": False,
        "quarantined": 0,
    }
    after = _snapshot_fields(AgentSession.query.get(id=live_session.id))
    assert after == before


def test_restore_guard_orphan_index_only_does_not_run(archive_db):
    archived_sessions = [_make_session() for _ in range(2)]
    for session in archived_sessions:
        archive.export_session(session)
    for session in archived_sessions:
        session.delete()
    assert len(list(AgentSession.query.all())) == 0

    # Deliberately raw Redis write to simulate an orphaned index-set member --
    # exactly the scenario this guard branch exists to catch (a key present
    # without a corresponding queryable record). Not production code.
    from popoto.redis_db import POPOTO_REDIS_DB

    POPOTO_REDIS_DB.set("AgentSession:orphan-fake-key", "junk")

    result = archive.restore_if_empty()

    assert result == {
        "restored": 0,
        "skipped_reason": "redis_has_keys",
        "resumed": False,
        "quarantined": 0,
    }


def test_restore_idempotent(archive_db):
    sessions = [_make_session() for _ in range(2)]
    for session in sessions:
        archive.export_session(session)
    for session in sessions:
        session.delete()

    first = archive.restore_if_empty()
    assert first["restored"] == 2

    second = archive.restore_if_empty()
    assert second["restored"] == 0
    assert second["skipped_reason"] == "redis_has_records"


# ---------------------------------------------------------------------------
# Restore atomicity: partial-restore recovery, stuck sentinel, poison rows
# ---------------------------------------------------------------------------


def test_partial_restore_recovery(archive_db, monkeypatch):
    sessions = [_make_session() for _ in range(4)]
    for session in sessions:
        archive.export_session(session)
    target_id = sessions[1].id
    for session in sessions:
        session.delete()
    assert len(list(AgentSession.query.all())) == 0

    original_rehydrate = archive._rehydrate_row
    state = {"failed_once": False}

    def _flaky_rehydrate(row):
        if row["id"] == target_id and not state["failed_once"]:
            state["failed_once"] = True
            raise RuntimeError("simulated transient rehydrate failure")
        return original_rehydrate(row)

    monkeypatch.setattr(archive, "_rehydrate_row", _flaky_rehydrate)

    first = archive.restore_if_empty()
    assert first["restored"] == 3
    assert first["skipped_reason"] is None

    meta_after_first = _read_meta_row(archive_db)
    assert meta_after_first["restore_in_progress"] == 1
    assert meta_after_first["restore_complete"] == 0

    second = archive.restore_if_empty()
    assert second["restored"] == 4
    assert second["resumed"] is True

    meta_after_second = _read_meta_row(archive_db)
    assert meta_after_second["restore_in_progress"] == 0
    assert meta_after_second["restore_complete"] == 1
    assert len(list(AgentSession.query.all())) == 4


def test_stuck_sentinel_never_clobbers_healthy_full_redis(archive_db):
    kept_sessions = [_make_session() for _ in range(2)]
    for session in kept_sessions:
        archive.export_session(session)

    # Mutate one live session so it diverges from its archived snapshot.
    kept_sessions[0].status = "failed"
    kept_sessions[0].save()

    # A third session created AFTER the export -- present live, absent from
    # the archive entirely.
    extra_session = _make_session(status="running")

    live_ids = [s.id for s in (*kept_sessions, extra_session)]
    live_count = len(list(AgentSession.query.all()))
    assert live_count == 3
    before = {sid: _snapshot_fields(AgentSession.query.get(id=sid)) for sid in live_ids}

    # Simulate a stuck sentinel from an old interrupted restore whose
    # expected_row_count happens to match the CURRENT live count.
    _set_meta_sentinel(restore_in_progress=1, expected_row_count=live_count, resume_attempts=1)

    result = archive.restore_if_empty()

    assert result["restored"] == 0
    assert result["skipped_reason"] == "restore_already_complete"
    after = {sid: _snapshot_fields(AgentSession.query.get(id=sid)) for sid in live_ids}
    assert after == before  # byte-for-byte unchanged -- no clobber, no resurrection

    meta_row = _read_meta_row(archive_db)
    assert meta_row["restore_in_progress"] == 0
    assert meta_row["restore_complete"] == 1


def test_stuck_sentinel_does_not_mask_genuinely_missing_row(archive_db, monkeypatch):
    """A raw live_count >= expected_row_count match must NOT be trusted alone.

    Reproduces the exact bug scenario: a resume attempt rehydrates 2 of 3
    archived rows while one (the poison row) keeps failing but hasn't yet hit
    SESSION_ARCHIVE_ROW_ATTEMPT_CAP, so it isn't quarantined and the sentinel
    is left set. Before the next call, an UNRELATED new session is created by
    normal traffic, padding live_count up to match expected_row_count even
    though the poison row was never actually restored. The guard must detect
    that the archived, non-quarantined poison id is missing from Redis and
    refuse to declare `restore_already_complete` -- it must keep retrying
    until the poison row either succeeds or is properly quarantined, never
    silently dropping it.
    """
    sessions = [_make_session() for _ in range(3)]
    for session in sessions:
        archive.export_session(session)
    poison_id = sessions[0].id
    for session in sessions:
        session.delete()
    assert len(list(AgentSession.query.all())) == 0

    original_rehydrate = archive._rehydrate_row

    def _always_fails_for_poison(row):
        if row["id"] == poison_id:
            raise RuntimeError("permanently unrestorable row")
        return original_rehydrate(row)

    monkeypatch.setattr(archive, "_rehydrate_row", _always_fails_for_poison)

    # First call: restores the 2 good rows; the poison row fails once
    # (attempt 1 of SESSION_ARCHIVE_ROW_ATTEMPT_CAP=3) and is NOT quarantined
    # yet. The sentinel stays set because restored(2) + quarantined(0) < 3.
    first = archive.restore_if_empty()
    assert first["restored"] == 2
    assert first["skipped_reason"] is None
    assert first["quarantined"] == 0
    meta_after_first = _read_meta_row(archive_db)
    assert meta_after_first["restore_in_progress"] == 1
    assert meta_after_first["expected_row_count"] == 3

    # Simulate unrelated normal traffic creating a brand-new session that has
    # nothing to do with the archive/restore -- this pads live_count up to
    # match expected_row_count while the poison row remains missing.
    _make_session(project_key="test-unrelated-traffic")
    live_count = len(list(AgentSession.query.all()))
    assert live_count == 3  # matches expected_row_count -- the bug trigger
    assert AgentSession.query.get(id=poison_id) is None  # still genuinely missing

    # Second call: with the bug, live_count(3) >= expected_row_count(3) would
    # declare restore_already_complete and clear the sentinel here, silently
    # and permanently losing the poison row. The fix must instead detect the
    # missing archived id and keep resuming.
    second = archive.restore_if_empty()
    assert second["skipped_reason"] != "restore_already_complete"
    assert second["skipped_reason"] is None
    assert second["resumed"] is True
    meta_after_second = _read_meta_row(archive_db)
    assert meta_after_second["restore_in_progress"] == 1  # never cleared prematurely
    assert AgentSession.query.get(id=poison_id) is None  # still not silently resurrected

    # Third call: the poison row's attempt count reaches
    # SESSION_ARCHIVE_ROW_ATTEMPT_CAP(3) and is properly quarantined --
    # an operator-visible outcome, never a silent drop.
    third = archive.restore_if_empty()
    assert third["quarantined"] == 1
    assert third["restored"] + third["quarantined"] == 3
    meta_after_third = _read_meta_row(archive_db)
    assert meta_after_third["restore_in_progress"] == 0
    assert meta_after_third["restore_complete"] == 1

    # Final state: the two good archived rows plus the one unrelated session
    # are live; the poison row was never resurrected and is recorded as
    # quarantined (operator-visible), not silently dropped.
    assert len(list(AgentSession.query.all())) == 3
    assert AgentSession.query.get(id=poison_id) is None
    conn = sqlite3.connect(str(archive_db))
    conn.row_factory = sqlite3.Row
    try:
        quarantine_row = conn.execute(
            "SELECT quarantined_at FROM _restore_quarantine WHERE id=?", (poison_id,)
        ).fetchone()
    finally:
        conn.close()
    assert quarantine_row is not None
    assert quarantine_row["quarantined_at"] is not None


def test_poison_row_quarantine_and_completes(archive_db, monkeypatch):
    sessions = [_make_session() for _ in range(4)]
    for session in sessions:
        archive.export_session(session)
    poison_id = sessions[0].id
    for session in sessions:
        session.delete()

    original_rehydrate = archive._rehydrate_row

    def _always_fails_for_poison(row):
        if row["id"] == poison_id:
            raise RuntimeError("permanently unrestorable row")
        return original_rehydrate(row)

    monkeypatch.setattr(archive, "_rehydrate_row", _always_fails_for_poison)

    results = [archive.restore_if_empty() for _ in range(3)]

    # By the 3rd call the poison row has failed SESSION_ARCHIVE_ROW_ATTEMPT_CAP
    # (default 3) times and is quarantined; the sentinel clears because
    # restored + quarantined == expected_row_count (4).
    final = results[-1]
    assert final["quarantined"] == 1
    assert final["restored"] + final["quarantined"] == 4

    meta_row = _read_meta_row(archive_db)
    assert meta_row["restore_in_progress"] == 0
    assert meta_row["restore_complete"] == 1

    # The three good rows restored; the poison row never did.
    assert len(list(AgentSession.query.all())) == 3
    assert AgentSession.query.get(id=poison_id) is None


def test_poison_row_declares_wedged_before_quarantine_cap(archive_db, monkeypatch):
    # A tiny resume-attempt cap and a large row-attempt cap means the resume
    # budget runs out before the poison row would ever get quarantined --
    # restore must declare itself wedged rather than retry forever.
    monkeypatch.setattr(archive, "SESSION_ARCHIVE_ROW_ATTEMPT_CAP", 100)
    monkeypatch.setattr(archive, "SESSION_ARCHIVE_RESUME_ATTEMPT_CAP", 2)

    sessions = [_make_session() for _ in range(3)]
    for session in sessions:
        archive.export_session(session)
    poison_id = sessions[0].id
    for session in sessions:
        session.delete()

    original_rehydrate = archive._rehydrate_row

    def _always_fails_for_poison(row):
        if row["id"] == poison_id:
            raise RuntimeError("permanently unrestorable row")
        return original_rehydrate(row)

    monkeypatch.setattr(archive, "_rehydrate_row", _always_fails_for_poison)

    first = archive.restore_if_empty()  # resume_attempts -> 1
    assert first["skipped_reason"] is None
    second = archive.restore_if_empty()  # resume_attempts -> 2
    assert second["skipped_reason"] is None
    third = archive.restore_if_empty()  # resume_attempts(2) >= cap(2) -> wedged

    assert third["skipped_reason"] == "restore_wedged"
    assert third["restored"] == 0
    meta_row = _read_meta_row(archive_db)
    assert (
        meta_row["restore_in_progress"] == 1
    )  # never cleared -- stays wedged, not silently clobbered


# ---------------------------------------------------------------------------
# get_archive_status() -- never raises
# ---------------------------------------------------------------------------


def test_get_archive_status_healthy_shape(archive_db):
    session = _make_session()
    archive.export_session(session)

    status = archive.get_archive_status()

    assert status["exists"] is True
    assert status["healthy"] is True
    assert status["row_count"] == 1
    assert status["kind"] == "terminal"
    assert status["last_export_age_s"] is not None
    assert status["db_path"] == str(archive_db)


def test_periodic_sweep_populates_separate_periodic_timestamp(archive_db):
    """A periodic sweep advances last_periodic_export_ts; a terminal export does not (C3)."""
    session = _make_session()

    # Terminal-only export: last_export_ts fresh, periodic timestamp still None.
    archive.export_session(session)
    status = archive.get_archive_status()
    assert status["last_export_ts"] is not None
    assert status["last_periodic_export_ts"] is None
    # Cold-start grace: healthy falls back to the terminal timestamp before the
    # first sweep has ever run.
    assert status["healthy"] is True

    # Periodic sweep: now the periodic timestamp is populated.
    archive.export_all()
    status = archive.get_archive_status()
    assert status["last_periodic_export_ts"] is not None
    assert status["last_periodic_export_age_s"] is not None
    assert status["healthy"] is True


def test_dead_sweep_thread_reads_stale_despite_fresh_terminal_exports(archive_db):
    """C3: a dead periodic sweep must surface as stale even while terminal
    exports keep last_export_ts fresh (the silent-green failure mode)."""
    session = _make_session()

    # One periodic sweep runs, then the sweep thread "dies": age its timestamp
    # well past the freshness threshold.
    archive.export_all()
    stale_ts = time.time() - (archive.SESSION_ARCHIVE_FRESHNESS_THRESHOLD_S + 3600)
    conn = archive._connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE _meta SET last_periodic_export_ts=? WHERE id=1", (stale_ts,))
        conn.execute("COMMIT")
    finally:
        conn.close()

    # Terminal exports keep firing on every session completion, keeping
    # last_export_ts fresh -- this is exactly what would falsely mask the dead
    # sweep if health keyed off the shared timestamp.
    archive.export_session(session)

    status = archive.get_archive_status()
    # Terminal age is fresh...
    assert status["last_export_age_s"] < archive.SESSION_ARCHIVE_FRESHNESS_THRESHOLD_S
    # ...but the periodic age is stale, so health must be RED.
    assert status["last_periodic_export_age_s"] > archive.SESSION_ARCHIVE_FRESHNESS_THRESHOLD_S
    assert status["healthy"] is False


def test_get_archive_status_missing_db_never_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("SESSION_ARCHIVE_DB_PATH", str(tmp_path / "does-not-exist.db"))

    status = archive.get_archive_status()

    assert status["exists"] is False
    assert status["healthy"] is False
    assert status["row_count"] == 0
    assert status["last_export_age_s"] is None


def test_get_archive_status_corrupt_db_never_raises(archive_db):
    archive_db.write_bytes(b"this is not a sqlite database file")

    status = archive.get_archive_status()

    assert status["exists"] is False
    assert status["healthy"] is False


# ---------------------------------------------------------------------------
# Exception isolation
# ---------------------------------------------------------------------------


def test_restore_if_empty_never_raises_on_internal_error(archive_db, monkeypatch):
    def _boom(on_loop=False):
        raise RuntimeError("simulated connection failure")

    monkeypatch.setattr(archive, "_connect", _boom)

    result = archive.restore_if_empty()

    assert result == {"restored": 0, "skipped_reason": "error", "resumed": False, "quarantined": 0}
