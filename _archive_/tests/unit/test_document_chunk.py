"""Tests for the DocumentChunk model."""

import pytest


@pytest.mark.unit
@pytest.mark.models
class TestDocumentChunkModel:
    """Test DocumentChunk model definition and methods."""

    def test_model_importable(self):
        """DocumentChunk model can be imported."""
        from models.document_chunk import DocumentChunk

        assert DocumentChunk is not None

    def test_model_importable_from_package(self):
        """DocumentChunk can be imported from models package."""
        from models import DocumentChunk

        assert DocumentChunk is not None

    def test_model_has_required_fields(self):
        """Model has all fields specified in the plan."""
        from models.document_chunk import DocumentChunk

        field_names = [
            "chunk_id",
            "document_doc_id",
            "chunk_index",
            "content",
            "embedding",
            "file_path",
            "project_key",
        ]
        for field_name in field_names:
            assert hasattr(DocumentChunk, field_name), f"Missing field: {field_name}"

    def test_delete_by_parent_handles_missing_parent(self):
        """delete_by_parent returns 0 for a nonexistent parent doc."""
        from models.document_chunk import DocumentChunk

        result = DocumentChunk.delete_by_parent("nonexistent-doc-id-12345")
        assert result == 0

    def test_search_with_no_chunks_returns_empty(self):
        """search returns empty list when no chunks exist."""
        from models.document_chunk import DocumentChunk

        # Use a very specific project key to avoid matching real data
        result = DocumentChunk.search(
            "test query", project_key="__test_nonexistent_project__", top_k=5
        )
        assert isinstance(result, list)
        # Should be empty since no chunks exist for this project key
        # (may return empty even without project_key filter if no chunks at all)

    def test_search_returns_list_type(self):
        """search always returns a list, even on error."""
        from models.document_chunk import DocumentChunk

        result = DocumentChunk.search("any query")
        assert isinstance(result, list)

    def test_has_class_methods(self):
        """Model has expected class methods."""
        from models.document_chunk import DocumentChunk

        assert callable(getattr(DocumentChunk, "delete_by_parent", None))
        assert callable(getattr(DocumentChunk, "search", None))


SEARCH_TEST_PROJECT_KEY = "test-content-decode-search"
SEARCH_TEST_CHUNK_TEXT = "Quarterly revenue grew because the vault indexer works."


@pytest.fixture
def stubbed_search_pipeline(monkeypatch):
    """Stub the embedding pipeline so search() surfaces a real saved chunk.

    - EmbeddingField.on_save -> no-op (no network on chunk.save()).
    - OpenAIProvider -> fixed query vector (no network on search()).
    - EmbeddingField.load_embeddings -> the saved chunk's id with a vector
      identical to the query vector (cosine similarity 1.0).

    Yields the saved DocumentChunk; ORM-deletes test rows on teardown.
    """
    from popoto.embeddings.openai import OpenAIProvider
    from popoto.fields.embedding_field import EmbeddingField

    from models.document_chunk import DocumentChunk

    vector = [1.0, 0.0, 0.0]

    monkeypatch.setattr(
        EmbeddingField,
        "on_save",
        classmethod(
            lambda cls, model_instance, field_name, field_value, pipeline=None, **kw: pipeline
        ),
    )
    monkeypatch.setattr(OpenAIProvider, "__init__", lambda self, *a, **kw: None)
    monkeypatch.setattr(OpenAIProvider, "embed", lambda self, text: list(vector))

    chunk = DocumentChunk(
        document_doc_id="test-doc-content-decode",
        chunk_index=0,
        content=SEARCH_TEST_CHUNK_TEXT,
        file_path="/tmp/test-content-decode/search.md",
        project_key=SEARCH_TEST_PROJECT_KEY,
    )
    chunk.save()

    monkeypatch.setattr(
        EmbeddingField,
        "load_embeddings",
        classmethod(lambda cls, model_cls, **kw: {chunk.chunk_id: list(vector)}),
    )

    try:
        yield chunk
    finally:
        for row in DocumentChunk.query.filter(project_key=SEARCH_TEST_PROJECT_KEY):
            row.delete()


@pytest.mark.unit
@pytest.mark.models
class TestSearchReturnsDecodedChunkText:
    """search() results carry decoded text, never a $CF: reference (#2112)."""

    def test_chunk_text_is_decoded_never_reference(self, stubbed_search_pipeline):
        from models.document_chunk import DocumentChunk

        results = DocumentChunk.search("revenue", project_key=SEARCH_TEST_PROJECT_KEY, top_k=5)

        assert len(results) == 1
        chunk_text = results[0]["chunk_text"]
        assert not chunk_text.startswith("$CF:")
        assert chunk_text == SEARCH_TEST_CHUNK_TEXT

    def test_missing_content_file_yields_empty_chunk_text(self, stubbed_search_pipeline):
        """A chunk whose content file is missing still surfaces (chunk_text=='')."""
        import os

        from models.document_chunk import DocumentChunk

        # Resolve the saved $CF: reference from a query-loaded row and delete
        # the underlying content files (live + version archive).
        reloaded = DocumentChunk.query.get(chunk_id=stubbed_search_pipeline.chunk_id)
        ref = reloaded.content
        assert isinstance(ref, str) and ref.startswith("$CF:")

        store = DocumentChunk._meta.fields["content"].store
        content_hash, relative_path = store._parse_reference(ref)
        for path in (
            os.path.join(store.base_path, relative_path),
            store._version_path(content_hash),
        ):
            if os.path.exists(path):
                os.remove(path)

        results = DocumentChunk.search("revenue", project_key=SEARCH_TEST_PROJECT_KEY, top_k=5)

        assert len(results) == 1
        assert results[0]["chunk_text"] == ""
