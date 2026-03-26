"""
Voice Transcription Tool

Dual-backend audio transcription:
1. SuperWhisper (primary) - local macOS app, free, fast
2. OpenAI Whisper API (fallback) - cloud API, paid per minute
"""

import json
import logging
import os
import subprocess
import time
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

WHISPER_URL = "https://api.openai.com/v1/audio/transcriptions"
SUPPORTED_FORMATS = {"mp3", "mp4", "mpeg", "mpga", "m4a", "wav", "webm", "ogg", "flac"}
MAX_FILE_SIZE = 25 * 1024 * 1024  # 25MB

# SuperWhisper configuration
SUPERWHISPER_RECORDINGS_DIR = Path(
    os.environ.get(
        "SUPERWHISPER_RECORDINGS_DIR",
        os.path.expanduser("~/Documents/superwhisper/recordings"),
    )
)
SUPERWHISPER_POLL_INTERVAL = 0.5  # seconds
SUPERWHISPER_TIMEOUT = 30  # seconds

# Cache for SuperWhisper availability check
_superwhisper_available_cache: dict[str, float | bool] = {
    "timestamp": 0.0,
    "available": False,
}
_SUPERWHISPER_CACHE_TTL = 60  # seconds


class TranscriptionError(Exception):
    """Transcription operation failed."""

    def __init__(self, message: str, category: str = "execution"):
        self.message = message
        self.category = category
        super().__init__(message)


def _is_superwhisper_available() -> bool:
    """
    Check if SuperWhisper is running, with 60s caching.

    Returns True if the SuperWhisper process is detected via pgrep.
    """
    now = time.time()
    if now - _superwhisper_available_cache["timestamp"] < _SUPERWHISPER_CACHE_TTL:
        return bool(_superwhisper_available_cache["available"])

    try:
        result = subprocess.run(
            ["pgrep", "-x", "superwhisper"],
            capture_output=True,
            timeout=5,
        )
        available = result.returncode == 0
    except Exception:
        available = False

    _superwhisper_available_cache["timestamp"] = now
    _superwhisper_available_cache["available"] = available

    if available:
        logger.debug("SuperWhisper is available")
    else:
        logger.debug("SuperWhisper is not available, will use OpenAI fallback")

    return available


def _get_existing_folders() -> set[str]:
    """Get set of existing folder names in the SuperWhisper recordings directory."""
    if not SUPERWHISPER_RECORDINGS_DIR.exists():
        return set()
    return {f.name for f in SUPERWHISPER_RECORDINGS_DIR.iterdir() if f.is_dir()}


def _transcribe_superwhisper(audio_source: str) -> dict | None:
    """
    Transcribe audio using SuperWhisper.

    Sends the audio file to SuperWhisper via `open -g -a` and polls for
    the result in the recordings directory.

    Returns a dict with transcription result, or None if transcription failed.
    """
    audio_path = Path(audio_source)

    if not SUPERWHISPER_RECORDINGS_DIR.exists():
        logger.debug("SuperWhisper recordings directory does not exist")
        return None

    # Snapshot existing folders before sending
    existing_folders = _get_existing_folders()

    # Send audio to SuperWhisper (background, no activation)
    try:
        result = subprocess.run(
            ["open", "-g", "-a", "superwhisper", str(audio_path)],
            capture_output=True,
            timeout=10,
        )
        if result.returncode != 0:
            logger.debug(f"Failed to send audio to SuperWhisper: {result.stderr}")
            return None
    except Exception as e:
        logger.debug(f"Failed to launch SuperWhisper: {e}")
        return None

    # Poll for new folder with meta.json
    start_time = time.time()
    while time.time() - start_time < SUPERWHISPER_TIMEOUT:
        time.sleep(SUPERWHISPER_POLL_INTERVAL)

        current_folders = _get_existing_folders()
        new_folders = current_folders - existing_folders

        for folder_name in sorted(new_folders, reverse=True):
            meta_path = SUPERWHISPER_RECORDINGS_DIR / folder_name / "meta.json"
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text())
                    transcription_text = meta.get("result", "").strip()

                    if not transcription_text:
                        logger.debug("SuperWhisper meta.json has empty result field")
                        return None

                    output = {
                        "text": transcription_text,
                        "language": None,
                    }

                    # Include duration if available (convert ms to seconds)
                    duration_ms = meta.get("duration")
                    if duration_ms is not None:
                        output["duration"] = duration_ms / 1000.0

                    logger.info(
                        f"SuperWhisper transcription complete "
                        f"(model={meta.get('modelName', 'unknown')}, "
                        f"processing={meta.get('processingTime', '?')}ms)"
                    )
                    return output

                except (json.JSONDecodeError, OSError) as e:
                    logger.debug(f"Failed to read SuperWhisper meta.json: {e}")
                    return None

    logger.debug(f"SuperWhisper timed out after {SUPERWHISPER_TIMEOUT}s")
    return None


def transcribe(
    audio_source: str,
    language: str | None = None,
    response_format: str = "json",
    timestamps: bool = False,
    prompt: str | None = None,
) -> dict:
    """
    Transcribe audio using SuperWhisper (primary) or OpenAI Whisper API (fallback).

    Tries SuperWhisper first if the app is running. Falls back to OpenAI Whisper
    API if SuperWhisper is unavailable, times out, or returns an empty result.

    Args:
        audio_source: File path to audio file
        language: ISO-639-1 language code (optional, auto-detected if not provided)
        response_format: Output format (json, text, srt, verbose_json, vtt)
        timestamps: Include word-level timestamps (requires verbose_json format)
        prompt: Optional prompt to guide transcription style

    Returns:
        dict with keys:
            - text: Transcribed text
            - language: Detected language
            - duration: Audio duration in seconds (if verbose_json)
            - segments: List of segments with timestamps (if verbose_json)
            - words: List of words with timestamps (if timestamps=True)
            - error: Error message (if failed)
    """
    # Validate audio source (common to both backends)
    audio_path = Path(audio_source)
    if not audio_path.exists():
        return {"error": f"Audio file not found: {audio_source}"}

    ext = audio_path.suffix.lower().lstrip(".")
    if ext not in SUPPORTED_FORMATS:
        return {
            "error": f"Unsupported audio format: {ext}. Supported: {', '.join(SUPPORTED_FORMATS)}"
        }

    file_size = audio_path.stat().st_size
    if file_size == 0:
        return {"error": "Audio file is empty (0 bytes)"}
    if file_size > MAX_FILE_SIZE:
        return {"error": f"File too large: {file_size / 1024 / 1024:.1f}MB. Max: 25MB"}

    # Try SuperWhisper first (only for basic json format without special options)
    if _is_superwhisper_available() and response_format == "json" and not timestamps:
        result = _transcribe_superwhisper(audio_source)
        if result is not None:
            return result
        logger.info("SuperWhisper failed or timed out, falling back to OpenAI Whisper API")

    # Fall back to OpenAI Whisper API
    return _transcribe_openai(
        audio_source=audio_source,
        language=language,
        response_format=response_format,
        timestamps=timestamps,
        prompt=prompt,
    )


def _transcribe_openai(
    audio_source: str,
    language: str | None = None,
    response_format: str = "json",
    timestamps: bool = False,
    prompt: str | None = None,
) -> dict:
    """Transcribe audio using OpenAI Whisper API."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return {"error": "OPENAI_API_KEY environment variable not set"}

    audio_path = Path(audio_source)
    ext = audio_path.suffix.lower().lstrip(".")

    # If timestamps requested, use verbose_json
    if timestamps:
        response_format = "verbose_json"

    try:
        with open(audio_path, "rb") as audio_file:
            files = {"file": (audio_path.name, audio_file, f"audio/{ext}")}
            data = {
                "model": "whisper-1",
                "response_format": response_format,
            }

            if language:
                data["language"] = language
            if prompt:
                data["prompt"] = prompt
            if timestamps:
                data["timestamp_granularities[]"] = "word"

            response = requests.post(
                WHISPER_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                files=files,
                data=data,
                timeout=300,
            )

        response.raise_for_status()

        if response_format in ("text", "srt", "vtt"):
            return {
                "text": response.text,
                "format": response_format,
            }

        result = response.json()

        output = {
            "text": result.get("text", ""),
            "language": result.get("language"),
        }

        if response_format == "verbose_json":
            output["duration"] = result.get("duration")
            output["segments"] = result.get("segments", [])
            if timestamps and "words" in result:
                output["words"] = result["words"]

        return output

    except requests.exceptions.Timeout:
        return {"error": "Transcription request timed out"}
    except requests.exceptions.RequestException as e:
        error_msg = str(e)
        if hasattr(e, "response") and e.response is not None:
            try:
                error_detail = e.response.json().get("error", {}).get("message", "")
                if error_detail:
                    error_msg = error_detail
            except Exception:
                pass
        return {"error": f"API request failed: {error_msg}"}
    except Exception as e:
        return {"error": f"Unexpected error: {str(e)}"}


def transcribe_with_timestamps(
    audio_source: str,
    language: str | None = None,
) -> dict:
    """
    Transcribe audio with word-level timestamps.

    Args:
        audio_source: File path to audio file
        language: ISO-639-1 language code (optional)

    Returns:
        dict with text, words (with timestamps), segments, and duration
    """
    return transcribe(
        audio_source=audio_source,
        language=language,
        response_format="verbose_json",
        timestamps=True,
    )


def get_supported_formats() -> set:
    """Return set of supported audio formats."""
    return SUPPORTED_FORMATS.copy()


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m tools.transcribe 'path/to/audio.mp3'")
        sys.exit(1)

    audio_path = sys.argv[1]
    print(f"Transcribing: {audio_path}")

    result = transcribe(audio_path)

    if "error" in result:
        print(f"Error: {result['error']}")
        sys.exit(1)
    else:
        print(f"\nTranscription:\n{result['text']}")
        if result.get("language"):
            print(f"\nLanguage: {result['language']}")
        if result.get("duration"):
            print(f"Duration: {result['duration']:.1f}s")
