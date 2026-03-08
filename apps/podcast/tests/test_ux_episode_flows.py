"""UX Flow Tests for Episode Editor User Journey.

End-to-end user flow tests verifying transitions between the 9 journey
stages documented in docs/plans/episode-editor-user-journey.md.

Tests for flows through existing features PASS; tests for flows that
hit missing features are marked xfail with descriptive reasons.
"""

import pytest
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
class TestFlowStage1to2NavigateToCreate(TestCase):
    """Flow: Stage 1 -> Stage 2.

    Owner navigates to podcast detail, clicks New Episode, sees creation
    form with title and description fields.

    Reference: docs/plans/episode-editor-user-journey.md stages 1-2
    """

    def setUp(self):
        self.owner = User.objects.create_user(
            username="owner", password="testpass123", is_staff=False
        )
        self.podcast = Podcast.objects.create(
            title="Flow Test Podcast",
            slug="flow-test-podcast",
            description="A podcast for flow testing.",
            author_name="Author",
            author_email="author@test.com",
            privacy=Podcast.Privacy.PUBLIC,
            published_at=timezone.now() - timezone.timedelta(hours=1),
            owner=self.owner,
        )
        self.staff_user = User.objects.create_user(
            username="staff", password="testpass123", is_staff=True
        )

    def test_navigate_to_podcast_and_see_creation_form(self):
        """Staff navigates to podcast, clicks New Episode, sees creation form with fields.

        Expected flow:
        1. GET /podcast/{slug}/ -> see podcast detail
        2. GET /podcast/{slug}/new/ -> see form with title/description
        """
        self.client.login(username="staff", password="testpass123")

        # Step 1: Navigate to podcast detail
        response = self.client.get(f"/podcast/{self.podcast.slug}/")
        self.assertEqual(response.status_code, 200)

        # Step 2: GET the creation form
        response = self.client.get(f"/podcast/{self.podcast.slug}/new/")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn('name="title"', content)
        self.assertIn('name="description"', content)

    def test_create_episode_with_form_data(self):
        """Staff submits creation form and episode is created with correct data.

        This tests the full form-based creation flow.
        """
        self.client.login(username="staff", password="testpass123")

        # POST with form data
        response = self.client.post(
            f"/podcast/{self.podcast.slug}/new/",
            data={
                "title": "My Test Episode",
                "description": "A deep dive into testing.",
            },
        )
        self.assertEqual(response.status_code, 302)

        # Verify episode was created with correct data
        episode = Episode.objects.filter(podcast=self.podcast).first()
        self.assertIsNotNone(episode)
        self.assertEqual(episode.title, "My Test Episode")
        self.assertEqual(episode.description, "A deep dive into testing.")

        # Verify redirect goes to workflow step 1
        self.assertIn("/edit/1/", response.url)


@override_settings(STORAGES=SIMPLE_STORAGES)
class TestFlowStage2to3CreateToEdit(TestCase):
    """Flow: Stage 2 -> Stage 3.

    After creating episode with title, user is redirected to workflow
    with title pre-populated.

    Reference: docs/plans/episode-editor-user-journey.md stages 2-3
    """

    def setUp(self):
        self.podcast = Podcast.objects.create(
            title="Flow Test Podcast",
            slug="flow-test-podcast",
            description="A podcast for flow testing.",
            author_name="Author",
            author_email="author@test.com",
            privacy=Podcast.Privacy.PUBLIC,
        )
        self.staff_user = User.objects.create_user(
            username="staff", password="testpass123", is_staff=True
        )

    def test_create_with_title_and_land_on_workflow(self):
        """Create episode with custom title, then verify workflow shows it.

        Expected flow:
        1. POST /podcast/{slug}/new/ with title='My Custom Episode'
        2. Redirect to workflow step 1
        3. Workflow page shows 'My Custom Episode' as the title
        """
        self.client.login(username="staff", password="testpass123")

        # Step 1: Create episode with title
        response = self.client.post(
            f"/podcast/{self.podcast.slug}/new/",
            data={"title": "My Custom Episode", "description": "A great topic"},
        )
        self.assertEqual(response.status_code, 302)

        # Step 2: Follow redirect to workflow
        episode = Episode.objects.filter(podcast=self.podcast).first()
        self.assertIsNotNone(episode)
        self.assertEqual(episode.title, "My Custom Episode")

        # Step 3: Verify workflow page shows the title
        response = self.client.get(response.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "My Custom Episode")


@override_settings(STORAGES=SIMPLE_STORAGES)
class TestFlowStage3to4EditToStartPipeline(TestCase):
    """Flow: Stage 3 -> Stage 4.

    User edits description on workflow via HTMX PATCH, clicks Start Pipeline.

    Reference: docs/plans/episode-editor-user-journey.md stages 3-4
    """

    def setUp(self):
        self.podcast = Podcast.objects.create(
            title="Flow Test Podcast",
            slug="flow-test-podcast",
            description="A podcast for flow testing.",
            author_name="Author",
            author_email="author@test.com",
            privacy=Podcast.Privacy.PUBLIC,
        )
        self.episode = Episode.objects.create(
            podcast=self.podcast,
            title="Draft Episode",
            slug="draft-episode",
            episode_number=1,
            status="draft",
            description="Original description.",
        )
        self.staff_user = User.objects.create_user(
            username="staff", password="testpass123", is_staff=True
        )

    def _workflow_url(self, step=1):
        return f"/podcast/{self.podcast.slug}/{self.episode.slug}/edit/{step}/"

    def test_edit_description_via_htmx_patch(self):
        """User edits description on workflow step 1 via HTMX PATCH.

        Expected flow:
        1. GET workflow step 1 -> see description edit field
        2. PATCH updated description via inline edit (workflow PATCH endpoint)
        3. Description saved to Episode model
        """
        self.client.login(username="staff", password="testpass123")

        # Step 1: Load workflow page and verify description field
        response = self.client.get(self._workflow_url(1))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn('name="description"', content)

        # Step 2: PATCH description update via workflow update endpoint
        update_url = f"/podcast/{self.podcast.slug}/{self.episode.slug}/edit/1/update/"
        response = self.client.patch(
            update_url,
            data="field=description&description=An updated episode brief about AI testing.",
            content_type="application/x-www-form-urlencoded",
        )
        self.assertEqual(response.status_code, 200)

        # Step 3: Verify description saved
        self.episode.refresh_from_db()
        self.assertEqual(
            self.episode.description, "An updated episode brief about AI testing."
        )


@override_settings(STORAGES=SIMPLE_STORAGES)
class TestFlowStage4to6ResearchToAudio(TestCase):
    """Flow: Stage 4 -> Stage 6.

    Workflow shows quality gate pause, user can review content.

    Reference: docs/plans/episode-editor-user-journey.md stages 4-6
    """

    def setUp(self):
        self.podcast = Podcast.objects.create(
            title="Flow Test Podcast",
            slug="flow-test-podcast",
            description="A podcast for flow testing.",
            author_name="Author",
            author_email="author@test.com",
            privacy=Podcast.Privacy.PUBLIC,
        )
        self.episode = Episode.objects.create(
            podcast=self.podcast,
            title="Research Episode",
            slug="research-episode",
            episode_number=1,
            status="in_progress",
        )
        self.staff_user = User.objects.create_user(
            username="staff", password="testpass123", is_staff=True
        )

    def _workflow_url(self, step=1):
        return f"/podcast/{self.podcast.slug}/{self.episode.slug}/edit/{step}/"

    @pytest.mark.xfail(
        reason="Gap: No artifact viewer at quality gates without actual artifacts. "
        "Artifact viewer shows content only when phase_artifact context var is set, "
        "which requires actual EpisodeArtifact records in the database.",
        strict=True,
    )
    def test_quality_gate_shows_reviewable_content(self):
        """Quality gate pause shows content for user review.

        Expected flow:
        1. Pipeline reaches phase 6 (master briefing)
        2. Workflow pauses at quality gate
        3. User can see the briefing content for review
        4. User clicks Resume Pipeline
        """
        self.client.login(username="staff", password="testpass123")

        response = self.client.get(self._workflow_url(6))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        # Should display artifact content, not just status
        self.assertIn("artifact-content", content)


@override_settings(STORAGES=SIMPLE_STORAGES)
class TestFlowStage6to7AudioToPostProd(TestCase):
    """Flow: Stage 6 -> Stage 7.

    Audio generation completes, user can preview before proceeding.

    Reference: docs/plans/episode-editor-user-journey.md stages 6-7
    """

    def setUp(self):
        self.podcast = Podcast.objects.create(
            title="Flow Test Podcast",
            slug="flow-test-podcast",
            description="A podcast for flow testing.",
            author_name="Author",
            author_email="author@test.com",
            privacy=Podcast.Privacy.PUBLIC,
        )
        self.episode = Episode.objects.create(
            podcast=self.podcast,
            title="Audio Episode",
            slug="audio-episode",
            episode_number=1,
            status="in_progress",
            audio_url="https://example.com/generated-audio.mp3",
        )
        self.staff_user = User.objects.create_user(
            username="staff", password="testpass123", is_staff=True
        )

    def _workflow_url(self, step=1):
        return f"/podcast/{self.podcast.slug}/{self.episode.slug}/edit/{step}/"

    def test_audio_preview_before_post_production(self):
        """After audio generation, user can preview audio on workflow.

        Expected flow:
        1. Audio generation completes (audio_url set)
        2. Workflow page shows audio player for preview
        3. User can listen and approve before proceeding
        """
        self.client.login(username="staff", password="testpass123")

        response = self.client.get(self._workflow_url(9))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("<audio", content)
        self.assertIn("https://example.com/generated-audio.mp3", content)


@override_settings(STORAGES=SIMPLE_STORAGES)
class TestFlowStage7to8PostProdToPublish(TestCase):
    """Flow: Stage 7 -> Stage 8.

    User reviews metadata, can edit before publishing.

    Reference: docs/plans/episode-editor-user-journey.md stages 7-8
    """

    def setUp(self):
        self.podcast = Podcast.objects.create(
            title="Flow Test Podcast",
            slug="flow-test-podcast",
            description="A podcast for flow testing.",
            author_name="Author",
            author_email="author@test.com",
            privacy=Podcast.Privacy.PUBLIC,
        )
        self.episode = Episode.objects.create(
            podcast=self.podcast,
            title="Metadata Episode",
            slug="metadata-episode",
            episode_number=1,
            status="in_progress",
            description="AI-generated description.",
            show_notes="<p>AI-generated show notes.</p>",
        )
        self.staff_user = User.objects.create_user(
            username="staff", password="testpass123", is_staff=True
        )

    def _workflow_url(self, step=1):
        return f"/podcast/{self.podcast.slug}/{self.episode.slug}/edit/{step}/"

    def test_review_metadata_before_publishing(self):
        """User reviews and edits metadata before publishing.

        Expected flow:
        1. Post-production completes (phases 10-11)
        2. User sees metadata review form (title, description, show notes)
        3. User can edit metadata
        4. User proceeds to publish step
        """
        self.client.login(username="staff", password="testpass123")

        # Check metadata review page
        response = self.client.get(self._workflow_url(11))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")

        # Should show editable metadata fields
        self.assertIn('name="title"', content)
        self.assertIn('name="description"', content)
        self.assertIn('name="show_notes"', content)


@override_settings(STORAGES=SIMPLE_STORAGES)
class TestFlowStage8to9PublishToView(TestCase):
    """Flow: Stage 8 -> Stage 9.

    After publishing, success page shows links to episode.

    Reference: docs/plans/episode-editor-user-journey.md stages 8-9
    """

    def setUp(self):
        self.podcast = Podcast.objects.create(
            title="Flow Test Podcast",
            slug="flow-test-podcast",
            description="A podcast for flow testing.",
            author_name="Author",
            author_email="author@test.com",
            privacy=Podcast.Privacy.PUBLIC,
            published_at=timezone.now() - timezone.timedelta(hours=1),
        )
        self.episode = Episode.objects.create(
            podcast=self.podcast,
            title="Published Flow Episode",
            slug="published-flow-episode",
            episode_number=1,
            status="complete",
            audio_url="https://example.com/ep1.mp3",
            published_at=timezone.now() - timezone.timedelta(minutes=5),
        )
        self.staff_user = User.objects.create_user(
            username="staff", password="testpass123", is_staff=True
        )

    def test_published_episode_shows_success_on_step12(self):
        """Published episode shows success page with navigation links on step 12.

        When episode.status == 'complete' and we're on step 12, the workflow
        renders a success state with View Episode and RSS Feed links.
        """
        self.client.login(username="staff", password="testpass123")

        response = self.client.get(
            f"/podcast/{self.podcast.slug}/{self.episode.slug}/edit/12/"
        )
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        # Success page shows View Episode link and RSS link
        self.assertIn("published", content.lower())
        self.assertIn("View Episode", content)
        self.assertIn(
            f"/podcast/{self.podcast.slug}/{self.episode.slug}/",
            content,
        )


@override_settings(STORAGES=SIMPLE_STORAGES)
class TestFlowFullHappyPath(TestCase):
    """Full Happy Path: Complete journey from idea to published episode.

    Tests the complete end-to-end user journey through all 9 stages.

    Reference: docs/plans/episode-editor-user-journey.md (all stages)
    """

    def setUp(self):
        self.owner = User.objects.create_user(
            username="owner", password="testpass123", is_staff=False
        )
        self.podcast = Podcast.objects.create(
            title="Happy Path Podcast",
            slug="happy-path-podcast",
            description="A podcast for full journey testing.",
            author_name="Author",
            author_email="author@test.com",
            privacy=Podcast.Privacy.PUBLIC,
            published_at=timezone.now() - timezone.timedelta(hours=1),
            owner=self.owner,
        )
        self.staff_user = User.objects.create_user(
            username="staff", password="testpass123", is_staff=True
        )

    def test_complete_happy_path_idea_to_published(self):
        """Complete happy path: idea -> navigate -> create -> edit -> publish -> view.

        Expected full flow:
        1. Navigate to podcast detail page
        2. GET creation form, POST with title and description
        3. Land on workflow, verify title/description populated
        4-8. (Pipeline runs - simulated by updating episode status)
        9. View published episode with audio player and resources
        """
        self.client.login(username="staff", password="testpass123")

        # Stage 1: Navigate to podcast
        response = self.client.get(f"/podcast/{self.podcast.slug}/")
        self.assertEqual(response.status_code, 200)

        # Stage 2: Create episode with form
        response = self.client.post(
            f"/podcast/{self.podcast.slug}/new/",
            data={
                "title": "Test Happy Path Episode",
                "description": "A deep dive into testing strategies.",
            },
        )
        self.assertEqual(response.status_code, 302)

        episode = Episode.objects.filter(podcast=self.podcast).first()
        self.assertIsNotNone(episode)
        self.assertEqual(episode.title, "Test Happy Path Episode")
        self.assertEqual(episode.description, "A deep dive into testing strategies.")

        # Stage 3: Verify workflow has editable fields
        workflow_url = f"/podcast/{self.podcast.slug}/{episode.slug}/edit/1/"
        response = self.client.get(workflow_url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn('name="title"', content)
        self.assertIn('name="description"', content)

        # Stage 9: After simulated pipeline completion, view published
        episode.status = "complete"
        episode.audio_url = "https://example.com/happy-path.mp3"
        episode.published_at = timezone.now()
        episode.save()

        response = self.client.get(f"/podcast/{self.podcast.slug}/{episode.slug}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Test Happy Path Episode")
        self.assertContains(response, "<audio")


@override_settings(STORAGES=SIMPLE_STORAGES)
class TestFlowExistingWorkflowNavigation(TestCase):
    """Flow: Verify existing workflow navigation between steps.

    Tests that the existing workflow page allows navigating between
    different steps (1-12). This is existing functionality that should pass.

    Reference: docs/plans/episode-editor-user-journey.md (workflow dashboard)
    """

    def setUp(self):
        self.podcast = Podcast.objects.create(
            title="Nav Test Podcast",
            slug="nav-test-podcast",
            description="A podcast for navigation testing.",
            author_name="Author",
            author_email="author@test.com",
            privacy=Podcast.Privacy.PUBLIC,
        )
        self.episode = Episode.objects.create(
            podcast=self.podcast,
            title="Nav Test Episode",
            slug="nav-test-episode",
            episode_number=1,
            status="in_progress",
        )
        self.staff_user = User.objects.create_user(
            username="staff", password="testpass123", is_staff=True
        )

    def _workflow_url(self, step=1):
        return f"/podcast/{self.podcast.slug}/{self.episode.slug}/edit/{step}/"

    def test_all_12_steps_load_successfully(self):
        """All 12 workflow steps return 200 for staff."""
        self.client.login(username="staff", password="testpass123")
        for step in range(1, 13):
            response = self.client.get(self._workflow_url(step))
            self.assertEqual(
                response.status_code,
                200,
                f"Step {step} returned {response.status_code}, expected 200",
            )

    def test_workflow_context_tracks_current_step(self):
        """Workflow context correctly reflects the current step number."""
        self.client.login(username="staff", password="testpass123")
        for step in [1, 5, 12]:
            response = self.client.get(self._workflow_url(step))
            self.assertEqual(response.context["current_step"], step)

    def test_workflow_accessible_for_draft_episode(self):
        """Workflow is accessible for episodes in draft status."""
        self.episode.status = "draft"
        self.episode.save()
        self.client.login(username="staff", password="testpass123")
        response = self.client.get(self._workflow_url(1))
        self.assertEqual(response.status_code, 200)

    def test_workflow_accessible_for_complete_episode(self):
        """Workflow is accessible for episodes in complete status."""
        self.episode.status = "complete"
        self.episode.save()
        self.client.login(username="staff", password="testpass123")
        response = self.client.get(self._workflow_url(1))
        self.assertEqual(response.status_code, 200)


@override_settings(STORAGES=SIMPLE_STORAGES)
class TestFlowPodcastToEpisodeDetail(TestCase):
    """Flow: Navigate from podcast detail to episode detail and back.

    Tests the navigation flow between podcast and episode detail pages,
    which is existing functionality that should pass.

    Reference: docs/plans/episode-editor-user-journey.md stages 1, 9
    """

    def setUp(self):
        self.podcast = Podcast.objects.create(
            title="Detail Nav Podcast",
            slug="detail-nav-podcast",
            description="A podcast for detail navigation testing.",
            author_name="Author",
            author_email="author@test.com",
            privacy=Podcast.Privacy.PUBLIC,
            published_at=timezone.now() - timezone.timedelta(hours=1),
        )
        self.episode = Episode.objects.create(
            podcast=self.podcast,
            title="Detail Nav Episode",
            slug="detail-nav-episode",
            episode_number=1,
            audio_url="https://example.com/ep1.mp3",
            published_at=timezone.now() - timezone.timedelta(hours=1),
        )

    def test_podcast_detail_links_to_episode(self):
        """Podcast detail page has a link to the episode detail page."""
        response = self.client.get(f"/podcast/{self.podcast.slug}/")
        self.assertContains(
            response,
            f"/podcast/{self.podcast.slug}/{self.episode.slug}/",
        )

    def test_episode_detail_links_back_to_podcast(self):
        """Episode detail page has a back link to the podcast page."""
        response = self.client.get(f"/podcast/{self.podcast.slug}/{self.episode.slug}/")
        self.assertContains(response, f"/podcast/{self.podcast.slug}/")
        self.assertContains(response, "Back to")

    def test_roundtrip_podcast_to_episode_and_back(self):
        """User can navigate: podcast -> episode -> back to podcast."""
        # Start at podcast detail
        response = self.client.get(f"/podcast/{self.podcast.slug}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Detail Nav Episode")

        # Navigate to episode detail
        response = self.client.get(f"/podcast/{self.podcast.slug}/{self.episode.slug}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Detail Nav Episode")

        # Navigate back to podcast
        response = self.client.get(f"/podcast/{self.podcast.slug}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Detail Nav Podcast")
