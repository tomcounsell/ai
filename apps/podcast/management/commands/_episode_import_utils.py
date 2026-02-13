"""
Shared utilities for episode import commands (publish_episode, backfill_episodes).

This module is prefixed with an underscore so Django does not treat it as a
management command.  Both ``publish_episode`` and ``backfill_episodes`` import
from here to avoid duplicating ~300 lines of identical logic.
"""

import json
import os
from collections.abc import Callable
from pathlib import Path

from apps.podcast.models import EpisodeArtifact

# Files at episode root that map directly to Episode fields (not artifacts)
EPISODE_FIELD_FILES = {"report.md", "sources.md"}

# File extensions to skip entirely
SKIP_EXTENSIONS = {".mp3", ".png", ".jpg", ".jpeg", ".wav", ".m4a"}

# Specific files to skip (derivable or redundant)
SKIP_FILES = {"report.html", "transcript.html"}

# Directories to skip entirely
SKIP_DIRS = {"tmp"}


def extract_transcript_text(data) -> str:
    """Extract plain text from a transcript JSON structure.

    Handles common formats:
    - {"text": "..."} -- single text block
    - {"segments": [{"text": "..."}, ...]} -- segmented transcript
    - [{"text": "..."}, ...] -- list of segments
    - {"results": {"transcripts": [{"transcript": "..."}]}} -- AWS format
    """
    if isinstance(data, str):
        return data

    if isinstance(data, list):
        parts = []
        for segment in data:
            if isinstance(segment, dict) and "text" in segment:
                parts.append(segment["text"].strip())
            elif isinstance(segment, str):
                parts.append(segment.strip())
        return "\n".join(parts)

    if isinstance(data, dict):
        if "text" in data and isinstance(data["text"], str):
            return data["text"]

        if "segments" in data:
            parts = []
            for segment in data["segments"]:
                if isinstance(segment, dict) and "text" in segment:
                    parts.append(segment["text"].strip())
            return "\n".join(parts)

        if "results" in data:
            results = data["results"]
            if "transcripts" in results:
                parts = []
                for t in results["transcripts"]:
                    if "transcript" in t:
                        parts.append(t["transcript"])
                return "\n".join(parts)

    return json.dumps(data)


def get_audio_duration(audio_path: Path) -> int | None:
    """Get audio duration in seconds using mutagen if available.

    Returns duration as an integer, or None if mutagen is not installed
    or the file cannot be read.
    """
    try:
        from mutagen.mp3 import MP3

        audio = MP3(str(audio_path))
        if audio.info and audio.info.length:
            return int(audio.info.length)
    except ImportError:
        pass
    except Exception:
        pass
    return None


def populate_episode_fields(
    episode,
    episode_dir: Path,
    dry_run: bool,
    verbose: bool,
    stdout: Callable[[str], None],
) -> tuple[list[str], list[str]]:
    """Read files that map to Episode model fields.

    Returns (fields_populated, warnings) where fields_populated is a list
    of field names that were set and warnings is a list of warning strings.

    Parameters:
        episode: The Episode model instance to populate.
        episode_dir: Path to the episode working directory.
        dry_run: If True, do not write to the database.
        verbose: If True, emit progress messages.
        stdout: A callable that accepts a string for writing output.
    """
    fields_populated: list[str] = []
    warnings: list[str] = []

    # report.md -> report_text
    report_path = episode_dir / "report.md"
    if report_path.is_file():
        content = report_path.read_text(encoding="utf-8")
        if verbose:
            stdout(f"  report.md -> report_text ({len(content)} chars)")
        if not dry_run:
            episode.report_text = content
        fields_populated.append("report_text")
    else:
        warnings.append("report.md not found")

    # sources.md (at episode root, NOT research/sources.md) -> sources_text
    sources_path = episode_dir / "sources.md"
    if sources_path.is_file():
        content = sources_path.read_text(encoding="utf-8")
        if verbose:
            stdout(f"  sources.md -> sources_text ({len(content)} chars)")
        if not dry_run:
            episode.sources_text = content
        fields_populated.append("sources_text")
    else:
        warnings.append("sources.md not found at episode root")

    # *_transcript.json -> transcript
    transcript_files = list(episode_dir.glob("*_transcript.json"))
    if transcript_files:
        transcript_path = transcript_files[0]
        if len(transcript_files) > 1:
            warnings.append(
                f"Multiple transcript files found, using {transcript_path.name}"
            )
        try:
            transcript_data = json.loads(transcript_path.read_text(encoding="utf-8"))
            transcript_text = extract_transcript_text(transcript_data)
            if verbose:
                stdout(
                    f"  {transcript_path.name} -> transcript "
                    f"({len(transcript_text)} chars)"
                )
            if not dry_run:
                episode.transcript = transcript_text
            fields_populated.append("transcript")
        except (json.JSONDecodeError, KeyError) as e:
            warnings.append(f"Failed to parse {transcript_path.name}: {e}")
    else:
        warnings.append("No *_transcript.json file found")

    # *_chapters.json -> chapters
    chapters_files = list(episode_dir.glob("*_chapters.json"))
    if chapters_files:
        chapters_path = chapters_files[0]
        if len(chapters_files) > 1:
            warnings.append(
                f"Multiple chapters files found, using {chapters_path.name}"
            )
        try:
            chapters_content = chapters_path.read_text(encoding="utf-8")
            # Validate it's valid JSON
            json.loads(chapters_content)
            if verbose:
                stdout(
                    f"  {chapters_path.name} -> chapters "
                    f"({len(chapters_content)} chars)"
                )
            if not dry_run:
                episode.chapters = chapters_content
            fields_populated.append("chapters")
        except json.JSONDecodeError as e:
            warnings.append(f"Failed to parse {chapters_path.name}: {e}")
    else:
        warnings.append("No *_chapters.json file found")

    # Audio file metadata (*.mp3)
    audio_files = list(episode_dir.glob("*.mp3"))
    if audio_files:
        audio_path = audio_files[0]
        if len(audio_files) > 1:
            warnings.append(f"Multiple MP3 files found, using {audio_path.name}")

        # File size
        file_size = os.path.getsize(audio_path)
        if verbose:
            stdout(
                f"  {audio_path.name} -> audio metadata " f"(size={file_size} bytes)"
            )
        if not dry_run:
            episode.audio_file_size_bytes = file_size

        # Construct audio URL from slugs
        podcast_slug = episode.podcast.slug
        episode_slug = episode.slug
        audio_url = (
            f"https://research.yuda.me/podcast/episodes/"
            f"{podcast_slug}/{episode_slug}/{audio_path.name}"
        )
        if not dry_run:
            episode.audio_url = audio_url
        fields_populated.append("audio_url")
        fields_populated.append("audio_file_size_bytes")

        # Duration via mutagen (optional dependency)
        duration = get_audio_duration(audio_path)
        if duration is not None:
            if verbose:
                stdout(
                    f"  {audio_path.name} -> audio_duration_seconds " f"({duration}s)"
                )
            if not dry_run:
                episode.audio_duration_seconds = duration
            fields_populated.append("audio_duration_seconds")
        else:
            warnings.append(
                "Could not determine audio duration "
                "(install mutagen for MP3 duration support)"
            )

    # Cover image URL
    cover_files: list[Path] = []
    for ext in (".png", ".jpg", ".jpeg"):
        cover_files.extend(episode_dir.glob(f"cover{ext}"))
    if cover_files:
        cover_path = cover_files[0]
        podcast_slug = episode.podcast.slug
        episode_slug = episode.slug
        cover_url = (
            f"https://research.yuda.me/podcast/episodes/"
            f"{podcast_slug}/{episode_slug}/{cover_path.name}"
        )
        if verbose:
            stdout(f"  {cover_path.name} -> cover_image_url")
        if not dry_run:
            episode.cover_image_url = cover_url
        fields_populated.append("cover_image_url")

    # Save episode fields (not status yet -- that happens after artifacts)
    if not dry_run:
        episode.save()

    return fields_populated, warnings


def create_artifacts(
    episode,
    episode_dir: Path,
    dry_run: bool,
    verbose: bool,
    stdout: Callable[[str], None],
    normalize_title_fn: Callable[[str], str] | None = None,
) -> tuple[int, int, list[str]]:
    """Walk the episode directory and create EpisodeArtifact records
    for all markdown files that are not mapped to Episode fields.

    Parameters:
        episode: The Episode model instance.
        episode_dir: Path to the episode working directory.
        dry_run: If True, do not write to the database.
        verbose: If True, emit progress messages.
        stdout: A callable that accepts a string for writing output.
        normalize_title_fn: Optional callable to normalize artifact titles
            (used by backfill for legacy naming normalization).

    Returns (created_count, updated_count, warnings).
    """
    created = 0
    updated = 0
    warnings: list[str] = []

    for path in sorted(episode_dir.rglob("*")):
        if not path.is_file():
            continue

        # Get relative path from episode root
        rel_path = path.relative_to(episode_dir)
        rel_str = str(rel_path)

        # Skip files in excluded directories
        if any(part in SKIP_DIRS for part in rel_path.parts):
            if verbose:
                stdout(f"  SKIP (tmp dir): {rel_str}")
            continue

        # Skip binary files
        if path.suffix.lower() in SKIP_EXTENSIONS:
            if verbose:
                stdout(f"  SKIP (binary): {rel_str}")
            continue

        # Skip specific derivable files
        if path.name in SKIP_FILES:
            if verbose:
                stdout(f"  SKIP (derivable): {rel_str}")
            continue

        # Skip *_chapters.txt (redundant with JSON)
        if path.name.endswith("_chapters.txt"):
            if verbose:
                stdout(f"  SKIP (redundant): {rel_str}")
            continue

        # Skip JSON files (transcript/chapters already handled as fields,
        # other JSON files are not artifacts)
        if path.suffix.lower() == ".json":
            if verbose:
                stdout(f"  SKIP (json): {rel_str}")
            continue

        # Skip HTML files
        if path.suffix.lower() == ".html":
            if verbose:
                stdout(f"  SKIP (html): {rel_str}")
            continue

        # Only process markdown files as artifacts
        if path.suffix.lower() != ".md":
            if verbose:
                stdout(f"  SKIP (not markdown): {rel_str}")
            continue

        # Skip files that are mapped to Episode fields
        if rel_str in ("report.md", "sources.md"):
            if verbose:
                stdout(f"  SKIP (episode field): {rel_str}")
            continue

        # Read the file content
        try:
            content = path.read_text(encoding="utf-8")
        except Exception as e:
            warnings.append(f"Could not read {rel_str}: {e}")
            continue

        # Title is the relative path, optionally normalized
        title = normalize_title_fn(rel_str) if normalize_title_fn else rel_str

        if verbose:
            stdout(f"  ARTIFACT: {title} ({len(content)} chars)")

        if not dry_run:
            _, was_created = EpisodeArtifact.objects.update_or_create(
                episode=episode,
                title=title,
                defaults={"content": content},
            )
            if was_created:
                created += 1
            else:
                updated += 1
        else:
            # In dry-run mode, count as created
            created += 1

    return created, updated, warnings
