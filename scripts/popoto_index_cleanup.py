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
import os
import threading

logger = logging.getLogger(__name__)

# Per-model rebuild_indexes() wall-clock budget. Provisional/tunable — grain
# of salt on the exact value; override via env if a slow model needs more
# headroom without a code change.
_REBUILD_TIMEOUT_SECONDS = int(os.environ.get("POPOTO_INDEX_CLEANUP_REBUILD_TIMEOUT_SECONDS", 30))

# Live scheduler-state models excluded from the destructive rebuild_indexes()
# sweep. rebuild_indexes() deletes a model's class-set + KeyField indexes before
# reconstructing them; during that window Reflection.query.filter(name=...)
# returns empty, so a concurrent scheduler tick's get_or_create() spawns a fresh
# duplicate record with ran_at=None — which an every:-scheduled job reads as
# "never run" and fires on every tick (the daily-digest burst-fire bug). These
# small, continuously-save()-indexed models gain nothing from a periodic
# destructive rebuild; their orphans are negligible. (ReflectionRun is already
# excluded — it is not in models.__all__.)
_SCHEDULER_STATE_MODELS = frozenset({"Reflection"})

# Models whose index hygiene is already guarded elsewhere and must NOT go
# through this generic raw rebuild_indexes() sweep. AgentSession's base
# rebuild_indexes() has no identity-less guard at all — running it here
# re-inflates identity-less "AgentSession:None:..." phantom hash keys (the
# ~7.4M-key Redis flood of 2026-07-22, issue #2207). AgentSession index
# hygiene is instead handled by the A1-guarded ``AgentSession.repair_indexes()``
# called unconditionally from worker Step 2
# (``session_health.cleanup_corrupted_agent_sessions``) and by the hourly
# ``agent-session-cleanup`` reflection. Excluding it here is the primary fix
# for #2207 — do not remove without an equivalent guard in this sweep.
_GUARDED_ELSEWHERE = frozenset({"AgentSession"})


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
    during rebuild_indexes() and are handled separately — live
    scheduler-state models (``_SCHEDULER_STATE_MODELS``) whose indexes must not
    be destructively dropped while the scheduler is ticking — and models whose
    index hygiene is already guarded elsewhere (``_GUARDED_ELSEWHERE``, e.g.
    AgentSession).
    """
    try:
        import models as models_pkg

        model_classes = []
        seen_class_names = set()
        for name in models_pkg.__all__:
            obj = getattr(models_pkg, name, None)
            if obj is not None and hasattr(obj, "rebuild_indexes"):
                # models.__all__ can list the same class under multiple export
                # names (e.g. a "SessionLog" alias for AgentSession) — exclude
                # and dedupe by the class's own __name__, not the __all__
                # string, so an alias can't smuggle a guarded model past the
                # exclusion checks below.
                class_name = obj.__name__
                if class_name in seen_class_names:
                    continue
                if class_name in _SCHEDULER_STATE_MODELS:
                    logger.debug(
                        f"[popoto-cleanup] Skipping {class_name} "
                        "(live scheduler-state — rebuild races get_or_create)"
                    )
                    continue
                if class_name in _GUARDED_ELSEWHERE:
                    logger.debug(
                        f"[popoto-cleanup] Skipping {class_name} "
                        "(index hygiene guarded elsewhere — see _GUARDED_ELSEWHERE)"
                    )
                    continue
                if _has_embedding_field(obj):
                    logger.debug(
                        f"[popoto-cleanup] Skipping {class_name} "
                        "(has EmbeddingField — requires Ollama)"
                    )
                    continue
                seen_class_names.add(class_name)
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
    import os

    # ImportError must surface visibly: on popoto<1.6.0 these helpers do not
    # exist, and silently returning 0 here would mask a real configuration
    # problem (the dry-run path would report "Would remove ~0 disk orphans"
    # without explaining why). Log at WARNING and short-circuit with 0 so
    # the caller can still display a sensible summary.
    try:
        from popoto.fields.embedding_field import (
            _TMP_NPY_RE,
            _compute_expected_keep,
            _get_embeddings_dir,
        )
    except ImportError as e:
        logger.warning(
            "[popoto-cleanup] disk-orphan count unavailable: popoto<1.6.0 "
            "(missing _compute_expected_keep / _get_embeddings_dir / "
            "_TMP_NPY_RE) — install popoto>=1.6.0 to enable. Detail: %s",
            e,
        )
        return 0

    try:
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
        # Narrow non-ImportError failures (Redis, filesystem race) — keep at
        # DEBUG since these are expected during normal operation.
        logger.debug(
            f"[popoto-cleanup] Failed to count disk orphans for {model_class.__name__}: {e}"
        )
        return 0


def _get_keyspace_size(model_class) -> int:
    """Return the size of a model's class-set index (cheap keyspace signal).

    Uses SCARD on the canonical Popoto class-set key
    (``model_class._meta.db_class_set_key.redis_key``). Captured before and
    after each rebuild attempt so index-count swings (e.g. phantom-record
    inflation) are visible in the ``run_cleanup`` summary and worker startup
    log without an expensive full scan.
    """
    try:
        from popoto.redis_db import POPOTO_REDIS_DB

        class_set_key = model_class._meta.db_class_set_key.redis_key
        return POPOTO_REDIS_DB.scard(class_set_key)
    except Exception as e:
        logger.debug(
            f"[popoto-cleanup] Failed to get keyspace size for {model_class.__name__}: {e}"
        )
        return 0


def _run_rebuild_with_timeout(model_class):
    """Run ``model_class.rebuild_indexes()`` in a daemon thread with a wall-clock budget.

    Returns ``(rebuilt_count, timed_out, error)``. On timeout, ``rebuilt_count``
    is ``None``, ``timed_out`` is ``True``, and the thread is abandoned
    (never joined again) — it keeps running in the background but, being a
    daemon thread, can never block interpreter shutdown.

    This deliberately avoids ``concurrent.futures``' pool-based executor: its
    worker threads are non-daemon and are still joined at interpreter exit
    via its module-level ``_python_exit`` handler. Exiting a
    ``with <pool executor> as executor:`` block calls
    ``executor.shutdown(wait=True)``, which blocks forever joining a
    still-running rebuild thread — even after ``future.result(timeout=...)``
    already raised a timeout error. That mismatch (illusory timeout, real
    blocking join) caused the 8-hour zero-heartbeat worker wedge (#2207).
    """
    outcome: dict = {}

    def _target():
        try:
            outcome["count"] = model_class.rebuild_indexes()
        except Exception as e:  # noqa: BLE001 - surfaced to caller via outcome
            outcome["error"] = e

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join(timeout=_REBUILD_TIMEOUT_SECONDS)

    if thread.is_alive():
        return None, True, None
    if "error" in outcome:
        return None, False, outcome["error"]
    return outcome.get("count"), False, None


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

            # Cheap keyspace signal captured around the rebuild attempt so
            # inflation/deflation is visible in the summary regardless of
            # whether the rebuild completes, errors, or times out.
            keyspace_before = _get_keyspace_size(model_class)

            # Run rebuild with timeout — EmbeddingField models can hang on Redis SCAN
            rebuilt_count, timed_out, rebuild_error = _run_rebuild_with_timeout(model_class)

            keyspace_after = _get_keyspace_size(model_class)
            keyspace_delta = keyspace_after - keyspace_before

            if timed_out:
                error_msg = (
                    f"{model_name}: rebuild_indexes timed out after {_REBUILD_TIMEOUT_SECONDS}s"
                )
                errors.append(error_msg)
                results[model_name] = {
                    "status": "timeout",
                    "keyspace_before": keyspace_before,
                    "keyspace_after": keyspace_after,
                    "keyspace_delta": keyspace_delta,
                }
                logger.warning(
                    f"[popoto-cleanup] {error_msg} — abandoning rebuild thread (daemon, "
                    f"never joined further) and continuing sweep "
                    f"(keyspace {keyspace_before} -> {keyspace_after}, delta {keyspace_delta:+d})"
                )
                continue

            if rebuild_error is not None:
                raise rebuild_error

            total_rebuilt += rebuilt_count

            results[model_name] = {
                "orphans_found": orphan_count,
                "records_rebuilt": rebuilt_count,
                "status": "ok",
                "keyspace_before": keyspace_before,
                "keyspace_after": keyspace_after,
                "keyspace_delta": keyspace_delta,
            }

            if orphan_count > 0:
                logger.info(
                    f"[popoto-cleanup] {model_name}: {orphan_count} orphans cleaned, "
                    f"{rebuilt_count} records reindexed "
                    f"(keyspace {keyspace_before} -> {keyspace_after}, delta {keyspace_delta:+d})"
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
