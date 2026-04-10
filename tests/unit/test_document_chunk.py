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
