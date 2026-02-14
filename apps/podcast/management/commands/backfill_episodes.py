"""
Backfill existing episodes from the research repo into the database.

One-time migration command that imports ~49 episodes across 7 series into
3 Podcast records. Walks the source directory structure, creates Podcast and
Episode records as needed, populates Episode fields from files (report, sources,
transcript, chapters, audio metadata), and creates EpisodeArtifact records for
all other markdown files.

Usage:
    # Preview (no DB writes)
    uv run python manage.py backfill_episodes \\
        --source-dir /Users/tomcounsell/src/research/podcast/episodes/ --dry-run

    # Import for real
    uv run python manage.py backfill_episodes \\
        --source-dir /Users/tomcounsell/src/research/podcast/episodes/

    # With verbose output
    uv run python manage.py backfill_episodes \\
        --source-dir /Users/tomcounsell/src/research/podcast/episodes/ --verbose

The source directory should contain series subdirectories, each containing
episode subdirectories:
    {source-dir}/{series-slug}/{episode-slug}/

Series are mapped to podcasts via a hardcoded mapping (this is a one-time
migration, not a reusable abstraction).

Idempotent: uses update_or_create for both episodes and artifacts, so
re-running safely overwrites existing content.
"""

import datetime
import os
import re
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.podcast.management.commands._episode_import_utils import (
    EPISODE_FIELD_FILES,
    SKIP_DIRS,
    SKIP_EXTENSIONS,
    SKIP_FILES,
    create_artifacts,
    populate_episode_fields,
)
from apps.podcast.models import Episode, Podcast

# Hardcoded series -> podcast mapping
SERIES_TO_PODCAST = {
    "active-recovery": "yudame-research",
    "algorithms-for-life": "yudame-research",
    "building-a-micro-school": "yudame-research",
    "cardiovascular-health": "yudame-research",
    "kindergarten-first-principles": "yudame-research",
    "solomon-islands-telecom-series": "solomon-islands-telecom",
    "stablecoin-series": "stablecoin",
}

# Podcast definitions
PODCAST_DEFINITIONS = {
    "yudame-research": {
        "title": "Yudame Research",
        "description": (
            "Deep-dive research podcast exploring topics in health, education, "
            "technology, and decision-making. Each episode synthesizes academic "
            "and industry research into actionable insights."
        ),
        "is_public": True,
    },
    "solomon-islands-telecom": {
        "title": "Solomon Islands Telecom",
        "description": (
            "Research series examining telecommunications infrastructure, "
            "policy, and connectivity challenges in the Solomon Islands."
        ),
        "is_public": False,
    },
    "stablecoin": {
        "title": "Stablecoin",
        "description": (
            "Research series analyzing stablecoin technology, regulation, "
            "and market dynamics in the cryptocurrency ecosystem."
        ),
        "is_public": False,
    },
}

# Legacy files at episode root that should be normalized to subdirectories
LEGACY_NAME_NORMALIZATION = {
    "research-results.md": "research/research-results.md",
    "research-briefing.md": "research/research-briefing.md",
    "research-perplexity.md": "research/research-perplexity.md",
    "research-gemini.md": "research/research-gemini.md",
    "cross-validation.md": "research/cross-validation.md",
    "perplexity_phase1_raw.md": "research/perplexity_phase1_raw.md",
    "prompts.md": "logs/prompts.md",
    "metadata.md": "logs/metadata.md",
}


class Command(BaseCommand):
    help = (
        "Backfill existing episodes from the research repo into the database. "
        "One-time migration command."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--source-dir",
            type=str,
            required=True,
            help=(
                "Path to the research repo episodes directory. "
                "Expected structure: {source-dir}/{series-slug}/{episode-slug}/"
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview without writing to the database",
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Show each file being processed",
        )

    def handle(self, *args, **options) -> None:
        source_dir = Path(options["source_dir"]).resolve()
        self.dry_run: bool = options["dry_run"]
        self.verbose: bool = options["verbose"]

        if not source_dir.is_dir():
            raise CommandError(f"Source directory not found: {source_dir}")

        if self.dry_run:
            self.stdout.write(self.style.WARNING("[DRY RUN] No changes will be made"))

        self.stdout.write(f"Source directory: {source_dir}")
        self.stdout.write("")

        # Counters
        podcasts_created = 0
        podcasts_found = 0
        episodes_created = 0
        episodes_updated = 0
        total_artifacts_created = 0
        total_artifacts_updated = 0
        all_warnings: list[str] = []

        # Step 1: Create/get Podcast records
        podcast_cache: dict[str, Podcast] = {}
        for podcast_slug, defn in PODCAST_DEFINITIONS.items():
            if self.dry_run:
                # In dry-run mode, try to fetch existing or note creation
                try:
                    podcast = Podcast.objects.get(slug=podcast_slug)
                    podcasts_found += 1
                    self.stdout.write(
                        f"Found podcast: {podcast.title} ({podcast_slug})"
                    )
                except Podcast.DoesNotExist:
                    podcasts_created += 1
                    self.stdout.write(
                        f"Would create podcast: {defn['title']} ({podcast_slug})"
                    )
                    # Create a temporary object for dry-run processing
                    podcast = Podcast(
                        title=defn["title"],
                        slug=podcast_slug,
                        description=defn["description"],
                        author_name="Yudame Research",
                        author_email="podcast@yuda.me",
                        language="en",
                        is_public=defn["is_public"],
                    )
                podcast_cache[podcast_slug] = podcast
            else:
                podcast, created = Podcast.objects.get_or_create(
                    slug=podcast_slug,
                    defaults={
                        "title": defn["title"],
                        "description": defn["description"],
                        "author_name": "Yudame Research",
                        "author_email": "podcast@yuda.me",
                        "language": "en",
                        "is_public": defn["is_public"],
                    },
                )
                if created:
                    podcasts_created += 1
                    self.stdout.write(
                        f"Created podcast: {podcast.title} ({podcast_slug})"
                    )
                else:
                    podcasts_found += 1
                    self.stdout.write(
                        f"Found podcast: {podcast.title} ({podcast_slug})"
                    )
                podcast_cache[podcast_slug] = podcast

        self.stdout.write("")

        # Step 2: Walk series directories
        series_dirs = sorted(
            [d for d in source_dir.iterdir() if d.is_dir()],
            key=lambda d: d.name,
        )

        for series_dir in series_dirs:
            series_slug = series_dir.name

            if series_slug not in SERIES_TO_PODCAST:
                all_warnings.append(
                    f"Unknown series directory: {series_slug} (skipped)"
                )
                self.stdout.write(
                    self.style.WARNING(f"Skipping unknown series: {series_slug}")
                )
                continue

            podcast_slug = SERIES_TO_PODCAST[series_slug]
            podcast = podcast_cache.get(podcast_slug)
            if podcast is None:
                all_warnings.append(
                    f"Podcast {podcast_slug} not in cache for series {series_slug}"
                )
                continue

            self.stdout.write(f"--- Series: {series_slug} -> {podcast_slug} ---")

            # Step 3: Walk episode directories (sorted for deterministic numbering)
            episode_dirs = sorted(
                [d for d in series_dir.iterdir() if d.is_dir()],
                key=lambda d: d.name,
            )

            for episode_dir in episode_dirs:
                episode_slug = episode_dir.name
                title = self._derive_title(episode_slug)

                self.stdout.write(f'  Episode: {episode_slug} -> "{title}"')

                # Create or get the Episode
                if self.dry_run:
                    try:
                        episode = Episode.objects.get(
                            podcast=podcast, slug=episode_slug
                        )
                        episodes_updated += 1
                        self.stdout.write("    Would update existing episode")
                    except Episode.DoesNotExist:
                        episodes_created += 1
                        self.stdout.write("    Would create episode")
                        # Cannot process files in dry-run without a real episode
                        # but we can still count artifacts
                        ac, au, ws = self._count_artifacts_dry_run(episode_dir)
                        total_artifacts_created += ac
                        all_warnings.extend(ws)
                        continue
                    except Exception:
                        # podcast might not have pk in dry-run
                        episodes_created += 1
                        self.stdout.write("    Would create episode")
                        ac, au, ws = self._count_artifacts_dry_run(episode_dir)
                        total_artifacts_created += ac
                        all_warnings.extend(ws)
                        continue
                else:
                    episode, created = Episode.objects.update_or_create(
                        podcast=podcast,
                        slug=episode_slug,
                        defaults={
                            "title": title,
                        },
                    )
                    if created:
                        episodes_created += 1
                        self.stdout.write(
                            f"    Created episode #{episode.episode_number}"
                        )
                    else:
                        episodes_updated += 1
                        self.stdout.write(
                            f"    Updated episode #{episode.episode_number}"
                        )

                # Process files
                fields_populated, field_warnings = populate_episode_fields(
                    episode,
                    episode_dir,
                    self.dry_run,
                    self.verbose,
                    self.stdout.write,
                )
                all_warnings.extend(field_warnings)

                if fields_populated and self.verbose:
                    self.stdout.write(f"    Fields: {', '.join(fields_populated)}")

                arts_created, arts_updated, art_warnings = create_artifacts(
                    episode,
                    episode_dir,
                    self.dry_run,
                    self.verbose,
                    self.stdout.write,
                    normalize_title_fn=self._normalize_artifact_title,
                )
                total_artifacts_created += arts_created
                total_artifacts_updated += arts_updated
                all_warnings.extend(art_warnings)

                if self.verbose:
                    self.stdout.write(
                        f"    Artifacts: {arts_created} created, "
                        f"{arts_updated} updated"
                    )

                # Set status and published_at
                published_at = self._get_published_at(episode_dir)
                if not self.dry_run:
                    episode.status = "complete"
                    if not episode.published_at:
                        episode.published_at = published_at
                    episode.save()

            self.stdout.write("")

        # Final report
        self.stdout.write(self.style.SUCCESS("=== Backfill Summary ==="))
        self.stdout.write(
            f"Podcasts: {podcasts_created} created, {podcasts_found} found"
        )
        self.stdout.write(
            f"Episodes: {episodes_created} created, {episodes_updated} updated"
        )
        self.stdout.write(
            f"Artifacts: {total_artifacts_created} created, "
            f"{total_artifacts_updated} updated"
        )

        if all_warnings:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING(f"Warnings ({len(all_warnings)}):"))
            for warning in all_warnings:
                self.stdout.write(self.style.WARNING(f"  - {warning}"))

        if self.dry_run:
            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING("[DRY RUN] No changes were written to the database")
            )

    def _derive_title(self, slug: str) -> str:
        """Derive a human-readable title from an episode slug.

        Removes episode number prefixes (e.g. "ep01-", "ep1-") and converts
        hyphens to spaces with title case.

        Examples:
            "ep01-getting-started" -> "Getting Started"
            "ep10-game-theory" -> "Game Theory"
            "understanding-markets" -> "Understanding Markets"
        """
        title = slug
        # Remove episode number prefix like "ep01-", "ep1-", "ep001-"
        title = re.sub(r"^ep\d+-", "", title)
        # Convert hyphens to spaces and title case
        title = title.replace("-", " ").strip().title()
        return title

    def _get_published_at(self, episode_dir: Path) -> datetime.datetime:
        """Determine the published_at timestamp for an episode.

        Uses the modification time of the newest file in the directory,
        falling back to timezone.now() if no files are found.
        """
        newest_mtime: float = 0.0
        for path in episode_dir.rglob("*"):
            if path.is_file():
                try:
                    mtime = os.path.getmtime(path)
                    if mtime > newest_mtime:
                        newest_mtime = mtime
                except OSError:
                    continue

        if newest_mtime > 0:
            return datetime.datetime.fromtimestamp(newest_mtime, tz=datetime.UTC)
        return timezone.now()

    def _normalize_artifact_title(self, rel_str: str) -> str:
        """Apply legacy naming normalization for backfill.

        Files at the episode root that match known legacy patterns are
        remapped to subdirectory-based titles for consistency with the
        current naming conventions.
        """
        # Only normalize files at the episode root (no directory separator)
        if "/" not in rel_str and rel_str in LEGACY_NAME_NORMALIZATION:
            if self.verbose:
                normalized = LEGACY_NAME_NORMALIZATION[rel_str]
                self.stdout.write(f"      Normalized: {rel_str} -> {normalized}")
                return normalized
            return LEGACY_NAME_NORMALIZATION[rel_str]
        return rel_str

    def _count_artifacts_dry_run(self, episode_dir: Path) -> tuple[int, int, list[str]]:
        """Count artifacts that would be created in dry-run mode
        when we cannot create an Episode object (e.g. podcast not in DB).

        Returns (would_create, would_update, warnings).
        """
        count = 0
        warnings: list[str] = []

        for path in sorted(episode_dir.rglob("*")):
            if not path.is_file():
                continue

            rel_path = path.relative_to(episode_dir)
            rel_str = str(rel_path)

            if path.suffix.lower() in SKIP_EXTENSIONS:
                continue
            if path.name in SKIP_FILES:
                continue
            if path.suffix.lower() == ".html":
                continue
            if path.name.endswith("_chapters.txt"):
                continue
            if path.name.endswith("_chapters.json"):
                continue
            if path.name.endswith("_transcript.json") and len(rel_path.parts) > 1:
                continue
            if rel_str in ("report.md", "sources.md", "transcript.txt"):
                continue

            title = self._normalize_artifact_title(rel_str)
            if self.verbose:
                self.stdout.write(f"      ARTIFACT (dry-run): {title}")
            count += 1

        return count, 0, warnings
