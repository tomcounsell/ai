"""Sweep oversized rotated log backups during /update.

scripts/log_rotate.py's 30-minute LaunchAgent handles routine rotation, but
it only re-checks the *live* ``*.log`` file's size. A burst that writes
gigabytes between rotator runs gets shifted straight into a ``.N`` backup
slot and then sits there indefinitely if the live file stays quiet
afterward. This module reuses log_rotate's independent hard-cap sweep so
every /update run also reclaims that disk space, regardless of whether the
LaunchAgent happens to be installed/running on a given machine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from scripts import log_rotate


@dataclass
class LogCleanupResult:
    """Result of sweeping oversized rotated log backups."""

    removed: list[Path] = field(default_factory=list)
    freed_bytes: int = 0
    warnings: list[str] = field(default_factory=list)


def sweep_oversized_logs(project_dir: Path) -> LogCleanupResult:
    """Delete rotated log backups under ``project_dir/logs`` past the hard cap."""
    result = LogCleanupResult()
    logs_dir = project_dir / "logs"

    sizes: dict[Path, int] = {}
    if logs_dir.is_dir():
        for backup in logs_dir.glob("*.log.[0-9]*"):
            try:
                sizes[backup] = backup.stat().st_size
            except OSError:
                continue

    try:
        removed = log_rotate.sweep_oversized_backups(logs_dir)
    except Exception as exc:  # pragma: no cover - defensive, mirrors log_rotate.main()
        result.warnings.append(f"log cleanup sweep failed: {exc}")
        return result

    result.removed = removed
    result.freed_bytes = sum(sizes.get(p, 0) for p in removed)
    return result
