"""Per-record embedding-dimension coverage detection for the hybrid-eval harness.

Ground truth measured 2026-07-17 (supersedes spike-3's mixed-dims
narrative in docs/plans/hybrid-retrieval-eval.md): the on-disk embedding
cache (``~/.popoto/content/.embeddings/Memory/``) is now UNIFORMLY
1536-dim, written by popoto's ``OpenAIProvider`` (configured at bridge
startup in ``bridge/telegram_bridge.py``), and popoto's bulk loader
``EmbeddingField.load_embeddings(Memory)`` works again. The hazard this
module was written against -- mixed-dimension files crashing that bulk
loader, and a dimension-mismatched provider silently degrading the hybrid
arm to BM25-only -- remains REAL whenever the eval process configures a
provider whose dimension differs from the stored vectors (e.g. the repo's
768-dim Ollama provider against this 1536-dim corpus), so the module is
retained as the harness's per-record dimension-match safety check.

It reads each record's own ``.npy`` file directly (keyed off that record's
own Redis key, via the same path-construction popoto itself uses) and
compares its real vector shape against the CURRENT provider's
``dimensions``. A record whose stored vector is in a different dimension is
correctly bucketed as NOT usable for the hybrid arm -- the same bucket as
"no embedding" -- rather than falsely counted as coverage the way a naive
``getattr(record, "embedding", None)`` truthiness check would (that
attribute stores only a dimension-count INT, not the vector itself, and is
truthy for stale-dimension records too).
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class EmbeddingCoverage:
    """Coverage verdict for one record.

    ``naive_embedded`` mirrors spike-3's shallow "Memory bookkeeping" check
    (the dimension-count int stored in Redis is truthy) -- kept for the
    report renderer's explicit naive-vs-real distinction, not for gating.
    ``current_provider_valid`` is the field that actually matters for the
    hybrid arm's per-record non-zero-vector assertion and for known-item
    sampling.
    """

    naive_embedded: bool
    stored_dimension: int | None
    actual_dimension: int | None
    current_provider_dimension: int
    current_provider_valid: bool


def record_embedding_coverage(record: object, current_provider_dimension: int) -> EmbeddingCoverage:
    """Determine whether ``record``'s stored embedding is usable with the CURRENT provider.

    Reads only this one record's ``.npy`` file (via
    ``GracefulEmbeddingField._embedding_path``) -- never calls
    ``EmbeddingField.load_embeddings()``, the bulk loader that crashes on
    the corpus's dimension-mismatch files (see module docstring).
    """
    from models.graceful_embedding_field import GracefulEmbeddingField

    stored_dimension = getattr(record, "embedding", None)
    naive_embedded = bool(stored_dimension)

    redis_key = getattr(record, "_redis_key", None) or record.db_key.redis_key
    model_class_name = record.__class__.__name__
    npy_path = GracefulEmbeddingField._embedding_path(model_class_name, redis_key)

    actual_dimension: int | None = None
    if os.path.exists(npy_path):
        try:
            import numpy as np

            vector = np.load(npy_path)
            actual_dimension = int(vector.shape[0])
        except Exception:
            actual_dimension = None

    current_provider_valid = (
        actual_dimension is not None and actual_dimension == current_provider_dimension
    )

    return EmbeddingCoverage(
        naive_embedded=naive_embedded,
        stored_dimension=stored_dimension,
        actual_dimension=actual_dimension,
        current_provider_dimension=current_provider_dimension,
        current_provider_valid=current_provider_valid,
    )


def coverage_report(records: list, current_provider_dimension: int) -> dict:
    """Summarize naive-vs-real embedding coverage across ``records``.

    Used by the report renderer to surface BOTH numbers explicitly (per the
    build-harness task brief): the naive Memory-bookkeeping count (a
    false-positive-shaped check per spike-3) and the real
    current-provider-dimension-valid count (the number that actually
    determines how many known-item queries can be built from a genuinely
    embedded record without ``--backfill-embeddings``).
    """
    coverages = [record_embedding_coverage(r, current_provider_dimension) for r in records]
    total = len(coverages)
    naive_count = sum(1 for c in coverages if c.naive_embedded)
    valid_count = sum(1 for c in coverages if c.current_provider_valid)
    return {
        "total": total,
        "naive_embedded_count": naive_count,
        "current_provider_valid_count": valid_count,
        "naive_embedded_pct": (naive_count / total * 100) if total else 0.0,
        "current_provider_valid_pct": (valid_count / total * 100) if total else 0.0,
    }
