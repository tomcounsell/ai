from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.podcast.models import Episode, Podcast

User = get_user_model()


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

    def test_feed_403_for_private_podcast_without_token(self):
        """Private podcast without token returns 403."""
        private_podcast = Podcast.objects.create(
            title="Private Podcast",
            slug="private-podcast",
            description="desc",
            author_name="Author",
            author_email="a@b.com",
            is_public=False,
        )
        response = self.client.get(f"/podcast/{private_podcast.slug}/feed.xml")
        self.assertEqual(response.status_code, 403)

    def test_public_feed_has_cache_control(self):
        """Public feed has Cache-Control: public, max-age=300."""
        response = self.client.get(f"/podcast/{self.podcast.slug}/feed.xml")
        self.assertEqual(response["Cache-Control"], "public, max-age=300")

    def test_feed_404_for_nonexistent_slug(self):
        """Returns 404 for a nonexistent podcast slug."""
        response = self.client.get("/podcast/nonexistent-podcast/feed.xml")
        self.assertEqual(response.status_code, 404)

    def test_feed_contains_content_namespace(self):
        """Response contains xmlns:content namespace declaration."""
        response = self.client.get(f"/podcast/{self.podcast.slug}/feed.xml")
        content = response.content.decode("utf-8")
        self.assertIn("xmlns:content", content)
        self.assertIn("http://purl.org/rss/1.0/modules/content/", content)

    def test_feed_contains_content_encoded(self):
        """Response contains <content:encoded> element for episodes."""
        response = self.client.get(f"/podcast/{self.podcast.slug}/feed.xml")
        content = response.content.decode("utf-8")
        self.assertIn("content:encoded", content)

    def test_feed_filters_on_published_at(self):
        """Feed filters on published_at, not status field."""
        # Create an episode with status=complete but no published_at
        Episode.objects.create(
            podcast=self.podcast,
            title="Complete But Unpublished",
            slug="complete-unpublished",
            episode_number=5,
            status="complete",
            audio_url="https://example.com/complete.mp3",
            published_at=None,
        )
        response = self.client.get(f"/podcast/{self.podcast.slug}/feed.xml")
        content = response.content.decode("utf-8")
        self.assertNotIn("Complete But Unpublished", content)

    def test_feed_excludes_draft_episodes_without_published_at(self):
        """Draft episodes with no published_at don't appear in feed."""
        Episode.objects.create(
            podcast=self.podcast,
            title="Draft No Publish",
            slug="draft-no-publish",
            episode_number=6,
            status="draft",
            audio_url="https://example.com/draft.mp3",
            published_at=None,
        )
        response = self.client.get(f"/podcast/{self.podcast.slug}/feed.xml")
        content = response.content.decode("utf-8")
        self.assertNotIn("Draft No Publish", content)


@override_settings(SUPABASE_USER_ACCESS_TOKEN="valid-test-token")
class PrivateFeedTestCase(TestCase):
    """Tests for private podcast feed with token auth."""

    def setUp(self):
        self.private_podcast = Podcast.objects.create(
            title="Private Podcast",
            slug="private-test-podcast",
            description="A private podcast for testing.",
            author_name="Private Author",
            author_email="private@example.com",
            cover_image_url="https://example.com/private-cover.jpg",
            language="en",
            is_public=False,
            categories=["Business"],
            website_url="https://example.com/private",
        )
        self.episode = Episode.objects.create(
            podcast=self.private_podcast,
            title="Private Episode One",
            slug="private-episode-one",
            episode_number=1,
            description="A private episode.",
            audio_url="podcast/private-test-podcast/ep1/audio.mp3",
            audio_duration_seconds=600,
            audio_file_size_bytes=900000,
            published_at=timezone.now() - timezone.timedelta(days=1),
        )

    def test_private_feed_requires_token(self):
        """Private feed without ?token= returns 403."""
        response = self.client.get(f"/podcast/{self.private_podcast.slug}/feed.xml")
        self.assertEqual(response.status_code, 403)

    def test_private_feed_rejects_wrong_token(self):
        """Private feed with incorrect token returns 403."""
        response = self.client.get(
            f"/podcast/{self.private_podcast.slug}/feed.xml?token=wrong-token"
        )
        self.assertEqual(response.status_code, 403)

    def test_private_feed_rejects_empty_token(self):
        """Private feed with empty token returns 403."""
        response = self.client.get(
            f"/podcast/{self.private_podcast.slug}/feed.xml?token="
        )
        self.assertEqual(response.status_code, 403)

    @patch("apps.podcast.views.feed_views.get_file_url")
    def test_private_feed_accepts_valid_token(self, mock_get_file_url):
        """Private feed with correct token returns 200."""
        mock_get_file_url.side_effect = lambda key, **kw: f"https://signed.url/{key}"
        response = self.client.get(
            f"/podcast/{self.private_podcast.slug}/feed.xml?token=valid-test-token"
        )
        self.assertEqual(response.status_code, 200)

    @patch("apps.podcast.views.feed_views.get_file_url")
    def test_private_feed_content_type(self, mock_get_file_url):
        """Private feed has application/rss+xml content type."""
        mock_get_file_url.side_effect = lambda key, **kw: f"https://signed.url/{key}"
        response = self.client.get(
            f"/podcast/{self.private_podcast.slug}/feed.xml?token=valid-test-token"
        )
        self.assertIn("application/rss+xml", response["Content-Type"])

    @patch("apps.podcast.views.feed_views.get_file_url")
    def test_private_feed_no_cache(self, mock_get_file_url):
        """Private feed has Cache-Control: no-store."""
        mock_get_file_url.side_effect = lambda key, **kw: f"https://signed.url/{key}"
        response = self.client.get(
            f"/podcast/{self.private_podcast.slug}/feed.xml?token=valid-test-token"
        )
        self.assertEqual(response["Cache-Control"], "no-store")

    @patch("apps.podcast.views.feed_views.get_file_url")
    def test_private_feed_contains_podcast_title(self, mock_get_file_url):
        """Private feed XML contains the podcast title."""
        mock_get_file_url.side_effect = lambda key, **kw: f"https://signed.url/{key}"
        response = self.client.get(
            f"/podcast/{self.private_podcast.slug}/feed.xml?token=valid-test-token"
        )
        content = response.content.decode("utf-8")
        self.assertIn("Private Podcast", content)

    @patch("apps.podcast.views.feed_views.get_file_url")
    def test_private_feed_contains_episode_title(self, mock_get_file_url):
        """Private feed XML contains episode titles."""
        mock_get_file_url.side_effect = lambda key, **kw: f"https://signed.url/{key}"
        response = self.client.get(
            f"/podcast/{self.private_podcast.slug}/feed.xml?token=valid-test-token"
        )
        content = response.content.decode("utf-8")
        self.assertIn("Private Episode One", content)

    @patch("apps.podcast.views.feed_views.get_file_url")
    def test_private_feed_generates_signed_urls(self, mock_get_file_url):
        """Private feed replaces audio_url with signed URLs."""
        mock_get_file_url.return_value = (
            "https://test.supabase.co/storage/v1/object/sign/"
            "private-bucket/audio.mp3?token=signed123"
        )
        response = self.client.get(
            f"/podcast/{self.private_podcast.slug}/feed.xml?token=valid-test-token"
        )
        self.assertEqual(response.status_code, 200)

        # Verify get_file_url was called with public=False for the audio_url
        mock_get_file_url.assert_any_call(
            "podcast/private-test-podcast/ep1/audio.mp3", public=False
        )

        # Verify the signed URL appears in the response
        content = response.content.decode("utf-8")
        self.assertIn("signed123", content)

    @patch("apps.podcast.views.feed_views.get_file_url")
    def test_private_feed_signs_cover_image_url(self, mock_get_file_url):
        """Private feed also signs episode cover image URLs."""
        Episode.objects.create(
            podcast=self.private_podcast,
            title="Cover Episode",
            slug="cover-episode",
            episode_number=2,
            description="Has its own cover.",
            audio_url="podcast/private-test-podcast/ep2/audio.mp3",
            cover_image_url="podcast/private-test-podcast/ep2/cover.jpg",
            audio_duration_seconds=300,
            audio_file_size_bytes=500000,
            published_at=timezone.now() - timezone.timedelta(hours=6),
        )

        def fake_signed_url(key, public=True):
            return f"https://signed.url/{key}?token=signed"

        mock_get_file_url.side_effect = fake_signed_url

        response = self.client.get(
            f"/podcast/{self.private_podcast.slug}/feed.xml?token=valid-test-token"
        )
        self.assertEqual(response.status_code, 200)

        # Both audio and cover should have been signed
        calls = mock_get_file_url.call_args_list
        audio_calls = [c for c in calls if "audio" in str(c)]
        cover_calls = [c for c in calls if "cover" in str(c)]
        self.assertTrue(len(audio_calls) > 0, "Audio URL should be signed")
        self.assertTrue(len(cover_calls) > 0, "Cover URL should be signed")

    @override_settings(SUPABASE_USER_ACCESS_TOKEN="")
    def test_private_feed_no_configured_token_returns_403(self):
        """If server has no SUPABASE_USER_ACCESS_TOKEN configured, returns 403."""
        response = self.client.get(
            f"/podcast/{self.private_podcast.slug}/feed.xml?token=anything"
        )
        self.assertEqual(response.status_code, 403)

    @patch("apps.podcast.views.feed_views.get_file_url")
    def test_private_feed_allows_owner_without_token(self, mock_get_file_url):
        """Owner can access private feed without ?token= query param."""
        mock_get_file_url.side_effect = lambda key, **kw: f"https://signed.url/{key}"
        owner = User.objects.create_user(username="feedowner", password="testpass123")
        self.private_podcast.owner = owner
        # Save without triggering is_public change check — use update()
        Podcast.objects.filter(pk=self.private_podcast.pk).update(owner=owner)
        self.private_podcast.refresh_from_db()

        self.client.login(username="feedowner", password="testpass123")
        response = self.client.get(f"/podcast/{self.private_podcast.slug}/feed.xml")
        self.assertEqual(response.status_code, 200)

    def test_private_feed_rejects_non_owner_without_token(self):
        """Non-owner without token gets 403 on private feed."""
        owner = User.objects.create_user(username="feedowner2", password="testpass123")
        non_owner = User.objects.create_user(
            username="feedstranger", password="testpass123"
        )
        Podcast.objects.filter(pk=self.private_podcast.pk).update(owner=owner)

        self.client.login(username="feedstranger", password="testpass123")
        response = self.client.get(f"/podcast/{self.private_podcast.slug}/feed.xml")
        self.assertEqual(response.status_code, 403)

    @patch("apps.podcast.views.feed_views.get_file_url")
    def test_private_feed_still_allows_token_without_owner(self, mock_get_file_url):
        """Token auth still works on a private podcast with no owner set."""
        mock_get_file_url.side_effect = lambda key, **kw: f"https://signed.url/{key}"
        # Ensure no owner is set
        Podcast.objects.filter(pk=self.private_podcast.pk).update(owner=None)
        self.private_podcast.refresh_from_db()

        response = self.client.get(
            f"/podcast/{self.private_podcast.slug}/feed.xml?token=valid-test-token"
        )
        self.assertEqual(response.status_code, 200)
