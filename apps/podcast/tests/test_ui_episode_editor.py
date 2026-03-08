"""UI Tests for Episode Editor User Journey.

Tests verifying page loads, element presence, form fields, HTMX responses,
and access controls for every podcast route. Maps 1:1 to the 9-stage user
journey documented in docs/plans/episode-editor-user-journey.md.

Tests for existing features PASS; tests for missing features are marked
xfail with descriptive reasons, creating a concrete build backlog.
"""

import pytest
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.podcast.models import Episode, EpisodeWorkflow, Podcast

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
class TestStage1NavigateToPodcast(TestCase):
    """Stage 1: IDEA -> Navigate to Podcast.

    User journey stage 1: User has an idea for a new episode and navigates
    to the podcast page. Tests verify podcast list and detail pages load
    correctly and show the "+ New Episode" button for owners.

    Reference: docs/plans/episode-editor-user-journey.md#1-idea--navigate-to-podcast
    Status: EXISTS
    """

    def setUp(self):
        self.owner = User.objects.create_user(
            username="owner", password="testpass123", is_staff=False
        )
        self.podcast = Podcast.objects.create(
            title="Test Podcast",
            slug="test-podcast",
            description="A test podcast for UI testing.",
            author_name="Author",
            author_email="author@test.com",
            privacy=Podcast.Privacy.PUBLIC,
            published_at=timezone.now() - timezone.timedelta(hours=1),
            owner=self.owner,
        )
        self.staff_user = User.objects.create_user(
            username="staff", password="testpass123", is_staff=True
        )
        self.regular_user = User.objects.create_user(
            username="regular", password="testpass123", is_staff=False
        )

    def test_podcast_list_loads(self):
        """Podcast list page at /podcast/ returns 200."""
        response = self.client.get("/podcast/")
        self.assertEqual(response.status_code, 200)

    def test_podcast_detail_loads(self):
        """Podcast detail page at /podcast/{slug}/ returns 200."""
        response = self.client.get(f"/podcast/{self.podcast.slug}/")
        self.assertEqual(response.status_code, 200)

    def test_podcast_detail_shows_title(self):
        """Podcast detail page displays the podcast title."""
        response = self.client.get(f"/podcast/{self.podcast.slug}/")
        self.assertContains(response, "Test Podcast")

    def test_new_episode_button_visible_for_owner(self):
        """Podcast owner sees the 'New Episode' button on podcast detail."""
        self.client.login(username="owner", password="testpass123")
        response = self.client.get(f"/podcast/{self.podcast.slug}/")
        self.assertContains(response, "New Episode")

    def test_new_episode_button_hidden_for_anonymous(self):
        """Anonymous users do not see the 'New Episode' button."""
        response = self.client.get(f"/podcast/{self.podcast.slug}/")
        self.assertNotContains(response, "New Episode")

    def test_new_episode_button_hidden_for_regular_user(self):
        """Non-owner, non-staff users do not see the '+ New Episode' button."""
        self.client.login(username="regular", password="testpass123")
        response = self.client.get(f"/podcast/{self.podcast.slug}/")
        self.assertNotContains(response, "New Episode")

    def test_podcast_list_shows_podcast(self):
        """Podcast list includes the published podcast."""
        response = self.client.get("/podcast/")
        self.assertContains(response, "Test Podcast")

    def test_podcast_detail_has_breadcrumb_nav(self):
        """Podcast detail page has breadcrumb navigation back to list."""
        response = self.client.get(f"/podcast/{self.podcast.slug}/")
        self.assertContains(response, "/podcast/")
        self.assertContains(response, "Podcasts")


@override_settings(STORAGES=SIMPLE_STORAGES)
class TestStage2CreateDraft(TestCase):
    """Stage 2: CREATE DRAFT -> Click 'New Episode'.

    User journey stage 2: User clicks '+ New Episode' to see a creation form
    with title, description, and tags fields. Submitting creates a draft episode
    and redirects to workflow step 1.

    Reference: docs/plans/episode-editor-user-journey.md#2-create-draft--click-new-episode
    """

    def setUp(self):
        self.owner_user = User.objects.create_user(
            username="owner", password="testpass123", is_staff=False
        )
        self.podcast = Podcast.objects.create(
            title="Test Podcast",
            slug="test-podcast",
            description="A test podcast.",
            author_name="Author",
            author_email="author@test.com",
            privacy=Podcast.Privacy.PUBLIC,
            published_at=timezone.now() - timezone.timedelta(hours=1),
            owner=self.owner_user,
        )
        self.staff_user = User.objects.create_user(
            username="staff", password="testpass123", is_staff=True
        )

    def test_staff_can_access_creation_form(self):
        """Staff GET to /podcast/{slug}/new/ returns 200 with a form."""
        self.client.login(username="staff", password="testpass123")
        response = self.client.get(f"/podcast/{self.podcast.slug}/new/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Create Episode")

    def test_episode_creation_form_has_title_field(self):
        """Episode creation form has a title input field."""
        self.client.login(username="staff", password="testpass123")
        response = self.client.get(f"/podcast/{self.podcast.slug}/new/")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn('name="title"', content)

    def test_episode_creation_form_has_description_field(self):
        """Episode creation form has a description textarea."""
        self.client.login(username="staff", password="testpass123")
        response = self.client.get(f"/podcast/{self.podcast.slug}/new/")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn('name="description"', content)

    def test_staff_can_create_episode_via_post(self):
        """Staff POST to /podcast/{slug}/new/ with form data creates episode and redirects."""
        self.client.login(username="staff", password="testpass123")
        response = self.client.post(
            f"/podcast/{self.podcast.slug}/new/",
            data={
                "title": "My New Episode",
                "description": "A detailed description of the topic.",
            },
        )
        self.assertEqual(response.status_code, 302)
        episode = Episode.objects.filter(podcast=self.podcast).first()
        self.assertIsNotNone(episode)
        self.assertEqual(episode.title, "My New Episode")
        self.assertEqual(episode.description, "A detailed description of the topic.")

    def test_create_redirects_to_workflow(self):
        """After creating episode, user is redirected to workflow step 1."""
        self.client.login(username="staff", password="testpass123")
        response = self.client.post(
            f"/podcast/{self.podcast.slug}/new/",
            data={
                "title": "Redirect Test Episode",
                "description": "Testing redirect to workflow.",
            },
        )
        self.assertEqual(response.status_code, 302)
        episode = Episode.objects.filter(podcast=self.podcast).first()
        self.assertIn("/edit/1/", response.url)
        self.assertIn(episode.slug, response.url)

    def test_anonymous_cannot_create_episode(self):
        """Anonymous user cannot create an episode (redirected to login)."""
        response = self.client.post(f"/podcast/{self.podcast.slug}/new/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.url)

    def test_owner_can_create_episode(self):
        """Podcast owner (not staff) can create episodes."""
        self.client.login(username="owner", password="testpass123")
        response = self.client.post(
            f"/podcast/{self.podcast.slug}/new/",
            data={
                "title": "Owner Episode",
                "description": "Created by owner.",
            },
        )
        # Owner should get 302 redirect to workflow, not 403
        self.assertNotEqual(response.status_code, 403)
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("/login", response.url)

    def test_non_owner_non_staff_gets_403(self):
        """Non-owner, non-staff user cannot create episodes."""
        User.objects.create_user(
            username="stranger", password="testpass123", is_staff=False
        )
        self.client.login(username="stranger", password="testpass123")
        response = self.client.post(
            f"/podcast/{self.podcast.slug}/new/",
            data={"title": "Hacked", "description": "Should not work."},
        )
        self.assertEqual(response.status_code, 403)


@override_settings(STORAGES=SIMPLE_STORAGES)
class TestStage3EditEpisodeDetails(TestCase):
    """Stage 3: EDIT EPISODE DETAILS -> Workflow Step 1 (Setup).

    User journey stage 3: User lands on workflow page and can edit episode
    title, description, and tags via inline HTMX fields.

    Reference: docs/plans/episode-editor-user-journey.md#3-edit-episode-details--workflow-step-1-setup
    """

    def setUp(self):
        self.podcast = Podcast.objects.create(
            title="Test Podcast",
            slug="test-podcast",
            description="A test podcast.",
            author_name="Author",
            author_email="author@test.com",
            privacy=Podcast.Privacy.PUBLIC,
        )
        self.episode = Episode.objects.create(
            podcast=self.podcast,
            title="Test Episode",
            slug="test-episode",
            episode_number=1,
            status="draft",
            description="Episode description for testing.",
        )
        self.staff_user = User.objects.create_user(
            username="staff", password="testpass123", is_staff=True
        )

    def _workflow_url(self, step=1):
        return f"/podcast/{self.podcast.slug}/{self.episode.slug}/edit/{step}/"

    def test_workflow_step1_loads(self):
        """Workflow page at step 1 returns 200 for staff."""
        self.client.login(username="staff", password="testpass123")
        response = self.client.get(self._workflow_url(1))
        self.assertEqual(response.status_code, 200)

    def test_workflow_shows_12_phases(self):
        """Workflow page context contains 12 phases."""
        self.client.login(username="staff", password="testpass123")
        response = self.client.get(self._workflow_url(1))
        self.assertEqual(len(response.context["phases"]), 12)

    def test_workflow_step1_has_editable_title(self):
        """Workflow step 1 has an editable title field."""
        self.client.login(username="staff", password="testpass123")
        response = self.client.get(self._workflow_url(1))
        content = response.content.decode("utf-8")
        self.assertIn('name="title"', content)

    def test_workflow_step1_has_description_textarea(self):
        """Workflow step 1 has a description/brief textarea."""
        self.client.login(username="staff", password="testpass123")
        response = self.client.get(self._workflow_url(1))
        content = response.content.decode("utf-8")
        self.assertIn('name="description"', content)


@override_settings(STORAGES=SIMPLE_STORAGES)
class TestStage4to6ResearchPipeline(TestCase):
    """Stages 4-6: START PIPELINE -> Automated Research & Audio Generation.

    User journey stages 4-6: Pipeline runs research phases, user can view
    artifacts inline and interact with quality gates.

    Reference: docs/plans/episode-editor-user-journey.md#4-start-pipeline--automated-research-phases-2-6
    """

    def setUp(self):
        self.podcast = Podcast.objects.create(
            title="Test Podcast",
            slug="test-podcast",
            description="A test podcast.",
            author_name="Author",
            author_email="author@test.com",
            privacy=Podcast.Privacy.PUBLIC,
        )
        self.episode = Episode.objects.create(
            podcast=self.podcast,
            title="Pipeline Episode",
            slug="pipeline-episode",
            episode_number=1,
            status="in_progress",
        )
        self.staff_user = User.objects.create_user(
            username="staff", password="testpass123", is_staff=True
        )

    def _workflow_url(self, step=1):
        return f"/podcast/{self.podcast.slug}/{self.episode.slug}/edit/{step}/"

    def test_workflow_step9_has_audio_preview_when_audio_exists(self):
        """Workflow step 9 shows audio preview player when audio_url is set."""
        self.client.login(username="staff", password="testpass123")
        self.episode.audio_url = "https://example.com/test.mp3"
        self.episode.save()
        response = self.client.get(self._workflow_url(9))
        content = response.content.decode("utf-8")
        self.assertIn("<audio", content)

    @pytest.mark.xfail(
        reason="Gap: Audio upload UI only shows when workflow is paused with "
        "the right button_state. Without EpisodeWorkflow in paused state, "
        "the upload form is conditionally hidden.",
        strict=True,
    )
    def test_workflow_step9_has_audio_upload_form_without_workflow(self):
        """Workflow step 9 shows audio upload form when no audio and paused."""
        self.client.login(username="staff", password="testpass123")
        response = self.client.get(self._workflow_url(9))
        content = response.content.decode("utf-8")
        self.assertIn('type="file"', content)
        self.assertIn("audio", content.lower())

    def test_workflow_step9_has_audio_upload_form_when_paused(self):
        """Workflow step 9 shows audio upload form when paused for human and no audio."""
        self.client.login(username="staff", password="testpass123")
        self.episode.audio_url = ""
        self.episode.save()
        # Create workflow in paused_for_human state at Audio Generation step
        EpisodeWorkflow.objects.create(
            episode=self.episode,
            current_step="Audio Generation",
            status="paused_for_human",
        )
        response = self.client.get(self._workflow_url(9))
        content = response.content.decode("utf-8")
        self.assertIn('type="file"', content)


@override_settings(STORAGES=SIMPLE_STORAGES)
class TestStage7PostProduction(TestCase):
    """Stage 7: POST-PRODUCTION -> Phases 10-11.

    User journey stage 7: Post-production phases run. User can review and
    edit metadata and preview cover art on step 11.

    Reference: docs/plans/episode-editor-user-journey.md#7-post-production--phases-10-11
    """

    def setUp(self):
        self.podcast = Podcast.objects.create(
            title="Test Podcast",
            slug="test-podcast",
            description="A test podcast.",
            author_name="Author",
            author_email="author@test.com",
            privacy=Podcast.Privacy.PUBLIC,
        )
        self.episode = Episode.objects.create(
            podcast=self.podcast,
            title="Post-Production Episode",
            slug="postprod-episode",
            episode_number=1,
            status="in_progress",
        )
        self.staff_user = User.objects.create_user(
            username="staff", password="testpass123", is_staff=True
        )

    def _workflow_url(self, step=1):
        return f"/podcast/{self.podcast.slug}/{self.episode.slug}/edit/{step}/"

    def test_workflow_has_metadata_edit_form(self):
        """Workflow step 11 has a metadata edit form with show_notes field."""
        self.client.login(username="staff", password="testpass123")
        response = self.client.get(self._workflow_url(11))
        content = response.content.decode("utf-8")
        self.assertIn('name="show_notes"', content)

    def test_workflow_has_cover_art_preview(self):
        """Workflow step 11 shows cover art preview when cover_image_url is set."""
        self.client.login(username="staff", password="testpass123")
        self.episode.cover_image_url = "https://example.com/cover.png"
        self.episode.save()
        response = self.client.get(self._workflow_url(11))
        content = response.content.decode("utf-8")
        self.assertIn("cover", content.lower())
        self.assertIn("<img", content)

    def test_workflow_has_cover_art_section_without_image(self):
        """Workflow step 11 shows cover art section even without an image."""
        self.client.login(username="staff", password="testpass123")
        response = self.client.get(self._workflow_url(11))
        content = response.content.decode("utf-8")
        self.assertIn("Cover Art", content)

    def test_workflow_has_title_and_description_on_step11(self):
        """Workflow step 11 metadata form includes title and description fields."""
        self.client.login(username="staff", password="testpass123")
        response = self.client.get(self._workflow_url(11))
        content = response.content.decode("utf-8")
        self.assertIn('name="title"', content)
        self.assertIn('name="description"', content)


@override_settings(STORAGES=SIMPLE_STORAGES)
class TestStage8Publish(TestCase):
    """Stage 8: PUBLISH -> Phase 12.

    User journey stage 8: Publishing the episode. Has a confirmation
    step before publishing.

    Reference: docs/plans/episode-editor-user-journey.md#8-publish--phase-12
    """

    def setUp(self):
        self.podcast = Podcast.objects.create(
            title="Test Podcast",
            slug="test-podcast",
            description="A test podcast.",
            author_name="Author",
            author_email="author@test.com",
            privacy=Podcast.Privacy.PUBLIC,
        )
        self.episode = Episode.objects.create(
            podcast=self.podcast,
            title="Publish Episode",
            slug="publish-episode",
            episode_number=1,
            status="in_progress",
            audio_url="https://example.com/ep.mp3",
        )
        self.staff_user = User.objects.create_user(
            username="staff", password="testpass123", is_staff=True
        )
        # Create workflow at Publish step in running state (ready to publish)
        EpisodeWorkflow.objects.create(
            episode=self.episode,
            current_step="Publish",
            status="running",
        )

    def _workflow_url(self, step=1):
        return f"/podcast/{self.podcast.slug}/{self.episode.slug}/edit/{step}/"

    def test_workflow_step12_loads(self):
        """Workflow step 12 (publish phase) returns 200 for staff."""
        self.client.login(username="staff", password="testpass123")
        response = self.client.get(self._workflow_url(12))
        self.assertEqual(response.status_code, 200)

    def test_workflow_step12_has_publish_confirmation(self):
        """Workflow step 12 has a publish confirmation area."""
        self.client.login(username="staff", password="testpass123")
        response = self.client.get(self._workflow_url(12))
        content = response.content.decode("utf-8")
        # The publish confirmation section includes confirmation UI
        self.assertIn("confirm", content.lower())
        self.assertIn("publish", content.lower())

    def test_workflow_step12_shows_success_after_publish(self):
        """Workflow step 12 shows success state when episode is published."""
        self.client.login(username="staff", password="testpass123")
        # Mark episode as published
        self.episode.status = "complete"
        self.episode.published_at = timezone.now()
        self.episode.save()
        response = self.client.get(self._workflow_url(12))
        content = response.content.decode("utf-8")
        self.assertIn("published", content.lower())
        self.assertIn("View Episode", content)


@override_settings(STORAGES=SIMPLE_STORAGES)
class TestStage9ViewPublished(TestCase):
    """Stage 9: VIEW PUBLISHED EPISODE -> Episode Detail Page.

    User journey stage 9: Viewing the published episode. Tests verify
    audio player, resources, navigation, and platform links.

    Reference: docs/plans/episode-editor-user-journey.md#9-view-published-episode--episode-detail-page
    Status: EXISTS
    """

    def setUp(self):
        self.podcast = Podcast.objects.create(
            title="Test Podcast",
            slug="test-podcast",
            description="A test podcast.",
            author_name="Author",
            author_email="author@test.com",
            privacy=Podcast.Privacy.PUBLIC,
            published_at=timezone.now() - timezone.timedelta(hours=1),
            spotify_url="https://open.spotify.com/show/test123",
            apple_podcasts_url="https://podcasts.apple.com/us/podcast/test123",
        )
        self.episode = Episode.objects.create(
            podcast=self.podcast,
            title="Published Episode",
            slug="published-episode",
            episode_number=1,
            audio_url="https://example.com/ep1.mp3",
            published_at=timezone.now() - timezone.timedelta(hours=1),
            report_text="This is the episode report.",
            sources_text="Source 1, Source 2",
            description="Episode about testing.",
        )

    def test_episode_detail_loads(self):
        """Episode detail page returns 200."""
        response = self.client.get(f"/podcast/{self.podcast.slug}/{self.episode.slug}/")
        self.assertEqual(response.status_code, 200)

    def test_episode_detail_shows_audio_player(self):
        """Episode detail page shows an audio player when audio_url is set."""
        response = self.client.get(f"/podcast/{self.podcast.slug}/{self.episode.slug}/")
        content = response.content.decode("utf-8")
        self.assertIn("<audio", content)
        self.assertIn("https://example.com/ep1.mp3", content)

    def test_episode_detail_shows_download_button(self):
        """Episode detail page shows a download button for audio."""
        response = self.client.get(f"/podcast/{self.podcast.slug}/{self.episode.slug}/")
        self.assertContains(response, "Download")

    def test_episode_detail_shows_resources(self):
        """Episode detail page shows report and sources links."""
        response = self.client.get(f"/podcast/{self.podcast.slug}/{self.episode.slug}/")
        self.assertContains(response, "View Report")
        self.assertContains(response, "View Sources")

    def test_episode_detail_shows_platform_links(self):
        """Episode detail page shows Spotify and Apple Podcasts links."""
        response = self.client.get(f"/podcast/{self.podcast.slug}/{self.episode.slug}/")
        self.assertContains(response, "Spotify")
        self.assertContains(response, "Apple Podcasts")

    def test_episode_detail_has_navigation_back_to_podcast(self):
        """Episode detail page has a back link to the podcast page."""
        response = self.client.get(f"/podcast/{self.podcast.slug}/{self.episode.slug}/")
        content = response.content.decode("utf-8")
        self.assertIn(f"/podcast/{self.podcast.slug}/", content)
        self.assertContains(response, "Back to")

    def test_episode_detail_shows_rss_link(self):
        """Episode detail page shows RSS feed link."""
        response = self.client.get(f"/podcast/{self.podcast.slug}/{self.episode.slug}/")
        self.assertContains(response, "RSS")

    def test_episode_detail_shows_episode_title(self):
        """Episode detail page displays the episode title."""
        response = self.client.get(f"/podcast/{self.podcast.slug}/{self.episode.slug}/")
        self.assertContains(response, "Published Episode")


@override_settings(STORAGES=SIMPLE_STORAGES)
class TestWorkflowAccessControls(TestCase):
    """Cross-cutting: Access control tests for the workflow view.

    Verifies that the workflow view enforces proper authentication
    and authorization across all stages.

    Reference: docs/plans/episode-editor-user-journey.md (all stages)
    """

    def setUp(self):
        self.podcast = Podcast.objects.create(
            title="Access Test Podcast",
            slug="access-test",
            description="A test podcast.",
            author_name="Author",
            author_email="author@test.com",
            privacy=Podcast.Privacy.PUBLIC,
        )
        self.episode = Episode.objects.create(
            podcast=self.podcast,
            title="Access Test Episode",
            slug="access-episode",
            episode_number=1,
            status="in_progress",
        )
        self.staff_user = User.objects.create_user(
            username="staff", password="testpass123", is_staff=True
        )
        self.regular_user = User.objects.create_user(
            username="regular", password="testpass123", is_staff=False
        )

    def _workflow_url(self, step=1):
        return f"/podcast/{self.podcast.slug}/{self.episode.slug}/edit/{step}/"

    def test_anonymous_redirects_to_login(self):
        """Anonymous user is redirected to login page."""
        response = self.client.get(self._workflow_url(1))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.url)

    def test_non_staff_gets_403(self):
        """Non-staff user gets 403 Forbidden."""
        self.client.login(username="regular", password="testpass123")
        response = self.client.get(self._workflow_url(1))
        self.assertEqual(response.status_code, 403)

    def test_staff_gets_200(self):
        """Staff user gets 200 OK."""
        self.client.login(username="staff", password="testpass123")
        response = self.client.get(self._workflow_url(1))
        self.assertEqual(response.status_code, 200)

    def test_step_0_returns_404(self):
        """Step 0 (invalid) returns 404."""
        self.client.login(username="staff", password="testpass123")
        response = self.client.get(self._workflow_url(0))
        self.assertEqual(response.status_code, 404)

    def test_step_13_returns_404(self):
        """Step 13 (invalid) returns 404."""
        self.client.login(username="staff", password="testpass123")
        response = self.client.get(self._workflow_url(13))
        self.assertEqual(response.status_code, 404)

    def test_htmx_request_returns_partial(self):
        """HTMX request returns partial template (no DOCTYPE)."""
        self.client.login(username="staff", password="testpass123")
        response = self.client.get(
            self._workflow_url(1), headers={"hx-request": "true"}
        )
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertNotIn("<!DOCTYPE html", content)

    def test_full_request_returns_complete_page(self):
        """Non-HTMX request returns complete page with DOCTYPE."""
        self.client.login(username="staff", password="testpass123")
        response = self.client.get(self._workflow_url(1))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("<!DOCTYPE html", content)
