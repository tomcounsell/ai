"""Tests for the KnowledgeDocument model."""

import hashlib

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
