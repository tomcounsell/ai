#!/usr/bin/env python3
"""User-space log rotator for the logs/ directory.

Runs on a 30-minute schedule via the com.valor.log-rotate LaunchAgent
(com.valor.log-rotate.plist). Globs logs/*.log, checks each against
LOG_MAX_SIZE, and rotates via the same ``mv + shift + touch`` algorithm
that ``rotate_log()`` in ``scripts/valor-service.sh`` uses at service
start. Stdlib-only; no dependencies beyond Python 3.

Replaces the previous root-requiring newsyslog path. Files held open by
launchd (``StandardOutPath``/``StandardErrorPath``) are rotated by renaming
the old file and creating a fresh empty one — launchd keeps writing to the
old inode until the service restarts, which is the same behavior the old
newsyslog ``N`` flag produced. The startup ``rotate_log()`` calls in
``valor-service.sh`` cover event-driven rotation on every service
start/restart; this script covers between-restart windows for long-running
services.

Self-rotation safety: this script's own stdout/stderr are routed to
``logs/log_rotate.log`` and ``logs/log_rotate_error.log`` via the
LaunchAgent's ``StandardOutPath``/``StandardErrorPath``. launchd holds file
descriptors on those paths, so rotating them here would recreate the exact
FD-hold problem we are solving. The files are excluded via
``SELF_EXCLUDED_FILES``. They are expected to stay tiny (~KB per run at
~48 runs/day) so unbounded growth is not a practical concern.

Exits 0 even if individual files fail so launchd does not thrash the
agent into a throttle window.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# Match the existing shell rotator: 10 MB, 3 backups. See scripts/valor-service.sh:148-150.
LOG_MAX_SIZE = 10 * 1024 * 1024  # 10 MB
LOG_MAX_BACKUPS = 3

# Files this script writes to via the LaunchAgent's StandardOutPath /
# StandardErrorPath. Rotating them would recreate the launchd FD-hold
# problem — see module docstring.
SELF_EXCLUDED_FILES = frozenset({"log_rotate.log", "log_rotate_error.log"})

LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"

# Use stderr for diagnostics — the LaunchAgent routes stderr to
# logs/log_rotate_error.log (which is self-excluded from rotation).
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("log_rotate")


def _rotate_one(log_file: Path) -> bool:
    """Rotate ``log_file`` if it exceeds ``LOG_MAX_SIZE``.

    Returns True if a rotation occurred, False otherwise. All OSError
    variants (stat/rename/touch) are swallowed and logged — the caller
    continues to the next file so one bad file never breaks the whole run.
    """
    try:
        size = log_file.stat().st_size
    except FileNotFoundError:
        # Service hasn't started yet on a fresh install — nothing to do.
        return False
    except OSError as exc:
        logger.warning("skip %s: stat failed (%s)", log_file, exc)
        return False

    if size <= LOG_MAX_SIZE:
        return False

    logger.info("rotating %s (size=%d > limit=%d)", log_file, size, LOG_MAX_SIZE)

    # Shift .N-1 → .N, .N-2 → .N-1, ... working backward so we never
    # overwrite an existing backup.
    for i in range(LOG_MAX_BACKUPS, 1, -1):
        src = log_file.with_suffix(log_file.suffix + f".{i - 1}")
        dst = log_file.with_suffix(log_file.suffix + f".{i}")
        if src.exists():
            try:
                os.replace(src, dst)
            except OSError as exc:
                logger.warning("shift %s → %s failed (%s)", src, dst, exc)

    # Rotate current → .1.
    rotated = log_file.with_suffix(log_file.suffix + ".1")
    try:
        os.replace(log_file, rotated)
    except OSError as exc:
        logger.warning("rename %s → %s failed (%s)", log_file, rotated, exc)
        return False

    # Recreate the empty file so newly-started processes (and launchd,
    # for services that have restarted since the last rotation) have a
    # target to write to.
    try:
        log_file.touch()
    except OSError as exc:
        logger.warning("touch %s failed (%s)", log_file, exc)
        return False

    return True


def rotate_logs(logs_dir: Path = LOGS_DIR) -> tuple[int, int]:
    """Scan ``logs_dir`` and rotate every oversized *.log file.

    Returns ``(rotated_count, skipped_count)`` — the number of files rotated
    and the number skipped because they matched ``SELF_EXCLUDED_FILES``.
    """
    if not logs_dir.is_dir():
        logger.info("logs dir not found: %s", logs_dir)
        return (0, 0)

    rotated = 0
    skipped = 0
    for log_file in sorted(logs_dir.glob("*.log")):
        if log_file.name in SELF_EXCLUDED_FILES:
            skipped += 1
            continue
        if _rotate_one(log_file):
            rotated += 1

    return (rotated, skipped)


def main() -> int:
    try:
        rotated, skipped = rotate_logs()
        logger.info("done: rotated=%d skipped=%d", rotated, skipped)
    except Exception as exc:  # pragma: no cover - defensive belt-and-braces
        # Never propagate — exiting non-zero would make launchd throttle
        # the agent and stop rotating for 10+ minutes.
        logger.warning("unexpected error: %s", exc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
