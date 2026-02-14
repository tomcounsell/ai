"""DB-aware wrappers around the write_synthesis and plan_episode Named AI Tools.

Each function takes an episode_id, reads from the database, delegates to the
underlying AI tool, and writes results back.
"""

from __future__ import annotations

import json
import logging

from apps.podcast.models import Episode, EpisodeArtifact
from apps.podcast.services.plan_episode import EpisodePlan
from apps.podcast.services.plan_episode import plan_episode as _plan_episode
from apps.podcast.services.write_synthesis import write_synthesis as _write_synthesis

logger = logging.getLogger(__name__)


def synthesize_report(episode_id: int) -> str:
    """Read p3-briefing + p2-* artifacts, call write_synthesis AI tool, save to Episode.report_text.

    Steps:
        1. Load Episode by id.
        2. Load the ``p3-briefing`` artifact for the briefing text.
        3. Collect every ``p2-*`` artifact into a ``{title: content}`` dict.
        4. Invoke :func:`write_synthesis` with the gathered inputs.
        5. Persist the report narrative to ``Episode.report_text``.
        6. Persist cited sources to ``Episode.sources_text`` when available.

    Args:
        episode_id: Primary key of the :class:`Episode`.

    Returns:
        The full report text saved to the episode.

    Raises:
        Episode.DoesNotExist: If no episode matches *episode_id*.
        EpisodeArtifact.DoesNotExist: If the ``p3-briefing`` artifact is missing.
    """
    episode = Episode.objects.select_related("podcast").get(pk=episode_id)

    briefing_artifact = EpisodeArtifact.objects.get(
        episode=episode, title="p3-briefing"
    )
    briefing = briefing_artifact.content

    research_artifacts = EpisodeArtifact.objects.filter(
        episode=episode, title__startswith="p2-"
    )
    research_texts: dict[str, str] = {
        artifact.title: artifact.content for artifact in research_artifacts
    }

    logger.info(
        "synthesize_report: episode=%s research_sources=%d",
        episode.title,
        len(research_texts),
    )

    result = _write_synthesis(
        briefing=briefing,
        research_texts=research_texts,
        episode_title=episode.title,
    )

    # Build full report text from structured sections
    report_parts: list[str] = [f"# {result.title}\n"]
    for section in result.sections:
        report_parts.append(f"## {section.heading}\n")
        report_parts.append(section.content)
        if section.listener_implications:
            report_parts.append(
                f"\n**What this means for listeners:** {section.listener_implications}"
            )
        report_parts.append("")  # blank line between sections
    if result.core_takeaways:
        report_parts.append("## Core Takeaways\n")
        for i, takeaway in enumerate(result.core_takeaways, 1):
            report_parts.append(f"{i}. {takeaway}")
        report_parts.append("")

    report_text = "\n".join(report_parts)

    episode.report_text = report_text
    if result.sources_cited:
        episode.sources_text = "\n".join(result.sources_cited)
    episode.save(update_fields=["report_text", "sources_text"])

    logger.info(
        "synthesize_report: saved report (%d chars) for episode=%s",
        len(report_text),
        episode.title,
    )
    return report_text


def plan_episode_content(episode_id: int) -> EpisodeArtifact:
    """Read report_text + p3-briefing, call plan_episode AI tool, save content_plan artifact.

    Steps:
        1. Load Episode by id, reading ``report_text``.
        2. Load the ``p3-briefing`` artifact for the briefing.
        3. Invoke :func:`plan_episode` with report, briefing, title, and series name.
        4. Persist the result as an ``EpisodeArtifact`` with title ``content_plan``.

    Args:
        episode_id: Primary key of the :class:`Episode`.

    Returns:
        The created or updated ``content_plan`` :class:`EpisodeArtifact`.

    Raises:
        Episode.DoesNotExist: If no episode matches *episode_id*.
        EpisodeArtifact.DoesNotExist: If the ``p3-briefing`` artifact is missing.
        ValueError: If ``Episode.report_text`` is empty.
    """
    episode = Episode.objects.select_related("podcast").get(pk=episode_id)

    if not episode.report_text:
        raise ValueError(
            f"Episode '{episode.title}' has no report_text. "
            "Run synthesize_report first."
        )

    briefing_artifact = EpisodeArtifact.objects.get(
        episode=episode, title="p3-briefing"
    )

    logger.info("plan_episode_content: episode=%s", episode.title)

    result: EpisodePlan = _plan_episode(
        report=episode.report_text,
        briefing=briefing_artifact.content,
        episode_title=episode.title,
        series_name=episode.podcast.title,
    )

    # Serialize EpisodePlan to readable content
    content = json.dumps(result.model_dump(), indent=2)

    artifact, _created = EpisodeArtifact.objects.update_or_create(
        episode=episode,
        title="content_plan",
        defaults={
            "content": content,
            "description": "Structured episode plan for NotebookLM audio generation.",
            "workflow_context": "Synthesis -> Episode Planning",
            "metadata": {
                "core_question": result.metadata.core_question,
                "position": result.metadata.position,
                "evidence_status": result.metadata.evidence_status,
                "content_density": result.metadata.content_density,
                "hook_type": result.toolkit_selections.hook_type,
                "counterpoint_count": len(result.counterpoint_moments),
                "structure_sections": len(result.structure_map),
            },
        },
    )

    logger.info(
        "plan_episode_content: saved content_plan artifact (created=%s) for episode=%s",
        _created,
        episode.title,
    )
    return artifact
