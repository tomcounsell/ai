"""Per-turn Codex dev-lane evidence log (plan #2001, Phase 3).

Task 3 is the single schema of record for per-turn evidence: exactly one
append-only JSONL line per executed Codex turn, written to a
session-scoped lane file with exactly these fields — ``thread_id``,
``turn_count``, ``outcome``, ``usage``, ``wall_clock_ms``. No prompts,
credentials, or raw stderr ever land here. Task 5 asserts over this file
and Task 4b ingests it as backfill for the full telemetry dimensions, so
neither layer defines new per-turn fields.

Degraded mode is explicit best-effort with a recorded gap: on ``OSError``
the helper logs ``codex_turn_log_failed`` and returns ``"degraded"``; the
caller still returns the Codex result (a log failure never masks the turn
outcome). Task 5 counts only ``"ok"`` lines toward
``live_probe_pass_count`` and forces a warning with the linked log line
for any ``"degraded"`` return.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)


def lane_path_for(session_id: str, data_dir: Path | str | None = None) -> Path:
    """Session-scoped lane file path for ``session_id``."""
    base = Path(data_dir) if data_dir else Path("data") / "codex_lanes"
    return base / f"{session_id}.jsonl"


def log_codex_turn(lane_path: Path, record: dict) -> Literal["ok", "degraded"]:
    """Append one per-turn evidence line to ``lane_path``.

    ``record`` carries exactly ``thread_id``, ``turn_count``, ``outcome``,
    ``usage``, ``wall_clock_ms`` — extra keys are dropped, missing keys are
    written as None, so the schema stays fixed even as callers evolve.
    Append is atomic (``os.O_APPEND``) with ``flush()`` + ``fsync()`` per
    line. Returns ``"ok"`` on success, ``"degraded"`` on ``OSError`` (with
    a ``codex_turn_log_failed`` error log naming the lane path).
    """
    line_record: dict[str, Any] = {
        "thread_id": record.get("thread_id"),
        "turn_count": record.get("turn_count"),
        "outcome": record.get("outcome"),
        "usage": record.get("usage"),
        "wall_clock_ms": record.get("wall_clock_ms"),
    }
    line = json.dumps(line_record, sort_keys=True, default=str) + "\n"
    try:
        lane_path = Path(lane_path)
        if lane_path.parent != Path("") and str(lane_path.parent) not in (".", ""):
            lane_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(lane_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        return "ok"
    except OSError as exc:
        logger.error("codex_turn_log_failed lane=%s error=%s", lane_path, exc)
        return "degraded"
