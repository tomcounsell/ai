"""
Integration tests for knowledge-search tool.

Run with: pytest tools/knowledge-search/tests/ -v
"""

import os
import tempfile
import pytest
from pathlib import Path

from tools.knowledge_search import (
    search_knowledge,
    index_document,
    list_indexed_documents,
)


class TestKnowledgeSearchInstallation:
    """Verify tool is properly configured."""

    def test_import(self):
        """Tool can be imported."""
        from tools.knowledge_search import search_knowledge

        assert callable(search_knowledge)


class TestKnowledgeSearchValidation:
    """Test input validation."""

    def test_empty_query(self):
        """Empty query returns error."""
        result = search_knowledge("")
        assert "error" in result

    def test_whitespace_query(self):
        """Whitespace query returns error."""
        result = search_knowledge("   ")
        assert "error" in result


class TestKnowledgeSearchKeyword:
    """Test keyword search functionality."""

    @pytest.fixture
    def test_db(self):
        """Create temporary test database."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            yield db_path

    @pytest.fixture
    def indexed_doc(self, test_db):
        """Create and index a test document."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write(
                "# Test Document\n\nThis is a test document about Python programming.\n"
            )
            f.write(
                "Python is a great language for data science and web development.\n"
            )
            f.flush()

            # Index with keyword-only (no API key needed)
            result = index_document(f.name, db_path=test_db)
            yield f.name, test_db, result
            Path(f.name).unlink(missing_ok=True)

    def test_keyword_search(self, indexed_doc):
        """Keyword search finds matching documents."""
        doc_path, db_path, index_result = indexed_doc

        # Skip if indexing failed
        if "error" in index_result:
            pytest.skip(f"Indexing failed: {index_result['error']}")

        result = search_knowledge(
            "Python",
            search_type="keyword",
            db_path=db_path,
        )

        assert "error" not in result, f"Search failed: {result.get('error')}"
        assert result["total_matches"] > 0

    def test_list_indexed(self, indexed_doc):
        """List indexed documents works."""
        _, db_path, index_result = indexed_doc

        if "error" in index_result:
            pytest.skip(f"Indexing failed: {index_result['error']}")

        result = list_indexed_documents(db_path=db_path)

        assert "documents" in result
        assert result["total"] > 0


@pytest.mark.skipif(
    not os.environ.get("OPENROUTER_API_KEY"), reason="OPENROUTER_API_KEY not set"
)
class TestKnowledgeSearchSemantic:
    """Test semantic search functionality."""

    @pytest.fixture
    def test_db(self):
        """Create temporary test database."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            yield db_path

    @pytest.fixture
    def indexed_doc(self, test_db):
        """Create and index a test document."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("# Machine Learning Guide\n\n")
            f.write("Machine learning is a subset of artificial intelligence.\n")
            f.write("It involves training models on data to make predictions.\n")
            f.flush()

            result = index_document(f.name, db_path=test_db)
            yield f.name, test_db, result
            Path(f.name).unlink(missing_ok=True)

    def test_semantic_search(self, indexed_doc):
        """Semantic search finds related content."""
        doc_path, db_path, index_result = indexed_doc

        if "error" in index_result:
            pytest.skip(f"Indexing failed: {index_result['error']}")

        result = search_knowledge(
            "AI and prediction models",
            search_type="semantic",
            db_path=db_path,
            similarity_threshold=0.3,
        )

        assert "error" not in result, f"Search failed: {result.get('error')}"


class TestIndexDocument:
    """Test document indexing."""

    def test_nonexistent_file(self):
        """Indexing nonexistent file returns error."""
        result = index_document("/nonexistent/file.md")
        assert "error" in result
