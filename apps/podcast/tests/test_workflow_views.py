from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.podcast.models import Episode, Podcast

User = get_user_model()

SIMPLE_STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}


@override_settings(STORAGES=SIMPLE_STORAGES)
class EpisodeWorkflowViewTestCase(TestCase):
    """Integration tests for the EpisodeWorkflowView."""

    def setUp(self):
        self.podcast = Podcast.objects.create(
            title="Test Podcast",
            slug="test-podcast",
            description="A test podcast.",
            author_name="Author",
            author_email="author@example.com",
            is_public=True,
        )
        self.episode = Episode.objects.create(
            podcast=self.podcast,
            title="Test Episode",
            slug="test-episode",
            episode_number=1,
            status="in_progress",
            description="An episode in progress.",
            audio_url="https://example.com/ep1.mp3",
        )
        self.staff_user = User.objects.create_user(
            username="staff", password="pass", is_staff=True
        )
        self.regular_user = User.objects.create_user(
            username="regular", password="pass", is_staff=False
        )

    def _workflow_url(
        self,
        step: int = 1,
        slug: str = "test-podcast",
        episode_slug: str = "test-episode",
    ) -> str:
        return f"/podcast/{slug}/{episode_slug}/edit/{step}/"

    # 1. Anonymous user is redirected to login
    def test_anonymous_redirects_to_login(self):
        response = self.client.get(self._workflow_url(step=1))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.url)

    # 2. Non-staff user gets 403
    def test_non_staff_gets_403(self):
        self.client.login(username="regular", password="pass")
        response = self.client.get(self._workflow_url(step=1))
        self.assertEqual(response.status_code, 403)

    # 3. Staff user gets 200
    def test_staff_gets_200(self):
        self.client.login(username="staff", password="pass")
        response = self.client.get(self._workflow_url(step=1))
        self.assertEqual(response.status_code, 200)

    # 4. Step 0 returns 404
    def test_step_0_returns_404(self):
        self.client.login(username="staff", password="pass")
        response = self.client.get(self._workflow_url(step=0))
        self.assertEqual(response.status_code, 404)

    # 5. Step 13 returns 404
    def test_step_13_returns_404(self):
        self.client.login(username="staff", password="pass")
        response = self.client.get(self._workflow_url(step=13))
        self.assertEqual(response.status_code, 404)

    # 6. Step 1 returns 200
    def test_step_1_returns_200(self):
        self.client.login(username="staff", password="pass")
        response = self.client.get(self._workflow_url(step=1))
        self.assertEqual(response.status_code, 200)

    # 7. Step 12 returns 200
    def test_step_12_returns_200(self):
        self.client.login(username="staff", password="pass")
        response = self.client.get(self._workflow_url(step=12))
        self.assertEqual(response.status_code, 200)

    # 8. Nonexistent podcast returns 404
    def test_nonexistent_podcast_returns_404(self):
        self.client.login(username="staff", password="pass")
        response = self.client.get(self._workflow_url(step=1, slug="nonexistent"))
        self.assertEqual(response.status_code, 404)

    # 9. Nonexistent episode returns 404
    def test_nonexistent_episode_returns_404(self):
        self.client.login(username="staff", password="pass")
        response = self.client.get(
            self._workflow_url(step=1, episode_slug="nonexistent")
        )
        self.assertEqual(response.status_code, 404)

    # 10. HTMX request returns partial (no DOCTYPE)
    def test_htmx_request_returns_partial(self):
        self.client.login(username="staff", password="pass")
        response = self.client.get(self._workflow_url(step=1), HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertNotIn("<!DOCTYPE html", content)

    # 11. Full (non-HTMX) request returns complete page with DOCTYPE
    def test_full_request_returns_complete_page(self):
        self.client.login(username="staff", password="pass")
        response = self.client.get(self._workflow_url(step=1))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("<!DOCTYPE html", content)

    # 12. Context contains 12 phases
    def test_context_contains_phases(self):
        self.client.login(username="staff", password="pass")
        response = self.client.get(self._workflow_url(step=1))
        self.assertEqual(len(response.context["phases"]), 12)

    # 13. Context contains correct current_step
    def test_context_contains_current_step(self):
        self.client.login(username="staff", password="pass")
        response = self.client.get(self._workflow_url(step=5))
        self.assertEqual(response.context["current_step"], 5)

    # 14. Context contains correct current_phase
    def test_context_contains_current_phase(self):
        self.client.login(username="staff", password="pass")
        response = self.client.get(self._workflow_url(step=3))
        self.assertEqual(response.context["current_phase"].number, 3)

    # 15. Private podcast is still accessible to staff via workflow
    def test_private_podcast_accessible_to_staff(self):
        private_podcast = Podcast.objects.create(
            title="Private Podcast",
            slug="private-podcast",
            description="A private podcast.",
            author_name="Author",
            author_email="author@example.com",
            is_public=False,
        )
        private_episode = Episode.objects.create(
            podcast=private_podcast,
            title="Private Episode",
            slug="private-episode",
            episode_number=1,
            status="in_progress",
        )
        self.client.login(username="staff", password="pass")
        response = self.client.get(
            self._workflow_url(
                step=1, slug="private-podcast", episode_slug="private-episode"
            )
        )
        self.assertEqual(response.status_code, 200)
