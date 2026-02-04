"""
Integration tests for doc-summary tool.

Run with: pytest tools/doc-summary/tests/ -v
"""

import os
import tempfile
import pytest
from pathlib import Path

from tools.doc_summary import summarize, summarize_file, extract_key_points


class TestDocSummaryInstallation:
    """Verify tool is properly configured."""

    def test_import(self):
        """Tool can be imported."""
        from tools.doc_summary import summarize

        assert callable(summarize)

    def test_api_key_required(self):
        """Tool returns error when API keys missing."""
        original_anthropic = os.environ.get("ANTHROPIC_API_KEY")
        original_openrouter = os.environ.get("OPENROUTER_API_KEY")

        if "ANTHROPIC_API_KEY" in os.environ:
            del os.environ["ANTHROPIC_API_KEY"]
        if "OPENROUTER_API_KEY" in os.environ:
            del os.environ["OPENROUTER_API_KEY"]

        try:
            result = summarize("test content")
            assert "error" in result
        finally:
            if original_anthropic:
                os.environ["ANTHROPIC_API_KEY"] = original_anthropic
            if original_openrouter:
                os.environ["OPENROUTER_API_KEY"] = original_openrouter


class TestDocSummaryValidation:
    """Test input validation."""

    def test_empty_content(self):
        """Empty content returns error."""
        result = summarize("")
        assert "error" in result

    def test_whitespace_content(self):
        """Whitespace content returns error."""
        result = summarize("   ")
        assert "error" in result


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY")
    and not os.environ.get("OPENROUTER_API_KEY"),
    reason="Neither ANTHROPIC_API_KEY nor OPENROUTER_API_KEY set",
)
class TestDocSummaryCore:
    """Test core summarization functionality."""

    @pytest.fixture
    def sample_content(self):
        """Sample content for testing."""
        return """
        Machine learning is a branch of artificial intelligence that focuses on building
        systems that learn from data. These systems improve their performance over time
        without being explicitly programmed. Machine learning algorithms build models
        based on sample data, known as training data, to make predictions or decisions.

        There are several types of machine learning: supervised learning, where the
        algorithm learns from labeled training data; unsupervised learning, where the
        algorithm finds hidden patterns in unlabeled data; and reinforcement learning,
        where the algorithm learns by interacting with an environment.

        Deep learning is a subset of machine learning based on artificial neural networks.
        These networks have multiple layers that progressively extract higher-level features
        from raw input. Deep learning has been particularly successful in areas like image
        recognition, natural language processing, and speech recognition.
        """

    def test_brief_summary(self, sample_content):
        """Brief summary is concise."""
        result = summarize(sample_content, summary_type="brief")

        assert "error" not in result, f"Summary failed: {result.get('error')}"
        assert "summary" in result
        assert result["word_count"] < 100

    def test_standard_summary(self, sample_content):
        """Standard summary covers main points."""
        result = summarize(sample_content, summary_type="standard")

        assert "error" not in result, f"Summary failed: {result.get('error')}"
        assert "summary" in result
        assert "key_points" in result

    def test_bullets_summary(self, sample_content):
        """Bullets summary returns key points."""
        result = summarize(sample_content, summary_type="bullets")

        assert "error" not in result, f"Summary failed: {result.get('error')}"
        assert "key_points" in result

    def test_compression_ratio(self, sample_content):
        """Compression ratio is calculated."""
        result = summarize(sample_content)

        assert "error" not in result
        assert "compression_ratio" in result
        assert result["compression_ratio"] > 1


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY")
    and not os.environ.get("OPENROUTER_API_KEY"),
    reason="Neither ANTHROPIC_API_KEY nor OPENROUTER_API_KEY set",
)
class TestDocSummaryFile:
    """Test file summarization."""

    def test_summarize_file(self):
        """Summarize file by path."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("This is a test document about Python programming. " * 20)
            f.flush()

            try:
                result = summarize_file(f.name)
                assert "error" not in result, f"Summary failed: {result.get('error')}"
            finally:
                Path(f.name).unlink(missing_ok=True)


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY")
    and not os.environ.get("OPENROUTER_API_KEY"),
    reason="Neither ANTHROPIC_API_KEY nor OPENROUTER_API_KEY set",
)
class TestExtractKeyPoints:
    """Test key point extraction."""

    def test_extract_key_points(self):
        """Extract key points from content."""
        content = """
        Python is a programming language. It is known for its simple syntax.
        Python is used in web development. It is also used in data science.
        Python has a large community. There are many libraries available.
        """

        result = extract_key_points(content, max_points=3)

        assert "error" not in result, f"Extraction failed: {result.get('error')}"
        assert "key_points" in result
        assert result["count"] <= 3
