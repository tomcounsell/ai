"""models/graceful_embedding_field.py — EmbeddingField that persists on provider failure.

Why this exists (issue #1904):
    popoto's ``EmbeddingField.on_save`` calls the embedding provider (local Ollama,
    5s read timeout) synchronously inside popoto's ``Model.save`` field loop. On a
    provider timeout/unreachable/HTTP error the parent ``on_save`` raises
    ``RuntimeError`` (and ``ValueError`` on a dimension mismatch, ``OSError`` on the
    atomic ``.npy`` write). That exception propagates out of ``Model.save`` *before*
    ``internal_pipeline.execute()`` runs, so the queued main ``hset`` never commits —
    the entire Memory record (content, BM25 index, relevance) is lost, not merely
    degraded. Under concurrent load (the exact condition that saturates Ollama) this
    silently drops human messages.

The contract:
    ``GracefulEmbeddingField.on_save`` wraps the parent call in a try/except. On a
    provider/write failure it records the degradation and returns the pipeline
    unchanged, so the already-queued main record ``hset`` commits normally. The
    record persists with ``embedding = None`` (the field default) — the queryable
    "no vector" marker. Recall still serves it via the other RRF signals (BM25,
    relevance, confidence, bloom); the ``memory-embedding-backfill`` reflection
    re-embeds it later once the provider is healthy.

Pinned-popoto constraint:
    The parent ``on_save`` raises *before* writing the ``.npy`` file or queuing the
    embedding ``hset`` (verified against popoto>=1.7.1, see
    ``popoto/fields/embedding_field.py`` on_save ordering). So a caught error leaves
    no partial embedding artifact and no half-written pipeline op. If a future popoto
    reorders on_save to write the ``.npy`` before raising, a caught error could leave
    an orphan ``.npy`` — that case is already self-healing via the existing
    ``embedding-orphan-sweep`` reflection.

Warning throttle (issue #1904 critique C2):
    A per-save ``logger.warning`` would flood under the exact trigger condition
    (Ollama saturation → every concurrent save degrades). Instead a module-level
    monotonic-timestamp throttle emits at most one warning per throttle window, while
    a counter increments on every degraded save. The count is surfaced in the
    ``memory-embedding-backfill`` reflection summary so silent-embedding-loss stays
    observable without log spam.
"""

from __future__ import annotations

import logging
import threading
import time

from popoto.fields.embedding_field import EmbeddingField

logger = logging.getLogger(__name__)

# Emit at most one degradation warning per this many seconds. The counter below
# still increments on every degraded save — the throttle only gates the log line
# so a saturated Ollama does not flood the logs (critique C2).
_WARN_THROTTLE_SECONDS = 60.0

# Module-level degradation state. Guarded by a lock because concurrent worker
# saves can degrade simultaneously (Ollama saturation is inherently concurrent).
_state_lock = threading.Lock()
_degradation_count = 0
# Sentinel so the very first degradation always warns regardless of process
# uptime: -inf means (now - _last_warn_monotonic) always exceeds the window.
_last_warn_monotonic = float("-inf")


def _record_degradation(model_instance, exc: Exception) -> None:
    """Count a degraded save and emit a throttled warning.

    Always increments the counter; emits ``logger.warning`` only when the throttle
    window has elapsed since the last warning. Never raises — a broken logger must
    not turn a graceful degradation back into a dropped record.
    """
    global _degradation_count, _last_warn_monotonic
    try:
        model_name = getattr(type(model_instance), "__name__", "Model")
        now = time.monotonic()
        should_warn = False
        with _state_lock:
            _degradation_count += 1
            count = _degradation_count
            if now - _last_warn_monotonic >= _WARN_THROTTLE_SECONDS:
                _last_warn_monotonic = now
                should_warn = True
        if should_warn:
            logger.warning(
                "Embedding degraded — persisting %s without vector "
                "(%d degraded saves since start): %s",
                model_name,
                count,
                exc,
            )
    except Exception:
        # Never let observability crash the writer path.
        pass


def get_degradation_count() -> int:
    """Return the number of degraded (vectorless) saves observed this process.

    Surfaced in the ``memory-embedding-backfill`` reflection summary so operators
    can see in-process silent-embedding-loss even when the warning is throttled.
    """
    with _state_lock:
        return _degradation_count


def reset_degradation_state() -> None:
    """Reset the degradation counter and throttle clock (test hook)."""
    global _degradation_count, _last_warn_monotonic
    with _state_lock:
        _degradation_count = 0
        _last_warn_monotonic = float("-inf")


class GracefulEmbeddingField(EmbeddingField):
    """An ``EmbeddingField`` that persists the record when embedding fails.

    Storage-identical to ``EmbeddingField`` (same dimension-count int / ``None``,
    same ``.npy`` layout) — swapping it into a model requires no data migration.
    The only behavioral change is that a provider/write failure during ``on_save``
    no longer aborts the enclosing ``Model.save``: the record commits without a
    vector instead of vanishing.
    """

    @classmethod
    def on_save(cls, model_instance, field_name, field_value, pipeline=None, **kwargs):
        """Delegate to the parent, catching provider/write failures.

        Catches ``RuntimeError`` (provider timeout/unreachable/HTTP error, re-raised
        by the parent), ``ValueError`` (dimension mismatch), and ``OSError`` (atomic
        ``.npy`` write failure). On any of these the queued main record ``hset``
        stays intact, so returning the pipeline lets ``Model.save`` commit the record
        without a vector. Any other exception type still propagates — a genuinely
        un-persistable error should reach ``Memory.safe_save``'s backstop.
        """
        try:
            return super().on_save(
                model_instance, field_name, field_value, pipeline=pipeline, **kwargs
            )
        except (RuntimeError, ValueError, OSError) as exc:
            _record_degradation(model_instance, exc)
            return pipeline if pipeline else None
