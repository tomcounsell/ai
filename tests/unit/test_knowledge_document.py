"""Tests for the KnowledgeDocument model."""

import hashlib
import random

import pytest


@pytest.mark.unit
class TestKnowledgeDocumentModel:
    """Test KnowledgeDocument model definition and methods."""

    def test_model_importable(self):
        """KnowledgeDocument model can be imported."""
        from models.knowledge_document import KnowledgeDocument

        assert KnowledgeDocument is not None

    def test_model_has_required_fields(self):
        """Model has all fields specified in the plan."""
        from models.knowledge_document import KnowledgeDocument

        field_names = [
            "doc_id",
            "file_path",
            "project_key",
            "scope",
            "content",
            "embedding",
            "content_hash",
            "last_modified",
        ]
        for field_name in field_names:
            assert hasattr(KnowledgeDocument, field_name), f"Missing field: {field_name}"

    def test_safe_upsert_nonexistent_file(self):
        """safe_upsert returns None for nonexistent file."""
        from models.knowledge_document import KnowledgeDocument

        result = KnowledgeDocument.safe_upsert("/nonexistent/file.md", "test", "client")
        assert result is None

    def test_safe_upsert_empty_file(self, tmp_path):
        """safe_upsert returns None for empty file."""
        from models.knowledge_document import KnowledgeDocument

        empty_file = tmp_path / "empty.md"
        empty_file.write_text("")

        result = KnowledgeDocument.safe_upsert(str(empty_file), "test", "client")
        assert result is None

    def test_delete_by_path_nonexistent(self):
        """delete_by_path returns False for nonexistent document."""
        from models.knowledge_document import KnowledgeDocument

        result = KnowledgeDocument.delete_by_path("/nonexistent/file.md")
        assert result is False


@pytest.mark.unit
class TestKnowledgeDocumentHelpers:
    """Test helper functions."""

    def test_content_hash_consistency(self):
        """SHA-256 hash is deterministic for same content."""
        content = "test content for hashing"
        hash1 = hashlib.sha256(content.encode("utf-8")).hexdigest()
        hash2 = hashlib.sha256(content.encode("utf-8")).hexdigest()
        assert hash1 == hash2

    def test_scope_default(self):
        """Default scope is 'client'."""
        from models.knowledge_document import KnowledgeDocument

        # Check the field default
        KnowledgeDocument.__new__(KnowledgeDocument)
        # The field descriptor should have default="client"
        assert KnowledgeDocument.scope.default == "client"


def _dense_table_content(min_chars: int) -> str:
    """Build table-like content that tokenizes far denser than plain prose.

    Mirrors the fixture in tests/unit/test_chunking.py -- reproduces the
    failure mode from issue #1876 (dense vault docs whose char/token ratio
    breaks a char-based truncation cap) without depending on ~/work-vault.
    """
    rng = random.Random(1876)
    lines = []
    total_chars = 0
    i = 0
    while total_chars < min_chars:
        row = (
            f"| {i} | {rng.randint(0, 999999):06d} | {rng.randint(0, 999999):06d} "
            f"| {rng.randint(0, 999999):06d} | active |\n"
        )
        lines.append(row)
        total_chars += len(row)
        i += 1
    return "".join(lines)


@pytest.mark.unit
class TestSafeUpsertTokenTruncation:
    """Test that safe_upsert truncates oversized content by token count (issue #1876).

    Wires EmbeddingField to a fake provider implementing the popoto
    AbstractEmbeddingProvider interface (same pattern as the
    `deterministic_provider` fixture in
    tests/integration/test_memory_lifecycle.py) so this test never hits a
    real embedding API. The fake provider raises exactly like OpenAI's
    real 400 does when input exceeds 8,192 tokens, so this test proves the
    truncation in safe_upsert prevents that error from ever reaching the
    provider -- not just that content got shorter.
    """

    @pytest.fixture
    def token_limit_provider(self):
        """Fake provider that raises OpenAI's real 8,192-token 400 error message
        if given oversized input, and otherwise returns a deterministic vector.
        """
        from popoto.embeddings import AbstractEmbeddingProvider
        from popoto.fields.embedding_field import (
            get_default_provider,
            invalidate_cache,
            set_default_provider,
        )

        from tools.knowledge.chunking import _get_encoding

        encoding = _get_encoding()

        class _TokenLimitProvider(AbstractEmbeddingProvider):
            def embed(self, texts, input_type=None):
                for text in texts:
                    if len(encoding.encode(text)) > 8192:
                        raise RuntimeError(
                            "Error code: 400 - Invalid 'input[0]': "
                            "maximum input length is 8192 tokens."
                        )
                return [[0.1, 0.2, 0.3, 0.4] for _ in texts]

            @property
            def dimensions(self):
                return 4

            @property
            def max_batch_size(self):
                return 32

        prior = get_default_provider()
        set_default_provider(_TokenLimitProvider())
        invalidate_cache()
        try:
            yield
        finally:
            set_default_provider(prior)
            invalidate_cache()

    def test_dense_oversized_doc_upserts_without_hitting_provider_limit(
        self, tmp_path, caplog, token_limit_provider
    ):
        """A dense doc whose content exceeds 8,000 tokens is truncated before
        it reaches the embedding provider: safe_upsert returns a non-None
        doc, doc.content is <=8,000 tokens, and the "non-fatal" broad-except
        warning never fires (proving the fix, not just a shorter string).
        """
        import logging

        from models.knowledge_document import KnowledgeDocument
        from tools.knowledge.chunking import _get_encoding

        encoding = _get_encoding()

        # Dense content that would have exceeded 8,192 tokens even after
        # the old [:30000] char cap -- the exact bug reported in #1876.
        oversized = _dense_table_content(30000)
        assert len(encoding.encode(oversized[:30000])) > 8192, (
            "fixture must reproduce the pre-fix failure mode"
        )

        dense_file = tmp_path / "dense-doc.md"
        dense_file.write_text(oversized)

        with caplog.at_level(logging.WARNING, logger="models.knowledge_document"):
            doc = KnowledgeDocument.safe_upsert(str(dense_file), "test-1876", "client")

        assert doc is not None
        assert len(encoding.encode(doc.content)) <= 8000

        non_fatal_warnings = [record for record in caplog.records if "non-fatal" in record.message]
        assert non_fatal_warnings == [], (
            f"safe_upsert hit its broad except-Exception handler: {non_fatal_warnings}"
        )

        doc.delete()


@pytest.mark.unit
class TestSafeUpsertReembedGate:
    """safe_upsert re-embeds a record whose content hash matches but whose
    embedding is missing (issue #1876, critique concern #2).

    content_hash is computed from the full pre-truncation file, so the
    unchanged-skip short-circuit can otherwise no-op the fix: a doc that was
    persisted (hash written) but never got a usable embedding (e.g. a provider
    that returned no vector) would be skipped forever. The gate additionally
    requires a populated embedding before short-circuiting.
    """

    def _swap_provider(self, provider):
        from popoto.fields.embedding_field import (
            invalidate_cache,
            set_default_provider,
        )

        set_default_provider(provider)
        invalidate_cache()

    def test_matching_hash_but_empty_embedding_is_reembedded(self, tmp_path):
        from popoto.embeddings import AbstractEmbeddingProvider
        from popoto.fields.embedding_field import (
            get_default_provider,
            invalidate_cache,
            set_default_provider,
        )

        from models.knowledge_document import KnowledgeDocument

        class _EmptyVectorProvider(AbstractEmbeddingProvider):
            """Returns no vector -- popoto's on_save skips embedding without
            raising, so the record persists with content_hash but no embedding
            (the exact 'persisted hash, null embedding' state from concern #2)."""

            def embed(self, texts, input_type=None):
                return [[] for _ in texts]

            @property
            def dimensions(self):
                return 4

            @property
            def max_batch_size(self):
                return 32

        class _RealVectorProvider(AbstractEmbeddingProvider):
            def embed(self, texts, input_type=None):
                return [[0.1, 0.2, 0.3, 0.4] for _ in texts]

            @property
            def dimensions(self):
                return 4

            @property
            def max_batch_size(self):
                return 32

        doc_file = tmp_path / "reembed-gate.md"
        doc_file.write_text("# Heading\n\nStable content that will not change.\n")

        prior = get_default_provider()
        try:
            # First pass: provider returns no vector -> persisted with a
            # content_hash but a null embedding.
            self._swap_provider(_EmptyVectorProvider())
            doc = KnowledgeDocument.safe_upsert(str(doc_file), "test-1876", "client")
            assert doc is not None
            assert not doc.embedding, "precondition: record must persist without an embedding"
            stored_hash = doc.content_hash

            # Second pass: content unchanged (same hash) but a working
            # provider is now available. The gate must force a re-embed rather
            # than short-circuit on the matching hash.
            self._swap_provider(_RealVectorProvider())
            doc2 = KnowledgeDocument.safe_upsert(str(doc_file), "test-1876", "client")
            assert doc2 is not None
            assert doc2.content_hash == stored_hash
            assert doc2.embedding, "matching-hash record with a null embedding must be re-embedded"

            doc2.delete()
        finally:
            set_default_provider(prior)
            invalidate_cache()
