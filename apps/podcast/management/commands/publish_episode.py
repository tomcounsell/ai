"""
Publish a locally-produced podcast episode to the database.

Reads all files from an episode working directory, populates Episode fields
(report, sources, transcript, chapters, audio metadata), creates
EpisodeArtifact records for research/plans/logs/companion markdown files,
and marks the episode as complete.

Usage:
    # Test publish against local DB
    uv run python manage.py publish_episode \\
        apps/podcast/pending-episodes/algorithms-for-life/ep10-game-theory/

    # Real publish against production
    DATABASE_URL=<prod> uv run python manage.py publish_episode \\
        apps/podcast/pending-episodes/algorithms-for-life/ep10-game-theory/

Flags:
    --dry-run           Preview without writing to DB
    --verbose           Show each file being processed
    --skip-status-check Allow re-publishing a complete episode

The episode is identified from the directory path: the last two path
components are interpreted as {podcast_slug}/{episode_slug}. The command
looks up the Episode by (podcast__slug, slug) and populates it.

Idempotent: uses update_or_create for artifacts, so re-running overwrites
existing content rather than creating duplicates.
"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.podcast.management.commands._episode_import_utils import (
    create_artifacts,
    populate_episode_fields,
)
from apps.podcast.models import Episode


class Command(BaseCommand):
    help = "Publish an episode from a local working directory to the database"

    def add_arguments(self, parser):
        parser.add_argument(
            "episode_dir",
            type=str,
            help=(
                "Path to the episode working directory. The last two path "
                "components are {podcast_slug}/{episode_slug}."
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
        parser.add_argument(
            "--skip-status-check",
            action="store_true",
            help="Allow re-publishing a complete episode",
        )

    def handle(self, *args, **options):
        episode_dir = Path(options["episode_dir"]).resolve()
        dry_run = options["dry_run"]
        verbose = options["verbose"]
        skip_status_check = options["skip_status_check"]

        if not episode_dir.is_dir():
            raise CommandError(f"Directory not found: {episode_dir}")

        # Extract podcast_slug and episode_slug from path
        episode_slug = episode_dir.name
        podcast_slug = episode_dir.parent.name

        if not podcast_slug or not episode_slug:
            raise CommandError(
                f"Cannot extract podcast/episode slugs from path: {episode_dir}\n"
                f"Expected: .../{{podcast_slug}}/{{episode_slug}}/"
            )

        self.stdout.write(f"Publishing: podcast={podcast_slug}, episode={episode_slug}")
        self.stdout.write(f"Directory: {episode_dir}")

        if dry_run:
            self.stdout.write(self.style.WARNING("[DRY RUN] No changes will be made"))

        # Look up the Episode
        try:
            episode = Episode.objects.select_related("podcast").get(
                podcast__slug=podcast_slug, slug=episode_slug
            )
        except Episode.DoesNotExist:
            raise CommandError(
                f"Episode not found: podcast_slug={podcast_slug!r}, "
                f"slug={episode_slug!r}"
            )

        self.stdout.write(f"Found episode: {episode}")
        self.stdout.write(f"Current status: {episode.status}")

        # Status check
        if not skip_status_check and episode.status == "complete":
            raise CommandError(
                "Episode is already complete. Use --skip-status-check to re-publish."
            )

        # Process Episode field files
        fields_populated, warnings = populate_episode_fields(
            episode, episode_dir, dry_run, verbose, self.stdout.write
        )

        # Process artifact files
        artifacts_created, artifacts_updated, artifact_warnings = create_artifacts(
            episode, episode_dir, dry_run, verbose, self.stdout.write
        )
        warnings.extend(artifact_warnings)

        # Mark episode as published via service layer
        if not dry_run:
            from apps.podcast.services.publishing import (
                publish_episode as service_publish,
            )

            try:
                service_publish(episode.id)
                self.stdout.write(
                    self.style.SUCCESS("Episode published via service layer")
                )
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"Service publish failed: {e}"))
                # Fallback: set status directly if service fails
                episode.status = "complete"
                if not episode.published_at:
                    episode.published_at = timezone.now()
                episode.save()

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("=== Publish Summary ==="))
        self.stdout.write(
            f"Episode fields populated: {', '.join(fields_populated) or 'none'}"
        )
        self.stdout.write(f"Artifacts created: {artifacts_created}")
        self.stdout.write(f"Artifacts updated: {artifacts_updated}")
        self.stdout.write("Status: complete")
        if episode.published_at:
            self.stdout.write(f"Published at: {episode.published_at}")

        if warnings:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING(f"Warnings ({len(warnings)}):"))
            for warning in warnings:
                self.stdout.write(self.style.WARNING(f"  - {warning}"))

        if dry_run:
            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING("[DRY RUN] No changes were written to the database")
            )
