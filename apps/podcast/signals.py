"""Fan-in signals for parallel workflow steps.

When parallel sub-steps (Targeted Research, Publishing Assets) each save
their artifacts independently, this signal checks whether ALL artifacts
for the current step are populated.  If so, it enqueues the next step in
the pipeline.

Uses ``select_for_update`` inside ``transaction.atomic()`` to prevent
double-enqueue when two parallel tasks finish near-simultaneously.
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.podcast.models import EpisodeArtifact, EpisodeWorkflow

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Artifact prefix / title mapping for fan-in steps
# ---------------------------------------------------------------------------

# Publishing Assets: all required publishing artifacts
_PUBLISHING_ASSET_TITLES = {
    "metadata",
    "companion-summary",
    "companion-checklist",
    "companion-frameworks",
    "cover-art",
}


def _prefix_for_step(step: str) -> str | None:
    """Return the artifact title prefix associated with a fan-in step.

    Returns ``None`` for steps that do not use signal-based fan-in.
    """
    if step == "Targeted Research":
        return "p2-"
    if step == "Publishing Assets":
        return None  # Publishing uses explicit title checks, not prefix
    return None


def _check_targeted_research_complete(episode_id: int) -> bool:
    """Check whether all targeted research artifacts have content.

    Targeted Research expects all p2-* artifacts (except p2-perplexity,
    which is created in an earlier phase) to have content. The set of
    expected artifacts is determined by the placeholder artifacts created
    during question discovery.
    """
    targeted_artifacts = EpisodeArtifact.objects.filter(
        episode_id=episode_id,
        title__startswith="p2-",
    ).exclude(title="p2-perplexity")

    if not targeted_artifacts.exists():
        return False

    # All targeted artifacts must have content
    return not targeted_artifacts.filter(content="").exists()


def _check_publishing_assets_complete(episode_id: int) -> bool:
    """Check whether all publishing asset artifacts have content.

    Publishing Assets expects metadata, all three companion-* artifacts,
    and cover-art to have content.
    """
    titles_with_content = set(
        EpisodeArtifact.objects.filter(
            episode_id=episode_id,
        )
        .exclude(content="")
        .values_list("title", flat=True)
    )

    return _PUBLISHING_ASSET_TITLES.issubset(titles_with_content)


def _try_enqueue_next_step(episode_id: int, current_step: str) -> bool:
    """Atomically advance the workflow and enqueue the next step.

    Uses ``select_for_update`` to prevent two concurrent signals from
    both advancing the workflow.  The first signal to acquire the lock
    will advance; the second will see the step has already changed and
    return ``False``.

    Returns ``True`` if the next step was enqueued, ``False`` otherwise.
    """
    with transaction.atomic():
        wf = EpisodeWorkflow.objects.select_for_update().get(episode_id=episode_id)

        # Another signal already advanced past this step
        if wf.current_step != current_step:
            logger.debug(
                "Fan-in signal: episode %d already advanced past '%s' "
                "(now at '%s'), skipping",
                episode_id,
                current_step,
                wf.current_step,
            )
            return False

        # Workflow must be running
        if wf.status != "running":
            logger.debug(
                "Fan-in signal: episode %d workflow status is '%s', "
                "not 'running', skipping",
                episode_id,
                wf.status,
            )
            return False

    # Outside the lock -- enqueue the next step.
    # Import here to avoid circular imports.
    from apps.podcast.tasks import step_publish, step_research_digests

    if current_step == "Targeted Research":
        step_research_digests.enqueue(episode_id=episode_id)
        logger.info(
            "Fan-in signal: all targeted research complete for episode %d, "
            "enqueued step_research_digests",
            episode_id,
        )
        return True

    elif current_step == "Publishing Assets":
        from apps.podcast.services import workflow as wf_service

        wf_service.advance_step(episode_id, "Publishing Assets")
        step_publish.enqueue(episode_id=episode_id)
        logger.info(
            "Fan-in signal: all publishing assets complete for episode %d, "
            "enqueued step_publish",
            episode_id,
        )
        return True

    return False


# ---------------------------------------------------------------------------
# Signal handler
# ---------------------------------------------------------------------------


@receiver(post_save, sender=EpisodeArtifact)
def check_workflow_progression(sender, instance, **kwargs):
    """When an artifact is saved, check if the episode is ready to advance.

    This is the fan-in mechanism for parallel workflow steps.  Each time
    an artifact is saved, we check whether all artifacts for the current
    step have content.  If so, we enqueue the next step.
    """
    episode = instance.episode

    try:
        wf = episode.workflow
    except EpisodeWorkflow.DoesNotExist:
        return

    if wf.status not in ("running", "paused_for_human"):
        return

    current_step = wf.current_step

    # noqa: SIM114 - Keep if/elif for clarity over combined logical or
    if current_step == "Targeted Research" and _check_targeted_research_complete(
        episode.id
    ):
        _try_enqueue_next_step(episode.id, current_step)
    elif current_step == "Publishing Assets" and _check_publishing_assets_complete(
        episode.id
    ):
        _try_enqueue_next_step(episode.id, current_step)


# ---------------------------------------------------------------------------
# Feed cache invalidation
# ---------------------------------------------------------------------------


@receiver(post_save, sender="podcast.Episode")
def invalidate_feed_cache_on_episode_change(sender, instance, **kwargs):
    """Invalidate the podcast feed cache when an episode's publish status changes.

    This ensures the RSS feed is regenerated when episodes are published or
    unpublished, rather than relying solely on the 5-minute TTL.
    """
    from django.core.cache import cache

    # Only invalidate if the episode has a podcast (should always be true)
    if not instance.podcast_id:
        return

    # Build cache key for the podcast feed
    # This matches the cache key used by PodcastFeedView
    cache_key = f"podcast_feed_{instance.podcast.slug}"
    cache.delete(cache_key)

    logger.debug(
        "Feed cache invalidated for podcast %s (episode %s changed)",
        instance.podcast.slug,
        instance.slug,
    )
