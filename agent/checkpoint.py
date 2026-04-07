"""Stage-aware checkpoint persistence for SDLC pipeline recovery.

Saves checkpoint files to data/checkpoints/{slug}.json after each stage
completion. On session revival, checkpoints provide rich context about
completed stages and artifacts instead of the shallow "branch exists" check.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

CHECKPOINT_DIR = Path(__file__).parent.parent / "data" / "checkpoints"

# Canonical SDLC stage order
STAGE_ORDER = ["ISSUE", "PLAN", "BUILD", "TEST", "PATCH", "REVIEW", "DOCS", "MERGE"]


@dataclass
class PipelineCheckpoint:
    """Persistent state for an SDLC pipeline session."""

    session_id: str
    slug: str
    timestamp: str = ""
    current_stage: str = ""
    completed_stages: list[str] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)
    retry_counts: dict[str, int] = field(default_factory=dict)
    human_messages: list[str] = field(default_factory=list)


def save_checkpoint(cp: PipelineCheckpoint) -> None:
    """Atomically write checkpoint to data/checkpoints/{slug}.json."""
    cp.timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    target = CHECKPOINT_DIR / f"{cp.slug}.json"
    tmp = target.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(asdict(cp), indent=2))
        tmp.rename(target)
    except Exception as e:
        logger.warning(f"Failed to save checkpoint for {cp.slug}: {e}")
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def load_checkpoint(slug: str) -> PipelineCheckpoint | None:
    """Load checkpoint from disk. Returns None if missing or corrupt."""
    path = CHECKPOINT_DIR / f"{slug}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return PipelineCheckpoint(**data)
    except Exception as e:
        logger.warning(f"Failed to load checkpoint for {slug}: {e}")
        return None


def delete_checkpoint(slug: str) -> None:
    """Remove checkpoint file on successful completion."""
    path = CHECKPOINT_DIR / f"{slug}.json"
    try:
        path.unlink(missing_ok=True)
    except Exception as e:
        logger.warning(f"Failed to delete checkpoint for {slug}: {e}")


def record_stage_completion(
    cp: PipelineCheckpoint, stage: str, artifacts: dict[str, str] | None = None
) -> PipelineCheckpoint:
    """Record a stage as completed, deduplicating. Returns updated checkpoint."""
    stage_upper = stage.upper()
    if stage_upper not in cp.completed_stages:
        cp.completed_stages.append(stage_upper)
    cp.current_stage = stage_upper
    if artifacts:
        cp.artifacts.update(artifacts)
    return cp


def get_next_stage(cp: PipelineCheckpoint) -> str | None:
    """Return the first SDLC stage not yet completed, or None."""
    for stage in STAGE_ORDER:
        if stage not in cp.completed_stages:
            return stage
    return None


def build_compact_context(cp: PipelineCheckpoint) -> str:
    """Build a human-readable summary for revival context injection."""
    next_stage = get_next_stage(cp)
    lines = [f"Resumed session for: {cp.slug}"]
    if cp.completed_stages:
        lines.append(f"Completed stages: {', '.join(cp.completed_stages)}")
    if next_stage:
        lines.append(f"Next stage: {next_stage}")
    if cp.artifacts:
        lines.append("Artifacts:")
        for key, val in cp.artifacts.items():
            lines.append(f"  - {key}: {val}")
    return "\n".join(lines)


def cleanup_old_checkpoints(max_age_days: int = 7) -> list[str]:
    """Remove checkpoints older than max_age_days. Returns list of removed slugs."""
    if not CHECKPOINT_DIR.exists():
        return []
    removed = []
    cutoff = time.time() - (max_age_days * 86400)
    for path in CHECKPOINT_DIR.glob("*.json"):
        try:
            if path.stat().st_mtime < cutoff:
                slug = path.stem
                path.unlink()
                removed.append(slug)
                logger.info(f"Cleaned stale checkpoint: {slug}")
        except Exception as e:
            logger.warning(f"Failed to clean checkpoint {path.name}: {e}")
    return removed
