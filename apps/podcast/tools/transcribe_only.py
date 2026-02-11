#!/usr/bin/env python3
"""
Transcribe audio using local Whisper or OpenAI's Whisper API.

Usage:
    python transcribe_only.py /path/to/episode.mp3
    python transcribe_only.py /path/to/episode.mp3 --use-api  # Use OpenAI API
    python transcribe_only.py audio.mp3 --log-dir logs/  # Save to logs directory

Environment:
    OPENAI_API_KEY - Required only when using --use-api flag
    Can be set in .env file in repo root
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

import whisper
from openai import OpenAI


def transcribe_audio(
    audio_path: str,
    model_name: str = "base",
    verbose: bool = True,
    log_file: str = None,
):
    """Transcribe audio file using local Whisper."""

    def log(msg):
        if verbose:
            print(msg)
        if log_file:
            with open(log_file, "a") as f:
                f.write(msg + "\n")

    log(f"Loading Whisper model '{model_name}'...")
    model = whisper.load_model(model_name)

    log(f"Transcribing {audio_path}...")
    result = model.transcribe(audio_path, verbose=verbose)

    return result


def transcribe_audio_api(
    audio_path: str,
    model_name: str = "whisper-1",
    verbose: bool = True,
    log_file: str = None,
):
    """Transcribe audio file using OpenAI's Whisper API."""

    def log(msg):
        if verbose:
            print(msg)
        if log_file:
            with open(log_file, "a") as f:
                f.write(msg + "\n")

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        log("Error: OPENAI_API_KEY not found in environment or .env file")
        return None

    log(f"Transcribing {audio_path} using OpenAI API...")
    client = OpenAI(api_key=api_key)

    with open(audio_path, "rb") as audio_file:
        response = client.audio.transcriptions.create(
            model=model_name,
            file=audio_file,
            response_format="verbose_json",
            timestamp_granularities=["segment"],
        )

    result = {
        "text": response.text,
        "segments": [
            {"start": seg.start, "end": seg.end, "text": seg.text}
            for seg in response.segments
        ],
    }

    return result


def main():
    parser = argparse.ArgumentParser(description="Transcribe audio with Whisper")
    parser.add_argument("audio_file", help="Path to the audio file")
    parser.add_argument(
        "--model",
        default="base",
        choices=["tiny", "base", "small", "medium"],
        help="Whisper model size for local transcription (default: base)",
    )
    parser.add_argument(
        "--use-api",
        action="store_true",
        help="Use OpenAI's Whisper API instead of local model (requires OPENAI_API_KEY)",
    )
    parser.add_argument(
        "--output",
        "-o",
        help="Output path for transcript JSON (default: <audio_file>_transcript.json)",
    )
    parser.add_argument(
        "--log-dir",
        help="Directory for output and log files (default: same as audio file)",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Minimal output (suppress progress messages)",
    )

    args = parser.parse_args()
    audio_path = Path(args.audio_file)

    if not audio_path.exists():
        if not args.quiet:
            print(f"Error: File not found: {audio_path}")
        return 1

    # Set up log directory
    log_dir = Path(args.log_dir) if args.log_dir else audio_path.parent
    if args.log_dir and not log_dir.exists():
        log_dir.mkdir(parents=True, exist_ok=True)

    # Set up log file
    log_file = None
    if args.log_dir:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = str(log_dir / f"transcribe_log_{timestamp}.txt")

    def log(msg):
        if not args.quiet:
            print(msg)
        if log_file:
            with open(log_file, "a") as f:
                f.write(msg + "\n")

    log(f"Starting transcription: {audio_path.name}")
    log(f"Method: {'OpenAI API' if args.use_api else f'Local Whisper ({args.model})'}")

    # Transcribe using API or local model
    if args.use_api:
        result = transcribe_audio_api(
            str(audio_path), verbose=not args.quiet, log_file=log_file
        )
        if result is None:
            return 1
    else:
        result = transcribe_audio(
            str(audio_path), args.model, verbose=not args.quiet, log_file=log_file
        )

    # Determine output path
    if args.output:
        output_path = Path(args.output)
    elif args.log_dir:
        output_path = log_dir / f"{audio_path.stem}_transcript.json"
    else:
        output_path = audio_path.parent / f"{audio_path.stem}_transcript.json"

    # Save full result
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)

    log(f"\n✓ Transcript saved to: {output_path}")
    if log_file:
        log(f"✓ Log saved to: {log_file}")

    word_count = len(result["text"].split())
    log(f"✓ Transcript length: {word_count} words")

    return 0


if __name__ == "__main__":
    sys.exit(main())
