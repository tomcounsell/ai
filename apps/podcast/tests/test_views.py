from django.test import TestCase, override_settings
from django.utils import timezone

from apps.podcast.models import Episode, Podcast

SIMPLE_STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}


@override_settings(STORAGES=SIMPLE_STORAGES)
class PodcastListViewTestCase(TestCase):
    """Tests for the podcast list view."""

    def test_list_returns_200(self):
        """GET /podcast/ returns 200."""
        response = self.client.get("/podcast/")
        self.assertEqual(response.status_code, 200)

    def test_list_shows_public_podcasts(self):
        """Public podcast appears in response."""
        Podcast.objects.create(
            title="Public Podcast",
            slug="public-podcast",
            description="A public podcast.",
            author_name="Author",
            author_email="a@b.com",
            is_public=True,
        )
        response = self.client.get("/podcast/")
        self.assertContains(response, "Public Podcast")

    def test_list_hides_private_podcasts(self):
        """Private podcast doesn't appear."""
        Podcast.objects.create(
            title="Hidden Podcast",
            slug="hidden-podcast",
            description="A hidden podcast.",
            author_name="Author",
            author_email="a@b.com",
            is_public=False,
        )
        response = self.client.get("/podcast/")
        self.assertNotContains(response, "Hidden Podcast")


@override_settings(STORAGES=SIMPLE_STORAGES)
class PodcastDetailViewTestCase(TestCase):
    """Tests for the podcast detail view."""

    def setUp(self):
        self.podcast = Podcast.objects.create(
            title="Detail Podcast",
            slug="detail-podcast",
            description="Podcast for detail view tests.",
            author_name="Author",
            author_email="a@b.com",
            is_public=True,
        )
        self.published_episode = Episode.objects.create(
            podcast=self.podcast,
            title="Published Episode",
            slug="published-episode",
            episode_number=1,
            audio_url="https://example.com/ep1.mp3",
            published_at=timezone.now() - timezone.timedelta(hours=1),
        )
        self.draft_episode = Episode.objects.create(
            podcast=self.podcast,
            title="Draft Episode",
            slug="draft-episode",
            episode_number=2,
            audio_url="https://example.com/ep2.mp3",
            published_at=None,
        )

    def test_detail_returns_200(self):
        """GET /podcast/{slug}/ returns 200."""
        response = self.client.get(f"/podcast/{self.podcast.slug}/")
        self.assertEqual(response.status_code, 200)

    def test_detail_shows_episodes(self):
        """Published episodes appear."""
        response = self.client.get(f"/podcast/{self.podcast.slug}/")
        self.assertContains(response, "Published Episode")

    def test_detail_hides_draft_episodes(self):
        """Draft episodes don't appear."""
        response = self.client.get(f"/podcast/{self.podcast.slug}/")
        self.assertNotContains(response, "Draft Episode")

    def test_detail_404_for_private(self):
        """Private podcast returns 404."""
        private_podcast = Podcast.objects.create(
            title="Private Podcast",
            slug="private-detail",
            description="desc",
            author_name="Author",
            author_email="a@b.com",
            is_public=False,
        )
        response = self.client.get(f"/podcast/{private_podcast.slug}/")
        self.assertEqual(response.status_code, 404)


@override_settings(STORAGES=SIMPLE_STORAGES)
class EpisodeDetailViewTestCase(TestCase):
    """Tests for the episode detail view."""

    def setUp(self):
        self.podcast = Podcast.objects.create(
            title="Episode Detail Podcast",
            slug="ep-detail-podcast",
            description="Podcast for episode detail tests.",
            author_name="Author",
            author_email="a@b.com",
            is_public=True,
        )
        self.published_episode = Episode.objects.create(
            podcast=self.podcast,
            title="Viewable Episode",
            slug="viewable-episode",
            episode_number=1,
            audio_url="https://example.com/ep1.mp3",
            published_at=timezone.now() - timezone.timedelta(hours=1),
        )
        self.draft_episode = Episode.objects.create(
            podcast=self.podcast,
            title="Draft Only Episode",
            slug="draft-only",
            episode_number=2,
            audio_url="https://example.com/ep2.mp3",
            published_at=None,
        )

    def test_episode_detail_returns_200(self):
        """GET /podcast/{slug}/{episode-slug}/ returns 200."""
        response = self.client.get(
            f"/podcast/{self.podcast.slug}/{self.published_episode.slug}/"
        )
        self.assertEqual(response.status_code, 200)

    def test_episode_detail_404_for_draft(self):
        """Draft episode returns 404."""
        response = self.client.get(
            f"/podcast/{self.podcast.slug}/{self.draft_episode.slug}/"
        )
        self.assertEqual(response.status_code, 404)


class EpisodeReportViewTestCase(TestCase):
    """Tests for the episode report text view."""

    def setUp(self):
        self.podcast = Podcast.objects.create(
            title="Report Podcast",
            slug="report-podcast",
            description="Podcast for report tests.",
            author_name="Author",
            author_email="a@b.com",
            is_public=True,
        )
        self.episode_with_report = Episode.objects.create(
            podcast=self.podcast,
            title="Episode With Report",
            slug="ep-with-report",
            episode_number=1,
            audio_url="https://example.com/ep1.mp3",
            report_text="This is the episode report content.",
            published_at=timezone.now() - timezone.timedelta(hours=1),
        )
        self.episode_without_report = Episode.objects.create(
            podcast=self.podcast,
            title="Episode Without Report",
            slug="ep-without-report",
            episode_number=2,
            audio_url="https://example.com/ep2.mp3",
            report_text="",
            published_at=timezone.now() - timezone.timedelta(hours=1),
        )

    def test_report_returns_text(self):
        """Episode with report_text returns 200 text/plain."""
        response = self.client.get(
            f"/podcast/{self.podcast.slug}/{self.episode_with_report.slug}/report/"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/plain")
        self.assertEqual(
            response.content.decode("utf-8"),
            "This is the episode report content.",
        )

    def test_report_404_when_empty(self):
        """Episode without report_text returns 404."""
        response = self.client.get(
            f"/podcast/{self.podcast.slug}/{self.episode_without_report.slug}/report/"
        )
        self.assertEqual(response.status_code, 404)


class EpisodeSourcesViewTestCase(TestCase):
    """Tests for the episode sources text view."""

    def setUp(self):
        self.podcast = Podcast.objects.create(
            title="Sources Podcast",
            slug="sources-podcast",
            description="Podcast for sources tests.",
            author_name="Author",
            author_email="a@b.com",
            is_public=True,
        )
        self.episode_with_sources = Episode.objects.create(
            podcast=self.podcast,
            title="Episode With Sources",
            slug="ep-with-sources",
            episode_number=1,
            audio_url="https://example.com/ep1.mp3",
            sources_text="Source 1\nSource 2\nSource 3",
            published_at=timezone.now() - timezone.timedelta(hours=1),
        )
        self.episode_without_sources = Episode.objects.create(
            podcast=self.podcast,
            title="Episode Without Sources",
            slug="ep-without-sources",
            episode_number=2,
            audio_url="https://example.com/ep2.mp3",
            sources_text="",
            published_at=timezone.now() - timezone.timedelta(hours=1),
        )

    def test_sources_returns_text(self):
        """Episode with sources_text returns 200 text/plain."""
        response = self.client.get(
            f"/podcast/{self.podcast.slug}/{self.episode_with_sources.slug}/sources/"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/plain")
        self.assertEqual(
            response.content.decode("utf-8"),
            "Source 1\nSource 2\nSource 3",
        )

    def test_sources_404_when_empty(self):
        """Episode without sources_text returns 404."""
        response = self.client.get(
            f"/podcast/{self.podcast.slug}/{self.episode_without_sources.slug}/sources/"
        )
        self.assertEqual(response.status_code, 404)
