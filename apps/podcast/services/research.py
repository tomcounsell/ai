"""Run external research tools and persist results as EpisodeArtifact records."""

from __future__ import annotations

import logging

from apps.podcast.models import Episode, EpisodeArtifact

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_episode_context(episode: Episode) -> str:
    """Return the best available context text for a research prompt.

    Prefers the ``question-discovery`` artifact (richer context), then falls
    back to ``p1-brief``, and finally to :pyattr:`Episode.description`.
    """
    for title in ("question-discovery", "p1-brief"):
        try:
            artifact = EpisodeArtifact.objects.get(episode=episode, title=title)
            if artifact.content:
                return artifact.content
        except EpisodeArtifact.DoesNotExist:
            continue
    return episode.description


# ---------------------------------------------------------------------------
# Perplexity
# ---------------------------------------------------------------------------


def run_perplexity_research(episode_id: int, prompt: str) -> EpisodeArtifact:
    """Call Perplexity Deep Research API and save results as ``p2-perplexity``.

    The ``prompt`` is sent to the Perplexity *sonar-deep-research* model via
    :func:`apps.podcast.tools.perplexity_deep_research.run_perplexity_research`.

    Args:
        episode_id: Primary key of the target :class:`Episode`.
        prompt: The research query to send to Perplexity.

    Returns:
        The ``p2-perplexity`` :class:`EpisodeArtifact`.
    """
    episode = Episode.objects.get(pk=episode_id)
    context = _get_episode_context(episode)

    full_prompt = (
        f"Episode: {episode.title}\n\n"
        f"Context:\n{context}\n\n"
        f"Research query:\n{prompt}"
    )

    # Call the existing Perplexity tool
    from apps.podcast.tools.perplexity_deep_research import (
        run_perplexity_research as _perplexity,
    )

    content_text, response_data = _perplexity(
        prompt=full_prompt,
        verbose=False,
    )

    if content_text is None:
        content_text = ""
        logger.warning(
            "Perplexity research returned no content for episode %s", episode_id
        )

    metadata: dict = {}
    if response_data:
        from apps.podcast.tools.perplexity_deep_research import extract_metadata

        metadata = extract_metadata(response_data)

    artifact, created = EpisodeArtifact.objects.update_or_create(
        episode=episode,
        title="p2-perplexity",
        defaults={
            "content": content_text,
            "description": "Perplexity Deep Research output.",
            "workflow_context": "Research Gathering",
            "metadata": metadata,
        },
    )

    action = "Created" if created else "Updated"
    logger.info("%s p2-perplexity artifact for episode %s", action, episode_id)
    return artifact


# ---------------------------------------------------------------------------
# GPT-Researcher
# ---------------------------------------------------------------------------


def run_gpt_researcher(episode_id: int, prompt: str) -> EpisodeArtifact:
    """Call GPT-Researcher and save results as ``p2-chatgpt``.

    Uses :func:`apps.podcast.tools.gpt_researcher_run.run_research` (async)
    via ``asyncio.run`` since this function is synchronous.

    Args:
        episode_id: Primary key of the target :class:`Episode`.
        prompt: The research query to send to GPT-Researcher.

    Returns:
        The ``p2-chatgpt`` :class:`EpisodeArtifact`.
    """
    from asgiref.sync import async_to_sync

    episode = Episode.objects.get(pk=episode_id)
    context = _get_episode_context(episode)

    full_prompt = (
        f"Episode: {episode.title}\n\n"
        f"Context:\n{context}\n\n"
        f"Research query:\n{prompt}"
    )

    # Call the existing GPT-Researcher tool (async → sync bridge)
    from apps.podcast.tools.gpt_researcher_run import run_research

    content_text = async_to_sync(run_research)(
        prompt=full_prompt,
        verbose=False,
    )

    if content_text is None:
        content_text = ""
        logger.warning("GPT-Researcher returned no content for episode %s", episode_id)

    artifact, created = EpisodeArtifact.objects.update_or_create(
        episode=episode,
        title="p2-chatgpt",
        defaults={
            "content": content_text,
            "description": "GPT-Researcher multi-agent research output.",
            "workflow_context": "Research Gathering",
        },
    )

    action = "Created" if created else "Updated"
    logger.info("%s p2-chatgpt artifact for episode %s", action, episode_id)
    return artifact


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------


def run_gemini_research(episode_id: int, prompt: str) -> EpisodeArtifact:
    """Call Gemini Deep Research and save results as ``p2-gemini``.

    Uses :func:`apps.podcast.tools.gemini_deep_research.run_gemini_research`.

    Args:
        episode_id: Primary key of the target :class:`Episode`.
        prompt: The research query to send to Gemini.

    Returns:
        The ``p2-gemini`` :class:`EpisodeArtifact`.
    """
    episode = Episode.objects.get(pk=episode_id)
    context = _get_episode_context(episode)

    full_prompt = (
        f"Episode: {episode.title}\n\n"
        f"Context:\n{context}\n\n"
        f"Research query:\n{prompt}"
    )

    # Call the existing Gemini tool
    from apps.podcast.tools.gemini_deep_research import run_gemini_research as _gemini

    content_text = _gemini(
        prompt=full_prompt,
        verbose=False,
    )

    if content_text is None:
        content_text = ""
        logger.warning("Gemini research returned no content for episode %s", episode_id)

    artifact, created = EpisodeArtifact.objects.update_or_create(
        episode=episode,
        title="p2-gemini",
        defaults={
            "content": content_text,
            "description": "Gemini Deep Research output.",
            "workflow_context": "Research Gathering",
        },
    )

    action = "Created" if created else "Updated"
    logger.info("%s p2-gemini artifact for episode %s", action, episode_id)
    return artifact


# ---------------------------------------------------------------------------
# Together Open Deep Research
# ---------------------------------------------------------------------------


def run_together_research(episode_id: int, prompt: str) -> EpisodeArtifact:
    """Call Together Open Deep Research and save results as ``p2-together``.

    Uses :func:`apps.podcast.tools.together_deep_research.run_together_research`.

    Args:
        episode_id: Primary key of the target :class:`Episode`.
        prompt: The research query to send to Together.

    Returns:
        The ``p2-together`` :class:`EpisodeArtifact`.
    """
    episode = Episode.objects.get(pk=episode_id)
    context = _get_episode_context(episode)

    full_prompt = (
        f"Episode: {episode.title}\n\n"
        f"Context:\n{context}\n\n"
        f"Research query:\n{prompt}"
    )

    from apps.podcast.tools.together_deep_research import (
        run_together_research as _together,
    )

    content_text, metadata = _together(
        prompt=full_prompt,
        verbose=False,
    )

    if content_text is None:
        content_text = ""
        logger.warning(
            "Together research returned no content for episode %s", episode_id
        )

    artifact, created = EpisodeArtifact.objects.update_or_create(
        episode=episode,
        title="p2-together",
        defaults={
            "content": content_text,
            "description": "Together Open Deep Research multi-hop output.",
            "workflow_context": "Research Gathering",
            "metadata": metadata,
        },
    )

    action = "Created" if created else "Updated"
    logger.info("%s p2-together artifact for episode %s", action, episode_id)
    return artifact


# ---------------------------------------------------------------------------
# Manual / human-pasted research
# ---------------------------------------------------------------------------


def add_manual_research(episode_id: int, title: str, content: str) -> EpisodeArtifact:
    """Save human-pasted research as a ``p2-{title}`` artifact.

    Args:
        episode_id: Primary key of the target :class:`Episode`.
        title: Short identifier used in the artifact title (e.g. ``"expert-interview"``).
        content: The research text to store.

    Returns:
        The newly created or updated :class:`EpisodeArtifact`.
    """
    episode = Episode.objects.get(pk=episode_id)

    artifact_title = f"p2-{title}"

    artifact, created = EpisodeArtifact.objects.update_or_create(
        episode=episode,
        title=artifact_title,
        defaults={
            "content": content,
            "description": f"Manually added research: {title}.",
            "workflow_context": "Research Gathering",
        },
    )

    action = "Created" if created else "Updated"
    logger.info("%s %s artifact for episode %s", action, artifact_title, episode_id)
    return artifact
