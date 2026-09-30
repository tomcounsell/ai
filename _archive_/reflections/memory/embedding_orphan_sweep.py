"""reflections/memory/embedding_orphan_sweep.py — Reconcile on-disk embeddings vs live Redis set.

What it does: Two-phase sweep of Memory embedding files —
    (1) EmbeddingField.garbage_collect removes orphaned .npy files not in the
    SHA-256 expected-keep set (mtime guard of 5 minutes), and
    (2) EmbeddingField.sweep_stale_tempfiles removes leaked tmp*.npy atomic-write
    tempfiles older than 1 hour. Emits memory.embedding_orphans_swept and
    memory.embedding_tempfiles_swept metrics. In dry-run it only counts disk
    orphans (read-only) and defers the tempfile sweep.
Cadence: 86400s (daily)
Failure modes:
    - popoto < 1.6.0 (no sweep_stale_tempfiles) -> short-circuit "skipped" status
    - popoto import fails -> return {"status": "error", ...}
    - Memory import fails -> return {"status": "error", ...}
    - garbage_collect / sweep_stale_tempfiles raise -> logged finding, run continues
    - metric emission fails -> logged at debug, never crashes the reflection
Related reflections:
    - memory_decay_prune / memory_quality_audit operate on Memory records; this
      sweep reconciles the on-disk embedding artifacts those records reference.
Apply gating: dry-run by default (read-only count of disk orphans, tempfile sweep
    deferred). Set EMBEDDING_ORPHAN_SWEEP_APPLY=true (also "1"/"yes") to enable
    actual deletion. Defensively short-circuits "skipped" on popoto < 1.6.0.
See also: config/reflections.yaml (declaration), docs/features/reflections.md
"""

from __future__ import annotations

import logging

logger = logging.getLogger("reflections.memory_management")

# Mtime guard threshold for the orphan sweep — see plan Race Conditions section.
# 5 minutes covers Ollama timeout/retry pathologies during atomic writes.
_EMBEDDING_ORPHAN_MIN_AGE_SECONDS = 300

# Stale atomic-write tempfile cutoff — atomic writes complete in milliseconds,
# so anything older than 1 hour is unambiguously a leaked file.
_EMBEDDING_TEMPFILE_MAX_AGE_SECONDS = 3600


async def run() -> dict:
    """Reconcile on-disk Memory embeddings against the live Redis class set.

    Two-phase sweep:

    1. ``EmbeddingField.garbage_collect(Memory)`` — removes ``.npy`` files
       whose name is not in the SHA-256 hashed expected-keep set computed
       from ``$Class:Memory``. Mtime guard of 5 minutes protects in-flight
       saves.
    2. ``EmbeddingField.sweep_stale_tempfiles(Memory)`` — removes
       ``tmp*.npy`` atomic-write tempfiles older than 1 hour (leaked
       on process crashes between ``mkstemp`` and ``rename``).

    Defensive guard: if the installed Popoto is < 1.6.0 (stub
    ``garbage_collect`` body), the sweep short-circuits with a clear
    "skipped" status rather than silently appearing to succeed.

    Apply gating: ``EMBEDDING_ORPHAN_SWEEP_APPLY=true`` enables actual
    deletion. Default is dry-run — the sweep walks the directory and
    reports counts without unlinking anything.

    Emits two metrics:
      - ``memory.embedding_orphans_swept``    (count, dimensions={"mode": "..."})
      - ``memory.embedding_tempfiles_swept``  (count, dimensions={"mode": "..."})
    """
    import os

    findings: list[str] = []

    # --- Stub-detection guard (Popoto < 1.6.0) ------------------------------
    # Capability probe: Popoto 1.6.0 introduces EmbeddingField.sweep_stale_tempfiles
    # alongside the real garbage_collect implementation. The 1.5.x stub has only
    # garbage_collect (returning 0 unconditionally) and lacks sweep_stale_tempfiles.
    # Probing for the new method is a deterministic across-version signal — the
    # earlier docstring-marker check failed because the "Future enhancement" phrase
    # lived in the method body comment, not the docstring (verified live on 1.5.0).
    try:
        from popoto.fields.embedding_field import EmbeddingField
    except Exception as e:
        return {
            "status": "error",
            "findings": [f"popoto import failed: {e}"],
            "summary": "embedding-orphan-sweep error: popoto not importable",
        }

    if not hasattr(EmbeddingField, "sweep_stale_tempfiles"):
        logger.warning(
            "embedding-orphan-sweep: popoto-embedding-gc-stub-detected — install popoto>=1.6.0"
        )
        return {
            "status": "ok",
            "findings": ["popoto<1.6 — gc not implemented yet"],
            "summary": "embedding-orphan-sweep skipped (popoto stub)",
        }

    apply_mode = os.environ.get("EMBEDDING_ORPHAN_SWEEP_APPLY", "false").lower() in (
        "true",
        "1",
        "yes",
    )
    dry_run = not apply_mode
    mode_str = "DRY RUN" if dry_run else "APPLIED"

    try:
        from models.memory import Memory
    except Exception as e:
        logger.warning("embedding-orphan-sweep: Memory import failed: %s", e)
        return {
            "status": "error",
            "findings": [],
            "summary": f"embedding-orphan-sweep error: Memory import failed: {e}",
        }

    # --- Count-only when in dry-run mode ------------------------------------
    orphans_swept = 0
    tempfiles_swept = 0

    try:
        if dry_run:
            # Use the read-only count helper instead of touching the directory.
            try:
                from scripts.popoto_index_cleanup import _count_disk_orphans

                would_remove = _count_disk_orphans(Memory)
                orphans_swept = would_remove
            except Exception as e:
                logger.warning("embedding-orphan-sweep: dry-run count failed: %s", e)
                would_remove = 0
                orphans_swept = 0

            findings.append(
                f"[DRY RUN] Would remove ~{would_remove} disk orphans. "
                "Set EMBEDDING_ORPHAN_SWEEP_APPLY=true to enable deletion."
            )
            # Don't sweep tempfiles in dry-run either — keep this fully read-only
            findings.append("[DRY RUN] Stale tmp*.npy sweep deferred until apply mode.")
        else:
            try:
                orphans_swept = EmbeddingField.garbage_collect(
                    Memory, min_age_seconds=_EMBEDDING_ORPHAN_MIN_AGE_SECONDS
                )
            except Exception as e:
                logger.warning("embedding-orphan-sweep: garbage_collect failed: %s", e)
                findings.append(f"garbage_collect error: {e}")

            try:
                tempfiles_swept = EmbeddingField.sweep_stale_tempfiles(
                    Memory, max_age_seconds=_EMBEDDING_TEMPFILE_MAX_AGE_SECONDS
                )
            except Exception as e:
                logger.warning("embedding-orphan-sweep: sweep_stale_tempfiles failed: %s", e)
                findings.append(f"sweep_stale_tempfiles error: {e}")

            findings.append(
                f"Removed {orphans_swept} orphan .npy files and "
                f"{tempfiles_swept} stale tmp*.npy files."
            )
    except Exception as e:
        logger.warning("embedding-orphan-sweep failed: %s", e)
        return {
            "status": "error",
            "findings": findings,
            "summary": f"embedding-orphan-sweep error: {e}",
        }

    # Emit metrics — best-effort, never crash the reflection
    try:
        from analytics.collector import record_metric

        record_metric(
            "memory.embedding_orphans_swept",
            float(orphans_swept),
            dimensions={"mode": mode_str.lower().replace(" ", "_")},
        )
        record_metric(
            "memory.embedding_tempfiles_swept",
            float(tempfiles_swept),
            dimensions={"mode": mode_str.lower().replace(" ", "_")},
        )
    except Exception as e:
        logger.debug("embedding-orphan-sweep: metric emission failed: %s", e)

    summary = (
        f"embedding-orphan-sweep [{mode_str}]: {orphans_swept} orphans, {tempfiles_swept} tempfiles"
    )
    logger.info(summary)
    return {"status": "ok", "findings": findings, "summary": summary}
