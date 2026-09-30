"""Boot-SHA beacon writer (issue #1898).

At startup the bridge and the worker each record the git SHA they were
launched at to ``data/{process}_boot_sha`` so the update system can verify,
without touching the process, that the running release matches pulled HEAD
(``scripts/update/service.py::verify_running_release``).

Beacon format (two lines)::

    {short_sha}
    {iso_timestamp}

The SHA is the **short** form from ``scripts.update.git.get_short_sha`` — the
same helper the release classifier compares against, so writer and classifier
share one representation by construction (a full 40-char ``rev-parse HEAD``
SHA could never equal its short form and would make ``matches`` unreachable).

The timestamp lets the verifier detect an orphaned beacon left by a previous
process image: only ``beacon_ts > process_start_ts`` beacons are trusted.

Writes are best-effort: every failure is swallowed with a warning. A beacon
write must never crash bridge or worker startup, and a swallowed write can
only ever downgrade classification to ``unknown`` (warn) — never a false
FAILED or a restart.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_DIR = Path(__file__).resolve().parent.parent


def write_boot_beacon(process_name: str, project_dir: Path | None = None) -> bool:
    """Write ``data/{process_name}_boot_sha`` with the current short SHA.

    Best-effort: returns True on success, False on any failure (logged as a
    warning, never raised). ``process_name`` is ``"bridge"`` or ``"worker"``.
    """
    try:
        pd = project_dir if project_dir is not None else PROJECT_DIR
        from scripts.update.git import get_short_sha  # noqa: PLC0415

        sha = get_short_sha(pd)
        if not sha:
            logger.warning("Boot beacon: empty short SHA for %s — skipping write", process_name)
            return False
        beacon_path = pd / "data" / f"{process_name}_boot_sha"
        beacon_path.parent.mkdir(parents=True, exist_ok=True)
        beacon_path.write_text(f"{sha}\n{datetime.now(UTC).isoformat()}\n")
        logger.info("Boot beacon written: %s @ %s", beacon_path.name, sha)
        return True
    except Exception as exc:
        logger.warning("Boot beacon write failed for %s: %s", process_name, exc)
        return False
