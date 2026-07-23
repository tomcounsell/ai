"""reflections/memory/memory_decay_prune.py — Retire low-value, never-accessed memories.

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

On the apply path the removal MECHANISM differs per tier, because popoto's
`WriteFilterMixin._check_write_filter()` raises `SkipSaveException` for ANY save
(INSERT or UPDATE) whose importance is below WF_MIN_THRESHOLD (0.15). So a
`superseded_by=…; save()` tombstone on a below-floor record silently no-ops —
the tombstone never persists while a naive counter reports a phantom prune
(issue #2203 BLOCKER; empirically confirmed: `Memory(importance=0.10).save()`
returns False, record absent from Redis):

  - Tier 1 (importance < 0.15) → HARD-DELETE (`memory.delete()`). A tombstone
    `save()` is mechanically impossible below the write floor, and these records
    sit below the write-admission floor anyway (the write gate would refuse to
    re-admit them), so delete is the only persistable removal. `prune_count`
    increments ONLY after `delete()` returns without raising. Bounded by the
    shared MAX_PRUNE_PER_RUN cap.
  - Tier 2 (0.15 ≤ importance ≤ 1.0) → TOMBSTONE (`memory.superseded_by` set to
    `TIER2_SUPERSEDED_BY` + `save()`, which persists at/above the floor).
    Reversible/inspectable, following `memory_quality_audit.py`'s
    `CLEANUP_SUPERSEDED_BY` convention; strictly safer than #1822's old tier-2
    hard-delete. `prune_count` increments ONLY when `save()` returns truthy.

Tier-2 superseded records are already skipped by recall and by this reflection's
own re-run (see the `memory.superseded_by` skip check below), so tombstoning is
idempotent.

Cadence: 86400s (daily)
Failure modes:
    - Memory.query.all() raises -> return {"status": "error", ...}, no removals
    - Individual memory.delete()/save() raises -> logged, skipped, run continues
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

# Tier-2 tombstone sentinel (issue #2203). Deterministic, non-record `superseded_by`
# value -- same convention as memory_quality_audit.py's CLEANUP_SUPERSEDED_BY.
# Recall already filters any record with superseded_by set (see the skip check in
# the candidate-selection loop below), so the tier-2 tombstone is equivalent in
# effect to a delete but remains inspectable/reversible. Tier-1 records sit below
# the 0.15 write floor and cannot be tombstoned via save() (WriteFilterMixin), so
# they hard-delete -- only tier-2 uses a sentinel.
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
    """Retire low-value memories across two non-overlapping tiers (see module docstring).

    Tier-1 (importance < 0.15) hard-deletes; tier-2 (0.15 ≤ importance ≤ 1.0)
    tombstones via `superseded_by` (the write filter forbids a tombstone save
    below the floor — issue #2203 BLOCKER). Default: dry_run for BOTH tiers.
    Tier-1 apply requires MEMORY_DECAY_PRUNE_APPLY (or params={"apply": True} when
    the env var is unset); tier-2 apply requires MEMORY_NOISE_PRUNE_APPLY (same
    fallback). See `_resolve_tier_apply` for the env-as-kill-switch precedence
    rule. Caps total removals at MAX_PRUNE_PER_RUN across the deduped union.

    Args:
        params: Optional dict forwarded by the reflection scheduler (registry
            entries with a `params:` block in reflections.yaml). Only
            `params["apply"]` is consulted; absent/None is treated as False.
    """
    params = params or {}

    decay_apply = _resolve_tier_apply("MEMORY_DECAY_PRUNE_APPLY", params)
    noise_apply = _resolve_tier_apply("MEMORY_NOISE_PRUNE_APPLY", params)

    findings: list[str] = []
    deleted_count = 0  # tier-1 hard-deletes (persisted removals)
    tombstoned_count = 0  # tier-2 tombstones (persisted supersessions)

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
            # Both gates off: report the prospective union (what WOULD be removed
            # if both gates were enabled), not the gated (empty) removal set.
            prospective = min(tier1_count + tier2_count, MAX_PRUNE_PER_RUN)
            findings.append(
                f"[DRY RUN] Would remove up to {prospective} memories "
                f"(tier-1 hard-delete, tier-2 tombstone; cap={MAX_PRUNE_PER_RUN}). "
                "Set MEMORY_DECAY_PRUNE_APPLY=true and/or "
                "MEMORY_NOISE_PRUNE_APPLY=true to enable."
            )
            for memory in tier1[:3] + tier2[:3]:
                findings.append(
                    f"  Would remove: memory_id={memory.memory_id}, "
                    f"importance={(memory.importance or 0.0):.3f}, "
                    f"content={str(memory.content)[:60]}"
                )
        else:
            # Per-tier removal mechanism (issue #2203 BLOCKER): tier-1 records sit
            # below the 0.15 write floor, so a tombstone save() cannot persist
            # (WriteFilterMixin raises SkipSaveException) -- they hard-delete.
            # Tier-2 records are at/above the floor and tombstone via save().
            # prune_count increments ONLY on a persisted removal (no phantom).
            from config.memory_defaults import DEFAULT_PROJECT_KEY
            from models.memory_gate import _increment_gate_counter

            tier1_pruned = [m for label, m in capped if label == "tier1"]
            tier2_pruned = [m for label, m in capped if label == "tier2"]

            for memory in tier1_pruned:
                try:
                    memory.delete()  # hard-delete: only persistable removal below floor
                    deleted_count += 1
                    _increment_gate_counter(
                        memory.project_key or DEFAULT_PROJECT_KEY, "prune_count"
                    )
                except Exception as e:
                    logger.warning(
                        f"Memory decay prune: tier-1 hard-delete failed for {memory.memory_id}: {e}"
                    )

            for memory in tier2_pruned:
                try:
                    memory.superseded_by = TIER2_SUPERSEDED_BY
                    memory.superseded_by_rationale = TIER2_RATIONALE
                    # save() returns falsy if the write filter rejected it; only a
                    # truthy (persisted) supersession counts -- never a phantom.
                    saved = memory.save()
                    if saved is not False:
                        tombstoned_count += 1
                        _increment_gate_counter(
                            memory.project_key or DEFAULT_PROJECT_KEY, "prune_count"
                        )
                except Exception as e:
                    logger.warning(
                        f"Memory decay prune: tier-2 tombstone failed for {memory.memory_id}: {e}"
                    )

            removed_count = deleted_count + tombstoned_count
            findings.append(
                f"Removed {removed_count} of {len(to_prune)} gated candidates "
                f"(tier-1 deleted={deleted_count}, tier-2 tombstoned={tombstoned_count}, "
                f"cap={MAX_PRUNE_PER_RUN})"
            )

        candidate_count = tier1_count + tier2_count

    except Exception as e:
        logger.warning(f"Memory decay prune failed: {e}")
        return {"status": "error", "findings": [], "summary": f"Memory decay prune error: {e}"}

    any_apply = decay_apply or noise_apply
    mode_str = "APPLIED" if any_apply else "DRY RUN"
    summary = (
        f"Memory decay prune [{mode_str}]: {candidate_count} candidates "
        f"(tier1={tier1_count}, tier2={tier2_count}), "
        f"{deleted_count} deleted, {tombstoned_count} tombstoned"
    )
    logger.info(summary)
    return {"status": "ok", "findings": findings, "summary": summary}
