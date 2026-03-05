"""DB-aware services for audio transcription and chapter extraction.

Each function takes an episode_id, reads from the database, performs the
operation (or delegates to an existing tool), and writes results back.

Audio generation is handled by the local_audio_worker management command
using the notebooklm-py library. See:
  apps/podcast/management/commands/local_audio_worker.py
"""

from __future__ import annotations

import io
import json
import logging
import urllib.request

from apps.podcast.models import Episode
from apps.podcast.services import generate_chapters as _gen_chapters_mod

logger = logging.getLogger(__name__)


def transcribe_audio(episode_id: int) -> str:
    """Call OpenAI Whisper API to transcribe audio. Saves to Episode.transcript.

    Steps:
        1. Load Episode and read ``audio_url``.
        2. Download the audio bytes from the URL.
        3. Calculate audio duration using pydub.
        4. Send to OpenAI Whisper API for transcription.
        5. Save transcript and duration to Episode.

    Args:
        episode_id: Primary key of the :class:`Episode`.

    Returns:
        The transcript text.

    Raises:
        Episode.DoesNotExist: If no episode matches *episode_id*.
        ValueError: If ``Episode.audio_url`` is empty.
    """
    import openai
    from pydub import AudioSegment

    episode = Episode.objects.get(pk=episode_id)

    if not episode.audio_url:
        raise ValueError(
            f"Episode '{episode.title}' has no audio_url. " "Run generate_audio first."
        )

    logger.info("transcribe_audio: downloading audio from %s", episode.audio_url)

    # Download audio bytes from URL (timeout after 5 minutes)
    req = urllib.request.Request(episode.audio_url)
    with urllib.request.urlopen(req, timeout=300) as response:  # nosec B310
        audio_bytes = response.read()

    # Calculate audio duration
    audio = AudioSegment.from_file(io.BytesIO(audio_bytes), format="mp3")
    duration_seconds = int(audio.duration_seconds)

    logger.info(
        "transcribe_audio: calculated duration=%d seconds",
        duration_seconds,
    )

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

    # Save transcript and duration to episode
    episode.transcript = transcript
    episode.audio_duration_seconds = duration_seconds
    episode.save(update_fields=["transcript", "audio_duration_seconds"])

    logger.info(
        "transcribe_audio: saved transcript (%d chars) and duration (%d sec) for episode=%s",
        len(transcript),
        duration_seconds,
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

    result: _gen_chapters_mod.ChapterList = _gen_chapters_mod.generate_chapters(
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
