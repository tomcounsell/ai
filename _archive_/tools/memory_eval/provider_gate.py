"""Embedding-provider hard gate for the hybrid-retrieval eval harness.

Concern 1 (docs/plans/hybrid-retrieval-eval.md): ``Memory.embedding`` is a
``GracefulEmbeddingField`` (models/memory.py) that degrades SILENTLY when
the embedding provider is unreachable -- it just persists the record
without a vector. If the harness ran a comparison while the provider was
down, the forced-hybrid arm would collapse to BM25-only and the harness
could confidently report a false "do-not-adopt" verdict that actually
measured a broken embedding path, not hybrid retrieval. The harness MUST
fail closed instead: assert the provider is present BEFORE constructing
any query set or running any arm, and raise + exit non-zero if not.

The gate is TWO checks, both of which abort non-zero before any scoring:

1. **Provider presence** (:func:`assert_provider_available`): a default
   embedding provider is configured at all.
2. **Dimension match** (:func:`assert_provider_dimension_match`): the
   provider's output dimension equals the dimension of the vectors
   actually stored on disk for the eval corpus. Ground truth measured
   2026-07-17 (supersedes the plan's spike-3 mixed-dims narrative): the
   corpus embedding cache is uniformly 1536-dim, written by popoto's
   ``OpenAIProvider``, which ``bridge/telegram_bridge.py`` configures at
   bridge startup. The repo's Ollama provider
   (``agent/embedding_provider.py``, nomic-embed-text, 768-dim) is
   dimension-MISMATCHED with that corpus: popoto's hybrid pull path
   catches the resulting matmul shape error per query and silently
   degrades to BM25-only -- exactly the false-verdict trap this gate
   exists to stop. The harness therefore configures the corpus-matched
   ``OpenAIProvider`` itself at entry
   (``hybrid_eval.configure_corpus_provider()``); it never relies on the
   Ollama provider that ``models/memory.py``'s ``apply_defaults()``
   configures implicitly at import time.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class ProviderUnavailableError(RuntimeError):
    """Raised when the embedding provider is not configured/reachable.

    The harness's entry point catches this at the top level and exits
    non-zero BEFORE constructing any query set or running any arm -- a
    degraded provider must never silently produce a scored comparison.
    """


def assert_provider_available() -> object:
    """Fail-closed provider-presence gate.

    Returns the configured provider instance on success. Raises
    :class:`ProviderUnavailableError` if ``get_default_provider()`` returns
    ``None``.

    This function deliberately does NOT import ``agent`` itself -- callers
    own that ordering (see module docstring gotcha), and keeping the import
    out of this function keeps it trivially unit-testable via monkeypatching
    ``popoto.fields.embedding_field.get_default_provider``.
    """
    from popoto.fields.embedding_field import get_default_provider

    provider = get_default_provider()
    if provider is None:
        raise ProviderUnavailableError(
            "Embedding provider is not configured/reachable "
            "(get_default_provider() returned None). The hybrid-eval harness "
            "fails closed here rather than silently scoring a degraded run "
            "-- see docs/plans/hybrid-retrieval-eval.md Concern 1. Run "
            "hybrid_eval.configure_corpus_provider() before this check."
        )
    return provider


class ProviderDimensionMismatchError(RuntimeError):
    """Raised when the configured provider's embedding dimension does not
    match the dimension of the vectors stored on disk for the eval corpus.

    A mismatched provider makes popoto's hybrid vector search fail per
    query (matmul shape error) and silently degrade to BM25-only, so a
    "hybrid" run would not measure hybrid at all (Concern 1). Abort, never
    score.
    """


def assert_provider_dimension_match(provider: object, sample_records: list) -> int:
    """Fail-closed dimension-match gate.

    Compares the provider's output dimension (its ``dimensions`` attribute
    when present, else the length of a probe embedding) against the actual
    stored vector dimension of sampled corpus records (read from each
    record's own ``.npy`` file via
    :func:`tools.memory_eval.embedding_coverage.record_embedding_coverage`).

    Returns the matched dimension on success. Raises
    :class:`ProviderDimensionMismatchError` when any sampled stored vector's
    dimension differs from the provider's, or when no sampled record has a
    stored vector at all.
    """
    from tools.memory_eval.embedding_coverage import record_embedding_coverage

    provider_dim = getattr(provider, "dimensions", None)
    if provider_dim is None:
        probe = provider.embed(["dimension probe"], input_type="query")
        provider_dim = len(probe[0]) if probe and probe[0] else 0
    provider_dim = int(provider_dim)
    if provider_dim <= 0:
        raise ProviderDimensionMismatchError(
            "Could not determine the provider's embedding dimension "
            "(no `dimensions` attribute and empty probe embedding)."
        )

    stored_dims = set()
    for record in sample_records:
        coverage = record_embedding_coverage(record, provider_dim)
        if coverage.actual_dimension is not None:
            stored_dims.add(coverage.actual_dimension)

    if not stored_dims:
        raise ProviderDimensionMismatchError(
            f"None of the {len(sample_records)} sampled corpus records has a "
            "stored embedding vector on disk -- cannot verify dimension "
            "match, and a vectorless corpus cannot support a hybrid arm."
        )
    if stored_dims != {provider_dim}:
        raise ProviderDimensionMismatchError(
            f"Provider embedding dimension ({provider_dim}) does not match "
            f"the stored corpus vector dimension(s) {sorted(stored_dims)}. "
            "A mismatched provider silently degrades the hybrid arm to "
            "BM25-only (Concern 1) -- configure the corpus-matched provider "
            "(popoto OpenAIProvider; see module docstring) and re-run."
        )
    return provider_dim
