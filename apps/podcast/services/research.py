"""Run external research tools and persist results as EpisodeArtifact records."""

from __future__ import annotations

import logging
import os
from pathlib import Path

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
# Grok
# ---------------------------------------------------------------------------


def run_grok_research(episode_id: int, prompt: str) -> EpisodeArtifact:
    """Call Grok Deep Research API and save results as ``p2-grok``.

    Uses :func:`apps.podcast.tools.grok_deep_research.run_grok_research`.

    If the ``GROK_API_KEY`` environment variable is missing, this function
    logs a warning and creates a "skipped" artifact. The pipeline continues
    with other research sources.

    Args:
        episode_id: Primary key of the target :class:`Episode`.
        prompt: The research query to send to Grok.

    Returns:
        The ``p2-grok`` :class:`EpisodeArtifact` (either with research
        results or a "skipped" / "failed" message).

    NOTE: Service function added per plan issue #231, but not yet wired into
    ``apps/podcast/tasks.py`` as a ``step_grok_research`` pipeline task. Wiring
    deferred to follow-up issue #236 to avoid touching fan-in orchestration in
    the error-surfacing PR. Until then, ``p2-grok`` artifacts are not produced
    by the automated pipeline; this function can be called directly in shell
    or tests.
    """
    episode = Episode.objects.get(pk=episode_id)

    # Check for API key before attempting research
    if not os.getenv("GROK_API_KEY"):
        logger.warning(
            "GROK_API_KEY not found in environment. Skipping Grok "
            "research for episode %s. This is optional and the pipeline will "
            "continue with other research sources.",
            episode_id,
        )
        artifact, _ = EpisodeArtifact.objects.update_or_create(
            episode=episode,
            title="p2-grok",
            defaults={
                "content": "[SKIPPED: GROK_API_KEY not configured]",
                "description": "Grok Deep Research (skipped - API key missing).",
                "workflow_context": "Research Gathering",
                "metadata": {"skipped": True, "reason": "API key not configured"},
            },
        )
        return artifact

    context = _get_episode_context(episode)
    full_prompt = (
        f"Episode: {episode.title}\n\nContext:\n{context}\n\nResearch query:\n{prompt}"
    )

    from apps.podcast.tools.grok_deep_research import extract_metadata
    from apps.podcast.tools.grok_deep_research import run_grok_research as _grok

    content_text, response_data = _grok(prompt=full_prompt, verbose=False)

    if content_text is None or content_text == "":
        error_status = response_data.get("_error_status") if response_data else None
        error_message = response_data.get("_error_message") if response_data else None
        error_body = response_data.get("_error_body") if response_data else None

        if error_status:
            logger.warning(
                "Grok research API error %s for episode %s: %s",
                error_status,
                episode_id,
                error_message,
            )
            content = f"[FAILED: Grok API {error_status} - {error_message}]"
            description = f"Grok Deep Research (failed - API returned {error_status})."
            metadata = {"error": str(error_body or error_message)}
        else:
            logger.warning(
                "Grok research returned no content for episode %s. API response: %s",
                episode_id,
                response_data if response_data else "empty",
            )
            content = "[FAILED: Grok API returned empty content]"
            description = "Grok Deep Research (failed - empty content)."
            metadata = {"error": "API returned no content"}

        artifact, _ = EpisodeArtifact.objects.update_or_create(
            episode=episode,
            title="p2-grok",
            defaults={
                "content": content,
                "description": description,
                "workflow_context": "Research Gathering",
                "metadata": metadata,
            },
        )
        return artifact

    metadata = extract_metadata(response_data) if response_data else {}

    artifact, created = EpisodeArtifact.objects.update_or_create(
        episode=episode,
        title="p2-grok",
        defaults={
            "content": content_text,
            "description": "Grok Deep Research output.",
            "workflow_context": "Research Gathering",
            "metadata": metadata,
        },
    )

    action = "Created" if created else "Updated"
    logger.info("%s p2-grok artifact for episode %s", action, episode_id)
    return artifact


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
        f"Episode: {episode.title}\n\nContext:\n{context}\n\nResearch query:\n{prompt}"
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
        error_status = response_data.get("_error_status") if response_data else None
        error_message = response_data.get("_error_message") if response_data else None
        error_body = response_data.get("_error_body") if response_data else None

        if error_status:
            logger.warning(
                "Perplexity research API error %s for episode %s: %s",
                error_status,
                episode_id,
                error_message,
            )
            content = f"[FAILED: Perplexity API {error_status} - {error_message}]"
            description = (
                f"Perplexity Deep Research (failed - API returned {error_status})."
            )
            metadata = {"error": str(error_body or error_message)}
        else:
            logger.warning(
                "Perplexity research returned no content for episode %s. API response: %s",
                episode_id,
                response_data if response_data else "empty",
            )
            content = "[FAILED: Perplexity API returned empty content]"
            description = "Perplexity Deep Research (failed - empty content)."
            metadata = {"error": "API returned no content"}

        artifact, _ = EpisodeArtifact.objects.update_or_create(
            episode=episode,
            title="p2-perplexity",
            defaults={
                "content": content,
                "description": description,
                "workflow_context": "Research Gathering",
                "metadata": metadata,
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
        f"Episode: {episode.title}\n\nContext:\n{context}\n\nResearch query:\n{prompt}"
    )

    # Call the existing GPT-Researcher tool (async → sync bridge)
    from apps.podcast.tools.gpt_researcher_run import run_research

    content_text, response_data = async_to_sync(run_research)(
        prompt=full_prompt,
        verbose=False,
    )

    if content_text is None or content_text == "":
        error_message = response_data.get("_error_message") if response_data else None
        error_type = response_data.get("_error_type") if response_data else None

        if error_message:
            logger.warning(
                "GPT-Researcher failed for episode %s: %s (%s)",
                episode_id,
                error_message,
                error_type,
            )
            content = (
                f"[FAILED: GPT-Researcher {error_type or 'Error'} - {error_message}]"
            )
            description = "GPT-Researcher (failed - exception during research)."
            metadata = {
                "error": error_message,
                "error_type": error_type,
            }
        else:
            logger.warning(
                "GPT-Researcher returned no content for episode %s", episode_id
            )
            content = "[FAILED: GPT-Researcher returned empty content]"
            description = "GPT-Researcher (failed - empty content)."
            metadata = {"error": "No content returned"}

        artifact, _ = EpisodeArtifact.objects.update_or_create(
            episode=episode,
            title="p2-chatgpt",
            defaults={
                "content": content,
                "description": description,
                "workflow_context": "Research Gathering",
                "metadata": metadata,
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
        f"Episode: {episode.title}\n\nContext:\n{context}\n\nResearch query:\n{prompt}"
    )

    # Call the existing Gemini tool (returns tuple[str | None, dict])
    from apps.podcast.tools.gemini_deep_research import run_gemini_research as _gemini

    content_text, response_data = _gemini(
        prompt=full_prompt,
        verbose=False,
    )

    if content_text is None or content_text == "":
        error_status = response_data.get("_error_status") if response_data else None
        error_message = response_data.get("_error_message") if response_data else None
        error_body = response_data.get("_error_body") if response_data else None

        if error_status:
            logger.warning(
                "Gemini research API error %s for episode %s: %s",
                error_status,
                episode_id,
                error_message,
            )
            content = f"[FAILED: Gemini API {error_status} - {error_message}]"
            description = (
                f"Gemini Deep Research (failed - API returned {error_status})."
            )
            metadata = {"error": str(error_body or error_message)}
        else:
            logger.warning(
                "Gemini research returned no content for episode %s (API error or "
                "empty response). This is optional and the pipeline will continue "
                "with other research sources.",
                episode_id,
            )
            content = "[FAILED: Gemini API returned empty content]"
            description = "Gemini Deep Research (failed - empty content)."
            metadata = {"error": "API returned no content"}

        artifact, _ = EpisodeArtifact.objects.update_or_create(
            episode=episode,
            title="p2-gemini",
            defaults={
                "content": content,
                "description": description,
                "workflow_context": "Research Gathering",
                "metadata": metadata,
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
        f"Episode: {episode.title}\n\nContext:\n{context}\n\nResearch query:\n{prompt}"
    )

    from apps.podcast.tools.together_deep_research import (
        run_together_research as _together,
    )

    content_text, metadata = _together(
        prompt=full_prompt,
        verbose=False,
    )

    if content_text is None or content_text == "":
        error_status = metadata.get("_error_status") if metadata else None
        error_message = metadata.get("_error_message") if metadata else None

        if error_status:
            logger.warning(
                "Together research failed for episode %s: %s - %s",
                episode_id,
                error_status,
                error_message,
            )
            content = f"[FAILED: Together {error_status} - {error_message}]"
            description = f"Together Open Deep Research (failed - {error_status})."
            fail_metadata = {
                "error": metadata.get("error", str(error_message)),
                **{k: v for k, v in metadata.items() if not k.startswith("_")},
            }
        else:
            logger.warning(
                "Together research returned no content for episode %s", episode_id
            )
            content = "[FAILED: Together returned empty content]"
            description = "Together Open Deep Research (failed - empty content)."
            fail_metadata = {"error": "No content returned"}

        artifact, _ = EpisodeArtifact.objects.update_or_create(
            episode=episode,
            title="p2-together",
            defaults={
                "content": content,
                "description": description,
                "workflow_context": "Research Gathering",
                "metadata": fail_metadata,
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
        f"Episode: {episode.title}\n\nContext:\n{context}\n\nResearch query:\n{prompt}"
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
            "Claude research failed for episode %s: %s %s. This is optional "
            "and the pipeline will continue with other research sources.",
            episode_id,
            type(e).__name__,
            str(e),
        )
        artifact, _ = EpisodeArtifact.objects.update_or_create(
            episode=episode,
            title="p2-claude",
            defaults={
                "content": f"[FAILED: Claude {type(e).__name__} - {str(e)}]",
                "description": "Claude multi-agent deep research (failed - error).",
                "workflow_context": "Research Gathering",
                "metadata": {
                    "error": str(e),
                    "error_type": type(e).__name__,
                },
            },
        )
        return artifact


# ---------------------------------------------------------------------------
# MiroFish (swarm intelligence simulation)
# ---------------------------------------------------------------------------


def run_mirofish_research(episode_id: int, prompt: str) -> EpisodeArtifact:
    """Call MiroFish swarm intelligence API and save results as ``p2-mirofish``.

    MiroFish provides *perspective-oriented* research: stakeholder reaction
    modeling, prediction generation, counter-argument stress-testing, and
    audience reception simulation.  This complements the factual web-sourced
    research from other tools (Perplexity, Gemini, etc.).

    The MiroFish service runs as a separate sidecar (Docker or local process).
    If the service is unavailable or the ``MIROFISH_API_URL`` environment
    variable is not set, this function creates a ``[SKIPPED]`` artifact and
    the pipeline continues with other research sources.

    Args:
        episode_id: Primary key of the target :class:`Episode`.
        prompt: The research/simulation query to send to MiroFish.

    Returns:
        The ``p2-mirofish`` :class:`EpisodeArtifact` (either with simulation
        results or a "skipped" message).
    """
    import os

    episode = Episode.objects.get(pk=episode_id)

    # Check for API URL configuration
    api_url = os.getenv("MIROFISH_API_URL")
    if not api_url:
        logger.warning(
            "MIROFISH_API_URL not found in environment. Skipping MiroFish "
            "research for episode %s. This is optional and the pipeline will "
            "continue with other research sources.",
            episode_id,
        )
        artifact, _ = EpisodeArtifact.objects.update_or_create(
            episode=episode,
            title="p2-mirofish",
            defaults={
                "content": "[SKIPPED: MIROFISH_API_URL not configured]",
                "description": "MiroFish swarm intelligence (skipped - API URL missing).",
                "workflow_context": "Research Gathering",
                "metadata": {"skipped": True, "reason": "API URL not configured"},
            },
        )
        return artifact

    # Check service health before attempting a long simulation
    from apps.podcast.tools.mirofish_research import check_health

    if not check_health(api_url):
        logger.warning(
            "MiroFish service at %s is not reachable. Skipping MiroFish "
            "research for episode %s. This is optional and the pipeline will "
            "continue with other research sources.",
            api_url,
            episode_id,
        )
        artifact, _ = EpisodeArtifact.objects.update_or_create(
            episode=episode,
            title="p2-mirofish",
            defaults={
                "content": "[SKIPPED: MiroFish service unreachable]",
                "description": "MiroFish swarm intelligence (skipped - service down).",
                "workflow_context": "Research Gathering",
                "metadata": {
                    "skipped": True,
                    "reason": "Service unreachable",
                    "api_url": api_url,
                },
            },
        )
        return artifact

    # Build the perspective-oriented prompt with episode context
    context = _get_episode_context(episode)

    # The prompt template emphasises perspective simulation over factual search.
    # MiroFish's unique strength is multi-agent stakeholder modeling, prediction
    # generation, and counter-argument stress-testing -- not duplicating the
    # factual web search that Perplexity/Gemini already provide.
    full_prompt = (
        f"Episode: {episode.title}\n\n"
        f"Context:\n{context}\n\n"
        f"Research query:\n{prompt}\n\n"
        "SIMULATION DIRECTIVE: You are running a multi-agent swarm simulation. "
        "Do NOT search the web or summarise existing articles. Instead:\n"
        "1. Simulate a diverse panel of stakeholders reacting to the key claims "
        "in this episode topic.\n"
        "2. Generate evidence-based predictions about likely outcomes and "
        "consequences.\n"
        "3. Produce counter-arguments and identify blind spots the host should "
        "address.\n"
        "4. Model audience reception: what will resonate, what will be "
        "controversial, what needs more explanation.\n"
        "Focus on 'what would people think/do/say' -- not 'what are the facts'."
    )

    try:
        from apps.podcast.tools.mirofish_research import run_mirofish_simulation

        content_text, metadata = run_mirofish_simulation(
            prompt=full_prompt,
            api_url=api_url,
            verbose=False,
        )
    except Exception as exc:
        logger.warning(
            "MiroFish research failed for episode %s: %s. Skipping MiroFish "
            "research. This is optional and the pipeline will continue with "
            "other research sources.",
            episode_id,
            str(exc),
        )
        artifact, _ = EpisodeArtifact.objects.update_or_create(
            episode=episode,
            title="p2-mirofish",
            defaults={
                "content": f"[SKIPPED: MiroFish research failed - {str(exc)}]",
                "description": "MiroFish swarm intelligence (skipped - error).",
                "workflow_context": "Research Gathering",
                "metadata": {
                    "skipped": True,
                    "reason": f"Exception raised: {str(exc)}",
                    "error_type": type(exc).__name__,
                },
            },
        )
        return artifact

    if content_text is None or content_text == "":
        reason = metadata.get("error", "empty response")
        logger.warning(
            "MiroFish research returned no content for episode %s (%s). "
            "Skipping MiroFish research. This is optional and the pipeline "
            "will continue with other research sources.",
            episode_id,
            reason,
        )
        artifact, _ = EpisodeArtifact.objects.update_or_create(
            episode=episode,
            title="p2-mirofish",
            defaults={
                "content": f"[SKIPPED: MiroFish returned no content - {reason}]",
                "description": "MiroFish swarm intelligence (skipped - no content).",
                "workflow_context": "Research Gathering",
                "metadata": {"skipped": True, "reason": reason, **metadata},
            },
        )
        return artifact

    # Ensure metadata includes the skipped=False flag for consistency
    metadata["skipped"] = False

    artifact, created = EpisodeArtifact.objects.update_or_create(
        episode=episode,
        title="p2-mirofish",
        defaults={
            "content": content_text,
            "description": "MiroFish swarm intelligence simulation output.",
            "workflow_context": "Research Gathering",
            "metadata": metadata,
        },
    )

    action = "Created" if created else "Updated"
    logger.info("%s p2-mirofish artifact for episode %s", action, episode_id)
    return artifact


# ---------------------------------------------------------------------------
# File-based research (document parsing)
# ---------------------------------------------------------------------------


def add_file_research(
    episode_id: int, title: str, file_path: str | Path
) -> EpisodeArtifact:
    """Parse a document file and store the extracted text as a research artifact.

    Uses :func:`~apps.common.utilities.document_parser.parse_document` to
    extract text from binary formats (PDF, DOCX, ODT) and then delegates to
    :func:`add_manual_research` for storage.

    If parsing fails, the artifact is still created with empty content and
    metadata noting the failure, so the pipeline can continue.

    Args:
        episode_id: Primary key of the target :class:`Episode`.
        title: Short identifier used in the artifact title (e.g. ``"whitepaper"``).
        file_path: Path to the document file.

    Returns:
        The newly created or updated :class:`EpisodeArtifact`.
    """
    from apps.common.utilities.document_parser import parse_document

    file_path = Path(file_path)

    try:
        content = parse_document(file_path)
    except (FileNotFoundError, ValueError) as exc:
        logger.warning(
            "Failed to parse document %s for episode %s: %s",
            file_path,
            episode_id,
            exc,
        )
        content = ""

    if not content:
        # Create artifact with metadata noting the parse failure so the
        # pipeline can continue gracefully.
        episode = Episode.objects.get(pk=episode_id)
        artifact, _ = EpisodeArtifact.objects.update_or_create(
            episode=episode,
            title=f"p2-{title}",
            defaults={
                "content": "",
                "description": f"File research: {title} (parse failed or empty).",
                "workflow_context": "Research Gathering",
                "metadata": {
                    "file_path": str(file_path),
                    "parse_failed": True,
                },
            },
        )
        return artifact

    return add_manual_research(episode_id, title, content)


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
