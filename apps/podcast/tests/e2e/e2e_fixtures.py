"""ORM-based fixture creation for podcast E2E tests.

Creates real database rows that persist for the test server process.
Does NOT use Django TestCase (which wraps in transactions).

All fixture objects use an ``e2e-`` prefix so they are easy to identify
and clean up between test runs.
"""

from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.podcast.models import Episode, EpisodeArtifact, EpisodeWorkflow, Podcast

User = get_user_model()

# Shared credentials
E2E_PASSWORD = "e2e_pass_123"


@dataclass
class E2EData:
    """Container for all E2E fixture objects."""

    staff_user: object
    owner_user: object
    regular_user: object
    podcast: object
    draft_episode: object
    published_episode: object
    mid_pipeline_episode: object


def cleanup_e2e_data() -> None:
    """Remove any leftover E2E fixture data from previous runs."""
    Episode.objects.filter(slug__startswith="e2e-").delete()
    Podcast.objects.filter(slug__startswith="e2e-").delete()
    User.objects.filter(username__startswith="e2e_").delete()


def create_workflow_at_step(
    episode: Episode,
    step_name: str,
    status: str = "running",
    history: list | None = None,
) -> EpisodeWorkflow:
    """Create an EpisodeWorkflow at a specific step with optional history."""
    workflow, _ = EpisodeWorkflow.objects.update_or_create(
        episode=episode,
        defaults={
            "current_step": step_name,
            "status": status,
            "history": history or [],
        },
    )
    return workflow


def create_artifact(
    episode: Episode,
    title: str,
    content: str = "E2E test artifact content.",
) -> EpisodeArtifact:
    """Create an EpisodeArtifact with given title and content."""
    artifact, _ = EpisodeArtifact.objects.update_or_create(
        episode=episode,
        title=title,
        defaults={"content": content},
    )
    return artifact


def setup_e2e_data() -> E2EData:
    """Create all E2E fixture data. Idempotent -- safe to call multiple times.

    Returns an E2EData dataclass with references to all created objects.
    """
    cleanup_e2e_data()

    # -- Users --
    staff_user = User.objects.create_user(
        username="e2e_staff",
        email="e2e_staff@test.local",
        password=E2E_PASSWORD,
        is_staff=True,
    )
    owner_user = User.objects.create_user(
        username="e2e_owner",
        email="e2e_owner@test.local",
        password=E2E_PASSWORD,
        is_staff=False,
    )
    regular_user = User.objects.create_user(
        username="e2e_regular",
        email="e2e_regular@test.local",
        password=E2E_PASSWORD,
        is_staff=False,
    )

    # -- Podcast --
    podcast = Podcast.objects.create(
        title="E2E Test Podcast",
        slug="e2e-test-podcast",
        description="A podcast created for E2E browser testing.",
        author_name="E2E Author",
        author_email="e2e@test.local",
        privacy=Podcast.Privacy.PUBLIC,
        published_at=timezone.now() - timezone.timedelta(hours=1),
        owner=owner_user,
    )

    # -- Draft episode (for create/edit tests) --
    draft_episode = Episode.objects.create(
        podcast=podcast,
        title="E2E Draft Episode",
        slug="e2e-draft-episode",
        episode_number=100,
        status="draft",
        description="A draft episode for E2E testing.",
    )

    # -- Published episode (for detail page tests) --
    published_episode = Episode.objects.create(
        podcast=podcast,
        title="E2E Published Episode",
        slug="e2e-published-episode",
        episode_number=101,
        status="complete",
        description="A published episode for E2E testing.",
        audio_url="https://example.com/e2e-test.mp3",
        published_at=timezone.now() - timezone.timedelta(hours=1),
        report_text="This is the E2E test episode report with detailed findings.",
        sources_text="Source 1: https://example.com, Source 2: https://example.org",
    )

    # -- Mid-pipeline episode (for workflow UI tests) --
    mid_pipeline_episode = Episode.objects.create(
        podcast=podcast,
        title="E2E Mid-Pipeline Episode",
        slug="e2e-mid-pipeline-episode",
        episode_number=102,
        status="in_progress",
        description="An episode partway through the workflow for UI testing.",
    )
    create_workflow_at_step(
        mid_pipeline_episode,
        step_name="Cross-Validation",
        status="running",
        history=[
            {
                "step": "Setup",
                "status": "completed",
                "started_at": "",
                "completed_at": "",
            },
            {
                "step": "Perplexity Research",
                "status": "completed",
                "started_at": "",
                "completed_at": "",
            },
            {
                "step": "Question Discovery",
                "status": "completed",
                "started_at": "",
                "completed_at": "",
            },
            {
                "step": "Targeted Research",
                "status": "completed",
                "started_at": "",
                "completed_at": "",
            },
        ],
    )
    create_artifact(mid_pipeline_episode, "p1-brief", "E2E brief content for testing.")
    create_artifact(
        mid_pipeline_episode, "p2-research", "E2E research content for testing."
    )
    create_artifact(
        mid_pipeline_episode, "p3-questions", "E2E questions content for testing."
    )

    return E2EData(
        staff_user=staff_user,
        owner_user=owner_user,
        regular_user=regular_user,
        podcast=podcast,
        draft_episode=draft_episode,
        published_episode=published_episode,
        mid_pipeline_episode=mid_pipeline_episode,
    )
