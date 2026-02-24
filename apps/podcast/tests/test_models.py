from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from apps.podcast.models import Episode, EpisodeArtifact, Podcast, PodcastAccessToken


class PodcastModelTestCase(TestCase):
    """Tests for the Podcast model."""

    def setUp(self):
        # Clean up any pre-existing records to ensure test isolation
        Podcast.objects.all().delete()

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
            privacy=Podcast.Privacy.PUBLIC,
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
        self.assertEqual(podcast.privacy, Podcast.Privacy.PUBLIC)
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
        # Clean up any pre-existing records to ensure test isolation
        Episode.objects.all().delete()
        Podcast.objects.all().delete()

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
        self.assertEqual(episodes[0].episode_number, 3)
        self.assertEqual(episodes[1].episode_number, 2)
        self.assertEqual(episodes[2].episode_number, 1)

    def test_tag_list_splits_and_strips(self):
        """tag_list splits comma-separated tags and strips whitespace."""
        episode = Episode.objects.create(
            podcast=self.podcast,
            title="Tagged Episode",
            slug="tagged-ep",
            episode_number=1,
            audio_url="https://example.com/ep1.mp3",
            tags="Cardiovascular Health, Recovery, Exercise",
        )
        self.assertEqual(
            episode.tag_list, ["Cardiovascular Health", "Recovery", "Exercise"]
        )

    def test_tag_list_empty_when_no_tags(self):
        """tag_list returns empty list when tags is blank."""
        episode = Episode.objects.create(
            podcast=self.podcast,
            title="Untagged Episode",
            slug="untagged-ep",
            episode_number=2,
            audio_url="https://example.com/ep2.mp3",
        )
        self.assertEqual(episode.tag_list, [])

    def test_status_defaults_to_draft(self):
        """New episode has status='draft' by default."""
        episode = Episode.objects.create(
            podcast=self.podcast,
            title="Draft Episode",
            slug="draft-ep",
            episode_number=1,
            audio_url="https://example.com/ep1.mp3",
        )
        episode.refresh_from_db()
        self.assertEqual(episode.status, "draft")

    def test_episode_number_auto_assignment_when_none(self):
        """Episode with episode_number=None gets next available number."""
        Episode.objects.create(
            podcast=self.podcast,
            title="Episode One",
            slug="ep-one",
            episode_number=3,
            audio_url="https://example.com/ep1.mp3",
        )
        episode = Episode.objects.create(
            podcast=self.podcast,
            title="Episode Auto",
            slug="ep-auto",
            audio_url="https://example.com/ep-auto.mp3",
        )
        episode.refresh_from_db()
        self.assertEqual(episode.episode_number, 4)

    def test_episode_number_auto_assignment_first_episode(self):
        """First episode with episode_number=None gets number 1."""
        episode = Episode.objects.create(
            podcast=self.podcast,
            title="First Auto",
            slug="first-auto",
            audio_url="https://example.com/first-auto.mp3",
        )
        episode.refresh_from_db()
        self.assertEqual(episode.episode_number, 1)

    def test_episode_number_auto_scoped_to_podcast(self):
        """Auto-numbering is scoped to the podcast, not global."""
        other_podcast = Podcast.objects.create(
            title="Other Podcast",
            slug="other-podcast",
            description="Another podcast.",
            author_name="Author",
            author_email="a@b.com",
        )
        Episode.objects.create(
            podcast=other_podcast,
            title="Other Ep",
            slug="other-ep",
            episode_number=10,
            audio_url="https://example.com/other.mp3",
        )
        episode = Episode.objects.create(
            podcast=self.podcast,
            title="My Ep",
            slug="my-ep",
            audio_url="https://example.com/my-ep.mp3",
        )
        episode.refresh_from_db()
        self.assertEqual(episode.episode_number, 1)

    def test_audio_url_can_be_blank(self):
        """Episode can be saved with blank audio_url."""
        episode = Episode.objects.create(
            podcast=self.podcast,
            title="No Audio",
            slug="no-audio",
            episode_number=1,
            audio_url="",
        )
        episode.refresh_from_db()
        self.assertEqual(episode.audio_url, "")

    def test_save_without_episode_number_auto_assigns(self):
        """Episode saved without explicit episode_number gets one assigned."""
        ep = Episode(
            podcast=self.podcast,
            title="No Number",
            slug="no-number",
        )
        ep.save()
        ep.refresh_from_db()
        self.assertIsNotNone(ep.episode_number)
        self.assertEqual(ep.episode_number, 1)


class EpisodeArtifactModelTestCase(TestCase):
    """Tests for the EpisodeArtifact model."""

    def setUp(self):
        # Clean up any pre-existing records to ensure test isolation
        EpisodeArtifact.objects.all().delete()
        Episode.objects.all().delete()
        Podcast.objects.all().delete()

        self.podcast = Podcast.objects.create(
            title="Artifact Podcast",
            slug="artifact-podcast",
            description="A podcast for artifact tests.",
            author_name="Author",
            author_email="a@b.com",
        )
        self.episode = Episode.objects.create(
            podcast=self.podcast,
            title="Artifact Episode",
            slug="artifact-ep",
            episode_number=1,
            audio_url="https://example.com/ep1.mp3",
        )

    def test_create_artifact(self):
        """Create an EpisodeArtifact with episode FK, title, content."""
        artifact = EpisodeArtifact.objects.create(
            episode=self.episode,
            title="research/p2-perplexity.md",
            content="# Research\n\nSome research content.",
        )
        artifact.refresh_from_db()
        self.assertEqual(artifact.episode, self.episode)
        self.assertEqual(artifact.title, "research/p2-perplexity.md")
        self.assertEqual(artifact.content, "# Research\n\nSome research content.")
        self.assertEqual(artifact.metadata, {})
        self.assertIsNotNone(artifact.pk)

    def test_unique_together_episode_title(self):
        """Duplicate (episode, title) raises IntegrityError."""
        EpisodeArtifact.objects.create(
            episode=self.episode,
            title="research/p2-perplexity.md",
            content="First version.",
        )
        with self.assertRaises(IntegrityError):
            EpisodeArtifact.objects.create(
                episode=self.episode,
                title="research/p2-perplexity.md",
                content="Duplicate version.",
            )

    def test_ordering_by_title(self):
        """Artifacts are ordered by title."""
        EpisodeArtifact.objects.create(
            episode=self.episode,
            title="research/z-last.md",
            content="Last.",
        )
        EpisodeArtifact.objects.create(
            episode=self.episode,
            title="logs/a-first.md",
            content="First.",
        )
        EpisodeArtifact.objects.create(
            episode=self.episode,
            title="plans/m-middle.md",
            content="Middle.",
        )
        artifacts = list(EpisodeArtifact.objects.filter(episode=self.episode))
        self.assertEqual(artifacts[0].title, "logs/a-first.md")
        self.assertEqual(artifacts[1].title, "plans/m-middle.md")
        self.assertEqual(artifacts[2].title, "research/z-last.md")

    def test_str_representation(self):
        """__str__ returns 'episode / title' format."""
        artifact = EpisodeArtifact.objects.create(
            episode=self.episode,
            title="research/brief.md",
            content="Brief content.",
        )
        self.assertEqual(str(artifact), f"{self.episode} / research/brief.md")

    def test_metadata_json_field(self):
        """Metadata JSONField stores and retrieves structured data."""
        artifact = EpisodeArtifact.objects.create(
            episode=self.episode,
            title="logs/generation.md",
            content="Log content.",
            metadata={"quality_score": 8.5, "keywords": ["ai", "research"]},
        )
        artifact.refresh_from_db()
        self.assertEqual(artifact.metadata["quality_score"], 8.5)
        self.assertEqual(artifact.metadata["keywords"], ["ai", "research"])

    def test_cascade_delete_with_episode(self):
        """Deleting episode cascades to artifacts."""
        EpisodeArtifact.objects.create(
            episode=self.episode,
            title="research/brief.md",
            content="Content.",
        )
        self.assertEqual(EpisodeArtifact.objects.count(), 1)
        self.episode.delete()
        self.assertEqual(EpisodeArtifact.objects.count(), 0)


class PodcastPrivacyTestCase(TestCase):
    """Tests for Podcast.privacy field, convenience properties, and immutability."""

    def _create_podcast(self, slug, privacy=None, **kwargs):
        """Helper to create a podcast with given privacy."""
        defaults = {
            "title": f"Podcast {slug}",
            "slug": slug,
            "description": "desc",
            "author_name": "Author",
            "author_email": "a@b.com",
        }
        defaults.update(kwargs)
        if privacy is not None:
            defaults["privacy"] = privacy
        return Podcast.objects.create(**defaults)

    def test_default_privacy_is_unlisted(self):
        """Default privacy value is 'unlisted'."""
        podcast = self._create_podcast("default-priv")
        podcast.refresh_from_db()
        self.assertEqual(podcast.privacy, Podcast.Privacy.UNLISTED)

    def test_create_public_podcast(self):
        """Creating a podcast with privacy=PUBLIC works."""
        podcast = self._create_podcast("pub", privacy=Podcast.Privacy.PUBLIC)
        podcast.refresh_from_db()
        self.assertEqual(podcast.privacy, Podcast.Privacy.PUBLIC)

    def test_create_unlisted_podcast(self):
        """Creating a podcast with privacy=UNLISTED works."""
        podcast = self._create_podcast("unlist", privacy=Podcast.Privacy.UNLISTED)
        podcast.refresh_from_db()
        self.assertEqual(podcast.privacy, Podcast.Privacy.UNLISTED)

    def test_create_restricted_podcast(self):
        """Creating a podcast with privacy=RESTRICTED works."""
        podcast = self._create_podcast("restrict", privacy=Podcast.Privacy.RESTRICTED)
        podcast.refresh_from_db()
        self.assertEqual(podcast.privacy, Podcast.Privacy.RESTRICTED)

    def test_is_public_property_true_for_public(self):
        """is_public returns True only for PUBLIC privacy."""
        podcast = self._create_podcast("pub-prop", privacy=Podcast.Privacy.PUBLIC)
        self.assertTrue(podcast.is_public)

    def test_is_public_property_false_for_unlisted(self):
        """is_public returns False for UNLISTED privacy."""
        podcast = self._create_podcast("unl-prop", privacy=Podcast.Privacy.UNLISTED)
        self.assertFalse(podcast.is_public)

    def test_is_public_property_false_for_restricted(self):
        """is_public returns False for RESTRICTED privacy."""
        podcast = self._create_podcast("res-prop", privacy=Podcast.Privacy.RESTRICTED)
        self.assertFalse(podcast.is_public)

    def test_is_unlisted_property(self):
        """is_unlisted returns True only for UNLISTED privacy."""
        public = self._create_podcast("is-unl-pub", privacy=Podcast.Privacy.PUBLIC)
        unlisted = self._create_podcast("is-unl-unl", privacy=Podcast.Privacy.UNLISTED)
        restricted = self._create_podcast(
            "is-unl-res", privacy=Podcast.Privacy.RESTRICTED
        )
        self.assertFalse(public.is_unlisted)
        self.assertTrue(unlisted.is_unlisted)
        self.assertFalse(restricted.is_unlisted)

    def test_is_restricted_property(self):
        """is_restricted returns True only for RESTRICTED privacy."""
        public = self._create_podcast("is-res-pub", privacy=Podcast.Privacy.PUBLIC)
        unlisted = self._create_podcast("is-res-unl", privacy=Podcast.Privacy.UNLISTED)
        restricted = self._create_podcast(
            "is-res-res", privacy=Podcast.Privacy.RESTRICTED
        )
        self.assertFalse(public.is_restricted)
        self.assertFalse(unlisted.is_restricted)
        self.assertTrue(restricted.is_restricted)

    def test_uses_private_bucket_property(self):
        """uses_private_bucket returns True only for RESTRICTED privacy."""
        public = self._create_podcast("bucket-pub", privacy=Podcast.Privacy.PUBLIC)
        unlisted = self._create_podcast("bucket-unl", privacy=Podcast.Privacy.UNLISTED)
        restricted = self._create_podcast(
            "bucket-res", privacy=Podcast.Privacy.RESTRICTED
        )
        self.assertFalse(public.uses_private_bucket)
        self.assertFalse(unlisted.uses_private_bucket)
        self.assertTrue(restricted.uses_private_bucket)

    def test_cannot_change_privacy_after_creation(self):
        """Changing privacy after creation raises ValueError."""
        podcast = self._create_podcast("immut-1", privacy=Podcast.Privacy.PUBLIC)
        podcast.privacy = Podcast.Privacy.RESTRICTED
        with self.assertRaises(ValueError) as ctx:
            podcast.save()
        self.assertIn("cannot be changed", str(ctx.exception))

    def test_cannot_change_restricted_to_public(self):
        """Changing privacy from RESTRICTED to PUBLIC raises ValueError."""
        podcast = self._create_podcast("immut-2", privacy=Podcast.Privacy.RESTRICTED)
        podcast.privacy = Podcast.Privacy.PUBLIC
        with self.assertRaises(ValueError) as ctx:
            podcast.save()
        self.assertIn("cannot be changed", str(ctx.exception))

    def test_save_same_privacy_works(self):
        """Saving with same privacy value does not raise."""
        podcast = self._create_podcast("same-priv", privacy=Podcast.Privacy.PUBLIC)
        podcast.title = "Updated Title"
        podcast.save()  # Should not raise
        podcast.refresh_from_db()
        self.assertEqual(podcast.title, "Updated Title")
        self.assertEqual(podcast.privacy, Podcast.Privacy.PUBLIC)

    def test_save_restricted_same_privacy_works(self):
        """Saving restricted podcast with same privacy does not raise."""
        podcast = self._create_podcast(
            "same-restrict", privacy=Podcast.Privacy.RESTRICTED
        )
        podcast.description = "Updated description"
        podcast.save()  # Should not raise
        podcast.refresh_from_db()
        self.assertEqual(podcast.description, "Updated description")
        self.assertEqual(podcast.privacy, Podcast.Privacy.RESTRICTED)

    def test_privacy_choices_values(self):
        """Privacy enum has the expected values."""
        self.assertEqual(Podcast.Privacy.PUBLIC, "public")
        self.assertEqual(Podcast.Privacy.UNLISTED, "unlisted")
        self.assertEqual(Podcast.Privacy.RESTRICTED, "restricted")


class PodcastAccessTokenTestCase(TestCase):
    """Tests for the PodcastAccessToken model."""

    def setUp(self):
        self.podcast = Podcast.objects.create(
            title="Token Podcast",
            slug="token-podcast",
            description="A podcast for token tests.",
            author_name="Author",
            author_email="a@b.com",
            privacy=Podcast.Privacy.RESTRICTED,
        )

    def test_create_access_token(self):
        """Create a PodcastAccessToken with required fields."""
        token = PodcastAccessToken.objects.create(
            podcast=self.podcast,
            label="Tom iPhone",
        )
        token.refresh_from_db()
        self.assertEqual(token.podcast, self.podcast)
        self.assertEqual(token.label, "Tom iPhone")
        self.assertTrue(token.is_active)
        self.assertIsNone(token.last_accessed_at)
        self.assertEqual(token.access_count, 0)
        self.assertIsNotNone(token.pk)

    def test_token_auto_generated_on_save(self):
        """Token is auto-generated when not provided."""
        token = PodcastAccessToken.objects.create(
            podcast=self.podcast,
            label="Auto Token",
        )
        self.assertIsNotNone(token.token)
        self.assertGreater(len(token.token), 20)

    def test_token_unique(self):
        """Two tokens cannot share the same token value."""
        token1 = PodcastAccessToken.objects.create(
            podcast=self.podcast,
            label="Token 1",
        )
        with self.assertRaises(IntegrityError):
            PodcastAccessToken.objects.create(
                podcast=self.podcast,
                label="Token 2",
                token=token1.token,
            )

    def test_explicit_token_preserved(self):
        """When an explicit token is provided, it is used."""
        token = PodcastAccessToken.objects.create(
            podcast=self.podcast,
            label="Explicit Token",
            token="my-custom-token-value",
        )
        token.refresh_from_db()
        self.assertEqual(token.token, "my-custom-token-value")

    def test_str_representation(self):
        """__str__ returns 'podcast -- label' format."""
        token = PodcastAccessToken.objects.create(
            podcast=self.podcast,
            label="Client A",
        )
        expected = f"{self.podcast} \u2014 Client A"
        self.assertEqual(str(token), expected)

    def test_record_access_increments_count(self):
        """record_access() increments access_count and sets last_accessed_at."""
        token = PodcastAccessToken.objects.create(
            podcast=self.podcast,
            label="Access Test",
        )
        self.assertEqual(token.access_count, 0)
        self.assertIsNone(token.last_accessed_at)

        token.record_access()
        token.refresh_from_db()
        self.assertEqual(token.access_count, 1)
        self.assertIsNotNone(token.last_accessed_at)

    def test_record_access_multiple_times(self):
        """record_access() can be called multiple times."""
        token = PodcastAccessToken.objects.create(
            podcast=self.podcast,
            label="Multi Access",
        )
        token.record_access()
        token.record_access()
        token.record_access()
        token.refresh_from_db()
        self.assertEqual(token.access_count, 3)

    def test_cascade_delete_with_podcast(self):
        """Deleting podcast cascades to access tokens."""
        PodcastAccessToken.objects.create(
            podcast=self.podcast,
            label="Delete Test",
        )
        self.assertEqual(PodcastAccessToken.objects.count(), 1)
        self.podcast.delete()
        self.assertEqual(PodcastAccessToken.objects.count(), 0)

    def test_ordering_by_created_at_desc(self):
        """Tokens are ordered by -created_at."""
        t1 = PodcastAccessToken.objects.create(
            podcast=self.podcast,
            label="First",
        )
        t2 = PodcastAccessToken.objects.create(
            podcast=self.podcast,
            label="Second",
        )
        tokens = list(PodcastAccessToken.objects.filter(podcast=self.podcast))
        # Most recently created first
        self.assertEqual(tokens[0].pk, t2.pk)
        self.assertEqual(tokens[1].pk, t1.pk)

    def test_is_active_default_true(self):
        """Token is active by default."""
        token = PodcastAccessToken.objects.create(
            podcast=self.podcast,
            label="Active Test",
        )
        self.assertTrue(token.is_active)

    def test_deactivate_token(self):
        """Token can be deactivated."""
        token = PodcastAccessToken.objects.create(
            podcast=self.podcast,
            label="Deactivate Test",
        )
        token.is_active = False
        token.save()
        token.refresh_from_db()
        self.assertFalse(token.is_active)
