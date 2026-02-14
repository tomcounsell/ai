"""DB-aware services for audio generation, transcription, and chapter extraction.

Each function takes an episode_id, reads from the database, performs the
operation (or delegates to an existing tool), and writes results back.
"""

from __future__ import annotations

import io
import json
import logging
import urllib.request

from apps.common.services.storage import store_file
from apps.podcast.models import Episode, EpisodeArtifact
from apps.podcast.services.generate_chapters import ChapterList
from apps.podcast.services.generate_chapters import (
    generate_chapters as _generate_chapters,
)
from apps.podcast.tools.notebooklm_api import (
    create_audio_overview,
    create_notebook,
    delete_notebook,
    download_audio,
    generate_episode_focus,
    upload_source_text,
    wait_for_audio,
)

logger = logging.getLogger(__name__)


def generate_audio(episode_id: int) -> str:
    """Call NotebookLM API to generate audio and upload to storage.

    This is a long-running operation. It:
        1. Creates a NotebookLM notebook.
        2. Uploads relevant episode artifacts (report, briefing, content plan,
           sources) as text sources.
        3. Generates an episodeFocus prompt and triggers audio generation.
        4. Polls until audio generation completes.
        5. Downloads the audio bytes.
        6. Uploads to storage via :func:`store_file`.
        7. Updates Episode fields: ``audio_url``, ``audio_file_size_bytes``.

    Integration point: ``apps/podcast/tools/notebooklm_api.py``

    Args:
        episode_id: Primary key of the :class:`Episode`.

    Returns:
        The public URL of the uploaded audio file.

    Raises:
        Episode.DoesNotExist: If no episode matches *episode_id*.
    """
    episode = Episode.objects.select_related("podcast").get(pk=episode_id)

    logger.info("generate_audio: starting for episode=%s", episode.title)

    # Gather source texts from episode and artifacts
    sources: dict[str, str] = {}

    if episode.report_text:
        sources["report.md"] = episode.report_text
    if episode.sources_text:
        sources["sources.md"] = episode.sources_text

    # Load key artifacts
    artifact_titles = ["p3-briefing", "content_plan", "p1-brief"]
    for artifact_title in artifact_titles:
        try:
            artifact = EpisodeArtifact.objects.get(
                episode=episode, title=artifact_title
            )
            if artifact.content:
                sources[f"{artifact_title}.md"] = artifact.content
        except EpisodeArtifact.DoesNotExist:
            logger.debug(
                "generate_audio: artifact '%s' not found, skipping",
                artifact_title,
            )

    if not sources:
        raise ValueError(
            f"Episode '{episode.title}' has no source content for audio generation."
        )

    # 1. Create notebook
    notebook = create_notebook(f"Yudame Research: {episode.title}")
    notebook_id = notebook.get("notebookId")
    logger.info("generate_audio: created notebook %s", notebook_id)

    try:
        # 2. Upload sources
        for name, content in sources.items():
            upload_source_text(notebook_id, name, content)

        # 3. Generate episodeFocus prompt and trigger audio
        episode_focus = generate_episode_focus(episode.title, episode.podcast.title)
        create_audio_overview(notebook_id, episode_focus)

        # 4. Wait for completion (up to 30 minutes)
        wait_for_audio(notebook_id, timeout_minutes=30)

        # 5. Download audio to a temporary path, then read bytes
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir) / f"{episode.slug}.mp3"
            download_audio(notebook_id, tmp_path)
            audio_bytes = tmp_path.read_bytes()

        # 6. Upload to storage
        storage_key = f"podcast/{episode.podcast.slug}/{episode.slug}/audio.mp3"
        audio_url = store_file(storage_key, audio_bytes, "audio/mpeg")

        # 7. Update episode
        episode.audio_url = audio_url
        episode.audio_file_size_bytes = len(audio_bytes)
        episode.save(update_fields=["audio_url", "audio_file_size_bytes"])

        logger.info(
            "generate_audio: uploaded %d bytes for episode=%s url=%s",
            len(audio_bytes),
            episode.title,
            audio_url,
        )
        return audio_url

    finally:
        # Clean up the notebook
        try:
            delete_notebook(notebook_id)
        except Exception:
            logger.warning(
                "generate_audio: failed to clean up notebook %s",
                notebook_id,
                exc_info=True,
            )


def transcribe_audio(episode_id: int) -> str:
    """Call OpenAI Whisper API to transcribe audio. Saves to Episode.transcript.

    Steps:
        1. Load Episode and read ``audio_url``.
        2. Download the audio bytes from the URL.
        3. Send to OpenAI Whisper API for transcription.
        4. Save transcript to ``Episode.transcript``.

    Args:
        episode_id: Primary key of the :class:`Episode`.

    Returns:
        The transcript text.

    Raises:
        Episode.DoesNotExist: If no episode matches *episode_id*.
        ValueError: If ``Episode.audio_url`` is empty.
    """
    import openai

    episode = Episode.objects.get(pk=episode_id)

    if not episode.audio_url:
        raise ValueError(
            f"Episode '{episode.title}' has no audio_url. " "Run generate_audio first."
        )

    logger.info("transcribe_audio: downloading audio from %s", episode.audio_url)

    # Download audio bytes from URL (timeout after 5 minutes)
    req = urllib.request.Request(episode.audio_url)
    with urllib.request.urlopen(req, timeout=300) as response:
        audio_bytes = response.read()

    # Call OpenAI Whisper API
    client = openai.OpenAI()
    audio_file = io.BytesIO(audio_bytes)
    audio_file.name = "audio.mp3"

    logger.info(
        "transcribe_audio: sending %d bytes to Whisper API",
        len(audio_bytes),
    )

    transcription = client.audio.transcriptions.create(
        model="whisper-1",
        file=audio_file,
    )

    transcript = transcription.text

    # Save to episode
    episode.transcript = transcript
    episode.save(update_fields=["transcript"])

    logger.info(
        "transcribe_audio: saved transcript (%d chars) for episode=%s",
        len(transcript),
        episode.title,
    )
    return transcript


def generate_episode_chapters(episode_id: int) -> str:
    """Call generate_chapters AI tool on transcript. Saves to Episode.chapters.

    Steps:
        1. Load Episode and read ``transcript``.
        2. Invoke :func:`generate_chapters` AI tool.
        3. Format chapters as JSON string.
        4. Save to ``Episode.chapters``.

    Args:
        episode_id: Primary key of the :class:`Episode`.

    Returns:
        The chapters as a JSON string.

    Raises:
        Episode.DoesNotExist: If no episode matches *episode_id*.
        ValueError: If ``Episode.transcript`` is empty.
    """
    episode = Episode.objects.get(pk=episode_id)

    if not episode.transcript:
        raise ValueError(
            f"Episode '{episode.title}' has no transcript. "
            "Run transcribe_audio first."
        )

    logger.info("generate_episode_chapters: episode=%s", episode.title)

    result: ChapterList = _generate_chapters(
        transcript=episode.transcript,
        episode_title=episode.title,
    )

    chapters_json = json.dumps(result.model_dump(), indent=2)

    episode.chapters = chapters_json
    episode.save(update_fields=["chapters"])

    logger.info(
        "generate_episode_chapters: saved %d chapters for episode=%s",
        len(result.chapters),
        episode.title,
    )
    return chapters_json
