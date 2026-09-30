"""The archive's quarantine mirrors to a dead letter, and only mirrors (#3183).

``_restore_quarantine`` stays authoritative. It is the cold-start skip list
AND the per-row attempt counter, and it lives on disk precisely to survive an
emptied Redis — the event the archive exists to recover from. Moving it into
Popoto would put the skip list inside the store the archive rebuilds, and
every cold start would re-attempt every poison row from attempt 0.

So the dead letter is observability: written once, at the attempt cap,
non-replayable, and wrapped so a Redis failure can never abort the SQLite
transaction that is the durable record.
"""

import sqlite3

import pytest

from agent import session_archive


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        "CREATE TABLE IF NOT EXISTS _restore_quarantine ("
        "id TEXT PRIMARY KEY, attempt_count INTEGER DEFAULT 0, quarantined_at TEXT)"
    )
    yield connection
    connection.close()


def _fail_row_until_cap(conn, monkeypatch, recorder):
    monkeypatch.setattr("bridge.dead_letters.record", recorder)
    for _ in range(session_archive.SESSION_ARCHIVE_ROW_ATTEMPT_CAP):
        session_archive._record_row_failure(conn, "poison-1", RuntimeError("cannot rehydrate"))


class TestQuarantineMirror:
    def test_a_dead_letter_is_written_at_the_attempt_cap(self, conn, monkeypatch):
        recorded = []
        _fail_row_until_cap(conn, monkeypatch, lambda *a, **k: recorded.append((a, k)))

        assert len(recorded) == 1, "exactly one row, at the cap — not one per failed attempt"
        (args, kwargs) = recorded[0]
        assert args[0] == "archive_restore"
        assert args[1]["archived_id"] == "poison-1"
        assert kwargs["replayable"] is False, (
            "retrying a poison row is precisely what the quarantine prevents"
        )

    def test_below_the_cap_nothing_is_written(self, conn, monkeypatch):
        recorded = []
        monkeypatch.setattr("bridge.dead_letters.record", lambda *a, **k: recorded.append(a))
        session_archive._record_row_failure(conn, "flaky-1", RuntimeError("transient"))
        assert recorded == []

    def test_sqlite_still_commits_when_the_dead_letter_write_raises(self, conn, monkeypatch):
        """The mirror must never be able to lose the authoritative record."""

        def _boom(*args, **kwargs):
            raise ConnectionError("redis down")

        _fail_row_until_cap(conn, monkeypatch, _boom)

        row = conn.execute(
            "SELECT attempt_count, quarantined_at FROM _restore_quarantine WHERE id=?",
            ("poison-1",),
        ).fetchone()
        assert row["attempt_count"] == session_archive.SESSION_ARCHIVE_ROW_ATTEMPT_CAP
        assert row["quarantined_at"] is not None, (
            "the on-disk quarantine is the durable record and must survive a Redis failure"
        )

    def test_the_quarantine_table_still_exists(self):
        """Anti-criterion: the table must not have been replaced by the model."""
        import inspect

        source = inspect.getsource(session_archive)
        assert "CREATE TABLE IF NOT EXISTS _restore_quarantine" in source
