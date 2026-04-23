"""
reflections/memory_management.py — Memory management reflection callables.

Reflection callables:
  - run_memory_decay_prune    — Delete below-threshold memories (dry_run default)
  - run_memory_quality_audit  — Flag zero-access + dismissed memories
  - run_knowledge_reindex     — Re-index work-vault docs into KnowledgeDocument

All functions accept no arguments and return:
  {"status": "ok"|"error", "findings": [...], "summary": str}
"""

from __future__ import annotations

import logging
import time as _time
from pathlib import Path

logger = logging.getLogger("reflections.memory_management")

# Importance floor matching Memory._wf_min_threshold
WF_MIN_THRESHOLD = 0.15

# Maximum deletions per run to prevent runaway pruning
MAX_PRUNE_PER_RUN = 50

# Memories created less than 30 days ago are exempt from pruning
PRUNE_AGE_DAYS = 30

# Memories with importance >= 7.0 are exempt from pruning (same as memory-dedup rule)
IMPORTANCE_EXEMPT_THRESHOLD = 7.0


async def run_memory_decay_prune() -> dict:
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


async def run_memory_quality_audit() -> dict:
    """Flag memories with quality issues: zero-access after 30 days, chronically dismissed.

    Does NOT delete — logs findings only.

    Criteria flagged:
    - access_count == 0 after 30 days old
    - dismissal behavior: confidence decayed below 0.2 (indicator of chronic dismissal)
    """
    findings: list[str] = []
    flagged_zero_access = 0
    flagged_low_confidence = 0

    try:
        from models.memory import Memory

        cutoff = _time.time() - (PRUNE_AGE_DAYS * 86400)

        try:
            all_memories = Memory.query.all()
        except Exception as e:
            logger.warning(f"Memory quality audit: could not query memories: {e}")
            return {"status": "error", "findings": [], "summary": f"Query error: {e}"}

        if not all_memories:
            return {
                "status": "ok",
                "findings": [],
                "summary": "Memory quality audit: no memories to audit",
            }

        for memory in all_memories:
            # Skip superseded memories
            if memory.superseded_by:
                continue

            created_at = getattr(memory, "created_at", None)
            if created_at is None:
                continue
            from bridge.utc import to_unix_ts

            created_ts = to_unix_ts(created_at)
            if created_ts is None:
                continue

            # Flag: zero access after 30 days
            access_count = memory.access_count or 0
            if access_count == 0 and created_ts < cutoff:
                flagged_zero_access += 1
                if flagged_zero_access <= 5:
                    findings.append(
                        f"Zero-access memory: memory_id={memory.memory_id}, "
                        f"importance={memory.importance:.2f}, "
                        f"content={str(memory.content)[:80]}"
                    )

            # Flag: very low confidence (indicator of chronic dismissal or decay)
            try:
                confidence_val = float(memory.confidence) if memory.confidence is not None else None
                if confidence_val is not None and confidence_val < 0.2:
                    flagged_low_confidence += 1
                    if flagged_low_confidence <= 5:
                        findings.append(
                            f"Low-confidence memory: memory_id={memory.memory_id}, "
                            f"confidence={confidence_val:.3f}, "
                            f"importance={memory.importance:.2f}"
                        )
            except (TypeError, ValueError):
                pass

        # Summary finding
        findings.append(
            f"Audit totals: {flagged_zero_access} zero-access, "
            f"{flagged_low_confidence} low-confidence memories"
        )

    except Exception as e:
        logger.warning(f"Memory quality audit failed: {e}")
        return {"status": "error", "findings": [], "summary": f"Memory quality audit error: {e}"}

    summary = (
        f"Memory quality audit: {flagged_zero_access} zero-access, "
        f"{flagged_low_confidence} low-confidence flagged"
    )
    logger.info(summary)
    return {"status": "ok", "findings": findings, "summary": summary}


async def run_knowledge_reindex() -> dict:
    """Re-index work-vault docs into KnowledgeDocument records.

    Idempotent: existing records with matching hash are skipped.

    If KnowledgeDocument is not available (issue #728 not yet merged),
    returns a stub result with status "skipped".

    If ~/src/work-vault/ does not exist (e.g., CI), returns gracefully.
    """
    # Check for work-vault directory
    vault_path = Path.home() / "src" / "work-vault"
    if not vault_path.exists():
        logger.info("knowledge-reindex: ~/src/work-vault/ not found, skipping")
        return {
            "status": "ok",
            "findings": [],
            "summary": "knowledge-reindex skipped: ~/src/work-vault/ not found",
        }

    # Probe for KnowledgeDocument availability
    try:
        import tools.knowledge.indexer as _indexer  # noqa: F401
    except (ImportError, ModuleNotFoundError):
        logger.info("knowledge-reindex: tools.knowledge.indexer not available (issue #728 pending)")
        return {
            "status": "ok",
            "findings": [],
            "summary": "knowledge-reindex skipped: KnowledgeDocument not available (see #728)",
        }

    try:
        from tools.knowledge.indexer import reindex_vault

        result = reindex_vault(str(vault_path))
        indexed = result.get("indexed", 0)
        skipped = result.get("skipped", 0)
        errors = result.get("errors", [])

        findings = [f"Indexed {indexed} docs, skipped {skipped} unchanged"]
        for error in errors[:5]:
            findings.append(f"Error: {error}")

        summary = f"knowledge-reindex: {indexed} indexed, {skipped} skipped, {len(errors)} errors"
        logger.info(summary)
        return {"status": "ok", "findings": findings, "summary": summary}

    except Exception as e:
        logger.warning(f"knowledge-reindex failed: {e}")
        return {"status": "error", "findings": [], "summary": f"knowledge-reindex error: {e}"}
