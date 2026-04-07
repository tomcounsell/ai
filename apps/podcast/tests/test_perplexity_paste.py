"""Tests for PastePerplexityResearchView."""

from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.podcast.models import Episode, EpisodeArtifact, EpisodeWorkflow, Podcast

User = get_user_model()

SIMPLE_STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

PERPLEXITY_PASTE_URL = "/podcast/{slug}/{episode_slug}/perplexity-paste/"


def _paste_url(slug="test-podcast", episode_slug="test-episode"):
    return PERPLEXITY_PASTE_URL.format(slug=slug, episode_slug=episode_slug)


@override_settings(STORAGES=SIMPLE_STORAGES)
class PastePerplexityResearchViewTestCase(TestCase):
    """Tests for PastePerplexityResearchView."""

    def setUp(self):
        self.podcast = Podcast.objects.create(
            title="Test Podcast",
            slug="test-podcast",
            description="A test podcast.",
            author_name="Author",
            author_email="author@example.com",
            privacy=Podcast.Privacy.PUBLIC,
        )
        self.episode = Episode.objects.create(
            podcast=self.podcast,
            title="Test Episode",
            slug="test-episode",
            episode_number=1,
            status="in_progress",
            description="An episode in progress.",
        )
        self.staff_user = User.objects.create_user(
            username="staff", password="pass", is_staff=True
        )
        self.regular_user = User.objects.create_user(
            username="regular", password="pass", is_staff=False
        )
        # Create p2-perplexity artifact in skipped state
        self.perplexity_artifact = EpisodeArtifact.objects.create(
            episode=self.episode,
            title="p2-perplexity",
            content="[SKIPPED: no Perplexity API key]",
        )

    def _login(self):
        self.client.login(username="staff", password="pass")

    # --- Authentication / authorization ---

    def test_anonymous_redirects_to_login(self):
        response = self.client.post(_paste_url(), {"content": "Some research text"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.url)

    def test_non_staff_gets_403(self):
        self.client.login(username="regular", password="pass")
        response = self.client.post(_paste_url(), {"content": "Some research text"})
        self.assertEqual(response.status_code, 403)

    # --- Valid paste ---

    @patch("apps.podcast.workflow._resolve_task")
    def test_valid_paste_writes_artifact_and_redirects(self, mock_resolve):
        """Valid content should overwrite p2-perplexity and redirect to step 3."""
        mock_task = MagicMock()
        mock_resolve.return_value = mock_task

        self._login()
        content = "This is valid Perplexity research output with real content."
        response = self.client.post(_paste_url(), {"content": content})

        self.assertEqual(response.status_code, 302)
        self.assertIn("/edit/3/", response.url)

        self.perplexity_artifact.refresh_from_db()
        self.assertEqual(self.perplexity_artifact.content, content)
        self.assertTrue(self.perplexity_artifact.metadata.get("manually_pasted"))
        self.assertIn("manually_pasted_at", self.perplexity_artifact.metadata)

    @patch("apps.podcast.workflow._resolve_task")
    def test_valid_paste_enqueues_question_discovery(self, mock_resolve):
        """Valid paste should call step_question_discovery.enqueue."""
        mock_task = MagicMock()
        mock_resolve.return_value = mock_task

        self._login()
        content = "Valid research content from Perplexity."
        self.client.post(_paste_url(), {"content": content})

        mock_resolve.assert_called_once_with(
            "apps.podcast.tasks.step_question_discovery"
        )
        mock_task.enqueue.assert_called_once_with(episode_id=self.episode.pk)

    @patch("apps.podcast.workflow._resolve_task")
    def test_valid_paste_resumes_paused_workflow(self, mock_resolve):
        """If workflow is paused_for_human, valid paste should resume it."""
        mock_task = MagicMock()
        mock_resolve.return_value = mock_task

        wf = EpisodeWorkflow.objects.create(
            episode=self.episode,
            current_step="Question Discovery",
            status="paused_for_human",
        )

        self._login()
        self.client.post(_paste_url(), {"content": "Real research output here."})

        wf.refresh_from_db()
        self.assertEqual(wf.status, "running")

    # --- Invalid content: empty ---

    @patch("apps.podcast.workflow._resolve_task")
    def test_empty_content_does_not_write(self, mock_resolve):
        """Empty content should not write to the artifact or enqueue."""
        mock_task = MagicMock()
        mock_resolve.return_value = mock_task

        original_content = self.perplexity_artifact.content
        self._login()
        self.client.post(_paste_url(), {"content": ""})

        self.perplexity_artifact.refresh_from_db()
        self.assertEqual(self.perplexity_artifact.content, original_content)
        mock_task.enqueue.assert_not_called()

    @patch("apps.podcast.workflow._resolve_task")
    def test_whitespace_only_content_does_not_write(self, mock_resolve):
        """Whitespace-only content should be treated as empty and rejected."""
        mock_task = MagicMock()
        mock_resolve.return_value = mock_task

        original_content = self.perplexity_artifact.content
        self._login()
        self.client.post(_paste_url(), {"content": "   \n\t  "})

        self.perplexity_artifact.refresh_from_db()
        self.assertEqual(self.perplexity_artifact.content, original_content)
        mock_task.enqueue.assert_not_called()

    # --- Invalid content: sentinel-prefixed ---

    @patch("apps.podcast.workflow._resolve_task")
    def test_skipped_sentinel_content_is_rejected(self, mock_resolve):
        """Content starting with [SKIPPED: is an error sentinel and should be rejected."""
        mock_task = MagicMock()
        mock_resolve.return_value = mock_task

        original_content = self.perplexity_artifact.content
        self._login()
        self.client.post(_paste_url(), {"content": "[SKIPPED: no API key configured]"})

        self.perplexity_artifact.refresh_from_db()
        self.assertEqual(self.perplexity_artifact.content, original_content)
        mock_task.enqueue.assert_not_called()

    @patch("apps.podcast.workflow._resolve_task")
    def test_failed_sentinel_content_is_rejected(self, mock_resolve):
        """Content starting with [FAILED: is an error sentinel and should be rejected."""
        mock_task = MagicMock()
        mock_resolve.return_value = mock_task

        original_content = self.perplexity_artifact.content
        self._login()
        self.client.post(_paste_url(), {"content": "[FAILED: API timeout]"})

        self.perplexity_artifact.refresh_from_db()
        self.assertEqual(self.perplexity_artifact.content, original_content)
        mock_task.enqueue.assert_not_called()

    # --- Missing artifact ---

    @patch("apps.podcast.workflow._resolve_task")
    def test_missing_artifact_does_not_crash(self, mock_resolve):
        """If p2-perplexity artifact doesn't exist, view should redirect silently."""
        mock_task = MagicMock()
        mock_resolve.return_value = mock_task

        self.perplexity_artifact.delete()

        self._login()
        response = self.client.post(_paste_url(), {"content": "Real research here."})

        # Should redirect without crashing
        self.assertEqual(response.status_code, 302)
        # Should NOT enqueue task since artifact was missing
        mock_task.enqueue.assert_not_called()
