"""Popoto index cleanup reflection.

Iterates all Popoto models, counts orphaned index entries, and runs
rebuild_indexes() on each model to clean them up. Designed to run as a
daily reflection via the reflection scheduler.

The cleanup process is SCAN-based (production-safe) and self-correcting:
- rebuild_indexes() uses Redis SCAN (cursor-based, non-blocking)
- Concurrent creates/deletes are safe: the next run fixes any inconsistencies
- Each model is processed independently; one model failure does not abort the sweep

See docs/features/popoto-index-hygiene.md for full design details.
"""

import concurrent.futures
import logging

logger = logging.getLogger(__name__)

_REBUILD_TIMEOUT_SECONDS = 30


def _has_embedding_field(model_class) -> bool:
    """Return True if a model has any EmbeddingField.

    EmbeddingField models require live Ollama calls during rebuild_indexes()
    (the on_save() hook regenerates embeddings), making them unsuitable for
    startup cleanup. They're skipped here to avoid hanging the worker.
    """
    try:
        from popoto.fields.embedding_field import EmbeddingField

        return any(isinstance(field, EmbeddingField) for field in model_class._meta.fields.values())
    except Exception:
        return False


def _get_all_models() -> list:
    """Import and return all Popoto models from models/__init__.__all__.

    Excludes models with EmbeddingField — those require live Ollama calls
    during rebuild_indexes() and are handled separately.
    """
    try:
        import models as models_pkg

        model_classes = []
        for name in models_pkg.__all__:
            obj = getattr(models_pkg, name, None)
            if obj is not None and hasattr(obj, "rebuild_indexes"):
                if _has_embedding_field(obj):
                    logger.debug(
                        f"[popoto-cleanup] Skipping {name} (has EmbeddingField — requires Ollama)"
                    )
                    continue
                model_classes.append(obj)
        return model_classes
    except Exception as e:
        logger.error(f"[popoto-cleanup] Failed to import models: {e}")
        return []


def _count_orphans(model_class) -> int:
    """Count orphaned index entries for a model by scanning index sets.

    Checks each entry in the model's class set against actual hash existence.
    Returns the count of index entries pointing to non-existent hashes.

    Uses the canonical Popoto class-set key
    ``model_class._meta.db_class_set_key.redis_key`` (= ``$Class:{Name}``).
    The legacy ``{Name}:_all`` key is empty in production — reading from
    it would always return zero orphans regardless of true state, which
    is the bug this function was originally written to detect (#1214).
    """
    try:
        from popoto.redis_db import POPOTO_REDIS_DB

        class_set_key = model_class._meta.db_class_set_key.redis_key

        # Get all members of the class set (index of all instances)
        members = POPOTO_REDIS_DB.smembers(class_set_key)
        if not members:
            return 0

        orphan_count = 0
        for member in members:
            key = member.decode() if isinstance(member, bytes) else str(member)
            if not POPOTO_REDIS_DB.exists(key):
                orphan_count += 1

        return orphan_count
    except Exception as e:
        logger.debug(f"[popoto-cleanup] Failed to count orphans for {model_class.__name__}: {e}")
        return 0


def _count_disk_orphans(model_class) -> int:
    """Count on-disk .npy files for ``model_class`` that have no live record.

    Walks ``~/.popoto/content/.embeddings/{ModelName}/`` and counts files
    that are NOT in the expected-to-survive set (computed via the shared
    Popoto helper :func:`popoto.fields.embedding_field._compute_expected_keep`)
    AND are not atomic-write tempfiles (``tmp*.npy``, swept separately).

    Returns 0 if the embedding directory does not exist (fresh install).

    Read-only — never deletes. Use
    :meth:`popoto.fields.embedding_field.EmbeddingField.garbage_collect`
    to actually remove the orphans.
    """
    try:
        import os

        from popoto.fields.embedding_field import (
            _TMP_NPY_RE,
            _compute_expected_keep,
            _get_embeddings_dir,
        )

        model_name = model_class.__name__
        emb_dir = os.path.join(_get_embeddings_dir(), model_name)
        if not os.path.isdir(emb_dir):
            return 0

        expected_keep = _compute_expected_keep(model_class)

        try:
            disk_files = os.listdir(emb_dir)
        except OSError:
            return 0

        orphan_count = 0
        for filename in disk_files:
            if not filename.endswith(".npy"):
                continue
            if _TMP_NPY_RE.match(filename):
                continue
            if filename in expected_keep:
                continue
            orphan_count += 1

        return orphan_count
    except Exception as e:
        logger.debug(
            f"[popoto-cleanup] Failed to count disk orphans for {model_class.__name__}: {e}"
        )
        return 0


def run_cleanup() -> dict:
    """Run cleanup across all Popoto models.

    For each model:
    1. Count orphaned index entries (dry-run scan)
    2. Run rebuild_indexes() to clean them up
    3. Log per-model results

    Returns:
        Summary dict with per-model orphan counts and rebuild results.
    """
    model_classes = _get_all_models()
    if not model_classes:
        logger.warning("[popoto-cleanup] No models found to clean")
        return {"status": "no_models", "models_processed": 0}

    results = {}
    total_orphans = 0
    total_rebuilt = 0
    errors = []

    for model_class in model_classes:
        model_name = model_class.__name__
        try:
            # Count orphans before cleanup
            orphan_count = _count_orphans(model_class)
            total_orphans += orphan_count

            # Run rebuild with timeout — EmbeddingField models can hang on Redis SCAN
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(model_class.rebuild_indexes)
                    rebuilt_count = future.result(timeout=_REBUILD_TIMEOUT_SECONDS)
            except concurrent.futures.TimeoutError:
                error_msg = (
                    f"{model_name}: rebuild_indexes timed out after {_REBUILD_TIMEOUT_SECONDS}s"
                )
                errors.append(error_msg)
                results[model_name] = {"status": "timeout"}
                logger.warning(f"[popoto-cleanup] {error_msg} — skipping")
                continue
            total_rebuilt += rebuilt_count

            results[model_name] = {
                "orphans_found": orphan_count,
                "records_rebuilt": rebuilt_count,
                "status": "ok",
            }

            if orphan_count > 0:
                logger.info(
                    f"[popoto-cleanup] {model_name}: {orphan_count} orphans cleaned, "
                    f"{rebuilt_count} records reindexed"
                )
            else:
                logger.debug(f"[popoto-cleanup] {model_name}: clean ({rebuilt_count} records)")
        except Exception as e:
            error_msg = f"{model_name}: {e}"
            errors.append(error_msg)
            results[model_name] = {"status": "error", "error": str(e)}
            logger.error(f"[popoto-cleanup] Error processing {model_name}: {e}")

    summary = {
        "status": "completed",
        "models_processed": len(model_classes),
        "total_orphans_found": total_orphans,
        "total_records_rebuilt": total_rebuilt,
        "errors": errors,
        "per_model": results,
    }

    logger.info(
        f"[popoto-cleanup] Complete: {len(model_classes)} models processed, "
        f"{total_orphans} orphans found, {total_rebuilt} records rebuilt, "
        f"{len(errors)} errors"
    )

    return summary
