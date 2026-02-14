import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.podcast.models import Episode, EpisodeArtifact, Podcast


class StartEpisodeCommandTestCase(TestCase):
    """Tests for the start_episode management command."""

    def setUp(self):
        self.podcast = Podcast.objects.create(
            title="Start Test Podcast",
            slug="start-test-podcast",
            description="A podcast for start_episode tests.",
            author_name="Author",
            author_email="a@b.com",
        )
        self.draft_episode = Episode.objects.create(
            podcast=self.podcast,
            title="Draft Episode",
            slug="draft-ep",
            episode_number=1,
            status="draft",
            description="This is the episode brief.",
        )

    def test_start_creates_directory_and_files(self):
        """start_episode creates the local working directory with scaffolded files."""
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            self.settings(BASE_DIR=Path(tmpdir)),
        ):
            out = StringIO()
            call_command(
                "start_episode",
                "--podcast",
                "start-test-podcast",
                "--episode",
                "draft-ep",
                stdout=out,
            )

            base = (
                Path(tmpdir)
                / "apps"
                / "podcast"
                / "pending-episodes"
                / "start-test-podcast"
                / "draft-ep"
            )
            self.assertTrue(base.exists())
            self.assertTrue((base / "research" / "documents").is_dir())
            self.assertTrue((base / "logs").is_dir())
            self.assertTrue((base / "tmp").is_dir())
            self.assertTrue((base / "research" / "p1-brief.md").is_file())
            self.assertTrue((base / "logs" / "prompts.md").is_file())
            self.assertTrue((base / "sources.md").is_file())

            # Verify brief content includes episode description
            brief_content = (base / "research" / "p1-brief.md").read_text()
            self.assertIn("Draft Episode", brief_content)
            self.assertIn("This is the episode brief.", brief_content)

    def test_start_sets_status_to_in_progress(self):
        """start_episode transitions episode status from draft to in_progress."""
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            self.settings(BASE_DIR=Path(tmpdir)),
        ):
            out = StringIO()
            call_command(
                "start_episode",
                "--podcast",
                "start-test-podcast",
                "--episode",
                "draft-ep",
                stdout=out,
            )
            self.draft_episode.refresh_from_db()
            self.assertEqual(self.draft_episode.status, "in_progress")

    def test_start_raises_error_if_not_draft(self):
        """start_episode raises CommandError if episode status is not draft."""
        self.draft_episode.status = "in_progress"
        self.draft_episode.save()

        with self.assertRaises(CommandError) as ctx:
            out = StringIO()
            call_command(
                "start_episode",
                "--podcast",
                "start-test-podcast",
                "--episode",
                "draft-ep",
                stdout=out,
            )
        self.assertIn("in_progress", str(ctx.exception))

    def test_start_raises_error_if_episode_not_found(self):
        """start_episode raises CommandError for nonexistent episode."""
        with self.assertRaises(CommandError) as ctx:
            out = StringIO()
            call_command(
                "start_episode",
                "--podcast",
                "start-test-podcast",
                "--episode",
                "nonexistent-ep",
                stdout=out,
            )
        self.assertIn("No episode found", str(ctx.exception))


class PublishEpisodeCommandTestCase(TestCase):
    """Tests for the publish_episode management command."""

    def setUp(self):
        self.podcast = Podcast.objects.create(
            title="Publish Test Podcast",
            slug="publish-test-podcast",
            description="A podcast for publish_episode tests.",
            author_name="Author",
            author_email="a@b.com",
        )
        self.episode = Episode.objects.create(
            podcast=self.podcast,
            title="Publishable Episode",
            slug="publishable-ep",
            episode_number=1,
            status="in_progress",
        )

    def _create_episode_dir(self, tmpdir: str) -> Path:
        """Helper: create an episode working directory with test files."""
        ep_dir = Path(tmpdir) / "publish-test-podcast" / "publishable-ep"
        ep_dir.mkdir(parents=True)

        # report.md
        (ep_dir / "report.md").write_text("# Episode Report\n\nFull report here.")

        # sources.md
        (ep_dir / "sources.md").write_text("# Sources\n\n- Source A\n- Source B")

        # research/p2-perplexity.md (artifact)
        (ep_dir / "research").mkdir()
        (ep_dir / "research" / "p2-perplexity.md").write_text(
            "# Perplexity Research\n\nResearch content."
        )

        # logs/prompts.md (artifact)
        (ep_dir / "logs").mkdir()
        (ep_dir / "logs" / "prompts.md").write_text("# Prompts Log\n\nPrompt content.")

        # tmp/ should be skipped
        (ep_dir / "tmp").mkdir()
        (ep_dir / "tmp" / "scratch.md").write_text("Temp stuff.")

        return ep_dir

    def test_dry_run_does_not_modify_database(self):
        """publish_episode --dry-run does not modify the database."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ep_dir = self._create_episode_dir(tmpdir)
            out = StringIO()
            call_command(
                "publish_episode",
                str(ep_dir),
                "--dry-run",
                stdout=out,
            )

        self.episode.refresh_from_db()
        self.assertEqual(self.episode.status, "in_progress")
        self.assertEqual(self.episode.report_text, "")
        self.assertEqual(EpisodeArtifact.objects.count(), 0)

    def test_populates_report_text(self):
        """publish_episode populates report_text from report.md."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ep_dir = self._create_episode_dir(tmpdir)
            out = StringIO()
            call_command(
                "publish_episode",
                str(ep_dir),
                stdout=out,
            )

        self.episode.refresh_from_db()
        self.assertIn("Full report here", self.episode.report_text)

    def test_creates_artifacts_from_md_files(self):
        """publish_episode creates EpisodeArtifact records from .md files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ep_dir = self._create_episode_dir(tmpdir)
            out = StringIO()
            call_command(
                "publish_episode",
                str(ep_dir),
                stdout=out,
            )

        artifacts = EpisodeArtifact.objects.filter(episode=self.episode)
        artifact_titles = set(artifacts.values_list("title", flat=True))
        self.assertIn("research/p2-perplexity.md", artifact_titles)
        self.assertIn("logs/prompts.md", artifact_titles)
        # report.md and sources.md should NOT be artifacts
        self.assertNotIn("report.md", artifact_titles)
        self.assertNotIn("sources.md", artifact_titles)
        # tmp/ files are imported as artifacts (process artifacts)
        self.assertIn("tmp/scratch.md", artifact_titles)

    def test_idempotent_no_duplicate_artifacts(self):
        """Re-running publish_episode updates rather than duplicates artifacts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ep_dir = self._create_episode_dir(tmpdir)
            out = StringIO()
            call_command(
                "publish_episode",
                str(ep_dir),
                "--skip-status-check",
                stdout=out,
            )
            first_count = EpisodeArtifact.objects.filter(episode=self.episode).count()

            # Update a file and re-publish
            (ep_dir / "research" / "p2-perplexity.md").write_text(
                "# Updated Research\n\nNew content."
            )
            out2 = StringIO()
            call_command(
                "publish_episode",
                str(ep_dir),
                "--skip-status-check",
                stdout=out2,
            )
            second_count = EpisodeArtifact.objects.filter(episode=self.episode).count()

        self.assertEqual(first_count, second_count)
        # Verify content was updated
        artifact = EpisodeArtifact.objects.get(
            episode=self.episode, title="research/p2-perplexity.md"
        )
        self.assertIn("Updated Research", artifact.content)

    def test_sets_status_to_complete_and_published_at(self):
        """publish_episode sets status=complete and published_at."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ep_dir = self._create_episode_dir(tmpdir)
            out = StringIO()
            call_command(
                "publish_episode",
                str(ep_dir),
                stdout=out,
            )

        self.episode.refresh_from_db()
        self.assertEqual(self.episode.status, "complete")
        self.assertIsNotNone(self.episode.published_at)

    def test_raises_error_for_nonexistent_directory(self):
        """publish_episode raises CommandError for nonexistent directory."""
        with self.assertRaises(CommandError):
            out = StringIO()
            call_command(
                "publish_episode",
                "/nonexistent/path/publish-test-podcast/publishable-ep",
                stdout=out,
            )

    def test_raises_error_for_already_complete(self):
        """publish_episode raises CommandError if episode is already complete."""
        self.episode.status = "complete"
        self.episode.save()

        with tempfile.TemporaryDirectory() as tmpdir:
            ep_dir = self._create_episode_dir(tmpdir)
            with self.assertRaises(CommandError) as ctx:
                out = StringIO()
                call_command(
                    "publish_episode",
                    str(ep_dir),
                    stdout=out,
                )
            self.assertIn("already complete", str(ctx.exception))


class BackfillEpisodesCommandTestCase(TestCase):
    """Tests for the backfill_episodes management command."""

    def test_dry_run_shows_what_would_be_imported(self):
        """backfill_episodes --dry-run does not create any database records."""
        episode_count_before = Episode.objects.count()

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a minimal source directory structure
            source_dir = Path(tmpdir) / "episodes"
            series_dir = source_dir / "algorithms-for-life"
            ep_dir = series_dir / "ep01-test-episode"
            ep_dir.mkdir(parents=True)
            (ep_dir / "report.md").write_text("# Report\n\nSome content.")
            (ep_dir / "research").mkdir()
            (ep_dir / "research" / "brief.md").write_text("# Brief")

            out = StringIO()
            call_command(
                "backfill_episodes",
                "--source-dir",
                str(source_dir),
                "--dry-run",
                stdout=out,
            )

        output_text = out.getvalue()
        self.assertIn("DRY RUN", output_text)
        # Dry run should not create any new episodes
        self.assertEqual(Episode.objects.count(), episode_count_before)

    def test_raises_error_for_nonexistent_source(self):
        """backfill_episodes raises CommandError for nonexistent source directory."""
        with self.assertRaises(CommandError):
            out = StringIO()
            call_command(
                "backfill_episodes",
                "--source-dir",
                "/nonexistent/path/episodes",
                stdout=out,
            )

    def test_derive_title(self):
        """Verify the _derive_title helper produces correct titles."""
        from apps.podcast.management.commands.backfill_episodes import Command

        cmd = Command()
        self.assertEqual(cmd._derive_title("ep01-getting-started"), "Getting Started")
        self.assertEqual(cmd._derive_title("ep10-game-theory"), "Game Theory")
        self.assertEqual(
            cmd._derive_title("understanding-markets"), "Understanding Markets"
        )
