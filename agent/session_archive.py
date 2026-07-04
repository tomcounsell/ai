"""Durable secondary store: periodic AgentSession export to SQLite.

Redis is, and remains, the single authoritative store for `AgentSession`
records. This module gives it a transactional secondary copy on local disk
(``data/session_archive.db``) so a total Redis data-dir loss (FLUSHALL, disk
failure, ``rm -rf`` of the data dir) is recoverable. See
``docs/plans/session-archive-sqlite.md`` for the full design rationale —
this module implements that plan's Task 1 (the core archive) exactly.

Four public entry points:
    export_session(session)   -- single-row terminal upsert (finalize hook)
    export_all()               -- full periodic sweep (daemon thread)
    restore_if_empty()         -- guarded cold-start rehydrate (worker startup)
    get_archive_status()       -- read-only status for dashboard/doctor/CLI

Connection model (deliberate divergence from analytics/collector.py):
    Every public entry point opens its OWN fresh ``sqlite3.Connection`` and
    closes it in a ``finally`` block -- there is no shared module-level
    connection. The archive is written from two threads: the periodic sweep
    runs on its own daemon thread while the terminal hook (``export_session``)
    runs on the asyncio event-loop thread (via
    ``models.session_lifecycle.finalize_session``). A single
    ``sqlite3.Connection`` is thread-affine under the default
    ``check_same_thread=True`` and raises ``sqlite3.ProgrammingError`` when
    reused across threads. WAL mode + a busy-timeout then serialize the two
    independent connections at the SQLite level, so at worst one write waits
    briefly for the other -- see the Race Conditions section of the plan.

Key preservation on restore is the load-bearing correctness property: the
real ``AutoKeyField`` ``id`` (not the ``agent_session_id`` property alias,
which ``AgentSession._normalize_kwargs`` pops and discards) is archived and
explicitly passed back as ``id=`` on reconstruction, so restored sessions
keep their original primary key and every ``parent_agent_session_id`` link
stays intact.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent.constants import (
    SESSION_ARCHIVE_BUSY_TIMEOUT_MS,
    SESSION_ARCHIVE_FRESHNESS_THRESHOLD_S,
    SESSION_ARCHIVE_ONLOOP_BUSY_TIMEOUT_MS,
    SESSION_ARCHIVE_RESUME_ATTEMPT_CAP,
    SESSION_ARCHIVE_ROW_ATTEMPT_CAP,
)
from models.agent_session import AgentSession

logger = logging.getLogger(__name__)

_DB_DIR = Path(__file__).parent.parent / "data"
_DEFAULT_DB_PATH = _DB_DIR / "session_archive.db"

# Bounded SCAN cap for the cold-start "any AgentSession* key exists" check --
# `count` is a per-call hint to Redis, `_SCAN_MAX_ITERATIONS` bounds the
# number of cursor advances so a huge/corrupt keyspace can never hang the
# restore guard indefinitely.
_SCAN_COUNT_HINT = 1000
_SCAN_MAX_ITERATIONS = 100

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    project_key TEXT,
    status TEXT,
    updated_at TEXT,
    payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS _meta (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_export_ts REAL,
    row_count INTEGER NOT NULL DEFAULT 0,
    kind TEXT,
    restore_in_progress INTEGER NOT NULL DEFAULT 0,
    restore_complete INTEGER NOT NULL DEFAULT 0,
    expected_row_count INTEGER NOT NULL DEFAULT 0,
    resume_attempts INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS _restore_quarantine (
    id TEXT PRIMARY KEY,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    quarantined_at TEXT
);
"""

# Cache of AgentSession field names whose values are Python `datetime`
# objects (DatetimeField / SortedField), so restore knows which JSON string
# payload values to parse back into datetimes. Built lazily once per process
# -- the AgentSession field set does not change at runtime.
_DATETIME_FIELD_NAMES: set[str] | None = None


def _datetime_field_names() -> set[str]:
    global _DATETIME_FIELD_NAMES
    if _DATETIME_FIELD_NAMES is None:
        from popoto import DatetimeField, SortedField

        _DATETIME_FIELD_NAMES = {
            name
            for name, field in AgentSession._meta.fields.items()
            if isinstance(field, DatetimeField | SortedField)
        }
    return _DATETIME_FIELD_NAMES


# ---------------------------------------------------------------------------
# Connection / schema
# ---------------------------------------------------------------------------


def _db_path() -> Path:
    """Return the archive DB path, honoring the SESSION_ARCHIVE_DB_PATH override.

    Read fresh on every call (never cached) so tests can point the archive at
    a `tmp_path` via `monkeypatch.setenv` without needing to reload this module.
    """
    override = os.environ.get("SESSION_ARCHIVE_DB_PATH")
    return Path(override) if override else _DEFAULT_DB_PATH


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    conn.execute("INSERT OR IGNORE INTO _meta (id) VALUES (1)")


def _connect(on_loop: bool = False) -> sqlite3.Connection:
    """Open a FRESH connection for one operation (never a shared module-level one).

    Args:
        on_loop: True for the on-loop terminal export_session() write, which
            uses the tight SESSION_ARCHIVE_ONLOOP_BUSY_TIMEOUT_MS so a
            WAL-lock stall can never block the event loop for long. False
            (default) uses the longer SESSION_ARCHIVE_BUSY_TIMEOUT_MS for the
            off-loop periodic sweep, restore, and read paths.

    Caller is responsible for closing the returned connection (`finally`).
    """
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    busy_ms = SESSION_ARCHIVE_ONLOOP_BUSY_TIMEOUT_MS if on_loop else SESSION_ARCHIVE_BUSY_TIMEOUT_MS
    # isolation_level=None -> autocommit mode; every transaction below is
    # explicit (BEGIN IMMEDIATE ... COMMIT/ROLLBACK), matching the plan's
    # "transaction is the atomic primitive" design.
    conn = sqlite3.connect(str(path), timeout=busy_ms / 1000, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {busy_ms}")
    conn.execute("PRAGMA journal_mode=WAL")
    _init_schema(conn)
    return conn


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _serialize_session(session: AgentSession) -> dict[str, Any]:
    """Serialize one AgentSession's full field set, keyed on the real `id`.

    Enumerates `session._meta.fields` (never a hand-maintained list) so the
    export stays correct as AgentSession gains fields. Datetimes become
    ISO-8601 strings; dict/list fields are left JSON-native. The whole field
    map is stored verbatim in the `payload` column -- no size cap (Concern #3
    was cut: truncation silently corrupts round-trip fidelity).
    """
    fields: dict[str, Any] = {}
    for name in session._meta.fields:
        fields[name] = _to_jsonable(getattr(session, name, None))

    return {
        "id": session.id,
        "session_id": fields.get("session_id"),
        "project_key": fields.get("project_key"),
        "status": fields.get("status"),
        "updated_at": fields.get("updated_at"),
        "payload": json.dumps(fields),
    }


def _deserialize_payload(payload: str) -> dict[str, Any]:
    """Parse an archived payload JSON blob back into save()-ready kwargs.

    Converts ISO-8601 strings back to `datetime` for DatetimeField/SortedField
    columns. Does NOT pop `id` -- callers that need to key on it explicitly
    (restore) pop it themselves so it is never accidentally double-passed.
    """
    fields: dict[str, Any] = json.loads(payload)
    for name in _datetime_field_names():
        value = fields.get(name)
        if isinstance(value, str):
            try:
                fields[name] = datetime.fromisoformat(value)
            except ValueError:
                logger.warning(
                    "[session_archive] could not parse datetime field %s=%r during restore",
                    name,
                    value,
                )
    return fields


def _upsert_row(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO sessions (id, session_id, project_key, status, updated_at, payload)
        VALUES (:id, :session_id, :project_key, :status, :updated_at, :payload)
        ON CONFLICT(id) DO UPDATE SET
            session_id=excluded.session_id,
            project_key=excluded.project_key,
            status=excluded.status,
            updated_at=excluded.updated_at,
            payload=excluded.payload
        """,
        row,
    )


def _touch_meta(conn: sqlite3.Connection, *, kind: str, row_count: int) -> None:
    conn.execute(
        "UPDATE _meta SET last_export_ts=?, row_count=?, kind=? WHERE id=1",
        (time.time(), row_count, kind),
    )


# ---------------------------------------------------------------------------
# export_session / export_all
# ---------------------------------------------------------------------------


def export_session(session: AgentSession) -> None:
    """Upsert one session's full field snapshot (terminal-transition hook).

    Runs on its own connection opened with the tight on-loop busy-timeout
    (SESSION_ARCHIVE_ONLOOP_BUSY_TIMEOUT_MS) -- this function is called
    inline from the synchronous `finalize_session`, which executes on the
    asyncio event-loop thread, so a WAL-lock stall must be bounded tightly.

    Raises on genuine programming errors (bad session, schema mismatch) --
    the caller (`finalize_session`) is responsible for exception isolation so
    an archive failure never breaks the terminal transition itself. Lock
    contention with the concurrent periodic sweep is handled at the SQLite
    level by the busy-timeout, not by swallowing exceptions here.
    """
    row = _serialize_session(session)
    conn = _connect(on_loop=True)
    try:
        conn.execute("BEGIN IMMEDIATE")
        _upsert_row(conn, row)
        row_count = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        _touch_meta(conn, kind="terminal", row_count=row_count)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def export_all() -> None:
    """Full periodic sweep: upsert every current AgentSession in one transaction.

    Each row is serialized inside its own try/except -- a session that fails
    to serialize is logged (with its id) and skipped, so one pathological
    row can never abort the whole sweep. The successfully-serialized rows
    are then upserted in a single `BEGIN IMMEDIATE ... COMMIT` transaction
    for a crash-safe, consistent snapshot.

    Rows present in the archive but absent from the current Redis snapshot
    are retained -- the archive is a durability floor (a superset), never a
    mirror of Redis deletions.
    """
    sessions = list(AgentSession.query.all())

    rows: list[dict[str, Any]] = []
    for session in sessions:
        try:
            rows.append(_serialize_session(session))
        except Exception as exc:
            session_id = getattr(session, "id", "<unknown>")
            logger.warning(
                "[session_archive] export_all: skipping session id=%s -- serialization failed: %s",
                session_id,
                exc,
            )

    conn = _connect(on_loop=False)
    try:
        conn.execute("BEGIN IMMEDIATE")
        for row in rows:
            _upsert_row(conn, row)
        row_count = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        _touch_meta(conn, kind="periodic", row_count=row_count)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# restore_if_empty
# ---------------------------------------------------------------------------


def _redis_has_agentsession_keys() -> bool:
    """Bounded SCAN for any live `AgentSession*` key.

    Catches orphaned index-set members that `AgentSession.query.all()` might
    not surface. Bounded to `_SCAN_MAX_ITERATIONS` cursor advances so a huge
    or corrupt keyspace can never hang the restore guard.
    """
    from popoto.redis_db import POPOTO_REDIS_DB

    cursor = 0
    for _ in range(_SCAN_MAX_ITERATIONS):
        cursor, keys = POPOTO_REDIS_DB.scan(
            cursor=cursor, match="AgentSession*", count=_SCAN_COUNT_HINT
        )
        if keys:
            return True
        if cursor == 0:
            break
    return False


def _read_meta_sentinel(conn: sqlite3.Connection) -> dict[str, int]:
    row = conn.execute(
        "SELECT restore_in_progress, expected_row_count, resume_attempts FROM _meta WHERE id=1"
    ).fetchone()
    if row is None:
        return {"restore_in_progress": 0, "expected_row_count": 0, "resume_attempts": 0}
    return {
        "restore_in_progress": row["restore_in_progress"],
        "expected_row_count": row["expected_row_count"],
        "resume_attempts": row["resume_attempts"],
    }


def _quarantined_ids(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT id FROM _restore_quarantine WHERE quarantined_at IS NOT NULL"
    ).fetchall()
    return {row["id"] for row in rows}


def _quarantined_count(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM _restore_quarantine WHERE quarantined_at IS NOT NULL"
    ).fetchone()[0]


def _record_row_failure(conn: sqlite3.Connection, archived_id: str, exc: Exception) -> None:
    """Increment the per-row failure counter; quarantine past SESSION_ARCHIVE_ROW_ATTEMPT_CAP."""
    row = conn.execute(
        "SELECT attempt_count FROM _restore_quarantine WHERE id=?", (archived_id,)
    ).fetchone()
    attempt_count = (row["attempt_count"] if row else 0) + 1
    if row is None:
        conn.execute(
            "INSERT INTO _restore_quarantine (id, attempt_count) VALUES (?, ?)",
            (archived_id, attempt_count),
        )
    else:
        conn.execute(
            "UPDATE _restore_quarantine SET attempt_count=? WHERE id=?",
            (attempt_count, archived_id),
        )
    logger.warning(
        "[session_archive] restore: row id=%s failed to rehydrate (attempt %d/%d): %s",
        archived_id,
        attempt_count,
        SESSION_ARCHIVE_ROW_ATTEMPT_CAP,
        exc,
    )
    if attempt_count >= SESSION_ARCHIVE_ROW_ATTEMPT_CAP:
        conn.execute(
            "UPDATE _restore_quarantine SET quarantined_at=? WHERE id=? AND quarantined_at IS NULL",
            (datetime.now(UTC).isoformat(), archived_id),
        )
        logger.error(
            "[session_archive] restore: quarantining poison row id=%s after %d failed attempts -- "
            "skipped on all future resumes",
            archived_id,
            attempt_count,
        )


def _rehydrate_row(row: sqlite3.Row) -> None:
    """Reconstruct and save one AgentSession from an archived row.

    The archived `id` is passed explicitly as `id=` -- never as
    `agent_session_id=`, which `AgentSession._normalize_kwargs` pops and
    discards, minting a fresh UUID and dangling every
    `parent_agent_session_id` reference.
    """
    fields = _deserialize_payload(row["payload"])
    archived_id = fields.pop("id")
    AgentSession(id=archived_id, **fields).save()


def restore_if_empty(*, dry_run: bool = False) -> dict[str, Any]:
    """Guarded, idempotent cold-start rehydrate. Never raises.

    Returns a structured result:
        {"restored": int, "skipped_reason": str | None, "resumed": bool, "quarantined": int}

    Pass `dry_run=True` to compute and return the exact same guard decision
    (skip reason, or -- if the guard would proceed -- a `would_restore` count
    of archived rows it would attempt) WITHOUT writing anything: the sentinel
    columns in `_meta` are never touched and no row is rehydrated/`.save()`d.
    This is the read-only path `valor-session-archive restore --dry-run`
    uses; the default (`dry_run=False`) behavior and return shape are
    unchanged for every existing caller.

    See docs/plans/session-archive-sqlite.md "Empty-Redis guard" and
    "Restore atomicity" sections for the exact decision table this
    implements -- the central invariant is that the guard-bypass decision is
    re-computed every call against the FRESH live Redis count, never against
    the persisted `restore_in_progress` sentinel alone, so a stuck sentinel
    can never clobber an already-populated Redis.
    """
    try:
        return _restore_if_empty_impl(dry_run=dry_run)
    except Exception as exc:
        logger.error(
            "[session_archive] restore_if_empty failed unexpectedly: %s",
            exc,
            exc_info=True,
        )
        result: dict[str, Any] = {
            "restored": 0,
            "skipped_reason": "error",
            "resumed": False,
            "quarantined": 0,
        }
        if dry_run:
            result["would_restore"] = 0
        return result


def _restore_if_empty_impl(dry_run: bool = False) -> dict[str, Any]:
    live_ids = {session.id for session in AgentSession.query.all()}
    live_count = len(live_ids)

    conn = _connect(on_loop=False)
    try:
        meta = _read_meta_sentinel(conn)
        sentinel_was_set = bool(meta["restore_in_progress"])

        if sentinel_was_set:
            if live_count >= meta["expected_row_count"]:
                # A raw count match is necessary but NOT sufficient: unrelated
                # new sessions created since the interrupted restore can pad
                # live_count up to (or past) expected_row_count while a
                # genuinely un-rehydrated archived row is still missing from
                # Redis. Verify actual presence of every archived,
                # non-quarantined id before trusting the count -- otherwise a
                # poison row that hasn't yet hit the quarantine cap gets
                # silently and permanently dropped (never quarantined, never
                # retried, sentinel says "done").
                quarantined_ids_check = _quarantined_ids(conn)
                archived_ids = {
                    row["id"] for row in conn.execute("SELECT id FROM sessions").fetchall()
                }
                missing_ids = (archived_ids - quarantined_ids_check) - live_ids
                if not missing_ids:
                    # Redis is genuinely already whole -- a stuck sentinel must
                    # NEVER force a re-upsert of an already-populated Redis. In
                    # dry-run mode we report this decision without clearing
                    # the sentinel.
                    if not dry_run:
                        conn.execute("BEGIN IMMEDIATE")
                        conn.execute(
                            "UPDATE _meta SET restore_in_progress=0, restore_complete=1 WHERE id=1"
                        )
                        conn.execute("COMMIT")
                        logger.info(
                            "[session_archive] restore: stale sentinel cleared -- Redis already "
                            "whole (live=%d >= expected=%d)",
                            live_count,
                            meta["expected_row_count"],
                        )
                    result = {
                        "restored": 0,
                        "skipped_reason": "restore_already_complete",
                        "resumed": False,
                        "quarantined": _quarantined_count(conn),
                    }
                    if dry_run:
                        result["would_restore"] = 0
                    return result
                logger.warning(
                    "[session_archive] restore: live_count (%d) >= expected_row_count (%d) "
                    "but %d archived row(s) are missing from Redis (%s) -- refusing to "
                    "declare restore complete, continuing resume",
                    live_count,
                    meta["expected_row_count"],
                    len(missing_ids),
                    sorted(missing_ids),
                )
            if meta["resume_attempts"] >= SESSION_ARCHIVE_RESUME_ATTEMPT_CAP:
                logger.error(
                    "[session_archive] restore WEDGED after %d resume attempts "
                    "(live=%d < expected=%d) -- operator intervention required",
                    meta["resume_attempts"],
                    live_count,
                    meta["expected_row_count"],
                )
                result = {
                    "restored": 0,
                    "skipped_reason": "restore_wedged",
                    "resumed": True,
                    "quarantined": _quarantined_count(conn),
                }
                if dry_run:
                    result["would_restore"] = 0
                return result
        else:
            if live_count > 0:
                result = {
                    "restored": 0,
                    "skipped_reason": "redis_has_records",
                    "resumed": False,
                    "quarantined": 0,
                }
                if dry_run:
                    result["would_restore"] = 0
                return result
            if _redis_has_agentsession_keys():
                result = {
                    "restored": 0,
                    "skipped_reason": "redis_has_keys",
                    "resumed": False,
                    "quarantined": 0,
                }
                if dry_run:
                    result["would_restore"] = 0
                return result

        # Cold-start pass or valid resume: proceed.
        quarantined_ids = _quarantined_ids(conn)
        archived_rows = conn.execute("SELECT id, payload FROM sessions").fetchall()
        expected_row_count = len(archived_rows) - len(quarantined_ids)

        if dry_run:
            # Report the would-restore count without touching the sentinel or
            # rehydrating/saving a single row.
            return {
                "restored": 0,
                "skipped_reason": None,
                "resumed": sentinel_was_set,
                "quarantined": len(quarantined_ids),
                "would_restore": expected_row_count,
            }

        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE _meta SET restore_in_progress=1, "
            "expected_row_count=?, resume_attempts=resume_attempts+1 WHERE id=1",
            (expected_row_count,),
        )
        conn.execute("COMMIT")

        restored = 0
        for row in archived_rows:
            archived_id = row["id"]
            if archived_id in quarantined_ids:
                continue
            try:
                _rehydrate_row(row)
                restored += 1
            except Exception as exc:
                conn.execute("BEGIN IMMEDIATE")
                _record_row_failure(conn, archived_id, exc)
                conn.execute("COMMIT")

        quarantined_total = _quarantined_count(conn)
        if restored + quarantined_total >= expected_row_count:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("UPDATE _meta SET restore_in_progress=0, restore_complete=1 WHERE id=1")
            conn.execute("COMMIT")

        return {
            "restored": restored,
            "skipped_reason": None,
            "resumed": sentinel_was_set,
            "quarantined": quarantined_total,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# get_archive_status
# ---------------------------------------------------------------------------


def get_archive_status() -> dict[str, Any]:
    """Read-only archive status for the dashboard, /health, doctor, and CLI.

    Never raises -- any error (missing file, corrupt DB) returns a
    `healthy=False` shape.
    """
    path = _db_path()
    result: dict[str, Any] = {
        "db_path": str(path),
        "exists": False,
        "row_count": 0,
        "last_export_ts": None,
        "last_export_age_s": None,
        "kind": None,
        "healthy": False,
    }

    if not path.exists():
        return result

    try:
        conn = sqlite3.connect(str(path), timeout=SESSION_ARCHIVE_BUSY_TIMEOUT_MS / 1000)
        try:
            conn.execute(f"PRAGMA busy_timeout = {SESSION_ARCHIVE_BUSY_TIMEOUT_MS}")
            tables = {
                row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if "_meta" not in tables or "sessions" not in tables:
                return result

            result["exists"] = True
            row = conn.execute(
                "SELECT last_export_ts, row_count, kind FROM _meta WHERE id=1"
            ).fetchone()
            if row:
                last_export_ts, row_count, kind = row
                result["last_export_ts"] = last_export_ts
                result["row_count"] = row_count or 0
                result["kind"] = kind
                if last_export_ts is not None:
                    age = max(0.0, time.time() - last_export_ts)
                    result["last_export_age_s"] = age
                    result["healthy"] = age <= SESSION_ARCHIVE_FRESHNESS_THRESHOLD_S
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("[session_archive] get_archive_status failed for %s: %s", path, exc)
        result["exists"] = False
        result["healthy"] = False

    return result
