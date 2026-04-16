"""Ollama-based embedding provider for the Memory model's EmbeddingField.

Implements the provider interface expected by popoto's EmbeddingField:
  - embed(texts, input_type) -> list[list[float]]
  - dimensions -> int

Uses the local Ollama instance at localhost:11434 with the nomic-embed-text
model (768 dimensions). Gracefully degrades: if Ollama is unreachable,
configure_embedding_provider() logs a warning and leaves the global
provider unset, so EmbeddingField skips embedding on save and retrieval
falls back to the existing 3-signal RRF.
"""

from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)

# Default Ollama configuration
OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL = "nomic-embed-text"
OLLAMA_DIMENSIONS = 768
OLLAMA_TIMEOUT = 5.0  # seconds


class OllamaEmbeddingProvider:
    """Embedding provider that calls a local Ollama instance.

    Implements the interface expected by popoto's EmbeddingField:
      - embed(texts: list[str], input_type: str) -> list[list[float]]
      - dimensions: int property

    Args:
        model: Ollama model name. Default: nomic-embed-text.
        base_url: Ollama API base URL. Default: http://localhost:11434.
        timeout: HTTP request timeout in seconds. Default: 5.0.
    """

    def __init__(
        self,
        model: str = OLLAMA_MODEL,
        base_url: str = OLLAMA_BASE_URL,
        timeout: float = OLLAMA_TIMEOUT,
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._dimensions = OLLAMA_DIMENSIONS

    @property
    def dimensions(self) -> int:
        """Return the embedding dimension count for this model."""
        return self._dimensions

    def embed(self, texts: list[str], input_type: str = "document") -> list[list[float]]:
        """Generate embeddings for a list of texts via Ollama.

        Args:
            texts: List of text strings to embed.
            input_type: Either "document" (for storage) or "query" (for search).
                Ollama's embed API does not distinguish, but the interface requires it.

        Returns:
            List of embedding vectors (list of floats), one per input text.

        Raises:
            RuntimeError: If the Ollama API call fails.
        """
        if not texts:
            return []

        try:
            response = requests.post(
                f"{self.base_url}/api/embed",
                json={"model": self.model, "input": texts},
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()

            embeddings = data.get("embeddings", [])
            if len(embeddings) != len(texts):
                raise RuntimeError(
                    f"Ollama returned {len(embeddings)} embeddings for {len(texts)} inputs"
                )

            # Validate dimensions on first embedding
            if embeddings and len(embeddings[0]) != self._dimensions:
                # Update dimensions to match actual model output
                self._dimensions = len(embeddings[0])

            return embeddings

        except requests.exceptions.ConnectionError as e:
            raise RuntimeError(f"Ollama unreachable at {self.base_url}: {e}") from e
        except requests.exceptions.Timeout as e:
            raise RuntimeError(f"Ollama request timed out after {self.timeout}s: {e}") from e
        except requests.exceptions.HTTPError as e:
            raise RuntimeError(f"Ollama API error: {e}") from e

    def is_available(self) -> bool:
        """Check if Ollama is reachable and the model is loaded.

        Returns:
            True if Ollama responds to a health check, False otherwise.
        """
        try:
            response = requests.get(
                f"{self.base_url}/api/tags",
                timeout=2.0,
            )
            response.raise_for_status()
            models = response.json().get("models", [])
            model_names = [m.get("name", "").split(":")[0] for m in models]
            return self.model.split(":")[0] in model_names
        except Exception:
            return False


def configure_embedding_provider() -> OllamaEmbeddingProvider | None:
    """Configure the global embedding provider for popoto's EmbeddingField.

    Creates an OllamaEmbeddingProvider and sets it as the default provider
    via popoto's set_default_provider(). If Ollama is unreachable or the
    model is not available, logs a warning and returns None (no provider set).

    Returns:
        The configured provider instance, or None if setup failed.
    """
    try:
        from popoto.fields.embedding_field import set_default_provider
    except ImportError:
        logger.warning(
            "[embedding_provider] popoto EmbeddingField not available; "
            "skipping embedding provider configuration"
        )
        return None

    provider = OllamaEmbeddingProvider()

    if not provider.is_available():
        logger.warning(
            f"[embedding_provider] Ollama model '{provider.model}' not available at "
            f"{provider.base_url}; memory embeddings disabled. "
            f"Run 'ollama pull {provider.model}' to enable semantic recall."
        )
        return None

    set_default_provider(provider)
    logger.info(
        f"[embedding_provider] Configured OllamaEmbeddingProvider "
        f"(model={provider.model}, dims={provider.dimensions})"
    )
    return provider
