"""API views for the local audio worker.

Provides endpoints for a local worker to poll for pending audio generation
jobs and submit completed audio back to the server.
"""

from __future__ import annotations

import functools
import json
import logging

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from apps.podcast.models import Episode, EpisodeArtifact, EpisodeWorkflow
from apps.podcast.services import workflow
from apps.podcast.tasks import step_transcribe_audio

logger = logging.getLogger(__name__)


def require_worker_api_key(view_func):
    """Decorator that checks Authorization: Bearer <LOCAL_WORKER_API_KEY>."""

    @functools.wraps(view_func)
    def wrapper(request, *args, **kwargs):
        api_key = getattr(settings, "LOCAL_WORKER_API_KEY", "")
        if not api_key:
            return JsonResponse({"error": "Worker API not configured"}, status=503)
        auth_header = request.headers.get("Authorization", "")
        if auth_header != f"Bearer {api_key}":
            return JsonResponse({"error": "Unauthorized"}, status=401)
        return view_func(request, *args, **kwargs)

    return wrapper


@require_GET
@require_worker_api_key
def pending_audio(request):
    """Return episodes whose workflow is paused waiting for audio generation.

    GET /api/podcast/pending-audio/

    Returns JSON::

        {
            "episodes": [
                {
                    "id": 42,
                    "title": "Episode Title",
                    "slug": "episode-slug",
                    "podcast_slug": "podcast-slug",
                    "sources": {
                        "report.md": "...",
                        "sources.md": "...",
                        "briefing.md": "...",
                        "content_plan.md": "...",
                        "brief.md": "..."
                    }
                }
            ]
        }
    """
    workflows = EpisodeWorkflow.objects.filter(
        status="paused_for_human",
        blocked_on="audio_generation",
    ).select_related("episode", "episode__podcast")

    episodes = []
    for wf in workflows:
        episode = wf.episode
        sources = {}

        if episode.report_text:
            sources["report.md"] = episode.report_text
        if episode.sources_text:
            sources["sources.md"] = episode.sources_text

        # Load key artifacts
        artifact_mapping = {
            "p3-briefing": "briefing.md",
            "content-plan": "content_plan.md",
            "p1-brief": "brief.md",
        }
        artifacts = EpisodeArtifact.objects.filter(
            episode=episode,
            title__in=list(artifact_mapping.keys()),
        )
        for artifact in artifacts:
            filename = artifact_mapping.get(artifact.title)
            if filename and artifact.content:
                sources[filename] = artifact.content

        episodes.append(
            {
                "id": episode.id,
                "title": episode.title,
                "slug": episode.slug,
                "podcast_slug": episode.podcast.slug,
                "sources": sources,
            }
        )

    return JsonResponse({"episodes": episodes})


@csrf_exempt
@require_POST
@require_worker_api_key
def audio_callback(request, episode_id: int):
    """Receive completed audio from the local worker and resume the workflow.

    POST /api/podcast/episodes/<episode_id>/audio-callback/

    Accepts JSON body::

        {
            "audio_url": "https://...",
            "audio_file_size_bytes": 12345
        }

    Returns JSON::

        {"status": "ok", "message": "Audio received, transcription enqueued"}
    """
    # Parse and validate request body
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON body"}, status=400)

    audio_url = body.get("audio_url")
    audio_file_size_bytes = body.get("audio_file_size_bytes")

    if not audio_url:
        return JsonResponse({"error": "audio_url is required"}, status=400)

    # Validate episode exists
    try:
        episode = Episode.objects.get(pk=episode_id)
    except Episode.DoesNotExist:
        return JsonResponse({"error": f"Episode {episode_id} not found"}, status=404)

    # Validate workflow state
    try:
        wf = EpisodeWorkflow.objects.get(episode_id=episode_id)
    except EpisodeWorkflow.DoesNotExist:
        return JsonResponse(
            {"error": f"No workflow for episode {episode_id}"}, status=404
        )

    if wf.status != "paused_for_human" or wf.blocked_on != "audio_generation":
        return JsonResponse(
            {
                "error": (
                    f"Episode {episode_id} is not waiting for audio. "
                    f"Status: {wf.status}, blocked_on: {wf.blocked_on}"
                )
            },
            status=409,
        )

    # Update episode with audio data
    episode.audio_url = audio_url
    if audio_file_size_bytes is not None:
        episode.audio_file_size_bytes = audio_file_size_bytes
    episode.save(update_fields=["audio_url", "audio_file_size_bytes"])

    # Resume workflow and advance past Audio Generation
    workflow.resume_workflow(episode_id)
    workflow.advance_step(episode_id, "Audio Generation")

    # Enqueue transcription
    step_transcribe_audio.enqueue(episode_id=episode_id)

    logger.info(
        "audio_callback: received audio for episode %d, transcription enqueued",
        episode_id,
    )

    return JsonResponse(
        {"status": "ok", "message": "Audio received, transcription enqueued"}
    )
