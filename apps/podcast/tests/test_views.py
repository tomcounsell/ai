from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

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
class PodcastListViewTestCase(TestCase):
    """Tests for the podcast list view."""

    def test_list_returns_200(self):
        """GET /podcast/ returns 200."""
        response = self.client.get("/podcast/")
        self.assertEqual(response.status_code, 200)

    def test_list_shows_public_podcasts(self):
        """Public and published podcast appears in response."""
        Podcast.objects.create(
            title="Public Podcast",
            slug="public-podcast",
            description="A public podcast.",
            author_name="Author",
            author_email="a@b.com",
            privacy=Podcast.Privacy.PUBLIC,
            published_at=timezone.now() - timezone.timedelta(hours=1),
        )
        response = self.client.get("/podcast/")
        self.assertContains(response, "Public Podcast")

    def test_list_hides_unpublished_public_podcasts(self):
        """Public but unpublished podcast does not appear."""
        Podcast.objects.create(
            title="Unpublished Public",
            slug="unpublished-public",
            description="Public but not yet published.",
            author_name="Author",
            author_email="a@b.com",
            privacy=Podcast.Privacy.PUBLIC,
        )
        response = self.client.get("/podcast/")
        self.assertNotContains(response, "Unpublished Public")

    def test_list_hides_private_podcasts(self):
        """Private podcast doesn't appear."""
        Podcast.objects.create(
            title="Hidden Podcast",
            slug="hidden-podcast",
            description="A hidden podcast.",
            author_name="Author",
            author_email="a@b.com",
            privacy=Podcast.Privacy.RESTRICTED,
        )
        response = self.client.get("/podcast/")
        self.assertNotContains(response, "Hidden Podcast")

    def test_list_shows_owner_private_podcasts(self):
        """Owner's private podcast appears in list when logged in."""
        owner = User.objects.create_user(username="podowner", password="testpass123")
        Podcast.objects.create(
            title="My Private Show",
            slug="my-private-show",
            description="Private but mine.",
            author_name="Author",
            author_email="a@b.com",
            privacy=Podcast.Privacy.RESTRICTED,
            owner=owner,
        )
        self.client.login(username="podowner", password="testpass123")
        response = self.client.get("/podcast/")
        self.assertContains(response, "My Private Show")

    def test_list_hides_others_private_podcasts(self):
        """Another user's private podcast does not appear in list."""
        other_user = User.objects.create_user(
            username="otherowner", password="testpass123"
        )
        User.objects.create_user(username="viewer", password="testpass123")
        Podcast.objects.create(
            title="Not My Show",
            slug="not-my-show",
            description="Belongs to someone else.",
            author_name="Author",
            author_email="a@b.com",
            privacy=Podcast.Privacy.RESTRICTED,
            owner=other_user,
        )
        self.client.login(username="viewer", password="testpass123")
        response = self.client.get("/podcast/")
        self.assertNotContains(response, "Not My Show")


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
            privacy=Podcast.Privacy.PUBLIC,
            published_at=timezone.now() - timezone.timedelta(hours=1),
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
            privacy=Podcast.Privacy.RESTRICTED,
        )
        response = self.client.get(f"/podcast/{private_podcast.slug}/")
        self.assertEqual(response.status_code, 404)

    def test_detail_404_for_unpublished(self):
        """Public but unpublished podcast returns 404."""
        unpublished = Podcast.objects.create(
            title="Unpublished Podcast",
            slug="unpublished-detail",
            description="desc",
            author_name="Author",
            author_email="a@b.com",
            privacy=Podcast.Privacy.PUBLIC,
        )
        response = self.client.get(f"/podcast/{unpublished.slug}/")
        self.assertEqual(response.status_code, 404)

    def test_detail_allows_owner_of_private_podcast(self):
        """Owner of a private podcast can access the detail page."""
        owner = User.objects.create_user(username="detailowner", password="testpass123")
        private_podcast = Podcast.objects.create(
            title="Owner Private Podcast",
            slug="owner-private",
            description="desc",
            author_name="Author",
            author_email="a@b.com",
            privacy=Podcast.Privacy.RESTRICTED,
            owner=owner,
        )
        self.client.login(username="detailowner", password="testpass123")
        response = self.client.get(f"/podcast/{private_podcast.slug}/")
        self.assertEqual(response.status_code, 200)

    def test_detail_404_for_non_owner_of_private_podcast(self):
        """Non-owner gets 404 for a private podcast."""
        owner = User.objects.create_user(username="realowner", password="testpass123")
        User.objects.create_user(username="stranger", password="testpass123")
        private_podcast = Podcast.objects.create(
            title="Not Yours",
            slug="not-yours",
            description="desc",
            author_name="Author",
            author_email="a@b.com",
            privacy=Podcast.Privacy.RESTRICTED,
            owner=owner,
        )
        self.client.login(username="stranger", password="testpass123")
        response = self.client.get(f"/podcast/{private_podcast.slug}/")
        self.assertEqual(response.status_code, 404)

    def test_detail_404_for_anonymous_on_private_podcast(self):
        """Anonymous user gets 404 for a private podcast."""
        owner = User.objects.create_user(username="anonowner", password="testpass123")
        private_podcast = Podcast.objects.create(
            title="Anon Private",
            slug="anon-private",
            description="desc",
            author_name="Author",
            author_email="a@b.com",
            privacy=Podcast.Privacy.RESTRICTED,
            owner=owner,
        )
        response = self.client.get(f"/podcast/{private_podcast.slug}/")
        self.assertEqual(response.status_code, 404)

    def test_detail_shows_platform_links(self):
        """Platform links appear when spotify_url and apple_podcasts_url are set."""
        podcast_with_links = Podcast.objects.create(
            title="Linked Podcast",
            slug="linked-podcast",
            description="Has platform links.",
            author_name="Author",
            author_email="a@b.com",
            privacy=Podcast.Privacy.PUBLIC,
            published_at=timezone.now() - timezone.timedelta(hours=1),
            spotify_url="https://open.spotify.com/show/test123",
            apple_podcasts_url="https://podcasts.apple.com/us/podcast/test123",
        )
        response = self.client.get(f"/podcast/{podcast_with_links.slug}/")
        self.assertContains(response, "Spotify")
        self.assertContains(response, "Apple Podcasts")
        self.assertContains(response, "https://open.spotify.com/show/test123")
        self.assertContains(response, "https://podcasts.apple.com/us/podcast/test123")

    def test_detail_hides_empty_platform_links(self):
        """Platform links are hidden when URLs are empty, but RSS always shows."""
        response = self.client.get(f"/podcast/{self.podcast.slug}/")
        self.assertNotContains(response, "Spotify")
        self.assertNotContains(response, "Apple Podcasts")
        self.assertContains(response, "RSS Feed")


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
            privacy=Podcast.Privacy.PUBLIC,
            published_at=timezone.now() - timezone.timedelta(hours=1),
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

    def test_episode_detail_allows_owner_of_private_podcast(self):
        """Owner can access episode detail on a private podcast."""
        owner = User.objects.create_user(username="epowner", password="testpass123")
        private_podcast = Podcast.objects.create(
            title="Private Ep Podcast",
            slug="private-ep-podcast",
            description="desc",
            author_name="Author",
            author_email="a@b.com",
            privacy=Podcast.Privacy.RESTRICTED,
            owner=owner,
        )
        episode = Episode.objects.create(
            podcast=private_podcast,
            title="Private Episode",
            slug="private-episode",
            episode_number=1,
            audio_url="https://example.com/ep.mp3",
            published_at=timezone.now() - timezone.timedelta(hours=1),
        )
        self.client.login(username="epowner", password="testpass123")
        response = self.client.get(f"/podcast/{private_podcast.slug}/{episode.slug}/")
        self.assertEqual(response.status_code, 200)

    def test_episode_detail_404_for_non_owner(self):
        """Non-owner gets 404 for episode on private podcast."""
        owner = User.objects.create_user(username="epowner2", password="testpass123")
        User.objects.create_user(username="epstranger", password="testpass123")
        private_podcast = Podcast.objects.create(
            title="Stranger Ep Podcast",
            slug="stranger-ep-podcast",
            description="desc",
            author_name="Author",
            author_email="a@b.com",
            privacy=Podcast.Privacy.RESTRICTED,
            owner=owner,
        )
        episode = Episode.objects.create(
            podcast=private_podcast,
            title="Blocked Episode",
            slug="blocked-episode",
            episode_number=1,
            audio_url="https://example.com/ep.mp3",
            published_at=timezone.now() - timezone.timedelta(hours=1),
        )
        self.client.login(username="epstranger", password="testpass123")
        response = self.client.get(f"/podcast/{private_podcast.slug}/{episode.slug}/")
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
            privacy=Podcast.Privacy.PUBLIC,
            published_at=timezone.now() - timezone.timedelta(hours=1),
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

    def test_report_returns_html(self):
        """Episode with report_text returns 200 with rendered HTML."""
        response = self.client.get(
            f"/podcast/{self.podcast.slug}/{self.episode_with_report.slug}/report/"
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response["Content-Type"])
        self.assertContains(response, "This is the episode report content.")

    def test_report_404_when_empty(self):
        """Episode without report_text returns 404."""
        response = self.client.get(
            f"/podcast/{self.podcast.slug}/{self.episode_without_report.slug}/report/"
        )
        self.assertEqual(response.status_code, 404)

    def test_report_allows_owner_of_private_podcast(self):
        """Owner can access report on a private podcast."""
        owner = User.objects.create_user(username="reportowner", password="testpass123")
        private_podcast = Podcast.objects.create(
            title="Private Report Podcast",
            slug="private-report-podcast",
            description="desc",
            author_name="Author",
            author_email="a@b.com",
            privacy=Podcast.Privacy.RESTRICTED,
            owner=owner,
        )
        episode = Episode.objects.create(
            podcast=private_podcast,
            title="Private Report Episode",
            slug="private-report-ep",
            episode_number=1,
            audio_url="https://example.com/ep.mp3",
            report_text="Private report content.",
            published_at=timezone.now() - timezone.timedelta(hours=1),
        )
        self.client.login(username="reportowner", password="testpass123")
        response = self.client.get(
            f"/podcast/{private_podcast.slug}/{episode.slug}/report/"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Private report content.")

    def test_report_404_for_non_owner_of_private_podcast(self):
        """Non-owner gets 404 for report on a private podcast."""
        owner = User.objects.create_user(
            username="reportowner2", password="testpass123"
        )
        User.objects.create_user(username="reportstranger", password="testpass123")
        private_podcast = Podcast.objects.create(
            title="Blocked Report Podcast",
            slug="blocked-report-podcast",
            description="desc",
            author_name="Author",
            author_email="a@b.com",
            privacy=Podcast.Privacy.RESTRICTED,
            owner=owner,
        )
        Episode.objects.create(
            podcast=private_podcast,
            title="Blocked Report Episode",
            slug="blocked-report-ep",
            episode_number=1,
            audio_url="https://example.com/ep.mp3",
            report_text="Should not see this.",
            published_at=timezone.now() - timezone.timedelta(hours=1),
        )
        self.client.login(username="reportstranger", password="testpass123")
        response = self.client.get(
            f"/podcast/{private_podcast.slug}/blocked-report-ep/report/"
        )
        self.assertEqual(response.status_code, 404)

    def test_report_404_for_anonymous_on_private_podcast(self):
        """Anonymous user gets 404 for report on a private podcast."""
        owner = User.objects.create_user(
            username="reportowner3", password="testpass123"
        )
        private_podcast = Podcast.objects.create(
            title="Anon Report Podcast",
            slug="anon-report-podcast",
            description="desc",
            author_name="Author",
            author_email="a@b.com",
            privacy=Podcast.Privacy.RESTRICTED,
            owner=owner,
        )
        Episode.objects.create(
            podcast=private_podcast,
            title="Anon Report Episode",
            slug="anon-report-ep",
            episode_number=1,
            audio_url="https://example.com/ep.mp3",
            report_text="Should not see this either.",
            published_at=timezone.now() - timezone.timedelta(hours=1),
        )
        response = self.client.get(
            f"/podcast/{private_podcast.slug}/anon-report-ep/report/"
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
            privacy=Podcast.Privacy.PUBLIC,
            published_at=timezone.now() - timezone.timedelta(hours=1),
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

    def test_sources_returns_html(self):
        """Episode with sources_text returns 200 with rendered HTML."""
        response = self.client.get(
            f"/podcast/{self.podcast.slug}/{self.episode_with_sources.slug}/sources/"
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response["Content-Type"])
        self.assertContains(response, "Source 1")

    def test_sources_404_when_empty(self):
        """Episode without sources_text returns 404."""
        response = self.client.get(
            f"/podcast/{self.podcast.slug}/{self.episode_without_sources.slug}/sources/"
        )
        self.assertEqual(response.status_code, 404)

    def test_sources_allows_owner_of_private_podcast(self):
        """Owner can access sources on a private podcast."""
        owner = User.objects.create_user(
            username="sourcesowner", password="testpass123"
        )
        private_podcast = Podcast.objects.create(
            title="Private Sources Podcast",
            slug="private-sources-podcast",
            description="desc",
            author_name="Author",
            author_email="a@b.com",
            privacy=Podcast.Privacy.RESTRICTED,
            owner=owner,
        )
        episode = Episode.objects.create(
            podcast=private_podcast,
            title="Private Sources Episode",
            slug="private-sources-ep",
            episode_number=1,
            audio_url="https://example.com/ep.mp3",
            sources_text="Private source 1\nPrivate source 2",
            published_at=timezone.now() - timezone.timedelta(hours=1),
        )
        self.client.login(username="sourcesowner", password="testpass123")
        response = self.client.get(
            f"/podcast/{private_podcast.slug}/{episode.slug}/sources/"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Private source 1")

    def test_sources_404_for_non_owner_of_private_podcast(self):
        """Non-owner gets 404 for sources on a private podcast."""
        owner = User.objects.create_user(
            username="sourcesowner2", password="testpass123"
        )
        User.objects.create_user(username="sourcesstranger", password="testpass123")
        private_podcast = Podcast.objects.create(
            title="Blocked Sources Podcast",
            slug="blocked-sources-podcast",
            description="desc",
            author_name="Author",
            author_email="a@b.com",
            privacy=Podcast.Privacy.RESTRICTED,
            owner=owner,
        )
        Episode.objects.create(
            podcast=private_podcast,
            title="Blocked Sources Episode",
            slug="blocked-sources-ep",
            episode_number=1,
            audio_url="https://example.com/ep.mp3",
            sources_text="Should not see this.",
            published_at=timezone.now() - timezone.timedelta(hours=1),
        )
        self.client.login(username="sourcesstranger", password="testpass123")
        response = self.client.get(
            f"/podcast/{private_podcast.slug}/blocked-sources-ep/sources/"
        )
        self.assertEqual(response.status_code, 404)

    def test_sources_404_for_anonymous_on_private_podcast(self):
        """Anonymous user gets 404 for sources on a private podcast."""
        owner = User.objects.create_user(
            username="sourcesowner3", password="testpass123"
        )
        private_podcast = Podcast.objects.create(
            title="Anon Sources Podcast",
            slug="anon-sources-podcast",
            description="desc",
            author_name="Author",
            author_email="a@b.com",
            privacy=Podcast.Privacy.RESTRICTED,
            owner=owner,
        )
        Episode.objects.create(
            podcast=private_podcast,
            title="Anon Sources Episode",
            slug="anon-sources-ep",
            episode_number=1,
            audio_url="https://example.com/ep.mp3",
            sources_text="Should not see this either.",
            published_at=timezone.now() - timezone.timedelta(hours=1),
        )
        response = self.client.get(
            f"/podcast/{private_podcast.slug}/anon-sources-ep/sources/"
        )
        self.assertEqual(response.status_code, 404)


@override_settings(STORAGES=SIMPLE_STORAGES)
class EpisodeCreateViewTestCase(TestCase):
    """Tests for the episode create view (staff-only POST to create draft episode)."""

    def setUp(self):
        self.podcast = Podcast.objects.create(
            title="Create Test Podcast",
            slug="create-test-podcast",
            description="Podcast for episode creation tests.",
            author_name="Author",
            author_email="a@b.com",
            privacy=Podcast.Privacy.PUBLIC,
            published_at=timezone.now() - timezone.timedelta(hours=1),
        )
        self.private_podcast = Podcast.objects.create(
            title="Private Create Podcast",
            slug="private-create-podcast",
            description="Private podcast for creation tests.",
            author_name="Author",
            author_email="a@b.com",
            privacy=Podcast.Privacy.RESTRICTED,
        )
        self.staff_user = User.objects.create_user(
            "staffuser", "staff@test.com", "password", is_staff=True
        )
        self.regular_user = User.objects.create_user(
            "regularuser", "regular@test.com", "password", is_staff=False
        )

    def test_anonymous_post_redirects(self):
        """Anonymous user POSTing to /podcast/{slug}/new/ gets 302 redirect to login."""
        response = self.client.post(f"/podcast/{self.podcast.slug}/new/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/account/login", response.url)

    def test_non_staff_post_forbidden(self):
        """Logged-in non-staff user POSTing gets 403."""
        self.client.login(username="regularuser", password="password")
        response = self.client.post(f"/podcast/{self.podcast.slug}/new/")
        self.assertEqual(response.status_code, 403)

    def test_staff_post_creates_episode(self):
        """Staff user POSTing creates a draft Episode and redirects to workflow."""
        self.client.login(username="staffuser", password="password")
        response = self.client.post(f"/podcast/{self.podcast.slug}/new/")
        self.assertEqual(response.status_code, 302)

        episode = Episode.objects.get(podcast=self.podcast)
        self.assertEqual(episode.status, "draft")
        self.assertEqual(episode.title, "Untitled Episode")
        self.assertTrue(len(episode.slug) > 0)
        self.assertIsNotNone(episode.episode_number)
        self.assertIn(
            f"/podcast/{self.podcast.slug}/{episode.slug}/edit/1/", response.url
        )

    def test_staff_post_creates_episode_on_private_podcast(self):
        """Staff user can create episodes on private podcasts."""
        self.client.login(username="staffuser", password="password")
        response = self.client.post(f"/podcast/{self.private_podcast.slug}/new/")
        self.assertEqual(response.status_code, 302)

        episode = Episode.objects.get(podcast=self.private_podcast)
        self.assertEqual(episode.status, "draft")

    def test_post_nonexistent_podcast_returns_404(self):
        """POST to /podcast/nonexistent/new/ returns 404."""
        self.client.login(username="staffuser", password="password")
        response = self.client.post("/podcast/nonexistent/new/")
        self.assertEqual(response.status_code, 404)

    def test_button_visible_to_staff(self):
        """Staff user GETting the podcast detail page sees 'New Episode'."""
        self.client.login(username="staffuser", password="password")
        response = self.client.get(f"/podcast/{self.podcast.slug}/")
        self.assertContains(response, "New Episode")

    def test_button_hidden_from_anonymous(self):
        """Anonymous user GETting the podcast detail page does NOT see 'New Episode'."""
        response = self.client.get(f"/podcast/{self.podcast.slug}/")
        self.assertNotContains(response, "New Episode")

    def test_button_hidden_from_non_staff(self):
        """Logged-in non-staff user does NOT see 'New Episode'."""
        self.client.login(username="regularuser", password="password")
        response = self.client.get(f"/podcast/{self.podcast.slug}/")
        self.assertNotContains(response, "New Episode")


@override_settings(STORAGES=SIMPLE_STORAGES)
class PodcastEditViewTestCase(TestCase):
    """Tests for the podcast edit view (owner-only access)."""

    def setUp(self):
        self.owner = User.objects.create_user(
            username="editowner", password="testpass123"
        )
        self.other_user = User.objects.create_user(
            username="editother", password="testpass123"
        )
        self.podcast = Podcast.objects.create(
            title="Editable Podcast",
            slug="editable-podcast",
            description="A podcast for edit tests.",
            author_name="Author",
            author_email="a@b.com",
            privacy=Podcast.Privacy.PUBLIC,
            published_at=timezone.now() - timezone.timedelta(hours=1),
            owner=self.owner,
        )

    def test_edit_requires_login(self):
        """Anonymous user is redirected to login."""
        response = self.client.get(f"/podcast/{self.podcast.slug}/edit/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/account/login", response.url)

    def test_edit_returns_200_for_owner(self):
        """Owner can access the edit page."""
        self.client.login(username="editowner", password="testpass123")
        response = self.client.get(f"/podcast/{self.podcast.slug}/edit/")
        self.assertEqual(response.status_code, 200)

    def test_edit_returns_404_for_non_owner(self):
        """Non-owner gets 404 on edit page."""
        self.client.login(username="editother", password="testpass123")
        response = self.client.get(f"/podcast/{self.podcast.slug}/edit/")
        self.assertEqual(response.status_code, 404)

    def test_edit_contains_form(self):
        """Edit page contains form with podcast fields."""
        self.client.login(username="editowner", password="testpass123")
        response = self.client.get(f"/podcast/{self.podcast.slug}/edit/")
        self.assertContains(response, 'enctype="multipart/form-data"')
        self.assertContains(response, "Editable Podcast")

    def test_edit_post_updates_title(self):
        """POST with valid data updates the podcast title."""
        self.client.login(username="editowner", password="testpass123")
        response = self.client.post(
            f"/podcast/{self.podcast.slug}/edit/",
            {
                "title": "Updated Podcast Title",
                "description": "Updated description.",
                "author_name": "New Author",
                "author_email": "new@example.com",
                "language": "en",
                "website_url": "",
                "spotify_url": "",
                "apple_podcasts_url": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.podcast.refresh_from_db()
        self.assertEqual(self.podcast.title, "Updated Podcast Title")
        self.assertEqual(self.podcast.description, "Updated description.")
        self.assertEqual(self.podcast.author_name, "New Author")

    def test_edit_post_redirects_to_detail(self):
        """Successful POST redirects to podcast detail page."""
        self.client.login(username="editowner", password="testpass123")
        response = self.client.post(
            f"/podcast/{self.podcast.slug}/edit/",
            {
                "title": "Redirect Test",
                "description": "desc",
                "author_name": "Author",
                "author_email": "a@b.com",
                "language": "en",
                "website_url": "",
                "spotify_url": "",
                "apple_podcasts_url": "",
            },
        )
        self.assertRedirects(
            response,
            f"/podcast/{self.podcast.slug}/",
            fetch_redirect_response=False,
        )

    def test_edit_post_rejected_for_non_owner(self):
        """Non-owner POST returns 404."""
        self.client.login(username="editother", password="testpass123")
        response = self.client.post(
            f"/podcast/{self.podcast.slug}/edit/",
            {
                "title": "Hacked Title",
                "description": "desc",
                "author_name": "Author",
                "author_email": "a@b.com",
                "language": "en",
                "website_url": "",
                "spotify_url": "",
                "apple_podcasts_url": "",
            },
        )
        self.assertEqual(response.status_code, 404)
        self.podcast.refresh_from_db()
        self.assertEqual(self.podcast.title, "Editable Podcast")

    def test_edit_button_visible_to_owner(self):
        """Owner sees Edit button on detail page."""
        self.client.login(username="editowner", password="testpass123")
        response = self.client.get(f"/podcast/{self.podcast.slug}/")
        self.assertContains(response, "Edit")
        self.assertContains(response, f"/podcast/{self.podcast.slug}/edit/")

    def test_edit_button_hidden_from_non_owner(self):
        """Non-owner does not see Edit button on detail page."""
        self.client.login(username="editother", password="testpass123")
        response = self.client.get(f"/podcast/{self.podcast.slug}/")
        self.assertNotContains(response, f"/podcast/{self.podcast.slug}/edit/")

    def test_edit_button_hidden_from_anonymous(self):
        """Anonymous user does not see Edit button on detail page."""
        response = self.client.get(f"/podcast/{self.podcast.slug}/")
        self.assertNotContains(response, f"/podcast/{self.podcast.slug}/edit/")
