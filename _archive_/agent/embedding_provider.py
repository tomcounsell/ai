"""Corpus-matched embedding provider configuration for Memory's EmbeddingField.

Configures popoto's ``OpenAIProvider`` (text-embedding-3-small, 1536-dim) as
the process-wide default embedding provider -- the SAME provider the bridge
configures at startup (``bridge/telegram_bridge.py``) and the provider that
wrote every vector in the on-disk embedding cache
(``~/.popoto/content/.embeddings/Memory/``, measured uniformly 1536-dim on
2026-07-17 during the #2082 hybrid-retrieval eval).

Why one provider everywhere: a process configuring a provider with a
DIFFERENT output dimension than the stored corpus silently breaks vector
recall -- popoto's similarity paths catch the matmul shape mismatch per
query and degrade to lexical-only, and any save from that process would
write a mixed-dimension vector into the cache. The previous local Ollama
provider here (nomic-embed-text, 768-dim) had exactly that failure mode
against the 1536-dim corpus: worker/CLI processes ran with a dead cosine
signal while the bridge wrote 1536-dim vectors.

Graceful degradation: if ``OPENAI_API_KEY`` is not available,
``configure_embedding_provider()`` logs a warning and leaves the global
provider unset. ``GracefulEmbeddingField`` then persists records without a
vector and the ``memory-embedding-backfill`` reflection re-embeds them once
the provider is healthy again.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent


def configure_embedding_provider():
    """Configure the corpus-matched global embedding provider.

    Creates popoto's ``OpenAIProvider`` (1536-dim, matching the stored
    Memory embedding corpus) and installs it as the process-wide default
    via ``popoto.configure``. If ``OPENAI_API_KEY`` is missing (after
    falling back to the repo ``.env``) or provider construction fails,
    logs a warning and returns ``None`` -- embedding degrades gracefully,
    it never crashes the caller.

    Returns:
        The configured provider instance, or None if setup failed.
    """
    try:
        if not os.getenv("OPENAI_API_KEY"):
            # CLI/worker processes don't always inherit the vault env; the
            # repo .env is a symlink to the secrets vault.
            from dotenv import load_dotenv

            load_dotenv(REPO_ROOT / ".env")
        if not os.getenv("OPENAI_API_KEY"):
            logger.warning(
                "[embedding_provider] OPENAI_API_KEY not available; memory "
                "embeddings disabled for this process (GracefulEmbeddingField "
                "degrades to vectorless saves; the backfill reflection heals "
                "them once the provider is configured)."
            )
            return None

        import popoto
        from popoto.embeddings.openai import OpenAIProvider

        provider = OpenAIProvider()
        popoto.configure(embedding_provider=provider)
        logger.info(
            "[embedding_provider] Configured corpus-matched OpenAIProvider "
            f"(dims={getattr(provider, 'dimensions', 'unknown')})"
        )
        return provider
    except Exception as e:
        logger.warning(f"[embedding_provider] provider configuration failed: {e}")
        return None
