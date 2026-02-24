"""DB-aware services for episode publishing: cover art, metadata, companions.

Each function takes an episode_id, reads from the database, delegates to the
underlying tool or AI service, and writes results back.
"""

from __future__ import annotations

import json
import logging

from apps.podcast.models import Episode, EpisodeArtifact
from apps.podcast.services.write_metadata import EpisodeMetadata
from apps.podcast.services.write_metadata import write_metadata as _write_metadata

logger = logging.getLogger(__name__)


def generate_cover_art(episode_id: int) -> str:
    """Generate cover art and upload to storage. Returns URL.

    Integration point: ``apps/podcast/tools/cover_art.py``

    The cover art pipeline in ``tools/cover_art.py`` is a CLI tool that calls
    ``tools/generate_cover.py`` (AI image generation) and
    ``tools/add_logo_watermark.py`` (branding overlay). This service wraps
    that pipeline for DB-driven workflows:

        1. Read Episode and the ``content_plan`` artifact for metadata.
        2. Generate the cover art image (stubbed -- see TODO below).
        3. Upload resulting bytes via :func:`store_file`.
        4. Save URL to ``Episode.cover_image_url``.

    TODO: Integrate with ``tools/cover_art.py`` programmatically.  The CLI
    tool currently operates on filesystem paths.  A future refactor should
    extract the generation logic into importable functions that accept text
    inputs and return image bytes.

    Args:
        episode_id: Primary key of the :class:`Episode`.

    Returns:
        The public URL of the uploaded cover image.

    Raises:
        Episode.DoesNotExist: If no episode matches *episode_id*.
        NotImplementedError: Always, until the generation logic is extracted
            from the CLI tool.
    """
    episode = Episode.objects.select_related("podcast").get(pk=episode_id)

    logger.info("generate_cover_art: episode=%s", episode.title)

    # TODO: Replace this stub with actual cover art generation.
    #
    # The integration path is:
    #   1. Read report_text and content_plan artifact for context.
    #   2. Call an image generation API (e.g. via tools/generate_cover.py logic).
    #   3. Apply branding overlay (e.g. via tools/add_logo_watermark.py logic).
    #   4. Collect the final PNG bytes.
    #
    # Once image_bytes are available:
    #
    #   storage_key = f"podcast/{episode.podcast.slug}/{episode.slug}/cover.png"
    #   is_private = episode.podcast.uses_private_bucket
    #   cover_url = store_file(storage_key, image_bytes, "image/png", public=not is_private)
    #   if is_private:
    #       # For restricted podcasts, store the storage key instead of the URL.
    #       # Fresh signed URLs are generated on-demand in the feed view.
    #       episode.cover_image_url = storage_key
    #   else:
    #       episode.cover_image_url = cover_url
    #   episode.save(update_fields=["cover_image_url"])
    #   return cover_url

    raise NotImplementedError(
        "Cover art generation requires extracting the CLI pipeline from "
        "tools/cover_art.py into importable functions. "
        "See the TODO in this function for the integration plan."
    )


def write_episode_metadata(episode_id: int) -> EpisodeArtifact:
    """Call write_metadata AI tool. Save as metadata artifact.

    Steps:
        1. Load Episode with ``report_text``, ``transcript``, ``chapters``.
        2. Invoke :func:`write_metadata` AI tool.
        3. Save structured result as ``EpisodeArtifact`` with title ``metadata``.
        4. Also populate ``Episode.description`` and ``Episode.show_notes``.

    Args:
        episode_id: Primary key of the :class:`Episode`.

    Returns:
        The created or updated ``metadata`` :class:`EpisodeArtifact`.

    Raises:
        Episode.DoesNotExist: If no episode matches *episode_id*.
        ValueError: If required episode fields are empty.
    """
    episode = Episode.objects.select_related("podcast").get(pk=episode_id)

    if not episode.report_text:
        raise ValueError(
            f"Episode '{episode.title}' has no report_text. "
            "Run synthesize_report first."
        )
    if not episode.transcript:
        raise ValueError(
            f"Episode '{episode.title}' has no transcript. "
            "Run transcribe_audio first."
        )

    logger.info("write_episode_metadata: episode=%s", episode.title)

    result: EpisodeMetadata = _write_metadata(
        report=episode.report_text,
        transcript=episode.transcript,
        chapters_json=episode.chapters or "[]",
        episode_title=episode.title,
    )

    # Serialize to JSON for artifact storage
    content = json.dumps(result.model_dump(), indent=2)

    # Build show notes from metadata
    show_notes_parts: list[str] = []
    if result.what_youll_learn:
        show_notes_parts.append("**What you'll learn:**")
        for item in result.what_youll_learn:
            show_notes_parts.append(f"- {item}")
        show_notes_parts.append("")
    if result.key_timestamps:
        show_notes_parts.append("**Timestamps:**")
        for ts in result.key_timestamps:
            show_notes_parts.append(f"- {ts.time} - {ts.description}")
        show_notes_parts.append("")
    if result.resources:
        show_notes_parts.append("**Resources:**")
        for resource in result.resources:
            show_notes_parts.append(f"- [{resource.title}]({resource.url})")
        show_notes_parts.append("")

    artifact, _created = EpisodeArtifact.objects.update_or_create(
        episode=episode,
        title="metadata",
        defaults={
            "content": content,
            "description": "Episode publishing metadata: description, keywords, timestamps, CTAs.",
            "workflow_context": "Audio Processing -> Publishing",
            "metadata": {
                "keywords": result.keywords,
                "primary_cta": result.primary_cta,
                "resource_count": len(result.resources),
                "timestamp_count": len(result.key_timestamps),
            },
        },
    )

    # Update episode description and show notes
    episode.description = result.description
    if show_notes_parts:
        episode.show_notes = "\n".join(show_notes_parts)
    episode.save(update_fields=["description", "show_notes"])

    logger.info(
        "write_episode_metadata: saved metadata artifact (created=%s) for episode=%s",
        _created,
        episode.title,
    )
    return artifact


def generate_companions(episode_id: int) -> list[EpisodeArtifact]:
    """Generate companion resources. Save as artifacts.

    Integration point: ``apps/podcast/tools/generate_companion_resources.py``

    The companion resource generation in ``tools/generate_companion_resources.py``
    extracts key sections, takeaways, action items, frameworks, and statistics
    from report text to produce three companion documents. This service wraps
    that logic for DB-driven workflows.

    Steps:
        1. Read ``Episode.report_text`` and ``Episode.sources_text``.
        2. Extract content using the same logic as the CLI tool.
        3. Generate summary, checklist, and frameworks documents.
        4. Save each as an ``EpisodeArtifact``.

    Args:
        episode_id: Primary key of the :class:`Episode`.

    Returns:
        List of created/updated :class:`EpisodeArtifact` records:
        ``companion-summary``, ``companion-checklist``, ``companion-frameworks``.

    Raises:
        Episode.DoesNotExist: If no episode matches *episode_id*.
        ValueError: If ``Episode.report_text`` is empty.
    """
    from apps.podcast.tools.generate_companion_resources import (
        extract_action_items,
        extract_frameworks,
        extract_key_sections,
        extract_statistics,
        extract_takeaways,
        generate_checklist,
        generate_framework_doc,
        generate_summary,
    )

    episode = Episode.objects.get(pk=episode_id)

    if not episode.report_text:
        raise ValueError(
            f"Episode '{episode.title}' has no report_text. "
            "Run synthesize_report first."
        )

    logger.info("generate_companions: episode=%s", episode.title)

    content = episode.report_text

    # Extract content elements using the existing tool functions
    sections = extract_key_sections(content)
    takeaways = extract_takeaways(content)
    actions = extract_action_items(content)
    frameworks = extract_frameworks(content)
    stats = extract_statistics(content)

    # Generate the three companion documents
    summary_text = generate_summary(
        episode.title, sections, takeaways, stats, frameworks
    )
    checklist_text = generate_checklist(episode.title, actions, takeaways)
    frameworks_text = generate_framework_doc(episode.title, frameworks, content)

    artifacts: list[EpisodeArtifact] = []

    companion_docs = [
        (
            "companion-summary",
            summary_text,
            "One-page episode summary with key takeaways and statistics.",
        ),
        (
            "companion-checklist",
            checklist_text,
            "Actionable checklist extracted from the episode content.",
        ),
        (
            "companion-frameworks",
            frameworks_text,
            "Framework and model documentation for visualization.",
        ),
    ]

    for title, text, description in companion_docs:
        artifact, _created = EpisodeArtifact.objects.update_or_create(
            episode=episode,
            title=title,
            defaults={
                "content": text,
                "description": description,
                "workflow_context": "Publishing -> Companion Resources",
            },
        )
        artifacts.append(artifact)

    # Also save to episode.companion_resources JSONField for quick access
    episode.companion_resources = {
        "summary": summary_text,
        "checklist": checklist_text,
        "frameworks": frameworks_text,
    }
    episode.save(update_fields=["companion_resources"])

    logger.info(
        "generate_companions: saved %d companion artifacts for episode=%s",
        len(artifacts),
        episode.title,
    )
    return artifacts


def publish_episode(episode_id: int) -> Episode:
    """Mark episode as complete and published.

    Sets ``Episode.status`` to ``"complete"`` and calls the
    :meth:`~apps.common.behaviors.Publishable.publish` method to set
    ``published_at`` to now.

    Args:
        episode_id: Primary key of the :class:`Episode`.

    Returns:
        The updated :class:`Episode` instance.

    Raises:
        Episode.DoesNotExist: If no episode matches *episode_id*.
    """
    episode = Episode.objects.get(pk=episode_id)

    logger.info("publish_episode: episode=%s", episode.title)

    episode.status = "complete"
    episode.publish()  # Sets published_at via the Publishable mixin
    episode.save(update_fields=["status", "published_at", "unpublished_at"])

    logger.info(
        "publish_episode: published episode=%s at %s",
        episode.title,
        episode.published_at,
    )
    return episode
