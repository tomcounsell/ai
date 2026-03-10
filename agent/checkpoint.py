"""Pipeline checkpoint/resume for abandoned and interrupted sessions.

Saves structured checkpoint files after each SDLC stage completes, enabling
deterministic resume that skips completed work and reconstructs context.

Checkpoint files live at data/checkpoints/{slug}.json and are human-readable
JSON. They survive process restarts, bridge crashes, and machine reboots.

Resume logic:
  - load_checkpoint() returns None when no file exists (start fresh)
  - record_stage_completion() advances the checkpoint forward
  - get_next_stage() determines what to do next based on completed stages
  - build_compact_context() reconstructs context from checkpoint artifacts
  - cleanup_old_checkpoints() removes stale checkpoints after max_age_days

See GitHub issue #332 for the full design.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Ordered SDLC pipeline stages. Resume logic uses this ordering.
PIPELINE_STAGES = ["PLAN", "BUILD", "TEST", "REVIEW", "DOCS"]

# Root directory for checkpoint files, relative to repo root.
_REPO_ROOT = Path(__file__).parent.parent
_CHECKPOINT_ROOT = _REPO_ROOT / "data" / "checkpoints"


def _utcnow() -> str:
    """Return current UTC time as an ISO 8601 string with Z suffix."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class PipelineCheckpoint:
    """Structured checkpoint for an SDLC pipeline session.

    Attributes:
        session_id: The session identifier from AgentSession.
        slug: Work item slug (e.g., "my-feature"). Used as the filename.
        timestamp: ISO 8601 timestamp of when the checkpoint was last updated.
        current_stage: The last completed stage (empty string if none).
        completed_stages: Ordered list of stages that have been completed.
        artifacts: Accumulated key-value pairs from stage completions
            (plan_path, branch, pr_url, test_results, etc.).
        retry_counts: Per-stage retry counters.
        human_messages: Queued steering messages not yet processed.
    """

    session_id: str
    slug: str
    timestamp: str = field(default_factory=_utcnow)
    current_stage: str = ""
    completed_stages: list[str] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)
    retry_counts: dict[str, int] = field(default_factory=dict)
    human_messages: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialize checkpoint to a JSON-compatible dict."""
        return {
            "session_id": self.session_id,
            "slug": self.slug,
            "timestamp": self.timestamp,
            "current_stage": self.current_stage,
            "completed_stages": list(self.completed_stages),
            "artifacts": dict(self.artifacts),
            "retry_counts": dict(self.retry_counts),
            "human_messages": list(self.human_messages),
        }

    @classmethod
    def from_dict(cls, data: dict) -> PipelineCheckpoint:
        """Deserialize a checkpoint from a dict."""
        return cls(
            session_id=data["session_id"],
            slug=data["slug"],
            timestamp=data.get("timestamp", _utcnow()),
            current_stage=data.get("current_stage", ""),
            completed_stages=list(data.get("completed_stages", [])),
            artifacts=dict(data.get("artifacts", {})),
            retry_counts=dict(data.get("retry_counts", {})),
            human_messages=list(data.get("human_messages", [])),
        )


def _checkpoint_path(slug: str) -> Path:
    """Return the path to the checkpoint file for a given slug."""
    return _CHECKPOINT_ROOT / f"{slug}.json"


def save_checkpoint(checkpoint: PipelineCheckpoint) -> None:
    """Persist a checkpoint to data/checkpoints/{slug}.json.

    Uses atomic write (write to .tmp then rename) to avoid corruption
    if the process is interrupted mid-write.

    Args:
        checkpoint: The checkpoint to save.
    """
    checkpoint.timestamp = _utcnow()
    path = _checkpoint_path(checkpoint.slug)
    path.parent.mkdir(parents=True, exist_ok=True)

    tmp_path = path.with_suffix(".json.tmp")
    try:
        with open(tmp_path, "w") as f:
            json.dump(checkpoint.to_dict(), f, indent=2)
        tmp_path.rename(path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise


def load_checkpoint(slug: str) -> PipelineCheckpoint | None:
    """Load a checkpoint for the given slug.

    Returns None when no checkpoint file exists or when the file is corrupt.
    Corrupt files log a warning but do not crash the system.

    Args:
        slug: Work item slug.

    Returns:
        PipelineCheckpoint if found and valid, None otherwise.
    """
    path = _checkpoint_path(slug)
    if not path.exists():
        return None

    try:
        with open(path) as f:
            data = json.load(f)
        return PipelineCheckpoint.from_dict(data)
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.warning(f"Corrupt checkpoint for slug {slug!r}: {e}")
        return None


def delete_checkpoint(slug: str) -> None:
    """Delete the checkpoint file for a slug. No-op if it doesn't exist.

    Called after successful session completion to clean up.

    Args:
        slug: Work item slug.
    """
    path = _checkpoint_path(slug)
    if path.exists():
        path.unlink()
        logger.info(f"Deleted checkpoint for slug {slug!r}")


def record_stage_completion(
    checkpoint: PipelineCheckpoint,
    stage: str,
    artifacts: dict[str, str] | None = None,
) -> PipelineCheckpoint:
    """Record that a stage has completed successfully.

    Adds the stage to completed_stages (if not already present),
    updates current_stage, and merges any new artifacts.

    Args:
        checkpoint: The current checkpoint.
        stage: The stage that just completed (e.g., "PLAN", "BUILD").
        artifacts: Optional dict of artifacts produced by this stage.

    Returns:
        Updated checkpoint (same object, mutated in place).
    """
    if stage not in checkpoint.completed_stages:
        checkpoint.completed_stages.append(stage)
    checkpoint.current_stage = stage
    checkpoint.timestamp = _utcnow()

    if artifacts:
        checkpoint.artifacts.update(artifacts)

    return checkpoint


def record_stage_retry(
    checkpoint: PipelineCheckpoint,
    stage: str,
) -> PipelineCheckpoint:
    """Increment the retry counter for a stage.

    Args:
        checkpoint: The current checkpoint.
        stage: The stage being retried.

    Returns:
        Updated checkpoint (same object, mutated in place).
    """
    checkpoint.retry_counts[stage] = checkpoint.retry_counts.get(stage, 0) + 1
    checkpoint.timestamp = _utcnow()
    return checkpoint


def get_next_stage(checkpoint: PipelineCheckpoint) -> str | None:
    """Determine the next stage to execute based on completed stages.

    Walks PIPELINE_STAGES in order and returns the first stage not
    in completed_stages. Returns None if all stages are complete.

    Args:
        checkpoint: The current checkpoint.

    Returns:
        The next stage name, or None if the pipeline is complete.
    """
    for stage in PIPELINE_STAGES:
        if stage not in checkpoint.completed_stages:
            return stage
    return None


def build_compact_context(checkpoint: PipelineCheckpoint) -> str:
    """Build a compact context string from checkpoint artifacts.

    This is used when resuming a session: the revived agent gets this
    context instead of starting with nothing. It summarizes what has
    been accomplished so far.

    Args:
        checkpoint: The checkpoint to build context from.

    Returns:
        Human-readable context string.
    """
    lines = [
        f"## Resumed session for: {checkpoint.slug}",
        f"Session ID: {checkpoint.session_id}",
        f"Completed stages: {', '.join(checkpoint.completed_stages)}",
    ]

    next_stage = get_next_stage(checkpoint)
    if next_stage:
        lines.append(f"Next stage: {next_stage}")
    else:
        lines.append("All stages complete.")

    if checkpoint.artifacts:
        lines.append("")
        lines.append("### Artifacts")
        for key, value in checkpoint.artifacts.items():
            lines.append(f"- {key}: {value}")

    if checkpoint.retry_counts:
        lines.append("")
        lines.append("### Retries")
        for stage, count in checkpoint.retry_counts.items():
            lines.append(f"- {stage}: {count} retries")

    return "\n".join(lines)


def check_worktree_recovery(repo_root: str, slug: str) -> dict:
    """Check if a worktree exists for a slug and whether it needs recovery.

    Examines the filesystem to determine if a worktree directory exists
    and reports its state. Does not perform git operations -- callers
    decide what recovery actions to take.

    Args:
        repo_root: Path to the repository root.
        slug: Work item slug.

    Returns:
        Dict with keys:
        - worktree_exists: bool
        - worktree_path: str (if exists)
    """
    wt_path = Path(repo_root) / ".worktrees" / slug
    result: dict = {
        "worktree_exists": wt_path.exists(),
    }
    if wt_path.exists():
        result["worktree_path"] = str(wt_path)
    return result


def cleanup_old_checkpoints(max_age_days: int = 7) -> list[str]:
    """Remove checkpoint files older than max_age_days.

    Args:
        max_age_days: Maximum age in days before a checkpoint is deleted.

    Returns:
        List of slug names that were cleaned up.
    """
    cleaned: list[str] = []
    if not _CHECKPOINT_ROOT.exists():
        return cleaned

    cutoff = time.time() - (max_age_days * 86400)

    for path in _CHECKPOINT_ROOT.glob("*.json"):
        if path.stat().st_mtime < cutoff:
            slug = path.stem
            path.unlink()
            cleaned.append(slug)
            logger.info(f"Cleaned up old checkpoint: {slug}")

    return cleaned
