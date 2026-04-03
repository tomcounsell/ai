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

import logging

logger = logging.getLogger(__name__)


def _get_all_models() -> list:
    """Import and return all Popoto models from models/__init__.__all__."""
    try:
        import models as models_pkg

        model_classes = []
        for name in models_pkg.__all__:
            obj = getattr(models_pkg, name, None)
            if obj is not None and hasattr(obj, "rebuild_indexes"):
                model_classes.append(obj)
        return model_classes
    except Exception as e:
        logger.error(f"[popoto-cleanup] Failed to import models: {e}")
        return []


def _count_orphans(model_class) -> int:
    """Count orphaned index entries for a model by scanning index sets.

    Checks each entry in the model's class set against actual hash existence.
    Returns the count of index entries pointing to non-existent hashes.
    """
    try:
        from popoto.redis_db import POPOTO_REDIS_DB

        model_name = model_class.__name__
        class_set_key = f"{model_name}:_all"

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

            # Run rebuild
            rebuilt_count = model_class.rebuild_indexes()
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
                logger.debug(
                    f"[popoto-cleanup] {model_name}: clean ({rebuilt_count} records)"
                )
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
