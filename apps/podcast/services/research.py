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

    If the ``PERPLEXITY_API_KEY`` environment variable is missing, this function
    logs a warning and creates a "skipped" artifact. The pipeline continues with
    other research sources.

    Args:
        episode_id: Primary key of the target :class:`Episode`.
        prompt: The research query to send to Perplexity.

    Returns:
        The ``p2-perplexity`` :class:`EpisodeArtifact` (either with research
        results or a "skipped" message).
    """
    import os

    episode = Episode.objects.get(pk=episode_id)

    # Check for API key before attempting research
    if not os.getenv("PERPLEXITY_API_KEY"):
        logger.warning(
            "PERPLEXITY_API_KEY not found in environment. Skipping Perplexity "
            "research for episode %s. This is optional and the pipeline will "
            "continue with other research sources.",
            episode_id,
        )
        artifact, _ = EpisodeArtifact.objects.update_or_create(
            episode=episode,
            title="p2-perplexity",
            defaults={
                "content": "[SKIPPED: PERPLEXITY_API_KEY not configured]",
                "description": "Perplexity Deep Research (skipped - API key missing).",
                "workflow_context": "Research Gathering",
                "metadata": {"skipped": True, "reason": "API key not configured"},
            },
        )
        return artifact
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

    if content_text is None or content_text == "":
        logger.warning(
            "Perplexity research returned no content for episode %s. "
            "API response: %s",
            episode_id,
            response_data if response_data else "empty",
        )
        artifact, _ = EpisodeArtifact.objects.update_or_create(
            episode=episode,
            title="p2-perplexity",
            defaults={
                "content": "[SKIPPED: Perplexity API returned no content]",
                "description": "Perplexity Deep Research (skipped - no content returned).",
                "workflow_context": "Research Gathering",
                "metadata": {"skipped": True, "reason": "API returned no content"},
            },
        )
        return artifact

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

    if content_text is None or content_text == "":
        logger.warning("GPT-Researcher returned no content for episode %s", episode_id)
        artifact, _ = EpisodeArtifact.objects.update_or_create(
            episode=episode,
            title="p2-chatgpt",
            defaults={
                "content": "[SKIPPED: GPT-Researcher returned no content]",
                "description": "GPT-Researcher (skipped - no content returned).",
                "workflow_context": "Research Gathering",
                "metadata": {"skipped": True, "reason": "No content returned"},
            },
        )
        return artifact

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

    If the ``GEMINI_API_KEY`` environment variable is missing or if the API
    returns an error (e.g., quota exceeded), this function logs a warning and
    creates a "skipped" artifact. The pipeline continues with other research
    sources.

    Args:
        episode_id: Primary key of the target :class:`Episode`.
        prompt: The research query to send to Gemini.

    Returns:
        The ``p2-gemini`` :class:`EpisodeArtifact` (either with research
        results or a "skipped" message).
    """
    import os

    episode = Episode.objects.get(pk=episode_id)

    # Check for API key before attempting research
    if not os.getenv("GEMINI_API_KEY"):
        logger.warning(
            "GEMINI_API_KEY not found in environment. Skipping Gemini "
            "research for episode %s. This is optional and the pipeline will "
            "continue with other research sources.",
            episode_id,
        )
        artifact, _ = EpisodeArtifact.objects.update_or_create(
            episode=episode,
            title="p2-gemini",
            defaults={
                "content": "[SKIPPED: GEMINI_API_KEY not configured]",
                "description": "Gemini Deep Research (skipped - API key missing).",
                "workflow_context": "Research Gathering",
                "metadata": {"skipped": True, "reason": "API key not configured"},
            },
        )
        return artifact

    context = _get_episode_context(episode)

    full_prompt = (
        f"Episode: {episode.title}\n\n"
        f"Context:\n{context}\n\n"
        f"Research query:\n{prompt}"
    )

    # Call the existing Gemini tool
    from apps.podcast.tools.gemini_deep_research import GeminiQuotaError
    from apps.podcast.tools.gemini_deep_research import run_gemini_research as _gemini

    try:
        content_text = _gemini(
            prompt=full_prompt,
            verbose=False,
        )
    except GeminiQuotaError:
        logger.warning(
            "Gemini API quota exceeded for episode %s. Upgrade billing at "
            "https://aistudio.google.com/apikey",
            episode_id,
        )
        artifact, _ = EpisodeArtifact.objects.update_or_create(
            episode=episode,
            title="p2-gemini",
            defaults={
                "content": (
                    "[SKIPPED: Gemini API quota exceeded. "
                    "Upgrade billing at https://aistudio.google.com/apikey]"
                ),
                "description": "Gemini Deep Research (skipped - quota exceeded).",
                "workflow_context": "Research Gathering",
                "metadata": {"skipped": True, "reason": "quota_exceeded"},
            },
        )
        return artifact

    # Handle None response (API error, empty content, etc.)
    if content_text is None:
        logger.warning(
            "Gemini research returned no content for episode %s (API error or "
            "empty response). Skipping Gemini research. This is optional "
            "and the pipeline will continue with other research sources.",
            episode_id,
        )
        artifact, _ = EpisodeArtifact.objects.update_or_create(
            episode=episode,
            title="p2-gemini",
            defaults={
                "content": "[SKIPPED: Gemini API error or empty response]",
                "description": "Gemini Deep Research (skipped - API error).",
                "workflow_context": "Research Gathering",
                "metadata": {"skipped": True, "reason": "api_error_or_empty"},
            },
        )
        return artifact

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

    If the required API keys (``TAVILY_API_KEY`` and one of
    ``ANTHROPIC_API_KEY``, ``OPENAI_API_KEY``, or ``OPENROUTER_API_KEY``) are
    missing, this function logs a warning and creates a "skipped" artifact. The
    pipeline continues with other research sources.

    Args:
        episode_id: Primary key of the target :class:`Episode`.
        prompt: The research query to send to Together.

    Returns:
        The ``p2-together`` :class:`EpisodeArtifact` (either with research
        results or a "skipped" message).
    """
    import os

    episode = Episode.objects.get(pk=episode_id)

    # Check for required API keys before attempting research
    tavily_key = os.getenv("TAVILY_API_KEY")
    has_llm_key = any(
        [
            os.getenv("ANTHROPIC_API_KEY"),
            os.getenv("OPENAI_API_KEY"),
            os.getenv("OPENROUTER_API_KEY"),
        ]
    )

    if not tavily_key or not has_llm_key:
        missing = []
        if not tavily_key:
            missing.append("TAVILY_API_KEY")
        if not has_llm_key:
            missing.append(
                "one of ANTHROPIC_API_KEY, OPENAI_API_KEY, or OPENROUTER_API_KEY"
            )

        logger.warning(
            "Missing required API keys for Together research: %s. Skipping "
            "Together research for episode %s. This is optional and the pipeline "
            "will continue with other research sources.",
            ", ".join(missing),
            episode_id,
        )

        artifact, _ = EpisodeArtifact.objects.update_or_create(
            episode=episode,
            title="p2-together",
            defaults={
                "content": f"[SKIPPED: Missing API keys - {', '.join(missing)}]",
                "description": "Together Open Deep Research (skipped - API keys missing).",
                "workflow_context": "Research Gathering",
                "metadata": {
                    "skipped": True,
                    "reason": f"Missing API keys: {', '.join(missing)}",
                },
            },
        )
        return artifact
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

    if content_text is None or content_text == "":
        logger.warning(
            "Together research returned no content for episode %s", episode_id
        )
        artifact, _ = EpisodeArtifact.objects.update_or_create(
            episode=episode,
            title="p2-together",
            defaults={
                "content": "[SKIPPED: Together research returned no content]",
                "description": "Together Open Deep Research (skipped - no content returned).",
                "workflow_context": "Research Gathering",
                "metadata": {"skipped": True, "reason": "No content returned"},
            },
        )
        return artifact

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
# Claude (multi-agent deep research)
# ---------------------------------------------------------------------------


def run_claude_research(episode_id: int, prompt: str) -> EpisodeArtifact:
    """Call Claude deep research orchestrator and save results as ``p2-claude``.

    Uses the multi-agent deep research pipeline
    (:func:`~apps.podcast.services.claude_deep_research.deep_research`)
    which plans subtasks, runs parallel Sonnet researchers, and synthesizes
    findings into a comprehensive report.

    If the deep research call fails (e.g., validation errors, API issues), this
    function logs a warning and creates a "skipped" artifact. The pipeline
    continues with other research sources.

    Args:
        episode_id: Primary key of the target :class:`Episode`.
        prompt: The research command to send to the orchestrator.

    Returns:
        The ``p2-claude`` :class:`EpisodeArtifact` (either with research
        results or a "skipped" message).
    """
    episode = Episode.objects.get(pk=episode_id)
    context = _get_episode_context(episode)

    full_prompt = (
        f"Episode: {episode.title}\n\n"
        f"Context:\n{context}\n\n"
        f"Research query:\n{prompt}"
    )

    try:
        from apps.podcast.services.claude_deep_research import deep_research

        report = deep_research(command=full_prompt)

        # Check if report is None or invalid
        if report is None:
            logger.warning(
                "Claude research returned no report for episode %s. Skipping "
                "Claude research. This is optional and the pipeline will "
                "continue with other research sources.",
                episode_id,
            )
            artifact, _ = EpisodeArtifact.objects.update_or_create(
                episode=episode,
                title="p2-claude",
                defaults={
                    "content": "[SKIPPED: Claude research returned no report]",
                    "description": "Claude multi-agent deep research (skipped - no report).",
                    "workflow_context": "Research Gathering",
                    "metadata": {"skipped": True, "reason": "No report returned"},
                },
            )
            return artifact

        # Format the report content as markdown
        content_text = report.content
        if report.key_findings:
            content_text += "\n\n## Key Findings\n\n"
            content_text += "\n".join(f"- {f}" for f in report.key_findings)
        if report.gaps_remaining:
            content_text += "\n\n## Gaps Remaining\n\n"
            content_text += "\n".join(f"- {g}" for g in report.gaps_remaining)
        content_text += (
            f"\n\n## Confidence Assessment\n\n{report.confidence_assessment}"
        )

        metadata = {
            "sources_cited": report.sources_cited,
            "key_findings": report.key_findings,
            "confidence_assessment": report.confidence_assessment,
            "gaps_remaining": report.gaps_remaining,
        }

        artifact, created = EpisodeArtifact.objects.update_or_create(
            episode=episode,
            title="p2-claude",
            defaults={
                "content": content_text,
                "description": "Claude multi-agent deep research output.",
                "workflow_context": "Research Gathering",
                "metadata": metadata,
            },
        )

        action = "Created" if created else "Updated"
        logger.info("%s p2-claude artifact for episode %s", action, episode_id)
        return artifact

    except Exception as e:
        logger.warning(
            "Claude research failed for episode %s: %s. Skipping Claude "
            "research. This is optional and the pipeline will continue with "
            "other research sources.",
            episode_id,
            str(e),
        )
        artifact, _ = EpisodeArtifact.objects.update_or_create(
            episode=episode,
            title="p2-claude",
            defaults={
                "content": f"[SKIPPED: Claude research failed - {str(e)}]",
                "description": "Claude multi-agent deep research (skipped - error).",
                "workflow_context": "Research Gathering",
                "metadata": {
                    "skipped": True,
                    "reason": f"Exception raised: {str(e)}",
                    "error_type": type(e).__name__,
                },
            },
        )
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
