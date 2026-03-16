#!/usr/bin/env python3
"""
Generate podcast chapters from an MP3 file.

Uses local Whisper for transcription (or existing transcript) and Claude for topic segmentation.
Outputs FFmpeg-compatible chapters file and Podcasting 2.0 JSON.

Usage:
    python generate_chapters.py /path/to/episode.mp3
    python generate_chapters.py /path/to/episode.mp3 --transcript existing_transcript.json
    python generate_chapters.py /path/to/episode.mp3 --quiet --log-dir logs/

Requirements:
    pip install openai-whisper anthropic python-dotenv

Environment:
    ANTHROPIC_API_KEY must be set (can be in .env file)
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

import anthropic
import whisper


def transcribe_audio(
    audio_path: str,
    model_name: str = "base",
    verbose: bool = True,
    log_file: str = None,
) -> list[dict]:
    """
    Transcribe audio file using local Whisper.

    Returns list of segments with start, end, and text.
    """

    def log(msg):
        if verbose:
            print(msg)
        if log_file:
            with open(log_file, "a") as f:
                f.write(msg + "\n")

    log(f"Loading Whisper model '{model_name}'...")
    model = whisper.load_model(model_name)

    log(f"Transcribing {audio_path}...")
    result = model.transcribe(audio_path, verbose=False)

    return result["segments"]


def load_transcript(transcript_path: str) -> list[dict]:
    """
    Load transcript from existing JSON file.

    Expected format: {"segments": [...]} or just [...]
    """
    with open(transcript_path) as f:
        data = json.load(f)

    # Handle both formats: {"segments": [...]} and [...]
    if isinstance(data, dict) and "segments" in data:
        return data["segments"]
    elif isinstance(data, list):
        return data
    else:
        raise ValueError("Invalid transcript format")


def chunk_segments(segments: list[dict], chunk_duration: float = 120.0) -> list[dict]:
    """
    Group segments into chunks of approximately chunk_duration seconds.

    Returns list of chunks with start, end, and combined text.
    """
    chunks = []
    current_chunk = {
        "start": segments[0]["start"],
        "end": segments[0]["end"],
        "text": segments[0]["text"],
    }

    for segment in segments[1:]:
        # If adding this segment would exceed chunk duration, finalize current chunk
        if segment["end"] - current_chunk["start"] > chunk_duration:
            chunks.append(current_chunk)
            current_chunk = {
                "start": segment["start"],
                "end": segment["end"],
                "text": segment["text"],
            }
        else:
            current_chunk["end"] = segment["end"]
            current_chunk["text"] += " " + segment["text"]

    # Don't forget the last chunk
    chunks.append(current_chunk)

    return chunks


def generate_chapter_titles(
    chunks: list[dict],
    client: anthropic.Anthropic,
    model: str = "claude-sonnet-4-20250514",
    verbose: bool = True,
    log_file: str = None,
) -> list[dict]:
    """
    Use Claude to generate chapter titles for each chunk.

    Returns chunks with added 'title' field.
    """

    def log(msg):
        if verbose:
            print(msg)
        if log_file:
            with open(log_file, "a") as f:
                f.write(msg + "\n")

    log("Generating chapter titles with Claude...")

    # Build the prompt with all chunks
    chunks_text = ""
    for i, chunk in enumerate(chunks):
        start_time = format_timestamp(chunk["start"])
        chunks_text += (
            f"\n--- Chunk {i + 1} (starts at {start_time}) ---\n{chunk['text']}\n"
        )

    prompt = f"""Analyze this podcast transcript and generate chapter titles.

The transcript is divided into chunks of approximately 2 minutes each. For each chunk, provide a concise chapter title (3-6 words) that captures the main topic being discussed.

If adjacent chunks discuss the same topic, you can merge them into a single chapter. If a chunk contains multiple distinct topics, note the more prominent one.

{chunks_text}

Respond with a JSON array of chapters. Each chapter should have:
- "start_chunk": the first chunk number (1-indexed)
- "end_chunk": the last chunk number (1-indexed)
- "title": the chapter title (3-6 words)

Example response:
[
  {{"start_chunk": 1, "end_chunk": 2, "title": "Introduction and Overview"}},
  {{"start_chunk": 3, "end_chunk": 5, "title": "Technical Deep Dive"}},
  {{"start_chunk": 6, "end_chunk": 6, "title": "Practical Applications"}}
]

Return only the JSON array, no other text."""

    response = client.messages.create(
        model=model, max_tokens=1024, messages=[{"role": "user", "content": prompt}]
    )

    # Parse the response
    response_text = response.content[0].text.strip()

    # Handle potential markdown code blocks
    if response_text.startswith("```"):
        response_text = response_text.split("```")[1]
        if response_text.startswith("json"):
            response_text = response_text[4:]
        response_text = response_text.strip()

    chapter_definitions = json.loads(response_text)

    # Convert chunk-based chapters to time-based chapters
    chapters = []
    for chapter_def in chapter_definitions:
        start_chunk_idx = chapter_def["start_chunk"] - 1  # Convert to 0-indexed
        end_chunk_idx = chapter_def["end_chunk"] - 1

        chapters.append(
            {
                "start": chunks[start_chunk_idx]["start"],
                "end": chunks[end_chunk_idx]["end"],
                "title": chapter_def["title"],
            }
        )

    return chapters


def format_timestamp(seconds: float) -> str:
    """Format seconds as HH:MM:SS or MM:SS."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)

    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def generate_ffmpeg_chapters(
    chapters: list[dict], output_path: str, verbose: bool = True, log_file: str = None
):
    """
    Generate FFmpeg-compatible chapters metadata file.
    """

    def log(msg):
        if verbose:
            print(msg)
        if log_file:
            with open(log_file, "a") as f:
                f.write(msg + "\n")

    with open(output_path, "w") as f:
        f.write(";FFMETADATA1\n")

        for chapter in chapters:
            # FFmpeg uses milliseconds
            start_ms = int(chapter["start"] * 1000)
            end_ms = int(chapter["end"] * 1000)

            f.write("\n[CHAPTER]\n")
            f.write("TIMEBASE=1/1000\n")
            f.write(f"START={start_ms}\n")
            f.write(f"END={end_ms}\n")
            f.write(f"title={chapter['title']}\n")

    log(f"Chapters written to {output_path}")


def generate_chapters_json(
    chapters: list[dict], output_path: str, verbose: bool = True, log_file: str = None
):
    """
    Generate Podcasting 2.0 compatible chapters JSON file.
    """

    def log(msg):
        if verbose:
            print(msg)
        if log_file:
            with open(log_file, "a") as f:
                f.write(msg + "\n")

    pc2_chapters = {
        "version": "1.2.0",
        "chapters": [
            {"startTime": chapter["start"], "title": chapter["title"]}
            for chapter in chapters
        ],
    }

    with open(output_path, "w") as f:
        json.dump(pc2_chapters, f, indent=2)

    log(f"Podcasting 2.0 chapters written to {output_path}")


def print_chapters_summary(
    chapters: list[dict], verbose: bool = True, log_file: str = None
):
    """Print a human-readable summary of chapters."""

    def log(msg):
        if verbose:
            print(msg)
        if log_file:
            with open(log_file, "a") as f:
                f.write(msg + "\n")

    log("\n" + "=" * 50)
    log("CHAPTER SUMMARY")
    log("=" * 50)

    for i, chapter in enumerate(chapters, 1):
        timestamp = format_timestamp(chapter["start"])
        log(f"{timestamp}  {chapter['title']}")

    log("=" * 50 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Generate podcast chapters from an MP3 file or transcript"
    )
    parser.add_argument("audio_file", help="Path to the MP3 file")
    parser.add_argument(
        "--transcript",
        help="Use existing transcript JSON instead of transcribing (avoids re-transcription)",
    )
    parser.add_argument(
        "--model",
        default="base",
        choices=["tiny", "base", "small", "medium"],
        help="Whisper model size for transcription (default: base, ignored if --transcript provided)",
    )
    parser.add_argument(
        "--claude-model",
        default="claude-sonnet-4-20250514",
        help="Claude model to use for chapter generation (default: claude-sonnet-4-20250514)",
    )
    parser.add_argument(
        "--chunk-duration",
        type=float,
        default=120.0,
        help="Target duration for transcript chunks in seconds (default: 120)",
    )
    parser.add_argument(
        "--log-dir",
        help="Directory for chapter files and logs (default: same as audio file)",
    )
    parser.add_argument(
        "--output",
        "-o",
        help="Output base path for chapter files (default: derived from audio filename)",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Minimal output (suppress progress messages)",
    )

    args = parser.parse_args()

    # Validate input file
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
        log_file = str(log_dir / f"chapters_log_{timestamp}.txt")

    def log(msg):
        if not args.quiet:
            print(msg)
        if log_file:
            with open(log_file, "a") as f:
                f.write(msg + "\n")

    # Check for API key
    if not os.environ.get("ANTHROPIC_API_KEY"):
        log("Error: ANTHROPIC_API_KEY environment variable not set")
        return 1

    # Initialize Anthropic client
    client = anthropic.Anthropic()

    # Step 1: Get transcript
    if args.transcript:
        transcript_path = Path(args.transcript)
        if not transcript_path.exists():
            log(f"Error: Transcript file not found: {transcript_path}")
            return 1

        log(f"Loading transcript from {transcript_path}...")
        try:
            segments = load_transcript(str(transcript_path))
            log(f"Loaded {len(segments)} segments from transcript")
        except Exception as e:
            log(f"Error loading transcript: {e}")
            return 1
    else:
        # Transcribe audio
        segments = transcribe_audio(
            str(audio_path), args.model, verbose=not args.quiet, log_file=log_file
        )
        log(f"Transcribed {len(segments)} segments")

    # Step 2: Chunk segments
    chunks = chunk_segments(segments, args.chunk_duration)
    log(f"Created {len(chunks)} chunks")

    # Step 3: Generate chapter titles
    chapters = generate_chapter_titles(
        chunks,
        client,
        model=args.claude_model,
        verbose=not args.quiet,
        log_file=log_file,
    )
    log(f"Generated {len(chapters)} chapters")

    # Step 4: Output files
    if args.output:
        base_path = Path(args.output)
        output_dir = base_path.parent
        base_name = base_path.stem
    else:
        output_dir = log_dir
        base_name = audio_path.stem

    # FFmpeg chapters file
    ffmpeg_path = output_dir / f"{base_name}_chapters.txt"
    generate_ffmpeg_chapters(
        chapters, str(ffmpeg_path), verbose=not args.quiet, log_file=log_file
    )

    # Podcasting 2.0 JSON
    json_path = output_dir / f"{base_name}_chapters.json"
    generate_chapters_json(
        chapters, str(json_path), verbose=not args.quiet, log_file=log_file
    )

    # Print summary
    print_chapters_summary(chapters, verbose=not args.quiet, log_file=log_file)

    # Print next steps
    if not args.quiet:
        log("\nTo embed chapters in the MP3:")
        log(
            f'  ffmpeg -i "{audio_path}" -i "{ffmpeg_path}" -map_metadata 1 -codec copy "{audio_path.stem}_with_chapters.mp3"'
        )

    if log_file:
        log(f"\n✓ Log saved to: {log_file}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
