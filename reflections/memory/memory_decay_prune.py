"""reflections/memory/memory_decay_prune.py — Delete low-value, never-accessed memories.

Two non-overlapping pruning tiers, each behind its own dry-run gate:

  Tier 1 (decay) — the original rule. Records whose importance < WF_MIN_THRESHOLD
    (0.15), access_count == 0, age > PRUNE_AGE_DAYS (30 days), and importance <
    IMPORTANCE_EXEMPT_THRESHOLD (7.0). Gated by MEMORY_DECAY_PRUNE_APPLY.

  Tier 2 (extraction noise, issue #1822) — the catch-all for noise that passes
    every upstream extraction filter and sits at the baseline. Records whose
    WF_MIN_THRESHOLD <= importance <= 1.0 (NON-overlapping with tier 1 by
    construction), access_count == 0, confidence ≈ 0.5 (never reinforced),
    age > NOISE_PRUNE_AGE_DAYS (14 days), and importance < IMPORTANCE_EXEMPT_THRESHOLD.
    Gated by its OWN MEMORY_NOISE_PRUNE_APPLY flag so the broader predicate can be
    validated in dry-run independently of tier 1.

The two tiers are disjoint by importance band, but the selected union is still
deduped by memory_id (belt-and-suspenders) before the shared MAX_PRUNE_PER_RUN
cap is applied, so a record is never double-counted or double-deleted.

Cadence: 86400s (daily)
Failure modes:
    - Memory.query.all() raises -> return {"status": "error", ...}, no deletions
    - Individual memory.delete() raises -> logged, skipped, run continues
Related reflections:
    - memory_quality_audit: shares the PRUNE_AGE_DAYS / IMPORTANCE_EXEMPT_THRESHOLD
      thresholds and the superseded_by convention; audit supersedes junk while this
      prunes low-value records.
Apply gating: dry-run by default for BOTH tiers.
    Set MEMORY_DECAY_PRUNE_APPLY=true (also "1"/"yes") to enable tier-1 deletion.
    Set MEMORY_NOISE_PRUNE_APPLY=true (also "1"/"yes") to enable tier-2 deletion.
See also: config/reflections.yaml (declaration), docs/features/reflections.md
"""

from __future__ import annotations

import logging
import time as _time

logger = logging.getLogger("reflections.memory_management")

# Importance floor matching Memory._wf_min_threshold. Tier-1 prunes strictly
# below this; tier-2 starts at this value (inclusive) so the tiers never overlap.
WF_MIN_THRESHOLD = 0.15

# Maximum deletions per run to prevent runaway pruning. Applied across the
# deduped union of both tiers.
MAX_PRUNE_PER_RUN = 50

# Tier-1: memories created less than 30 days ago are exempt from pruning.
PRUNE_AGE_DAYS = 30

# Tier-2 (issue #1822): extraction noise sits at importance = 1.0 and is never
# reinforced (confidence stays at the 0.5 baseline). 14 days is provisional and
# tunable — take it with a grain of salt; widen it from this single edit if
# production data shows real memories being caught before they are ever recalled.
NOISE_PRUNE_AGE_DAYS = 14

# Tier-2 upper importance bound. pattern/surprise extractions are saved at 1.0.
NOISE_IMPORTANCE_CEILING = 1.0

# Tier-2 baseline confidence (Memory ConfidenceField initial_confidence). A
# float, so compare with an epsilon rather than strict equality (spike-2).
NOISE_BASELINE_CONFIDENCE = 0.5
NOISE_CONFIDENCE_EPSILON = 1e-6

# Memories with importance >= 7.0 are exempt from pruning (same as memory-dedup rule)
IMPORTANCE_EXEMPT_THRESHOLD = 7.0


def _created_ts(memory) -> float | None:
    """Return the memory's created_at as a unix timestamp, or None if unreadable."""
    created_at = getattr(memory, "created_at", None)
    if created_at is None:
        return None
    from bridge.utc import to_unix_ts

    return to_unix_ts(created_at)


async def run() -> dict:
    """Delete low-value memories across two non-overlapping tiers (see module docstring).

    Default: dry_run for BOTH tiers. Tier-1 deletion requires
    MEMORY_DECAY_PRUNE_APPLY; tier-2 deletion requires MEMORY_NOISE_PRUNE_APPLY.
    Caps total deletions at MAX_PRUNE_PER_RUN across the deduped union.
    """
    import os

    decay_apply = os.environ.get("MEMORY_DECAY_PRUNE_APPLY", "false").lower() in (
        "true",
        "1",
        "yes",
    )
    noise_apply = os.environ.get("MEMORY_NOISE_PRUNE_APPLY", "false").lower() in (
        "true",
        "1",
        "yes",
    )

    findings: list[str] = []
    deleted_count = 0

    try:
        from models.memory import Memory

        now = _time.time()
        tier1_cutoff = now - (PRUNE_AGE_DAYS * 86400)
        tier2_cutoff = now - (NOISE_PRUNE_AGE_DAYS * 86400)

        try:
            all_memories = Memory.query.all()
        except Exception as e:
            logger.warning(f"Memory decay prune: could not query memories: {e}")
            return {"status": "error", "findings": [], "summary": f"Query error: {e}"}

        tier1: list = []  # decay tier (importance < WF_MIN_THRESHOLD)
        tier2: list = []  # extraction-noise tier (WF_MIN_THRESHOLD <= importance <= 1.0)
        for memory in all_memories:
            # Skip superseded memories (already handled by memory-dedup)
            if memory.superseded_by:
                continue

            importance = memory.importance or 0.0
            if importance >= IMPORTANCE_EXEMPT_THRESHOLD:
                continue

            access_count = memory.access_count or 0
            if access_count > 0:
                continue

            created_ts = _created_ts(memory)
            if created_ts is None:
                continue

            if importance < WF_MIN_THRESHOLD:
                # Tier 1: decay floor, 30-day age.
                if created_ts <= tier1_cutoff:
                    tier1.append(memory)
            elif importance <= NOISE_IMPORTANCE_CEILING:
                # Tier 2: extraction noise. Never-reinforced (confidence ≈ 0.5),
                # 14-day age. Disjoint from tier 1 by the importance band above.
                confidence = memory.confidence
                if confidence is None:
                    confidence = NOISE_BASELINE_CONFIDENCE
                if abs(confidence - NOISE_BASELINE_CONFIDENCE) >= NOISE_CONFIDENCE_EPSILON:
                    continue
                if created_ts <= tier2_cutoff:
                    tier2.append(memory)

        tier1_count = len(tier1)
        tier2_count = len(tier2)

        # Build the deletion set from the gated tiers, deduped by memory_id
        # (belt-and-suspenders; the bands are disjoint) before the shared cap.
        to_delete: list = []
        seen_ids: set = set()
        for memory in (tier1 if decay_apply else []) + (tier2 if noise_apply else []):
            mid = memory.memory_id
            if mid in seen_ids:
                continue
            seen_ids.add(mid)
            to_delete.append(memory)
        capped = to_delete[:MAX_PRUNE_PER_RUN]

        findings.append(
            f"Tier-1 (decay, importance<{WF_MIN_THRESHOLD}, age>{PRUNE_AGE_DAYS}d): "
            f"{tier1_count} candidates [{'APPLY' if decay_apply else 'DRY RUN'}]"
        )
        findings.append(
            f"Tier-2 (noise, {WF_MIN_THRESHOLD}<=importance<={NOISE_IMPORTANCE_CEILING}, "
            f"confidence≈{NOISE_BASELINE_CONFIDENCE}, age>{NOISE_PRUNE_AGE_DAYS}d): "
            f"{tier2_count} candidates [{'APPLY' if noise_apply else 'DRY RUN'}]"
        )

        if not (decay_apply or noise_apply):
            # Both gates off: report the prospective union (what WOULD be deleted
            # if both gates were enabled), not the gated (empty) deletion set.
            prospective = min(tier1_count + tier2_count, MAX_PRUNE_PER_RUN)
            findings.append(
                f"[DRY RUN] Would delete up to {prospective} memories "
                f"(cap={MAX_PRUNE_PER_RUN}). Set MEMORY_DECAY_PRUNE_APPLY=true and/or "
                "MEMORY_NOISE_PRUNE_APPLY=true to enable."
            )
            for memory in tier1[:3] + tier2[:3]:
                findings.append(
                    f"  Would delete: memory_id={memory.memory_id}, "
                    f"importance={(memory.importance or 0.0):.3f}, "
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
                f"Deleted {deleted_count} of {len(to_delete)} gated candidates "
                f"(cap={MAX_PRUNE_PER_RUN})"
            )

        candidate_count = tier1_count + tier2_count

    except Exception as e:
        logger.warning(f"Memory decay prune failed: {e}")
        return {"status": "error", "findings": [], "summary": f"Memory decay prune error: {e}"}

    any_apply = decay_apply or noise_apply
    mode_str = "APPLIED" if any_apply else "DRY RUN"
    summary = (
        f"Memory decay prune [{mode_str}]: {candidate_count} candidates "
        f"(tier1={tier1_count}, tier2={tier2_count}), {deleted_count} deleted"
    )
    logger.info(summary)
    return {"status": "ok", "findings": findings, "summary": summary}
