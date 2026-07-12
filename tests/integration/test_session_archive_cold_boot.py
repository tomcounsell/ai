"""Integration test: session_archive cold-boot recovery, end-to-end.

Covers Success Criterion #7 and the "Cold-boot recovery, end-to-end
(validation altitude — Concern #4)" case in
docs/plans/session-archive-sqlite.md's Failure Path Test Strategy.

The unit tests in tests/unit/test_session_archive.py already prove that
``restore_if_empty()`` rehydrates rows and that ``AgentSession.query.get(id=...)``
finds them in isolation. This test proves something stronger: driving the REAL
worker startup ordering (restore *then* the Step 1 index rebuild via
``scripts.popoto_index_cleanup.run_cleanup()``, exactly as ``worker/__main__.py``
does) leaves the rehydrated sessions reachable through the normal
secondary-index query paths the rest of the app actually uses
(``query.filter(status=...)``, ``query.get(id=...)``, and a child's
``parent_agent_session_id`` resolving back to its parent via a query). A
restore that ran *after* the rebuild — or a rebuild that silently failed to
process AgentSession — would leave rows unindexed and this test would fail.

Everything here is real: real Redis (isolated to the per-worker test db via the
autouse ``redis_test_db`` fixture), real Popoto save/query paths, real
``session_archive.restore_if_empty()``, and the real
``scripts.popoto_index_cleanup.run_cleanup()`` the worker calls at startup.
Nothing is mocked.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import popoto.redis_db as rdb
import pytest

import agent.session_archive as archive
from models.agent_session import AgentSession
from scripts.popoto_index_cleanup import run_cleanup

pytestmark = pytest.mark.usefixtures("redis_test_db")


@pytest.fixture
def archive_db(tmp_path, monkeypatch):
    """Point the archive at an isolated per-test SQLite file (never the real
    data/session_archive.db)."""
    db_path = tmp_path / "session_archive.db"
    monkeypatch.setenv("SESSION_ARCHIVE_DB_PATH", str(db_path))
    return db_path


def _make_session(status: str = "completed", **overrides) -> AgentSession:
    defaults = dict(
        session_id=f"tg_cold_boot_test_{uuid.uuid4().hex[:8]}",
        project_key="test-session-archive-cold-boot",
        working_dir="/tmp",
        status=status,
    )
    defaults.update(overrides)
    session = AgentSession(**defaults)
    session.save()
    return session


def test_cold_boot_restore_then_rebuild_leaves_sessions_queryable(archive_db):
    """Seed archive -> wipe Redis -> real restore -> real rebuild -> query.

    Drives the exact ordering worker/__main__.py runs at startup: restore
    happens BEFORE the Step 1 index rebuild (see the comment at that call site
    and tests/unit/test_worker_entry.py::test_session_archive_restore_ordering,
    which pins this ordering statically). This test proves the *runtime*
    consequence of that ordering: the rehydrated rows are actually reachable
    through the secondary-index query paths the app uses everywhere else.
    """
    # --- Seed: parent + child, with a real datetime field set on the parent ---
    now = datetime.now(UTC).replace(microsecond=456000)
    parent = _make_session(status="completed", created_at=now, completed_at=now)
    parent_id = parent.id
    child = _make_session(status="running", parent_agent_session_id=parent_id)
    child_id = child.id

    assert len(list(AgentSession.query.all())) == 2

    # Popoto strips tzinfo on read-back from Redis (a pre-existing quirk
    # unrelated to the archive -- see tests/unit/test_session_archive.py's
    # datetime fidelity test for the same pattern). Capture THIS baseline so
    # the assertions below isolate the archive's round-trip fidelity from
    # that unrelated behavior.
    baseline_parent = AgentSession.query.get(id=parent_id)

    # Archive both sessions from the live (pre-wipe) Redis into the isolated
    # SQLite file via the real export path.
    archive.export_all()
    status_before_wipe = archive.get_archive_status()
    assert status_before_wipe["row_count"] == 2
    assert status_before_wipe["exists"] is True

    # --- Simulate "Redis wiped": flush ONLY the isolated per-worker test db ---
    # (the same db `redis_test_db` already pointed Popoto at -- never db=0/production).
    # This must never touch the archive SQLite file.
    rdb.POPOTO_REDIS_DB.flushdb()
    assert list(AgentSession.query.all()) == []

    # --- Real worker startup sequence, in the correct order ---
    # 1. restore_if_empty() -- the guarded cold-start rehydrate.
    restore_result = archive.restore_if_empty()
    assert restore_result["restored"] == 2
    assert restore_result["skipped_reason"] is None
    assert restore_result["quarantined"] == 0

    # 2. The same Step 1 index rebuild the worker runs immediately after
    #    restore (see worker/__main__.py: "BEFORE the Step 1 index rebuild
    #    (so the rebuild reindexes any rehydrated rows)"). This is the REAL
    #    function, not a reimplementation.
    cleanup_result = run_cleanup()
    assert cleanup_result.get("status") == "completed"
    assert cleanup_result.get("models_processed", 0) > 0

    # --- Assert reachability via the NORMAL secondary-index query paths ---

    # query.filter(status=...) -- IndexedField secondary-index lookup.
    completed = list(AgentSession.query.filter(status="completed"))
    completed_ids = {s.id for s in completed}
    assert parent_id in completed_ids, (
        "restored parent must be reachable via query.filter(status=...) "
        "after the real restore -> rebuild sequence"
    )

    # query.get(id=...) -- primary-key lookup, must return the restored row
    # with its ORIGINAL id (not a regenerated UUID).
    restored_parent = AgentSession.query.get(id=parent_id)
    assert restored_parent is not None
    assert restored_parent.id == parent_id
    assert restored_parent.created_at == baseline_parent.created_at
    assert restored_parent.completed_at == baseline_parent.completed_at

    # The child's parent_agent_session_id must resolve back to the parent --
    # via a QUERY (KeyField secondary-index lookup), not just a dict access.
    restored_child = AgentSession.query.get(id=child_id)
    assert restored_child is not None
    assert restored_child.parent_agent_session_id == parent_id

    children_of_parent = list(AgentSession.query.filter(parent_agent_session_id=parent_id))
    children_ids = {s.id for s in children_of_parent}
    assert child_id in children_ids, (
        "child must be reachable via query.filter(parent_agent_session_id=...) "
        "-- proves the parent/child index survives the restore -> rebuild ordering, "
        "not just that the raw row was written"
    )


def test_cold_boot_restore_tolerates_legacy_payload_dead_keys(archive_db):
    """Schema diet (#1927): the real cold-boot restore -> rebuild sequence
    must not raise when an archived row predates the diet -- carrying
    deleted-field keys and the old `watchdog_unhealthy` rename-source key --
    and the rehydrated row must still be reachable through the normal
    secondary-index query paths afterward.
    """
    session = _make_session(status="completed")
    session_id = session.id
    archive.export_all()

    # Rewrite the archived payload to simulate a pre-#1927 export.
    conn = archive._connect()
    try:
        row = conn.execute("SELECT payload FROM sessions WHERE id=?", (session_id,)).fetchone()
        fields = json.loads(row["payload"])
        fields.pop("unhealthy_reason", None)
        fields.update(
            {
                "self_report_sent_at": None,
                "sdk_connection_torn_down_at": None,
                "session_mode": "pm",
                "pm_transcript_path": "/tmp/pm.jsonl",
                "dev_transcript_path": "/tmp/dev.jsonl",
                "startup_failure_kind": "ceiling",
                "startup_captured_frame": "some frame",
                "compaction_count": 3,
                "compaction_skipped_count": 1,
                "nudge_deferred_count": 2,
                "metered_input_tokens": 10,
                "metered_output_tokens": 20,
                "metered_cache_read_tokens": 5,
                "metered_cost_usd": 0.42,
                "watchdog_unhealthy": "stuck > 300s (legacy payload)",
            }
        )
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE sessions SET payload=? WHERE id=?", (json.dumps(fields), session_id))
        conn.execute("COMMIT")
    finally:
        conn.close()

    # Simulate "Redis wiped": flush ONLY the isolated per-worker test db.
    rdb.POPOTO_REDIS_DB.flushdb()
    assert list(AgentSession.query.all()) == []

    # Real worker startup sequence: restore, then the Step 1 index rebuild.
    restore_result = archive.restore_if_empty()
    assert restore_result == {
        "restored": 1,
        "skipped_reason": None,
        "resumed": False,
        "quarantined": 0,
    }
    cleanup_result = run_cleanup()
    assert cleanup_result.get("status") == "completed"

    # Reachable via query.get AND query.filter, with the rename resolved.
    restored = AgentSession.query.get(id=session_id)
    assert restored is not None
    assert restored.unhealthy_reason == "stuck > 300s (legacy payload)"

    completed = list(AgentSession.query.filter(status="completed"))
    assert session_id in {s.id for s in completed}
