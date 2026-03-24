"""Tests for the document parser utility and file research service function."""

import os
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, patch

from django.test import TestCase as DjangoTestCase

from apps.common.utilities.document_parser import SUPPORTED_EXTENSIONS, parse_document


class ParseDocumentTestCase(TestCase):
    """Tests for apps.common.utilities.document_parser.parse_document().

    These are pure unit tests with no database dependency.
    """

    def test_parse_text_file(self):
        """Plain .txt files are read directly without kreuzberg."""
        with tempfile.NamedTemporaryFile(suffix=".txt", mode="w", delete=False) as f:
            f.write("Hello, world!")
            f.flush()
            self.addCleanup(os.unlink, f.name)
            result = parse_document(Path(f.name))
        self.assertEqual(result, "Hello, world!")

    def test_parse_markdown_file(self):
        """Plain .md files are read directly without kreuzberg."""
        with tempfile.NamedTemporaryFile(suffix=".md", mode="w", delete=False) as f:
            f.write("# Title\n\nSome content.")
            f.flush()
            self.addCleanup(os.unlink, f.name)
            result = parse_document(Path(f.name))
        self.assertEqual(result, "# Title\n\nSome content.")

    def test_file_not_found(self):
        """Non-existent paths raise FileNotFoundError."""
        nonexistent = Path(tempfile.gettempdir()) / "nonexistent_file_12345.pdf"
        with self.assertRaises(FileNotFoundError):
            parse_document(nonexistent)

    def test_unsupported_extension(self):
        """Unsupported extensions raise ValueError."""
        with tempfile.NamedTemporaryFile(suffix=".xyz", delete=False) as f:
            self.addCleanup(os.unlink, f.name)
        with self.assertRaises(ValueError):
            parse_document(Path(f.name))

    def test_empty_text_file(self):
        """Empty .txt file returns empty string."""
        with tempfile.NamedTemporaryFile(suffix=".txt", mode="w", delete=False) as f:
            f.write("")
            f.flush()
            self.addCleanup(os.unlink, f.name)
            result = parse_document(Path(f.name))
        self.assertEqual(result, "")

    def test_string_path_accepted(self):
        """String paths are accepted and converted to Path objects."""
        with tempfile.NamedTemporaryFile(suffix=".txt", mode="w", delete=False) as f:
            f.write("string path test")
            f.flush()
            self.addCleanup(os.unlink, f.name)
            result = parse_document(f.name)
        self.assertEqual(result, "string path test")

    def test_supported_extensions_are_documented(self):
        """SUPPORTED_EXTENSIONS contains expected formats."""
        self.assertIn(".pdf", SUPPORTED_EXTENSIONS)
        self.assertIn(".docx", SUPPORTED_EXTENSIONS)
        self.assertIn(".odt", SUPPORTED_EXTENSIONS)

    @patch("kreuzberg.extract_file_sync")
    def test_pdf_extraction_uses_kreuzberg(self, mock_extract):
        """PDF files are passed to kreuzberg's extract_file_sync."""
        mock_result = MagicMock()
        mock_result.content = "Extracted PDF text"
        mock_extract.return_value = mock_result

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"%PDF-1.4 fake")
            f.flush()
            self.addCleanup(os.unlink, f.name)
            result = parse_document(Path(f.name))

        self.assertEqual(result, "Extracted PDF text")
        mock_extract.assert_called_once_with(f.name)

    @patch(
        "kreuzberg.extract_file_sync",
        side_effect=Exception("corrupt file"),
    )
    def test_kreuzberg_failure_returns_empty(self, mock_extract):
        """When kreuzberg fails, parse_document returns empty string."""
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"%PDF-1.4 corrupt")
            f.flush()
            self.addCleanup(os.unlink, f.name)
            result = parse_document(Path(f.name))

        self.assertEqual(result, "")


class AddFileResearchTestCase(DjangoTestCase):
    """Tests for apps.podcast.services.research.add_file_research().

    These require the database for Episode and EpisodeArtifact creation.
    """

    def setUp(self):
        from apps.podcast.models import Podcast

        self.podcast = Podcast.objects.create(
            title="Test Podcast",
            slug="test-podcast",
            description="A test podcast",
            author_name="Tester",
            author_email="test@example.com",
        )

        from apps.podcast.models import Episode

        self.episode = Episode.objects.create(
            podcast=self.podcast,
            title="Test Episode",
            slug="test-episode",
        )

    @patch("apps.common.utilities.document_parser.parse_document")
    def test_successful_parse_creates_artifact(self, mock_parse):
        """When parsing succeeds, artifact is created via add_manual_research."""
        mock_parse.return_value = "Extracted document text content"

        from apps.podcast.services.research import add_file_research

        fake_path = Path(tempfile.gettempdir()) / "test.pdf"
        artifact = add_file_research(self.episode.pk, "whitepaper", fake_path)

        mock_parse.assert_called_once()
        self.assertEqual(artifact.title, "p2-whitepaper")
        self.assertEqual(artifact.content, "Extracted document text content")
        self.assertEqual(artifact.episode, self.episode)

    @patch(
        "apps.common.utilities.document_parser.parse_document",
        side_effect=FileNotFoundError("not found"),
    )
    def test_file_not_found_creates_empty_artifact(self, mock_parse):
        """When file is missing, artifact is created with empty content and metadata."""
        from apps.podcast.services.research import add_file_research

        fake_path = Path(tempfile.gettempdir()) / "nope.pdf"
        artifact = add_file_research(self.episode.pk, "missing-doc", fake_path)

        self.assertEqual(artifact.content, "")
        self.assertTrue(artifact.metadata.get("parse_failed"))
        self.assertEqual(artifact.episode, self.episode)

    @patch(
        "apps.common.utilities.document_parser.parse_document",
        side_effect=ValueError("unsupported"),
    )
    def test_unsupported_format_creates_empty_artifact(self, mock_parse):
        """When format is unsupported, artifact is created with failure metadata."""
        from apps.podcast.services.research import add_file_research

        fake_path = Path(tempfile.gettempdir()) / "test.xyz"
        artifact = add_file_research(self.episode.pk, "bad-format", fake_path)

        self.assertEqual(artifact.content, "")
        self.assertTrue(artifact.metadata.get("parse_failed"))

    @patch("apps.common.utilities.document_parser.parse_document")
    def test_empty_parse_result_creates_failure_artifact(self, mock_parse):
        """When parsing returns empty string, failure artifact is created."""
        mock_parse.return_value = ""

        from apps.podcast.services.research import add_file_research

        fake_path = Path(tempfile.gettempdir()) / "empty.pdf"
        artifact = add_file_research(self.episode.pk, "empty-doc", fake_path)

        self.assertEqual(artifact.content, "")
        self.assertTrue(artifact.metadata.get("parse_failed"))
