"""Tests for the `ep` management command."""

from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.podcast.models import Episode, EpisodeArtifact, EpisodeWorkflow, Podcast


def _make_podcast(**kwargs):
    defaults = {
        "title": "Test Podcast",
        "slug": "test-podcast",
        "description": "A podcast for testing.",
        "author_name": "Author",
        "author_email": "author@example.com",
    }
    defaults.update(kwargs)
    return Podcast.objects.create(**defaults)


def _make_episode(podcast, **kwargs):
    defaults = {
        "title": "Test Episode",
        "slug": "test-ep",
        "episode_number": 1,
        "status": "draft",
        "description": "Episode description here.",
    }
    defaults.update(kwargs)
    return Episode.objects.create(podcast=podcast, **defaults)


class EpCommandShowTest(TestCase):
    """ep <slug> — show episode fields, workflow state, artifact list."""

    def setUp(self):
        self.podcast = _make_podcast()
        self.episode = _make_episode(self.podcast)

    def test_show_prints_episode_fields(self):
        out = StringIO()
        call_command("ep", "test-ep", stdout=out)
        output = out.getvalue()

        self.assertIn("Test Episode", output)
        self.assertIn("test-ep", output)
        self.assertIn("Test Podcast", output)
        self.assertIn("draft", output)
        self.assertIn("Episode description here", output)

    def test_show_truncates_description_at_120_chars(self):
        long_desc = "A" * 150
        self.episode.description = long_desc
        self.episode.save()

        out = StringIO()
        call_command("ep", "test-ep", stdout=out)
        output = out.getvalue()

        self.assertIn("...", output)
        # The truncated description should be 120 chars + "..."
        self.assertNotIn("A" * 121, output)

    def test_show_no_workflow_shows_placeholder(self):
        out = StringIO()
        call_command("ep", "test-ep", stdout=out)
        output = out.getvalue()
        self.assertIn("(no workflow)", output)

    def test_show_with_workflow(self):
        EpisodeWorkflow.objects.create(
            episode=self.episode,
            current_step="Research",
            status="running",
        )
        out = StringIO()
        call_command("ep", "test-ep", stdout=out)
        output = out.getvalue()
        self.assertIn("Research", output)
        self.assertIn("running", output)

    def test_show_lists_artifacts(self):
        EpisodeArtifact.objects.create(
            episode=self.episode,
            title="p1-brief",
            content="Some brief content",
        )
        out = StringIO()
        call_command("ep", "test-ep", stdout=out)
        output = out.getvalue()
        self.assertIn("p1-brief", output)

    def test_unknown_slug_raises_command_error(self):
        with self.assertRaises(CommandError) as ctx:
            call_command("ep", "nonexistent-slug")
        self.assertIn("No episode found", str(ctx.exception))
        self.assertIn("nonexistent-slug", str(ctx.exception))


class EpCommandSetTest(TestCase):
    """ep <slug> set field=value — update Episode fields."""

    def setUp(self):
        self.podcast = _make_podcast()
        self.episode = _make_episode(self.podcast)

    def test_set_valid_field_updates_episode(self):
        out = StringIO()
        call_command("ep", "test-ep", "set", "description=New description", stdout=out)
        output = out.getvalue()

        self.episode.refresh_from_db()
        self.assertEqual(self.episode.description, "New description")
        self.assertIn("description", output)

    def test_set_multiple_fields(self):
        out = StringIO()
        call_command(
            "ep", "test-ep", "set", "title=New Title", "status=in_progress", stdout=out
        )
        self.episode.refresh_from_db()
        self.assertEqual(self.episode.title, "New Title")
        self.assertEqual(self.episode.status, "in_progress")

    def test_set_unknown_slug_raises_error(self):
        with self.assertRaises(CommandError) as ctx:
            call_command("ep", "no-such-slug", "set", "description=test")
        self.assertIn("No episode found", str(ctx.exception))

    def test_set_unknown_field_raises_error_before_save(self):
        original_description = self.episode.description

        with self.assertRaises(CommandError) as ctx:
            call_command("ep", "test-ep", "set", "nonexistent_field=value")

        self.assertIn("nonexistent_field", str(ctx.exception))
        # Verify no save occurred
        self.episode.refresh_from_db()
        self.assertEqual(self.episode.description, original_description)

    def test_set_missing_equals_raises_error(self):
        with self.assertRaises(CommandError) as ctx:
            call_command("ep", "test-ep", "set", "description")
        self.assertIn("expected field=value", str(ctx.exception))

    def test_set_no_pairs_raises_error(self):
        with self.assertRaises(CommandError) as ctx:
            call_command("ep", "test-ep", "set")
        self.assertIn("set requires at least one", str(ctx.exception))


class EpCommandBriefTest(TestCase):
    """ep <slug> brief — print p1-brief artifact content."""

    def setUp(self):
        self.podcast = _make_podcast()
        self.episode = _make_episode(self.podcast)

    def test_brief_prints_artifact_content(self):
        EpisodeArtifact.objects.create(
            episode=self.episode,
            title="p1-brief",
            content="This is the episode brief content.",
        )
        out = StringIO()
        call_command("ep", "test-ep", "brief", stdout=out)
        output = out.getvalue()
        self.assertIn("This is the episode brief content.", output)

    def test_brief_not_found_prints_message(self):
        out = StringIO()
        call_command("ep", "test-ep", "brief", stdout=out)
        output = out.getvalue()
        self.assertIn("p1-brief artifact not found", output)

    def test_brief_not_found_exits_cleanly(self):
        # Should not raise CommandError — just print a message and return
        out = StringIO()
        try:
            call_command("ep", "test-ep", "brief", stdout=out)
        except CommandError:
            self.fail("brief raised CommandError when artifact missing — should exit 0")


class EpCommandSetupTest(TestCase):
    """ep <slug> setup — call setup_episode and report artifact info."""

    def setUp(self):
        self.podcast = _make_podcast()
        self.episode = _make_episode(
            self.podcast,
            description="Brief content for setup.",
        )

    def test_setup_calls_setup_episode_and_prints_artifact_info(self):
        out = StringIO()
        call_command("ep", "test-ep", "setup", stdout=out)
        output = out.getvalue()

        # setup_episode creates p1-brief artifact
        self.assertIn("p1-brief", output)
        # word count should appear
        self.assertRegex(output, r"\d+ words")

    def test_setup_creates_artifact_in_db(self):
        call_command("ep", "test-ep", "setup")
        artifact = EpisodeArtifact.objects.filter(
            episode=self.episode, title="p1-brief"
        ).first()
        self.assertIsNotNone(artifact)

    def test_setup_idempotent_on_rerun(self):
        call_command("ep", "test-ep", "setup")
        call_command("ep", "test-ep", "setup")
        # Should still only have one p1-brief artifact
        count = EpisodeArtifact.objects.filter(
            episode=self.episode, title="p1-brief"
        ).count()
        self.assertEqual(count, 1)


class EpCommandProductionWarningTest(TestCase):
    """Production database warning banner."""

    def setUp(self):
        self.podcast = _make_podcast()
        self.episode = _make_episode(self.podcast)

    def test_remote_db_url_prints_warning_to_stderr(self):
        with patch.dict(
            "os.environ",
            {"DATABASE_URL": "postgres://user:pass@prod.example.com/mydb"},
        ):
            err = StringIO()
            call_command("ep", "test-ep", stderr=err)
            self.assertIn("PRODUCTION DATABASE", err.getvalue())
            self.assertIn("prod.example.com", err.getvalue())

    def test_localhost_db_url_no_warning(self):
        with patch.dict(
            "os.environ",
            {"DATABASE_URL": "postgres://user@localhost/mydb"},
        ):
            err = StringIO()
            call_command("ep", "test-ep", stderr=err)
            self.assertNotIn("PRODUCTION DATABASE", err.getvalue())

    def test_missing_db_url_no_warning(self):
        env = {k: v for k, v in __import__("os").environ.items() if k != "DATABASE_URL"}
        with patch.dict("os.environ", env, clear=True):
            err = StringIO()
            call_command("ep", "test-ep", stderr=err)
            self.assertNotIn("PRODUCTION DATABASE", err.getvalue())
