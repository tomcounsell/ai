"""Pattern sync - export/import ProceduralPatterns for cross-machine sharing.

Exports patterns from the local Redis `shared` namespace to a JSON file
in a configurable sync directory (e.g., iCloud Drive). Imports patterns
from JSON files written by other machines.

Export is atomic: writes to a temp file, then renames.
Import is idempotent: skips existing patterns with equal or newer timestamps;
higher sample_count breaks ties.

Environment:
    SHARED_PATTERNS_DIR: directory for synced JSON files (default: data/shared_patterns)
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

# Ensure project root is in sys.path
_project_root = str(Path(__file__).parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

logger = logging.getLogger(__name__)

# Default sync directory (local fallback)
DEFAULT_SYNC_DIR = Path(__file__).parent.parent / "data" / "shared_patterns"


def _get_sync_dir() -> Path:
    """Get the sync directory from environment or default."""
    env_dir = os.environ.get("SHARED_PATTERNS_DIR")
    if env_dir:
        return Path(env_dir)
    return DEFAULT_SYNC_DIR


def _get_machine_id() -> str:
    """Get a simple machine identifier for the export filename."""
    import socket

    return socket.gethostname().split(".")[0].lower().replace(" ", "-")


def export_shared_patterns() -> Path | None:
    """Export all ProceduralPatterns from the shared namespace to JSON.

    Returns the path to the exported file, or None on failure.
    """
    from models.procedural_pattern import ProceduralPattern

    sync_dir = _get_sync_dir()
    sync_dir.mkdir(parents=True, exist_ok=True)

    try:
        patterns = ProceduralPattern.query.filter(vault="shared")
        if not patterns:
            patterns = ProceduralPattern.query.all()
            # Filter to shared vault manually if query.filter doesn't work as expected
            patterns = [p for p in patterns if (p.vault or "shared") == "shared"]

        export_data = {
            "exported_at": time.time(),
            "machine": _get_machine_id(),
            "pattern_count": len(patterns),
            "patterns": [p.to_export_dict() for p in patterns],
        }

        filename = f"patterns_{_get_machine_id()}.json"
        target_path = sync_dir / filename

        # Atomic write: temp file + rename
        fd, tmp_path = tempfile.mkstemp(dir=str(sync_dir), suffix=".json.tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(export_data, f, indent=2)
            os.replace(tmp_path, str(target_path))
        except Exception:
            # Clean up temp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

        logger.info(f"Exported {len(patterns)} patterns to {target_path}")
        return target_path

    except Exception as e:
        logger.error(f"Pattern export failed: {e}")
        return None


def import_shared_patterns(source_dir: Path | None = None) -> int:
    """Import ProceduralPatterns from JSON files in the sync directory.

    Reads all patterns_*.json files (except our own machine's export),
    and imports/updates patterns using last-write-wins with sample_count
    as tiebreaker.

    Args:
        source_dir: Directory to import from (default: sync dir from env)

    Returns:
        Number of patterns imported or updated
    """
    from models.procedural_pattern import ProceduralPattern

    sync_dir = source_dir or _get_sync_dir()
    if not sync_dir.exists():
        logger.info(f"Sync directory {sync_dir} does not exist, nothing to import")
        return 0

    machine_id = _get_machine_id()
    imported = 0

    for json_file in sorted(sync_dir.glob("patterns_*.json")):
        # Skip our own export
        if json_file.stem == f"patterns_{machine_id}":
            continue

        try:
            with open(json_file) as f:
                data = json.load(f)

            patterns = data.get("patterns", [])
            for pattern_data in patterns:
                try:
                    ProceduralPattern.from_import_dict(pattern_data)
                    imported += 1
                except Exception as e:
                    logger.warning(f"Failed to import pattern from {json_file.name}: {e}")

        except json.JSONDecodeError as e:
            logger.warning(f"Invalid JSON in {json_file}: {e}")
        except Exception as e:
            logger.warning(f"Failed to read {json_file}: {e}")

    logger.info(f"Imported {imported} patterns from {sync_dir}")
    return imported
