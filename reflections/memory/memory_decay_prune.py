"""reflections/memory/memory_decay_prune.py — Delete below-threshold, never-accessed memories.

What it does: Reads the full Memory corpus via Popoto, selects records whose
    importance < WF_MIN_THRESHOLD (0.15), access_count == 0, age > 30 days, and
    importance < IMPORTANCE_EXEMPT_THRESHOLD (7.0), then deletes up to
    MAX_PRUNE_PER_RUN (50) of them. Dry-run by default — only reports candidates.
Cadence: 86400s (daily)
Failure modes:
    - Memory.query.all() raises -> return {"status": "error", ...}, no deletions
    - Individual memory.delete() raises -> logged, skipped, run continues
Related reflections:
    - memory_quality_audit: shares the PRUNE_AGE_DAYS / IMPORTANCE_EXEMPT_THRESHOLD
      thresholds and the superseded_by convention; audit supersedes junk while this
      prunes low-value records.
Apply gating: dry-run by default. Set MEMORY_DECAY_PRUNE_APPLY=true (also "1"/"yes")
    to enable actual deletion.
See also: config/reflections.yaml (declaration), docs/features/reflections.md
"""

from __future__ import annotations

import logging
import time as _time

logger = logging.getLogger("reflections.memory_management")

# Importance floor matching Memory._wf_min_threshold
WF_MIN_THRESHOLD = 0.15

# Maximum deletions per run to prevent runaway pruning
MAX_PRUNE_PER_RUN = 50

# Memories created less than 30 days ago are exempt from pruning
PRUNE_AGE_DAYS = 30

# Memories with importance >= 7.0 are exempt from pruning (same as memory-dedup rule)
IMPORTANCE_EXEMPT_THRESHOLD = 7.0


async def run() -> dict:
    """Delete below-threshold memories that have never been accessed.

    Criteria for deletion (all must be true):
    - importance < WF_MIN_THRESHOLD (0.15)
    - access_count == 0
    - created_at > 30 days ago (older than 30 days)
    - importance < 7.0 (exempt threshold)

    Default: dry_run=True for the first two weeks.
    Set env MEMORY_DECAY_PRUNE_APPLY=true to enable actual deletion.

    Caps at 50 deletions per run.
    """
    import os

    apply_mode = os.environ.get("MEMORY_DECAY_PRUNE_APPLY", "false").lower() in ("true", "1", "yes")
    dry_run = not apply_mode

    findings: list[str] = []
    deleted_count = 0
    candidate_count = 0

    try:
        from models.memory import Memory

        cutoff = _time.time() - (PRUNE_AGE_DAYS * 86400)

        try:
            all_memories = Memory.query.all()
        except Exception as e:
            logger.warning(f"Memory decay prune: could not query memories: {e}")
            return {"status": "error", "findings": [], "summary": f"Query error: {e}"}

        candidates = []
        for memory in all_memories:
            # Skip superseded memories (already handled by memory-dedup)
            if memory.superseded_by:
                continue

            importance = memory.importance or 0.0
            if importance >= WF_MIN_THRESHOLD:
                continue
            if importance >= IMPORTANCE_EXEMPT_THRESHOLD:
                continue

            access_count = memory.access_count or 0
            if access_count > 0:
                continue

            # Check age (created_at field)
            created_at = getattr(memory, "created_at", None)
            if created_at is None:
                continue
            from bridge.utc import to_unix_ts

            created_ts = to_unix_ts(created_at)
            if created_ts is None:
                continue
            if created_ts > cutoff:
                # Less than 30 days old — exempt
                continue

            candidates.append(memory)

        candidate_count = len(candidates)
        capped = candidates[:MAX_PRUNE_PER_RUN]

        if dry_run:
            findings.append(
                f"[DRY RUN] Would delete {candidate_count} memories "
                f"(capped at {MAX_PRUNE_PER_RUN}). "
                "Set MEMORY_DECAY_PRUNE_APPLY=true to enable."
            )
            for memory in capped[:5]:
                findings.append(
                    f"  Would delete: memory_id={memory.memory_id}, "
                    f"importance={memory.importance:.3f}, "
                    f"content={str(memory.content)[:60]}"
                )
        else:
            for memory in capped:
                try:
                    memory.delete()
                    deleted_count += 1
                except Exception as e:
                    logger.warning(f"Memory decay prune: delete failed for {memory.memory_id}: {e}")

            findings.append(
                f"Deleted {deleted_count} of {candidate_count} candidate memories "
                f"(cap={MAX_PRUNE_PER_RUN})"
            )

    except Exception as e:
        logger.warning(f"Memory decay prune failed: {e}")
        return {"status": "error", "findings": [], "summary": f"Memory decay prune error: {e}"}

    mode_str = "DRY RUN" if dry_run else "APPLIED"
    summary = (
        f"Memory decay prune [{mode_str}]: {candidate_count} candidates, {deleted_count} deleted"
    )
    logger.info(summary)
    return {"status": "ok", "findings": findings, "summary": summary}
