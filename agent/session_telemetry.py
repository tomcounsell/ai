"""
Session telemetry recorder — append-only JSONL trace per session.

Records structured events for each agent session: turn boundaries, tool usage,
token consumption, status transitions, and synthetic idle-gap markers.

Concurrency model:
    Per-session threading.Lock instances live in the module-level `_locks` dict.
    Acquisition via `_locks.setdefault(session_id, threading.Lock())` is safe
    because CPython's dict.setdefault is atomic under the GIL — two threads
    racing on the same key both see the same Lock object after the call returns.
    All state mutations (file writes, monotonic tracking, handle-cache eviction)
    happen inside the lock.

Event schema (v1-internal contract):
    turn_start          — beginning of a new turn
    turn_end            — from stream-json result event
    tool_use            — name + best-effort duration
    token_usage         — raw per-turn usage dict + total_cost_usd
    idle_gap            — synthetic; emitted when inter-event gap > IDLE_GAP_THRESHOLD
    status_transition   — session state machine transition
    telemetry_truncated — cap reached; no further events written for this session
    slash_command       — a TUI prompt starting with '/'; records command name
    human_steering      — substantive mid-run human prompt (ordinal > 0); records ordinal + snippet
    unknown             — event type was absent or unrecognised; raw payload preserved

Usage:
    from agent.session_telemetry import record_telemetry_event, read_session_timeline

    record_telemetry_event("sess-abc", {"type": "turn_start"})
    events = read_session_timeline("sess-abc")
"""

import datetime
import json
import logging
import threading
import time
from pathlib import Path
from typing import IO

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

IDLE_GAP_THRESHOLD: float = 60.0  # seconds between events before emitting synthetic idle_gap
MAX_EVENTS_PER_SESSION: int = 10_000  # per-session event cap (hard stop after truncation marker)
MAX_OPEN_HANDLES: int = 50  # maximum simultaneously open JSONL file handles

# Resolved once on first use via _get_telemetry_dir()
_TELEMETRY_DIR_RELATIVE = Path(__file__).parent.parent / "logs" / "session_telemetry"

# ---------------------------------------------------------------------------
# Module-level state (all mutations happen under per-session lock)
# ---------------------------------------------------------------------------

# Per-session threading locks.  Insertion via setdefault is GIL-atomic in CPython.
_locks: dict[str, threading.Lock] = {}

# Open file handles for JSONL append.  Bounded by MAX_OPEN_HANDLES.
# Key: session_id, Value: open file object (text mode, append)
_handles: dict[str, IO] = {}

# Per-session monotonic timestamps for idle-gap detection.
# Key: session_id, Value: monotonic float (from time.monotonic())
_last_event_monotonic: dict[str, float] = {}

# Per-session event counters used to enforce MAX_EVENTS_PER_SESSION.
# The counter includes the truncation marker itself.
_event_counts: dict[str, int] = {}

# Sessions that have been capped — no further writes accepted.
_truncated: set[str] = set()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_telemetry_dir() -> Path:
    """Return (and create) the telemetry directory."""
    d = _TELEMETRY_DIR_RELATIVE
    d.mkdir(parents=True, exist_ok=True)
    return d


def _utcnow_iso() -> str:
    """Return the current UTC time as an ISO 8601 string ending in 'Z'."""
    return datetime.datetime.utcnow().isoformat() + "Z"


def _evict_handle_if_needed() -> None:
    """Evict the oldest open handle when the cache is at capacity.

    Must be called while the CALLER already holds the per-session lock for the
    session being written.  The evicted handle may belong to a *different*
    session, so we acquire nothing here — the LRU eviction just pops the first
    key (insertion-ordered in Python 3.7+).
    """
    if len(_handles) >= MAX_OPEN_HANDLES:
        oldest_sid, fh = next(iter(_handles.items()))
        try:
            fh.flush()
            fh.close()
        except Exception:
            pass
        del _handles[oldest_sid]


def _get_handle(session_id: str) -> IO:
    """Return (or open) the append-mode file handle for *session_id*.

    Must be called while the per-session lock is held.
    """
    if session_id in _handles:
        # Move to end (LRU: accessed most recently)
        fh = _handles.pop(session_id)
        _handles[session_id] = fh
        return fh

    # Need to open a new handle — evict if over limit first
    _evict_handle_if_needed()

    telemetry_path = _get_telemetry_dir() / f"{session_id}.jsonl"
    fh = telemetry_path.open("a", encoding="utf-8")
    _handles[session_id] = fh
    return fh


def _write_event(session_id: str, event: dict) -> None:
    """Append one JSON line to the session JSONL file.

    Must be called while the per-session lock is held.
    """
    fh = _get_handle(session_id)
    fh.write(json.dumps(event, default=str) + "\n")
    fh.flush()
    _event_counts[session_id] = _event_counts.get(session_id, 0) + 1


def _normalize_event(session_id: str, event: dict) -> dict:
    """Return a copy of *event* normalized to the v1 schema.

    - Ensures ``session_id`` and ``ts`` fields are present.
    - Rewrites absent/empty ``type`` to ``"unknown"`` with the raw payload
      preserved under the ``raw`` key.
    """
    raw_type = event.get("type", "")
    if not raw_type:
        normalized = {
            "session_id": session_id,
            "ts": _utcnow_iso(),
            "type": "unknown",
            "raw": event,
        }
    else:
        normalized = dict(event)
        normalized["session_id"] = session_id
        if "ts" not in normalized:
            normalized["ts"] = _utcnow_iso()

    return normalized


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def record_telemetry_event(session_id: str, event: dict) -> None:
    """Append a telemetry event for *session_id* to its JSONL trace file.

    Fail-silent: any exception is caught, logged at DEBUG, and silently
    discarded.  This function NEVER raises.

    Args:
        session_id: The session to record against.  If ``None`` or an empty
            string, the call is a no-op.
        event: Arbitrary event payload.  Must be JSON-serialisable (or
            ``default=str`` will handle stragglers).  The ``type`` key
            determines the event kind; an absent/empty type is stored as
            ``"unknown"``.

    Behaviour:
        - Derives ``idle_gap``: if ``time.monotonic() - last_event_monotonic``
          exceeds ``IDLE_GAP_THRESHOLD`` seconds, a synthetic ``idle_gap``
          event is written *before* the real event.
        - Enforces ``MAX_EVENTS_PER_SESSION``: once the cap is reached, a
          ``telemetry_truncated`` marker is written and no further events are
          accepted for that session.
        - Creates ``logs/session_telemetry/`` if it does not exist.
    """
    try:
        if not session_id:
            return

        # Fast-path: already truncated
        if session_id in _truncated:
            return

        lock = _locks.setdefault(session_id, threading.Lock())
        with lock:
            # Re-check truncation inside the lock (another thread may have just hit the cap)
            if session_id in _truncated:
                return

            now_mono = time.monotonic()
            current_count = _event_counts.get(session_id, 0)

            # --- Idle-gap synthetic event ---
            last_mono = _last_event_monotonic.get(session_id)
            if last_mono is not None and (now_mono - last_mono) > IDLE_GAP_THRESHOLD:
                if current_count < MAX_EVENTS_PER_SESSION:
                    idle_event = {
                        "session_id": session_id,
                        "ts": _utcnow_iso(),
                        "type": "idle_gap",
                        "gap_seconds": round(now_mono - last_mono, 3),
                    }
                    _write_event(session_id, idle_event)
                    current_count = _event_counts.get(session_id, 0)

            # --- Cap check (after possible idle-gap emission) ---
            if current_count >= MAX_EVENTS_PER_SESSION:
                truncation_marker = {
                    "session_id": session_id,
                    "ts": _utcnow_iso(),
                    "type": "telemetry_truncated",
                }
                _write_event(session_id, truncation_marker)
                _truncated.add(session_id)
                logger.info(
                    "Telemetry cap reached for session %s (%d events); "
                    "no further events will be recorded.",
                    session_id,
                    MAX_EVENTS_PER_SESSION,
                )
                return

            # --- Normalise and write the real event ---
            normalized = _normalize_event(session_id, event)
            _write_event(session_id, normalized)

            # Update monotonic tracker AFTER successful write
            _last_event_monotonic[session_id] = now_mono

    except Exception as exc:
        logger.debug(
            "record_telemetry_event silently swallowed exception for session %s: %r",
            session_id,
            exc,
        )


def read_session_timeline(session_id: str, limit: int | None = None) -> list[dict]:
    """Read and return the ordered list of telemetry events for *session_id*.

    Args:
        session_id: The session whose trace to read.
        limit: If given, return only the first *limit* events.

    Returns:
        A list of event dicts in chronological order (earliest first).
        Returns ``[]`` if no trace file exists or *session_id* is empty.
        Malformed JSONL lines are silently skipped (logged at WARNING).
    """
    if not session_id:
        return []

    trace_path = _get_telemetry_dir() / f"{session_id}.jsonl"
    if not trace_path.exists():
        return []

    events: list[dict] = []
    try:
        with trace_path.open("r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, start=1):
                line = line.rstrip("\n")
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    logger.warning(
                        "Skipping malformed JSONL line %d in %s: %r — %s",
                        lineno,
                        trace_path.name,
                        line[:120],
                        exc,
                    )
                if limit is not None and len(events) >= limit:
                    break
    except Exception as exc:
        logger.warning(
            "read_session_timeline failed to read %s: %r",
            trace_path,
            exc,
        )

    return events


def finalize_session(session_id: str) -> None:
    """Reap all in-memory state for a completed session.

    Safe to call from the terminal ``status_transition`` path (finalize hook).
    Acquires the per-session lock before reaping to avoid races with concurrent
    writers, then removes the lock itself last.

    Fail-silent: any exception is caught, logged at DEBUG, and discarded.
    This function NEVER raises.

    Args:
        session_id: The session whose entries to evict from the module maps.
            No-op for unknown or empty session_id values.
    """
    if not session_id:
        return
    try:
        lock = _locks.get(session_id)
        if lock is None:
            # Session was never recorded — nothing to reap.
            return

        with lock:
            # Reap per-session state while holding the lock so no concurrent
            # writer can sneak in between the checks.
            _event_counts.pop(session_id, None)
            _last_event_monotonic.pop(session_id, None)
            _truncated.discard(session_id)

            # Close and evict any open file handle.
            fh = _handles.pop(session_id, None)
            if fh is not None:
                try:
                    fh.flush()
                    fh.close()
                except Exception:
                    pass

        # Remove the lock AFTER releasing it.  We use pop() so a concurrent
        # setdefault() that races here just re-inserts a fresh lock — harmless.
        _locks.pop(session_id, None)

    except Exception as exc:
        logger.debug(
            "finalize_session silently swallowed exception for session %s: %r",
            session_id,
            exc,
        )
