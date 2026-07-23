"""reflections/memory/memory_decay_prune.py — Tombstone low-value, never-accessed memories.

Two non-overlapping pruning tiers, each behind its own apply gate:

  Tier 1 (decay) — the original rule. Records whose importance < WF_MIN_THRESHOLD
    (0.15), access_count == 0, age > PRUNE_AGE_DAYS (30 days), and importance <
    IMPORTANCE_EXEMPT_THRESHOLD (7.0). Gated by MEMORY_DECAY_PRUNE_APPLY (or the
    `params={"apply": true}` kill-switch fallback — see Apply gating below).

  Tier 2 (extraction noise, issue #1822) — the catch-all for noise that passes
    every upstream extraction filter and sits at the baseline. Records whose
    WF_MIN_THRESHOLD <= importance <= 1.0 (NON-overlapping with tier 1 by
    construction), access_count == 0, confidence ≈ 0.5 (never reinforced),
    age > NOISE_PRUNE_AGE_DAYS (14 days), and importance < IMPORTANCE_EXEMPT_THRESHOLD.
    Gated by its OWN MEMORY_NOISE_PRUNE_APPLY flag so the broader predicate can be
    validated in dry-run independently of tier 1.

The two tiers are disjoint by importance band, but the selected union is still
deduped by memory_id (belt-and-suspenders) before the shared MAX_PRUNE_PER_RUN
cap is applied, so a record is never double-counted or double-tombstoned.

On the apply path, BOTH tiers supersede rather than hard-delete: `memory.
superseded_by` is set to a deterministic sentinel (`TIER1_SUPERSEDED_BY` /
`TIER2_SUPERSEDED_BY`) and the record is saved, following the same convention
`memory_quality_audit.py`'s `CLEANUP_SUPERSEDED_BY` uses. Superseded records are
already skipped by recall and by this reflection's own re-run (see the
`memory.superseded_by` skip check below), so tombstoning is idempotent and
reversible (issue #2203 — tombstone-first activation; #1822's tier-2 hard-delete
is retired).

Cadence: 86400s (daily)
Failure modes:
    - Memory.query.all() raises -> return {"status": "error", ...}, no tombstones
    - Individual memory.save() raises -> logged, skipped, run continues
Related reflections:
    - memory_quality_audit: shares the PRUNE_AGE_DAYS / IMPORTANCE_EXEMPT_THRESHOLD
      thresholds and the superseded_by convention; audit supersedes junk while this
      prunes low-value records.
Apply gating: dry-run by default for BOTH tiers (env-as-kill-switch precedence).
    Each tier resolves independently: if its own env var is explicitly set in
    os.environ, that value wins (true OR false); otherwise the tier falls back
    to the shared `params.get("apply", False)` passed in by the reflection
    scheduler (e.g. `reflections.yaml`'s `params: {apply: true}`), so a single
    config flip engages both tiers at once.
    Set MEMORY_DECAY_PRUNE_APPLY=true (also "1"/"yes") to force tier-1 on, or
    "false"/"0"/"no" to force it off regardless of params.
    Set MEMORY_NOISE_PRUNE_APPLY=true (also "1"/"yes") to force tier-2 on, or
    "false"/"0"/"no" to force it off regardless of params.
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

# Tombstone-first sentinels (issue #2203). Deterministic, non-record `superseded_by`
# values -- same convention as memory_quality_audit.py's CLEANUP_SUPERSEDED_BY.
# Recall already filters any record with superseded_by set (see the skip check in
# the candidate-selection loop below), so these tombstones are equivalent in
# effect to a delete but remain inspectable/reversible.
TIER1_SUPERSEDED_BY = "decay-prune-tier1"
TIER1_RATIONALE = "auto-prune: tier-1 decay (never accessed, low importance, aged out)"
TIER2_SUPERSEDED_BY = "decay-prune-tier2"
TIER2_RATIONALE = "auto-prune: tier-2 extraction noise (issue #1822, never reinforced)"


def _created_ts(memory) -> float | None:
    """Return the memory's created_at as a unix timestamp, or None if unreadable."""
    created_at = getattr(memory, "created_at", None)
    if created_at is None:
        return None
    from bridge.utc import to_unix_ts

    return to_unix_ts(created_at)


def _live_confidence(memory) -> float:
    """Return the LIVE confidence for a memory via the canonical accessor.

    The plain ``memory.confidence`` attribute only mirrors the 0.5 baseline on
    the model's main hash — ``ConfidenceField`` keeps the reinforced value in a
    companion Redis hash, so reading the attribute would report ~0.5 for every
    record regardless of reinforcement (the tier-2 "never reinforced" filter
    would be inert). ``ConfidenceField.get_confidence`` is the sanctioned reader
    of that companion hash; it returns ``initial_confidence`` (0.5) when no
    reinforcement data exists. Any failure falls back to the 0.5 baseline so a
    single bad read never aborts the whole reflection run. (The conjunctive
    ``access_count == 0`` predicate remains the primary "never acted on" guard;
    this confidence check is the secondary "never reinforced" filter.)
    """
    try:
        from popoto.fields.confidence_field import ConfidenceField

        return float(ConfidenceField.get_confidence(memory, "confidence"))
    except Exception:
        return NOISE_BASELINE_CONFIDENCE


def _resolve_tier_apply(env_name: str, params: dict) -> bool:
    """Env-as-kill-switch precedence for a single tier's apply flag.

    If `env_name` is explicitly present in `os.environ` (even as an explicit
    "false"), that value wins -- it can force apply OR force dry-run. When
    unset (the normal production posture), fall back to the shared
    `params.get("apply", False)` from the reflection scheduler's config
    (e.g. `reflections.yaml`), so a single `params={"apply": true}` engages
    every tier that doesn't have its own env override.
    """
    import os

    if env_name in os.environ:
        return os.environ[env_name].lower() in ("true", "1", "yes")
    return bool(params.get("apply", False))


async def run(params: dict | None = None) -> dict:
    """Tombstone low-value memories across two non-overlapping tiers (see module docstring).

    Default: dry_run for BOTH tiers. Tier-1 apply requires MEMORY_DECAY_PRUNE_APPLY
    (or params={"apply": True} when the env var is unset); tier-2 apply requires
    MEMORY_NOISE_PRUNE_APPLY (same fallback). See `_resolve_tier_apply` for the
    env-as-kill-switch precedence rule. Caps total tombstones at MAX_PRUNE_PER_RUN
    across the deduped union.

    Args:
        params: Optional dict forwarded by the reflection scheduler (registry
            entries with a `params:` block in reflections.yaml). Only
            `params["apply"]` is consulted; absent/None is treated as False.
    """
    params = params or {}

    decay_apply = _resolve_tier_apply("MEMORY_DECAY_PRUNE_APPLY", params)
    noise_apply = _resolve_tier_apply("MEMORY_NOISE_PRUNE_APPLY", params)

    findings: list[str] = []
    tombstoned_count = 0

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
                # Cheap age check first so the companion-hash confidence read
                # only happens for already-old records.
                if created_ts > tier2_cutoff:
                    continue  # younger than NOISE_PRUNE_AGE_DAYS — exempt
                if (
                    abs(_live_confidence(memory) - NOISE_BASELINE_CONFIDENCE)
                    >= NOISE_CONFIDENCE_EPSILON
                ):
                    continue  # reinforced/dismissed away from baseline — not noise
                tier2.append(memory)

        tier1_count = len(tier1)
        tier2_count = len(tier2)

        # Build the tombstone set from the gated tiers, deduped by memory_id
        # (belt-and-suspenders; the bands are disjoint) before the shared cap.
        # Each entry keeps its tier label so the apply loop below can pick the
        # right sentinel/rationale and split tier-1 vs tier-2 per-tier reporting.
        to_prune: list[tuple[str, object]] = []
        seen_ids: set = set()
        for tier_label, memory in [("tier1", m) for m in (tier1 if decay_apply else [])] + [
            ("tier2", m) for m in (tier2 if noise_apply else [])
        ]:
            mid = memory.memory_id
            if mid in seen_ids:
                continue
            seen_ids.add(mid)
            to_prune.append((tier_label, memory))
        capped = to_prune[:MAX_PRUNE_PER_RUN]

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
            # Both gates off: report the prospective union (what WOULD be pruned
            # if both gates were enabled), not the gated (empty) tombstone set.
            prospective = min(tier1_count + tier2_count, MAX_PRUNE_PER_RUN)
            findings.append(
                f"[DRY RUN] Would tombstone up to {prospective} memories "
                f"(cap={MAX_PRUNE_PER_RUN}). Set MEMORY_DECAY_PRUNE_APPLY=true and/or "
                "MEMORY_NOISE_PRUNE_APPLY=true to enable."
            )
            for memory in tier1[:3] + tier2[:3]:
                findings.append(
                    f"  Would tombstone: memory_id={memory.memory_id}, "
                    f"importance={(memory.importance or 0.0):.3f}, "
                    f"content={str(memory.content)[:60]}"
                )
        else:
            # Tombstone-first: split into two loops, one per tier, so each uses
            # its own sentinel/rationale (issue #2203). Records are superseded
            # via save(), never hard-removed, on this path.
            from config.memory_defaults import DEFAULT_PROJECT_KEY
            from models.memory_gate import _increment_gate_counter

            tier1_pruned = [m for label, m in capped if label == "tier1"]
            tier2_pruned = [m for label, m in capped if label == "tier2"]

            for memory in tier1_pruned:
                try:
                    memory.superseded_by = TIER1_SUPERSEDED_BY
                    memory.superseded_by_rationale = TIER1_RATIONALE
                    memory.save()
                    tombstoned_count += 1
                    _increment_gate_counter(
                        memory.project_key or DEFAULT_PROJECT_KEY, "prune_count"
                    )
                except Exception as e:
                    logger.warning(
                        f"Memory decay prune: tier-1 tombstone failed for {memory.memory_id}: {e}"
                    )

            for memory in tier2_pruned:
                try:
                    memory.superseded_by = TIER2_SUPERSEDED_BY
                    memory.superseded_by_rationale = TIER2_RATIONALE
                    memory.save()
                    tombstoned_count += 1
                    _increment_gate_counter(
                        memory.project_key or DEFAULT_PROJECT_KEY, "prune_count"
                    )
                except Exception as e:
                    logger.warning(
                        f"Memory decay prune: tier-2 tombstone failed for {memory.memory_id}: {e}"
                    )

            findings.append(
                f"Tombstoned {tombstoned_count} of {len(to_prune)} gated candidates "
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
        f"(tier1={tier1_count}, tier2={tier2_count}), {tombstoned_count} tombstoned"
    )
    logger.info(summary)
    return {"status": "ok", "findings": findings, "summary": summary}
