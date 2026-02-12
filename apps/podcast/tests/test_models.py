from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from apps.podcast.models import Episode, Podcast


class PodcastModelTestCase(TestCase):
    """Tests for the Podcast model."""

    def test_create_podcast(self):
        """Create a podcast with all required fields, verify it saves."""
        podcast = Podcast.objects.create(
            title="Test Podcast",
            slug="test-podcast",
            description="A test podcast description.",
            author_name="Test Author",
            author_email="test@example.com",
            cover_image_url="https://example.com/cover.jpg",
            language="en",
            is_public=True,
            categories=["Technology"],
            website_url="https://example.com",
        )
        podcast.refresh_from_db()
        self.assertEqual(podcast.title, "Test Podcast")
        self.assertEqual(podcast.slug, "test-podcast")
        self.assertEqual(podcast.description, "A test podcast description.")
        self.assertEqual(podcast.author_name, "Test Author")
        self.assertEqual(podcast.author_email, "test@example.com")
        self.assertEqual(podcast.cover_image_url, "https://example.com/cover.jpg")
        self.assertEqual(podcast.language, "en")
        self.assertTrue(podcast.is_public)
        self.assertEqual(podcast.categories, ["Technology"])
        self.assertEqual(podcast.website_url, "https://example.com")
        self.assertIsNotNone(podcast.pk)

    def test_str_representation(self):
        """Verify __str__ returns title."""
        podcast = Podcast.objects.create(
            title="My Great Podcast",
            slug="my-great-podcast",
            description="desc",
            author_name="Author",
            author_email="a@b.com",
        )
        self.assertEqual(str(podcast), "My Great Podcast")

    def test_slug_unique(self):
        """Verify unique slug constraint."""
        Podcast.objects.create(
            title="Podcast One",
            slug="unique-slug",
            description="desc",
            author_name="Author",
            author_email="a@b.com",
        )
        with self.assertRaises(IntegrityError):
            Podcast.objects.create(
                title="Podcast Two",
                slug="unique-slug",
                description="desc",
                author_name="Author",
                author_email="a@b.com",
            )

    def test_ordering(self):
        """Verify default ordering is by title."""
        Podcast.objects.create(
            title="Zebra Podcast",
            slug="zebra",
            description="desc",
            author_name="Author",
            author_email="a@b.com",
        )
        Podcast.objects.create(
            title="Alpha Podcast",
            slug="alpha",
            description="desc",
            author_name="Author",
            author_email="a@b.com",
        )
        podcasts = list(Podcast.objects.all())
        self.assertEqual(podcasts[0].title, "Alpha Podcast")
        self.assertEqual(podcasts[1].title, "Zebra Podcast")


class EpisodeModelTestCase(TestCase):
    """Tests for the Episode model."""

    def setUp(self):
        self.podcast = Podcast.objects.create(
            title="Test Podcast",
            slug="test-podcast",
            description="A test podcast.",
            author_name="Author",
            author_email="a@b.com",
            cover_image_url="https://example.com/podcast-cover.jpg",
        )

    def test_create_episode(self):
        """Create episode with podcast FK, verify it saves."""
        episode = Episode.objects.create(
            podcast=self.podcast,
            title="First Episode",
            slug="first-episode",
            episode_number=1,
            description="The first episode.",
            audio_url="https://example.com/ep1.mp3",
            audio_duration_seconds=630,
            audio_file_size_bytes=1000000,
        )
        episode.refresh_from_db()
        self.assertEqual(episode.podcast, self.podcast)
        self.assertEqual(episode.title, "First Episode")
        self.assertEqual(episode.slug, "first-episode")
        self.assertEqual(episode.episode_number, 1)
        self.assertEqual(episode.description, "The first episode.")
        self.assertEqual(episode.audio_url, "https://example.com/ep1.mp3")
        self.assertEqual(episode.audio_duration_seconds, 630)
        self.assertEqual(episode.audio_file_size_bytes, 1000000)
        self.assertIsNotNone(episode.pk)

    def test_str_representation(self):
        """Verify __str__ returns 'N. Title' format."""
        episode = Episode.objects.create(
            podcast=self.podcast,
            title="My Episode",
            slug="my-episode",
            episode_number=5,
            audio_url="https://example.com/ep5.mp3",
        )
        self.assertEqual(str(episode), "5. My Episode")

    def test_unique_together_episode_number(self):
        """Two episodes in same podcast can't share episode_number."""
        Episode.objects.create(
            podcast=self.podcast,
            title="Episode One",
            slug="episode-one",
            episode_number=1,
            audio_url="https://example.com/ep1.mp3",
        )
        with self.assertRaises(IntegrityError):
            Episode.objects.create(
                podcast=self.podcast,
                title="Another Episode",
                slug="another-episode",
                episode_number=1,
                audio_url="https://example.com/ep1b.mp3",
            )

    def test_unique_together_slug(self):
        """Two episodes in same podcast can't share slug."""
        Episode.objects.create(
            podcast=self.podcast,
            title="Episode One",
            slug="same-slug",
            episode_number=1,
            audio_url="https://example.com/ep1.mp3",
        )
        with self.assertRaises(IntegrityError):
            Episode.objects.create(
                podcast=self.podcast,
                title="Episode Two",
                slug="same-slug",
                episode_number=2,
                audio_url="https://example.com/ep2.mp3",
            )

    def test_different_podcasts_same_episode_number(self):
        """Different podcasts CAN have same episode_number."""
        other_podcast = Podcast.objects.create(
            title="Other Podcast",
            slug="other-podcast",
            description="Another podcast.",
            author_name="Author",
            author_email="a@b.com",
        )
        ep1 = Episode.objects.create(
            podcast=self.podcast,
            title="Ep 1 Podcast A",
            slug="ep-1",
            episode_number=1,
            audio_url="https://example.com/a-ep1.mp3",
        )
        ep2 = Episode.objects.create(
            podcast=other_podcast,
            title="Ep 1 Podcast B",
            slug="ep-1",
            episode_number=1,
            audio_url="https://example.com/b-ep1.mp3",
        )
        self.assertEqual(ep1.episode_number, ep2.episode_number)
        self.assertNotEqual(ep1.podcast, ep2.podcast)

    def test_effective_cover_image_url_uses_episode(self):
        """When episode has cover_image_url, use it."""
        episode = Episode.objects.create(
            podcast=self.podcast,
            title="Episode with Cover",
            slug="ep-cover",
            episode_number=1,
            audio_url="https://example.com/ep1.mp3",
            cover_image_url="https://example.com/episode-cover.jpg",
        )
        self.assertEqual(
            episode.effective_cover_image_url,
            "https://example.com/episode-cover.jpg",
        )

    def test_effective_cover_image_url_falls_back_to_podcast(self):
        """When episode has no cover, use podcast's."""
        episode = Episode.objects.create(
            podcast=self.podcast,
            title="Episode No Cover",
            slug="ep-no-cover",
            episode_number=1,
            audio_url="https://example.com/ep1.mp3",
            cover_image_url="",
        )
        self.assertEqual(
            episode.effective_cover_image_url,
            "https://example.com/podcast-cover.jpg",
        )

    def test_publishable_mixin(self):
        """Verify is_published property works (set published_at, check is_published=True)."""
        episode = Episode.objects.create(
            podcast=self.podcast,
            title="Published Episode",
            slug="published-ep",
            episode_number=1,
            audio_url="https://example.com/ep1.mp3",
        )
        self.assertFalse(episode.is_published)

        episode.published_at = timezone.now() - timezone.timedelta(hours=1)
        episode.save()
        episode.refresh_from_db()
        self.assertTrue(episode.is_published)

    def test_expirable_mixin(self):
        """Verify is_expired property works."""
        episode = Episode.objects.create(
            podcast=self.podcast,
            title="Expirable Episode",
            slug="expirable-ep",
            episode_number=2,
            audio_url="https://example.com/ep2.mp3",
        )
        self.assertFalse(episode.is_expired)

        episode.expired_at = timezone.now() - timezone.timedelta(hours=1)
        episode.save()
        episode.refresh_from_db()
        self.assertTrue(episode.is_expired)

    def test_ordering(self):
        """Verify default ordering by episode_number."""
        Episode.objects.create(
            podcast=self.podcast,
            title="Third",
            slug="third",
            episode_number=3,
            audio_url="https://example.com/ep3.mp3",
        )
        Episode.objects.create(
            podcast=self.podcast,
            title="First",
            slug="first",
            episode_number=1,
            audio_url="https://example.com/ep1.mp3",
        )
        Episode.objects.create(
            podcast=self.podcast,
            title="Second",
            slug="second",
            episode_number=2,
            audio_url="https://example.com/ep2.mp3",
        )
        episodes = list(Episode.objects.filter(podcast=self.podcast))
        self.assertEqual(episodes[0].episode_number, 1)
        self.assertEqual(episodes[1].episode_number, 2)
        self.assertEqual(episodes[2].episode_number, 3)
