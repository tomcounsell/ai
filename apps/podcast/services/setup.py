"""Initialize an episode workflow and create the initial brief artifact."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from apps.podcast.models import Episode, EpisodeArtifact, EpisodeWorkflow

logger = logging.getLogger(__name__)


def setup_episode(episode_id: int) -> EpisodeArtifact:
    """Initialize episode workflow.

    Creates a ``p1-brief`` artifact from :pyattr:`Episode.description` and
    creates an :class:`EpisodeWorkflow` record to track production state.

    Args:
        episode_id: Primary key of the :class:`Episode` to initialize.

    Returns:
        The ``p1-brief`` :class:`EpisodeArtifact`.

    Raises:
        Episode.DoesNotExist: If no episode with the given ID exists.
    """
    episode = Episode.objects.get(pk=episode_id)

    # Transition draft episodes to in_progress
    if episode.status == "draft":
        episode.status = "in_progress"
        episode.save(update_fields=["status"])

    # Create or update the initial brief artifact
    artifact, created = EpisodeArtifact.objects.update_or_create(
        episode=episode,
        title="p1-brief",
        defaults={
            "content": episode.description,
            "description": "Initial episode brief derived from the episode description.",
            "workflow_context": "Setup",
        },
    )

    action = "Created" if created else "Updated"
    logger.info("%s p1-brief artifact for episode %s", action, episode_id)

    # Create or update the workflow record
    now_iso = datetime.now(timezone.utc).isoformat()

    workflow, wf_created = EpisodeWorkflow.objects.update_or_create(
        episode=episode,
        defaults={
            "current_step": "Setup",
            "status": "running",
        },
    )

    if wf_created:
        workflow.history = [
            {"step": "Setup", "status": "started", "started_at": now_iso}
        ]
        workflow.save(update_fields=["history"])
        logger.info("Created EpisodeWorkflow for episode %s", episode_id)
    else:
        logger.info("Updated EpisodeWorkflow for episode %s", episode_id)

    return artifact
