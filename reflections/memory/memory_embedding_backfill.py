"""reflections/memory/memory_embedding_backfill.py — Re-embed vectorless Memory records.

What it does: Finds active ``Memory`` records saved without an embedding vector
    (``embedding`` falsy — the degradation marker written by
    ``GracefulEmbeddingField`` when Ollama was unreachable, issue #1904) and, when
    the provider is healthy again, re-embeds them so they regain the fourth RRF
    (semantic-similarity) recall signal. Skips ``superseded_by`` records. Caps each
    run at ``MAX_BACKFILL_PER_RUN`` so a long-outage backlog does not re-saturate
    Ollama the moment it recovers.

Re-embed mechanism (issue #1904 critique C1):
    Re-embed is a PARTIAL save: ``memory.save(update_fields=["embedding"])``. A bare
    ``memory.save()`` re-runs ``on_save`` for every field, which re-stamps
    ``Memory.relevance`` (a ``DecayingSortedField`` with ``auto_now=True``) to "now"
    — silently un-decaying stale memories and corrupting recall ranking. The partial
    path runs ``on_save`` only for ``embedding``, leaving ``relevance`` (and all
    other indexes) untouched. ``update_fields=[]`` is a no-op early-return in popoto,
    so the list MUST be ``["embedding"]``.

Cadence: 86400s (daily)
Failure modes:
    - Memory import fails -> return {"status": "error", ...}
    - Memory.query.all() raises -> return {"status": "error", ...}
    - Provider import/is_available() raises -> treated as unavailable, dry-run-safe
    - Individual memory.save() raises -> logged, skipped, run continues
Related reflections:
    - embedding-orphan-sweep reaps orphan .npy files; this reflection is the inverse,
      healing records that HAVE no .npy. Both are daily/low-priority and key on live
      records, so the orphan sweep's 5-minute mtime guard covers this write window.
Apply gating: dry-run by default (counts vectorless records, saves nothing). Set
    MEMORY_EMBEDDING_BACKFILL_APPLY=true (also "1"/"yes") to enable re-embedding.
    Even in apply mode, nothing is re-saved unless the corpus-matched embedding
    provider (configure_embedding_provider) is available — a still-down provider
    short-circuits to a reported skip, never a re-save storm.
See also: config/reflections.yaml (declaration), models/graceful_embedding_field.py,
    docs/features/subconscious-memory.md
"""

from __future__ import annotations

import logging

logger = logging.getLogger("reflections.memory_management")

# Maximum re-embeds per run. After a long Ollama outage many records will be
# vectorless; re-embedding them all at once would re-saturate the provider we
# just confirmed is healthy. 500/day drains a realistic backlog over a few days
# while keeping each run's load bounded (plan Open Question 1).
MAX_BACKFILL_PER_RUN = 500


async def run() -> dict:
    """Re-embed active Memory records that were persisted without a vector.

    Default: dry-run — reports how many vectorless records exist and how many
    would be re-embedded, saving nothing. Set MEMORY_EMBEDDING_BACKFILL_APPLY=true
    to enable re-embedding; even then, re-saves only happen when the embedding
    provider is available. Re-embed is a partial save on ``embedding`` alone so
    the record's ``relevance`` decay index is not re-stamped (critique C1).
    """
    import os

    apply_mode = os.environ.get("MEMORY_EMBEDDING_BACKFILL_APPLY", "false").lower() in (
        "true",
        "1",
        "yes",
    )
    dry_run = not apply_mode
    mode_str = "DRY RUN" if dry_run else "APPLIED"

    findings: list[str] = []

    try:
        from models.memory import Memory
    except Exception as e:
        logger.warning("memory-embedding-backfill: Memory import failed: %s", e)
        return {
            "status": "error",
            "findings": [],
            "summary": f"memory-embedding-backfill error: Memory import failed: {e}",
        }

    try:
        all_memories = Memory.query.all()
    except Exception as e:
        logger.warning("memory-embedding-backfill: could not query memories: %s", e)
        return {
            "status": "error",
            "findings": [],
            "summary": f"memory-embedding-backfill error: query failed: {e}",
        }

    # Collect active records with a falsy embedding (None / 0 dimension count).
    # Matches the KnowledgeDocument #1876 convention: a positive int means embedded,
    # None/0 means "no vector — needs re-embed".
    vectorless: list = []
    for memory in all_memories:
        if getattr(memory, "superseded_by", ""):
            continue
        if not getattr(memory, "embedding", None):
            vectorless.append(memory)

    vectorless_count = len(vectorless)
    findings.append(f"{vectorless_count} active records without an embedding vector.")

    # In-process degradation counter (throttled-warning companion, critique C2) —
    # surfaced so silent-embedding-loss is observable even when the field's warning
    # was throttled. Best-effort; never abort the reflection over an import.
    degraded_since_start = 0
    try:
        from models.graceful_embedding_field import get_degradation_count

        degraded_since_start = get_degradation_count()
    except Exception:
        pass
    if degraded_since_start:
        findings.append(
            f"{degraded_since_start} degraded (vectorless) saves observed in-process "
            "since worker start."
        )

    # Provider availability gate — a still-down provider means every re-embed would
    # fail the same way, so short-circuit rather than churn.
    provider_available = False
    try:
        from agent.embedding_provider import configure_embedding_provider

        provider_available = configure_embedding_provider() is not None
    except Exception as e:
        logger.warning("memory-embedding-backfill: provider probe failed: %s", e)
        provider_available = False

    reembedded = 0

    if dry_run:
        would = min(vectorless_count, MAX_BACKFILL_PER_RUN)
        findings.append(
            f"[DRY RUN] Would re-embed up to {would} records (cap={MAX_BACKFILL_PER_RUN}, "
            f"provider_available={provider_available}). "
            "Set MEMORY_EMBEDDING_BACKFILL_APPLY=true to enable."
        )
    elif not provider_available:
        findings.append(
            "[APPLY] Embedding provider unavailable — skipped all re-embeds "
            "(no re-save storm). Will heal on a later run once the provider is up."
        )
    else:
        for memory in vectorless[:MAX_BACKFILL_PER_RUN]:
            try:
                # Partial save on `embedding` ONLY — never a bare save() (critique C1):
                # a bare save re-stamps the relevance DecayingSortedField (auto_now),
                # un-decaying stale memories. update_fields=[] is a no-op, so the list
                # must be ["embedding"].
                memory.save(update_fields=["embedding"])
                # Count it as healed only if a vector actually landed.
                if getattr(memory, "embedding", None):
                    reembedded += 1
            except Exception as e:
                logger.warning(
                    "memory-embedding-backfill: re-embed failed for %s: %s",
                    getattr(memory, "memory_id", "?"),
                    e,
                )
        findings.append(
            f"Re-embedded {reembedded} of {vectorless_count} vectorless records "
            f"(cap={MAX_BACKFILL_PER_RUN})."
        )

    # Best-effort metric — never crash the reflection.
    try:
        from analytics.collector import record_metric

        record_metric(
            "memory.embedding_backfill_reembedded",
            float(reembedded),
            dimensions={"mode": mode_str.lower().replace(" ", "_")},
        )
    except Exception as e:
        logger.debug("memory-embedding-backfill: metric emission failed: %s", e)

    summary = (
        f"memory-embedding-backfill [{mode_str}]: {vectorless_count} vectorless, "
        f"{reembedded} re-embedded, {degraded_since_start} degraded-saves-since-start"
    )
    logger.info(summary)
    return {"status": "ok", "findings": findings, "summary": summary}
