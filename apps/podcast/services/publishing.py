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


def generate_cover_art(episode_id: int) -> str | None:
    """Generate cover art, brand it, upload to storage, and save URL.

    Pipeline:
        1. Load Episode and extract report text for prompt generation.
        2. Generate an AI image via OpenRouter (Gemini).
        3. Apply Yudame Research branding overlay.
        4. Upload to Supabase storage.
        5. Save URL to ``Episode.cover_image_url``.
        6. Create ``cover-art`` artifact with generation metadata.

    If ``OPENROUTER_API_KEY`` is not set, logs a warning, creates a
    placeholder artifact, and returns ``None`` (graceful degradation).

    Args:
        episode_id: Primary key of the :class:`Episode`.

    Returns:
        The URL of the uploaded cover image, or ``None`` if skipped.

    Raises:
        Episode.DoesNotExist: If no episode matches *episode_id*.
    """
    import os

    from apps.common.services.storage import store_file
    from apps.podcast.tools.add_logo_watermark import apply_branding
    from apps.podcast.tools.generate_cover import (
        generate_cover_image,
        generate_prompt_from_report,
    )

    episode = Episode.objects.select_related("podcast").get(pk=episode_id)
    logger.info("generate_cover_art: episode=%s", episode.title)

    # Check for API key (graceful degradation)
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        logger.warning(
            "generate_cover_art: OPENROUTER_API_KEY not set, skipping for episode=%s",
            episode.title,
        )
        EpisodeArtifact.objects.update_or_create(
            episode=episode,
            title="cover-art",
            defaults={
                "content": "[SKIPPED: OPENROUTER_API_KEY not configured]",
                "description": "Cover art skipped (no API key).",
                "workflow_context": "Publishing Assets",
                "metadata": {"skipped": True, "reason": "missing_api_key"},
            },
        )
        return None

    # Get report text for prompt generation
    report_text = episode.report_text or ""
    if not report_text:
        # Fall back to content_plan artifact
        try:
            plan_artifact = EpisodeArtifact.objects.get(
                episode=episode, title="content_plan"
            )
            report_text = plan_artifact.content or ""
        except EpisodeArtifact.DoesNotExist:
            pass

    if not report_text:
        logger.warning(
            "generate_cover_art: no report_text or content_plan for episode=%s, "
            "using title only",
            episode.title,
        )

    # Generate prompt from report
    prompt = (
        generate_prompt_from_report(report_text, episode.title)
        if report_text
        else (
            f'Modern podcast episode cover art for "{episode.title}": '
            "Clean, professional, abstract visualization. "
            "Color palette: Light warm cream (#F5F1E8) background with black "
            "and salmon (#E8B4A8) accents. "
            "Square format (1024x1024px) with space for text overlay. "
            "No text in the image."
        )
    )

    # Generate image
    image_bytes = generate_cover_image(prompt, api_key)

    # Apply branding
    series_name = episode.podcast.title if episode.podcast else None
    branded_bytes = apply_branding(image_bytes, series_text=series_name)

    # Upload to storage
    storage_key = f"podcast/{episode.podcast.slug}/{episode.slug}/cover.png"
    is_private = episode.podcast.uses_private_bucket
    cover_url = store_file(
        storage_key, branded_bytes, "image/png", public=not is_private
    )

    # Save URL to episode
    if is_private:
        episode.cover_image_url = storage_key
    else:
        episode.cover_image_url = cover_url
    episode.save(update_fields=["cover_image_url"])

    # Create artifact with metadata
    EpisodeArtifact.objects.update_or_create(
        episode=episode,
        title="cover-art",
        defaults={
            "content": json.dumps(
                {
                    "prompt": prompt,
                    "storage_key": storage_key,
                    "cover_url": cover_url,
                }
            ),
            "description": "AI-generated episode cover art with branding overlay.",
            "workflow_context": "Publishing Assets",
            "metadata": {
                "skipped": False,
                "image_size_bytes": len(branded_bytes),
                "storage_key": storage_key,
            },
        },
    )

    logger.info(
        "generate_cover_art: uploaded cover for episode=%s url=%s",
        episode.title,
        cover_url,
    )
    return cover_url


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
