"""
Voice Transcription Tool

Audio transcription using OpenAI Whisper API.
"""

import base64
import os
from pathlib import Path

import requests

WHISPER_URL = "https://api.openai.com/v1/audio/transcriptions"
SUPPORTED_FORMATS = {"mp3", "mp4", "mpeg", "mpga", "m4a", "wav", "webm", "ogg", "flac"}
MAX_FILE_SIZE = 25 * 1024 * 1024  # 25MB


class TranscriptionError(Exception):
    """Transcription operation failed."""

    def __init__(self, message: str, category: str = "execution"):
        self.message = message
        self.category = category
        super().__init__(message)


def transcribe(
    audio_source: str,
    language: str | None = None,
    response_format: str = "json",
    timestamps: bool = False,
    prompt: str | None = None,
) -> dict:
    """
    Transcribe audio using OpenAI Whisper API.

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
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return {"error": "OPENAI_API_KEY environment variable not set"}

    # Validate audio source
    audio_path = Path(audio_source)
    if not audio_path.exists():
        return {"error": f"Audio file not found: {audio_source}"}

    # Check file extension
    ext = audio_path.suffix.lower().lstrip(".")
    if ext not in SUPPORTED_FORMATS:
        return {
            "error": f"Unsupported audio format: {ext}. Supported: {', '.join(SUPPORTED_FORMATS)}"
        }

    # Check file size
    file_size = audio_path.stat().st_size
    if file_size > MAX_FILE_SIZE:
        return {"error": f"File too large: {file_size / 1024 / 1024:.1f}MB. Max: 25MB"}

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
