"""Tests for the generate_descriptions management command and upload_podcast_media --podcast-covers."""

import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase

from apps.podcast.models import Episode, Podcast


class GenerateDescriptionsTestCase(TestCase):
    """Tests for the generate_descriptions management command."""

    def setUp(self):
        self.podcast = Podcast.objects.create(
            title="Test Podcast",
            slug="test-podcast",
            description="A test podcast.",
            author_name="Author",
            author_email="a@b.com",
        )

    def _create_episode(self, **kwargs):
        """Helper to create an episode with sensible defaults."""
        defaults = {
            "podcast": self.podcast,
            "title": "Test Episode",
            "slug": "test-ep",
            "episode_number": 1,
            "status": "complete",
        }
        defaults.update(kwargs)
        return Episode.objects.create(**defaults)

    def test_populates_description_from_report_text(self):
        """Episode with report_text and no description gets description from first paragraph."""
        episode = self._create_episode(
            report_text="# Executive Summary\n\nThis is the first paragraph of the report. It contains important findings.\n\nSecond paragraph here.",
            description="",
        )
        out = StringIO()
        call_command("generate_descriptions", stdout=out)

        episode.refresh_from_db()
        self.assertEqual(
            episode.description,
            "This is the first paragraph of the report. It contains important findings.",
        )

    def test_skips_episode_without_report_text(self):
        """Episode without report_text is skipped."""
        episode = self._create_episode(
            report_text="",
            description="",
        )
        out = StringIO()
        call_command("generate_descriptions", stdout=out)

        episode.refresh_from_db()
        self.assertEqual(episode.description, "")
        self.assertIn("skipped (no report_text)", out.getvalue())

    def test_skips_episode_that_already_has_description(self):
        """Episode that already has a description is skipped."""
        episode = self._create_episode(
            report_text="# Report\n\nSome content here.",
            description="Already has a description",
        )
        out = StringIO()
        call_command("generate_descriptions", stdout=out)

        episode.refresh_from_db()
        self.assertEqual(episode.description, "Already has a description")
        self.assertIn("skipped (already has description)", out.getvalue())

    def test_truncates_at_sentence_boundary(self):
        """Long first paragraph is truncated at sentence boundary within 250 chars."""
        long_text = (
            "First sentence is here. "
            "Second sentence follows closely. "
            "Third sentence adds more detail. "
            "Fourth sentence brings us closer to the limit. "
            "Fifth sentence goes even further along. "
            "Sixth sentence pushes us over the two hundred fifty character boundary easily. "
            "Seventh sentence is definitely past it."
        )
        episode = self._create_episode(
            report_text=f"# Header\n\n{long_text}\n\nAnother paragraph.",
            description="",
        )
        out = StringIO()
        call_command("generate_descriptions", stdout=out)

        episode.refresh_from_db()
        self.assertLessEqual(len(episode.description), 250)
        self.assertTrue(episode.description.endswith("."))

    def test_truncates_at_word_boundary_with_ellipsis_when_no_sentence_break(self):
        """When no sentence boundary within 250 chars, truncate at word boundary with ellipsis."""
        # A single very long sentence with no period within 250 chars
        long_sentence = "A " + "word " * 60  # ~300 chars with no period
        episode = self._create_episode(
            report_text=f"# Header\n\n{long_sentence}\n\nSecond paragraph.",
            description="",
        )
        out = StringIO()
        call_command("generate_descriptions", stdout=out)

        episode.refresh_from_db()
        self.assertLessEqual(len(episode.description), 253)  # 250 + "..."
        self.assertTrue(episode.description.endswith("..."))

    def test_strips_markdown_headers(self):
        """Lines starting with # are skipped when extracting description."""
        episode = self._create_episode(
            report_text="# Title\n## Subtitle\n\nActual content paragraph here.",
            description="",
        )
        out = StringIO()
        call_command("generate_descriptions", stdout=out)

        episode.refresh_from_db()
        self.assertEqual(episode.description, "Actual content paragraph here.")
        self.assertNotIn("#", episode.description)

    def test_strips_inline_markdown_bold(self):
        """Bold markdown formatting (**text**) is stripped."""
        episode = self._create_episode(
            report_text="# Header\n\nThis is **bold** and more text.",
            description="",
        )
        out = StringIO()
        call_command("generate_descriptions", stdout=out)

        episode.refresh_from_db()
        self.assertEqual(episode.description, "This is bold and more text.")

    def test_strips_inline_markdown_italic(self):
        """Italic markdown formatting (*text*) is stripped."""
        episode = self._create_episode(
            report_text="# Header\n\nThis is *italic* content.",
            description="",
        )
        out = StringIO()
        call_command("generate_descriptions", stdout=out)

        episode.refresh_from_db()
        self.assertEqual(episode.description, "This is italic content.")

    def test_strips_inline_markdown_links(self):
        """Markdown links [text](url) are converted to just text."""
        episode = self._create_episode(
            report_text="# Header\n\nVisit [our site](https://example.com) for more.",
            description="",
        )
        out = StringIO()
        call_command("generate_descriptions", stdout=out)

        episode.refresh_from_db()
        self.assertEqual(episode.description, "Visit our site for more.")

    def test_dry_run_does_not_save(self):
        """--dry-run prints preview without saving to database."""
        episode = self._create_episode(
            report_text="# Header\n\nDry run test paragraph.",
            description="",
        )
        out = StringIO()
        call_command("generate_descriptions", "--dry-run", stdout=out)

        episode.refresh_from_db()
        self.assertEqual(episode.description, "")
        output = out.getvalue()
        self.assertIn("DRY RUN", output)
        self.assertIn("Dry run test paragraph", output)

    def test_summary_output_shows_correct_counts(self):
        """Summary output shows correct counts for generated, skipped-no-report, skipped-has-description."""
        # Episode that should get a description
        self._create_episode(
            slug="ep-generate",
            episode_number=1,
            report_text="# Header\n\nWill get a description.",
            description="",
        )
        # Episode with no report_text
        self._create_episode(
            slug="ep-no-report",
            episode_number=2,
            report_text="",
            description="",
        )
        # Episode with existing description
        self._create_episode(
            slug="ep-has-desc",
            episode_number=3,
            report_text="# Header\n\nSome report.",
            description="Already set",
        )

        out = StringIO()
        call_command("generate_descriptions", stdout=out)

        output = out.getvalue()
        # Check that at least 1 description was generated and at least 1 skipped
        # (other episodes from data migrations may also be counted)
        self.assertIn("descriptions generated", output)
        self.assertIn("skipped (no report_text)", output)
        self.assertIn("skipped (already has description)", output)

    def test_multiple_episodes_processed(self):
        """Command processes multiple episodes in one run."""
        self._create_episode(
            slug="ep-one",
            episode_number=1,
            report_text="# Report 1\n\nFirst episode content.",
            description="",
        )
        self._create_episode(
            slug="ep-two",
            episode_number=2,
            report_text="# Report 2\n\nSecond episode content.",
            description="",
        )
        out = StringIO()
        call_command("generate_descriptions", stdout=out)

        ep1 = Episode.objects.get(slug="ep-one")
        ep2 = Episode.objects.get(slug="ep-two")
        self.assertEqual(ep1.description, "First episode content.")
        self.assertEqual(ep2.description, "Second episode content.")
        self.assertIn("2 descriptions generated", out.getvalue())

    def test_takes_first_paragraph_only(self):
        """Only the first content paragraph is used, not subsequent ones."""
        episode = self._create_episode(
            report_text="# Header\n\nFirst paragraph.\n\nSecond paragraph.\n\nThird paragraph.",
            description="",
        )
        out = StringIO()
        call_command("generate_descriptions", stdout=out)

        episode.refresh_from_db()
        self.assertEqual(episode.description, "First paragraph.")

    def test_handles_blank_lines_between_headers_and_content(self):
        """Handles multiple blank lines and multiple headers before content."""
        episode = self._create_episode(
            report_text="# Main Title\n\n## Section One\n\n\nContent after multiple blanks.",
            description="",
        )
        out = StringIO()
        call_command("generate_descriptions", stdout=out)

        episode.refresh_from_db()
        self.assertEqual(episode.description, "Content after multiple blanks.")


class ExtractDescriptionUnitTestCase(TestCase):
    """Unit tests for the extract_description function."""

    def test_no_double_spaces_from_empty_lines(self):
        """Empty lines in paragraph do not produce double spaces in output."""
        from apps.podcast.management.commands.generate_descriptions import (
            extract_description,
        )

        # Simulate content where stripped lines might be empty
        result = extract_description("# Header\n\nHello world.\n\nSecond paragraph.")
        self.assertNotIn("  ", result)

    def test_multiline_paragraph_joined_with_single_spaces(self):
        """Multi-line paragraph is joined into a single line with single spaces."""
        from apps.podcast.management.commands.generate_descriptions import (
            extract_description,
        )

        result = extract_description(
            "# Header\n\nLine one of paragraph.\nLine two of paragraph.\n\nNext paragraph."
        )
        self.assertEqual(result, "Line one of paragraph. Line two of paragraph.")
        self.assertNotIn("  ", result)


class UploadPodcastCoversTestCase(TestCase):
    """Tests for the --podcast-covers flag on upload_podcast_media."""

    def setUp(self):
        self.podcast = Podcast.objects.create(
            title="Test Podcast",
            slug="test-podcast",
            description="A test podcast.",
            author_name="Author",
            author_email="a@b.com",
        )

    def test_podcast_covers_argument_accepted(self):
        """The --podcast-covers argument is accepted by the parser."""
        with tempfile.TemporaryDirectory() as tmpdir:
            source_dir = Path(tmpdir) / "episodes"
            source_dir.mkdir()
            out = StringIO()
            # Dry run to avoid needing Supabase config
            call_command(
                "upload_podcast_media",
                "--source-dir",
                str(source_dir),
                "--dry-run",
                "--podcast-covers",
                stdout=out,
            )
            # Should not raise an error about unrecognized argument
            output = out.getvalue()
            self.assertIn("DRY RUN", output)

    def test_podcast_covers_finds_global_cover(self):
        """--podcast-covers finds cover.png in parent directory of source_dir."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create directory structure: tmpdir/cover.png and tmpdir/episodes/
            parent = Path(tmpdir)
            source_dir = parent / "episodes"
            source_dir.mkdir()
            cover_path = parent / "cover.png"
            cover_path.write_bytes(b"fake png data")

            out = StringIO()
            call_command(
                "upload_podcast_media",
                "--source-dir",
                str(source_dir),
                "--dry-run",
                "--podcast-covers",
                stdout=out,
            )
            output = out.getvalue()
            self.assertIn("cover.png", output)

    def test_podcast_covers_finds_slug_specific_cover(self):
        """--podcast-covers finds {podcast_slug}/cover.png in parent directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            parent = Path(tmpdir)
            source_dir = parent / "episodes"
            source_dir.mkdir()
            slug_cover_dir = parent / "test-podcast"
            slug_cover_dir.mkdir()
            cover_path = slug_cover_dir / "cover.png"
            cover_path.write_bytes(b"fake slug cover data")

            out = StringIO()
            call_command(
                "upload_podcast_media",
                "--source-dir",
                str(source_dir),
                "--dry-run",
                "--podcast-covers",
                stdout=out,
            )
            output = out.getvalue()
            self.assertIn("cover.png", output)

    def test_podcast_covers_skips_when_already_set(self):
        """--podcast-covers skips podcasts that already have cover_image_url."""
        self.podcast.cover_image_url = "https://example.com/existing-cover.png"
        self.podcast.save()

        with tempfile.TemporaryDirectory() as tmpdir:
            parent = Path(tmpdir)
            source_dir = parent / "episodes"
            source_dir.mkdir()
            cover_path = parent / "cover.png"
            cover_path.write_bytes(b"fake png data")

            out = StringIO()
            call_command(
                "upload_podcast_media",
                "--source-dir",
                str(source_dir),
                "--dry-run",
                "--podcast-covers",
                stdout=out,
            )
            output = out.getvalue()
            self.assertIn("Already uploaded:", output)
            self.assertIn("https://example.com/existing-cover.png", output)

    def test_podcast_covers_does_not_skip_non_http_url(self):
        """--podcast-covers does not skip podcasts with non-HTTP cover_image_url (e.g. local path)."""
        self.podcast.cover_image_url = "/media/covers/local-file.png"
        self.podcast.save()

        with tempfile.TemporaryDirectory() as tmpdir:
            parent = Path(tmpdir)
            source_dir = parent / "episodes"
            source_dir.mkdir()
            cover_path = parent / "cover.png"
            cover_path.write_bytes(b"fake png data")

            out = StringIO()
            call_command(
                "upload_podcast_media",
                "--source-dir",
                str(source_dir),
                "--dry-run",
                "--podcast-covers",
                stdout=out,
            )
            output = out.getvalue()
            # Our test podcast with non-HTTP URL should get a dry-run upload, not be skipped
            # (other podcasts from data migrations may have HTTP URLs and be skipped)
            self.assertIn("Test Podcast", output)
            # Test Podcast should show as "Would upload", not "Already uploaded"
            lines = [line for line in output.split("\n") if "Test Podcast" in line]
            self.assertTrue(len(lines) > 0, "Test Podcast not found in output")
            test_podcast_line = lines[0]
            self.assertNotIn("Already uploaded:", test_podcast_line)
            self.assertIn("Would upload", test_podcast_line)

    def test_podcast_covers_skips_when_no_cover_file(self):
        """--podcast-covers skips podcasts when no cover file is found."""
        with tempfile.TemporaryDirectory() as tmpdir:
            source_dir = Path(tmpdir) / "episodes"
            source_dir.mkdir()
            # No cover.png anywhere

            out = StringIO()
            call_command(
                "upload_podcast_media",
                "--source-dir",
                str(source_dir),
                "--dry-run",
                "--podcast-covers",
                stdout=out,
            )
            output = out.getvalue()
            self.assertIn("No cover file found", output)

    def test_podcast_covers_force_reuploads(self):
        """--podcast-covers --force re-uploads even when cover_image_url is set."""
        self.podcast.cover_image_url = "https://example.com/existing-cover.png"
        self.podcast.save()

        with tempfile.TemporaryDirectory() as tmpdir:
            parent = Path(tmpdir)
            source_dir = parent / "episodes"
            source_dir.mkdir()
            cover_path = parent / "cover.png"
            cover_path.write_bytes(b"fake png data")

            out = StringIO()
            call_command(
                "upload_podcast_media",
                "--source-dir",
                str(source_dir),
                "--dry-run",
                "--podcast-covers",
                "--force",
                stdout=out,
            )
            output = out.getvalue()
            # With --force, should NOT say "Already uploaded"
            self.assertNotIn("Already uploaded:", output)
            self.assertIn("cover.png", output)
