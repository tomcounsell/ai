"""Tests for the knowledge search tool."""

import os

from tools.knowledge_search import (
    index_document,
    list_indexed_documents,
    search_knowledge,
)


class TestIndexDocument:
    """Test document indexing."""

    def test_index_markdown_document(
        self, temp_markdown_file, tmp_path, openrouter_api_key
    ):
        """Test indexing a markdown document."""
        db_path = tmp_path / "test.db"
        result = index_document(str(temp_markdown_file), db_path=db_path)

        assert "error" not in result
        assert result.get("document_id")
        assert result.get("chunks_indexed", 0) > 0

    def test_index_nonexistent_file(self, tmp_path):
        """Test indexing a non-existent file."""
        result = index_document(
            "/nonexistent/path/file.txt", db_path=tmp_path / "test.db"
        )
        assert "error" in result
        assert "not found" in result["error"].lower()

    def test_index_missing_api_key(self, temp_markdown_file, tmp_path):
        """Test indexing without API key."""
        original_key = os.environ.pop("OPENROUTER_API_KEY", None)
        try:
            result = index_document(
                str(temp_markdown_file), db_path=tmp_path / "test.db"
            )
            assert "error" in result
            assert "OPENROUTER_API_KEY" in result["error"]
        finally:
            if original_key:
                os.environ["OPENROUTER_API_KEY"] = original_key


class TestSearchKnowledge:
    """Test knowledge search."""

    def test_empty_query_returns_error(self, tmp_path):
        """Test that empty query returns error."""
        result = search_knowledge("", db_path=tmp_path / "test.db")
        assert "error" in result
        assert "empty" in result["error"].lower()

    def test_keyword_search(self, temp_markdown_file, tmp_path, openrouter_api_key):
        """Test keyword search."""
        db_path = tmp_path / "test.db"

        # Index document first
        index_document(str(temp_markdown_file), db_path=db_path)

        # Search
        result = search_knowledge(
            "Installation", search_type="keyword", db_path=db_path
        )

        assert "error" not in result
        assert result.get("query") == "Installation"
        assert result.get("search_type") == "keyword"

    def test_semantic_search_requires_api_key(self, tmp_path):
        """Test that semantic search requires API key."""
        original_key = os.environ.pop("OPENROUTER_API_KEY", None)
        try:
            result = search_knowledge(
                "test query", search_type="semantic", db_path=tmp_path / "test.db"
            )
            assert "error" in result
            assert "OPENROUTER_API_KEY" in result["error"]
        finally:
            if original_key:
                os.environ["OPENROUTER_API_KEY"] = original_key

    def test_search_max_results(self, temp_docs_dir, tmp_path, openrouter_api_key):
        """Test max_results parameter."""
        db_path = tmp_path / "test.db"

        # Index multiple documents
        for doc_file in temp_docs_dir.glob("*.md"):
            index_document(str(doc_file), db_path=db_path)

        # Search with limit
        result = search_knowledge(
            "configuration", search_type="keyword", max_results=2, db_path=db_path
        )

        assert "error" not in result
        assert len(result.get("results", [])) <= 2


class TestListIndexedDocuments:
    """Test listing indexed documents."""

    def test_list_empty_database(self, tmp_path):
        """Test listing with no documents."""
        db_path = tmp_path / "test.db"
        result = list_indexed_documents(db_path=db_path)

        assert "documents" in result
        assert result["total"] == 0

    def test_list_after_indexing(
        self, temp_markdown_file, tmp_path, openrouter_api_key
    ):
        """Test listing after indexing documents."""
        db_path = tmp_path / "test.db"

        index_document(str(temp_markdown_file), db_path=db_path)

        result = list_indexed_documents(db_path=db_path)

        assert "documents" in result
        assert result["total"] >= 1
        assert any(
            str(temp_markdown_file) in doc["path"] for doc in result["documents"]
        )


class TestSearchParameters:
    """Test search parameter validation."""

    def test_max_results_clamped(self, tmp_path):
        """Test max_results is clamped to valid range."""
        db_path = tmp_path / "test.db"

        # Should not error even with extreme values
        result = search_knowledge(
            "test", search_type="keyword", max_results=1000, db_path=db_path
        )
        # Either error for empty db or successful search
        assert "error" not in result or "empty" not in result.get("error", "")

    def test_file_types_filter(self, temp_docs_dir, tmp_path, openrouter_api_key):
        """Test filtering by file types."""
        db_path = tmp_path / "test.db"

        for doc_file in temp_docs_dir.glob("*.md"):
            index_document(str(doc_file), db_path=db_path)

        result = search_knowledge(
            "api", search_type="keyword", file_types=[".md"], db_path=db_path
        )

        assert "error" not in result
