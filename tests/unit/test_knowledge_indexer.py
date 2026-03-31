"""Tests for the knowledge document indexer pipeline."""

import json
from unittest.mock import MagicMock, patch

import pytest

from config.models import HAIKU
from tools.knowledge.indexer import (
    LARGE_DOC_WORD_THRESHOLD,
    SUPPORTED_EXTENSIONS,
    _is_hidden_or_archived,
    _is_supported_file,
    _make_reference,
    _split_by_headings,
    _summarize_content,
)


@pytest.mark.unit
class TestIndexerHelpers:
    """Test indexer helper functions."""

    def test_supported_extensions(self):
        """Supported extensions include md and txt."""
        assert ".md" in SUPPORTED_EXTENSIONS
        assert ".txt" in SUPPORTED_EXTENSIONS
        assert ".markdown" in SUPPORTED_EXTENSIONS

    def test_is_supported_file_md(self):
        assert _is_supported_file("/path/to/doc.md") is True

    def test_is_supported_file_txt(self):
        assert _is_supported_file("/path/to/doc.txt") is True

    def test_is_supported_file_pdf(self):
        assert _is_supported_file("/path/to/doc.pdf") is False

    def test_is_supported_file_py(self):
        assert _is_supported_file("/path/to/code.py") is False

    def test_is_supported_file_image(self):
        assert _is_supported_file("/path/to/image.png") is False

    def test_is_hidden_dotfile(self):
        assert _is_hidden_or_archived("/path/.hidden/doc.md") is True

    def test_is_hidden_dotdir(self):
        assert _is_hidden_or_archived("/path/.obsidian/doc.md") is True

    def test_is_archived(self):
        assert _is_hidden_or_archived("/path/_archive_/doc.md") is True

    def test_normal_path_not_hidden(self):
        assert _is_hidden_or_archived("/path/to/doc.md") is False

    def test_make_reference(self):
        ref = _make_reference("/path/to/doc.md")
        parsed = json.loads(ref)
        assert parsed["tool"] == "read_file"
        assert parsed["params"]["file_path"] == "/path/to/doc.md"


@pytest.mark.unit
class TestSplitByHeadings:
    """Test heading-based document splitting."""

    def test_no_headings(self):
        content = "Just some text without headings."
        sections = _split_by_headings(content)
        assert len(sections) == 1
        assert sections[0][0] == ""
        assert "Just some text" in sections[0][1]

    def test_single_heading(self):
        content = "# Title\nSome content here."
        sections = _split_by_headings(content)
        assert len(sections) == 1
        assert "# Title" in sections[0][0]
        assert "Some content" in sections[0][1]

    def test_multiple_headings(self):
        content = "# First\nContent 1\n# Second\nContent 2"
        sections = _split_by_headings(content)
        assert len(sections) == 2
        assert "First" in sections[0][0]
        assert "Content 1" in sections[0][1]
        assert "Second" in sections[1][0]
        assert "Content 2" in sections[1][1]

    def test_h2_headings(self):
        content = "## Section A\nText A\n## Section B\nText B"
        sections = _split_by_headings(content)
        assert len(sections) == 2

    def test_content_before_first_heading(self):
        content = "Preamble text\n# Heading\nBody text"
        sections = _split_by_headings(content)
        assert len(sections) == 2
        assert "Preamble" in sections[0][1]
        assert "Body text" in sections[1][1]

    def test_empty_sections_skipped(self):
        content = "# First\n# Second\nContent"
        sections = _split_by_headings(content)
        # First heading has no content, should be skipped
        assert len(sections) == 1
        assert "Second" in sections[0][0]

    def test_h3_not_split(self):
        """H3 and below should not trigger splits."""
        content = "# Title\nIntro\n### Subsection\nDetail"
        sections = _split_by_headings(content)
        assert len(sections) == 1
        assert "Subsection" in sections[0][1]


@pytest.mark.unit
class TestIndexerPipeline:
    """Test the indexer pipeline functions."""

    def test_index_file_unsupported_extension(self):
        from tools.knowledge.indexer import index_file

        result = index_file("/path/to/image.png")
        assert result is False

    def test_index_file_nonexistent(self):
        from tools.knowledge.indexer import index_file

        result = index_file("/nonexistent/path/doc.md")
        assert result is False

    def test_index_file_hidden(self):
        from tools.knowledge.indexer import index_file

        result = index_file("/path/.hidden/doc.md")
        assert result is False

    def test_delete_file_nonexistent(self):
        from tools.knowledge.indexer import delete_file

        result = delete_file("/nonexistent/path/doc.md")
        assert result is False

    def test_large_doc_threshold(self):
        """Threshold for large doc splitting is 2000 words."""
        assert LARGE_DOC_WORD_THRESHOLD == 2000


@pytest.mark.unit
class TestSummarizeContent:
    """Test the _summarize_content function."""

    @patch("tools.knowledge.indexer.anthropic")
    def test_summarize_uses_haiku_constant(self, mock_anthropic_module):
        """Verify _summarize_content passes the HAIKU model constant to the API."""
        mock_client = MagicMock()
        mock_anthropic_module.Anthropic.return_value = mock_client
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="A summary of the document.")]
        mock_client.messages.create.return_value = mock_response

        result = _summarize_content("Some document content here.", "/path/to/doc.md")

        mock_client.messages.create.assert_called_once()
        call_kwargs = mock_client.messages.create.call_args
        assert call_kwargs.kwargs.get("model") or call_kwargs[1].get("model") == HAIKU
        # Verify the actual model value
        if call_kwargs.kwargs:
            assert call_kwargs.kwargs["model"] == HAIKU
        else:
            assert call_kwargs[1]["model"] == HAIKU
        assert result == "A summary of the document."

    @patch("tools.knowledge.indexer.anthropic")
    def test_summarize_fallback_on_api_failure(self, mock_anthropic_module):
        """Verify _summarize_content falls back to truncation on API failure."""
        mock_client = MagicMock()
        mock_anthropic_module.Anthropic.return_value = mock_client
        mock_client.messages.create.side_effect = Exception("API error")

        content = "A" * 1000
        result = _summarize_content(content, "/path/to/doc.md")

        # Should fall back to truncated content
        assert len(result) <= 500 + 10  # Allow small margin for ellipsis
