from django.test import TestCase
from django.utils import timezone

from apps.podcast.models import Episode, Podcast


class PodcastFeedTestCase(TestCase):
    """Tests for the podcast RSS feed view."""

    def setUp(self):
        self.podcast = Podcast.objects.create(
            title="Feed Test Podcast",
            slug="feed-test-podcast",
            description="A podcast for testing feeds.",
            author_name="Feed Author",
            author_email="feed@example.com",
            cover_image_url="https://example.com/cover.jpg",
            language="en",
            is_public=True,
            categories=["Technology", "Science"],
            website_url="https://example.com",
        )
        self.ep1 = Episode.objects.create(
            podcast=self.podcast,
            title="Feed Episode One",
            slug="feed-episode-one",
            episode_number=1,
            description="First feed episode.",
            audio_url="https://example.com/feed-ep1.mp3",
            audio_duration_seconds=630,
            audio_file_size_bytes=1000000,
            published_at=timezone.now() - timezone.timedelta(days=2),
        )
        self.ep2 = Episode.objects.create(
            podcast=self.podcast,
            title="Feed Episode Two",
            slug="feed-episode-two",
            episode_number=2,
            description="Second feed episode.",
            audio_url="https://example.com/feed-ep2.mp3",
            audio_duration_seconds=3900,
            audio_file_size_bytes=2000000,
            published_at=timezone.now() - timezone.timedelta(days=1),
        )
        self.ep3 = Episode.objects.create(
            podcast=self.podcast,
            title="Feed Episode Three",
            slug="feed-episode-three",
            episode_number=3,
            description="Third feed episode.",
            audio_url="https://example.com/feed-ep3.mp3",
            audio_duration_seconds=1800,
            audio_file_size_bytes=1500000,
            published_at=timezone.now() - timezone.timedelta(hours=6),
        )

    def test_feed_returns_200(self):
        """GET /podcast/{slug}/feed.xml returns 200."""
        response = self.client.get(f"/podcast/{self.podcast.slug}/feed.xml")
        self.assertEqual(response.status_code, 200)

    def test_feed_content_type(self):
        """Response content type is application/rss+xml."""
        response = self.client.get(f"/podcast/{self.podcast.slug}/feed.xml")
        self.assertIn("application/rss+xml", response["Content-Type"])

    def test_feed_contains_podcast_title(self):
        """Response contains podcast title."""
        response = self.client.get(f"/podcast/{self.podcast.slug}/feed.xml")
        content = response.content.decode("utf-8")
        self.assertIn("Feed Test Podcast", content)

    def test_feed_contains_episode_titles(self):
        """Response contains episode titles."""
        response = self.client.get(f"/podcast/{self.podcast.slug}/feed.xml")
        content = response.content.decode("utf-8")
        self.assertIn("Feed Episode One", content)
        self.assertIn("Feed Episode Two", content)
        self.assertIn("Feed Episode Three", content)

    def test_feed_contains_itunes_namespace(self):
        """Response contains iTunes namespace declaration."""
        response = self.client.get(f"/podcast/{self.podcast.slug}/feed.xml")
        content = response.content.decode("utf-8")
        self.assertIn("xmlns:itunes", content)
        self.assertIn("http://www.itunes.com/dtds/podcast-1.0.dtd", content)

    def test_feed_excludes_unpublished_episodes(self):
        """Create draft episode (no published_at), verify not in feed."""
        Episode.objects.create(
            podcast=self.podcast,
            title="Draft Episode",
            slug="draft-episode",
            episode_number=4,
            audio_url="https://example.com/draft.mp3",
            published_at=None,
        )
        response = self.client.get(f"/podcast/{self.podcast.slug}/feed.xml")
        content = response.content.decode("utf-8")
        self.assertNotIn("Draft Episode", content)

    def test_feed_404_for_private_podcast(self):
        """Podcast with is_public=False returns 404."""
        private_podcast = Podcast.objects.create(
            title="Private Podcast",
            slug="private-podcast",
            description="desc",
            author_name="Author",
            author_email="a@b.com",
            is_public=False,
        )
        response = self.client.get(f"/podcast/{private_podcast.slug}/feed.xml")
        self.assertEqual(response.status_code, 404)

    def test_feed_404_for_nonexistent_slug(self):
        """Returns 404 for a nonexistent podcast slug."""
        response = self.client.get("/podcast/nonexistent-podcast/feed.xml")
        self.assertEqual(response.status_code, 404)
