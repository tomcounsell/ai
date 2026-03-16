"""
Bootstrap the local working directory for a draft Episode.

Usage:
    # Pull a draft episode from production and set up the local workspace
    DATABASE_URL=<prod> uv run python manage.py start_episode \
        --podcast algorithms-for-life --episode ep10-game-theory

The command:
  1. Looks up the Episode by (podcast slug, episode slug) — must be status=draft
  2. Creates apps/podcast/pending-episodes/{podcast_slug}/{episode_slug}/
  3. Scaffolds research/, research/documents/, logs/, tmp/ subdirectories
  4. Pre-populates research/p1-brief.md from the Episode description
  5. Creates logs/prompts.md with episode metadata
  6. Creates sources.md template
  7. Sets Episode.status to in_progress
"""

from datetime import date
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.podcast.models import Episode


class Command(BaseCommand):
    help = (
        "Set up a local working directory for a draft episode and mark it in-progress"
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--podcast",
            required=True,
            help="Slug of the podcast (e.g. 'algorithms-for-life')",
        )
        parser.add_argument(
            "--episode",
            required=True,
            help="Slug of the episode (e.g. 'ep10-game-theory')",
        )

    def handle(self, *args, **options) -> None:
        podcast_slug: str = options["podcast"]
        episode_slug: str = options["episode"]

        # ── 1. Look up the episode ──────────────────────────────────
        try:
            episode = Episode.objects.select_related("podcast").get(
                podcast__slug=podcast_slug,
                slug=episode_slug,
            )
        except Episode.DoesNotExist:
            raise CommandError(
                f"No episode found with podcast slug '{podcast_slug}' "
                f"and episode slug '{episode_slug}'"
            )

        if episode.status != "draft":
            raise CommandError(
                f"Episode '{episode.title}' has status '{episode.status}' "
                f"(expected 'draft')"
            )

        # ── 1b. Create workflow tracking and p1-brief artifact in DB
        from apps.podcast.services.setup import setup_episode

        try:
            setup_episode(episode.id)
            self.stdout.write(
                self.style.SUCCESS("Created workflow tracking in database")
            )
        except Exception as e:
            self.stdout.write(
                self.style.WARNING(f"Could not create workflow tracking: {e}")
            )

        # ── 2. Create the local working directory ───────────────────
        base_dir: Path = (
            settings.BASE_DIR
            / "apps"
            / "podcast"
            / "pending-episodes"
            / podcast_slug
            / episode_slug
        )

        if base_dir.exists():
            self.stdout.write(
                self.style.WARNING(
                    f"Directory already exists: {base_dir}\n"
                    "  Only missing files will be created."
                )
            )

        subdirs = [
            base_dir / "research" / "documents",
            base_dir / "logs",
            base_dir / "tmp",
        ]
        for d in subdirs:
            d.mkdir(parents=True, exist_ok=True)

        # ── 3. research/p1-brief.md ─────────────────────────────────
        brief_path = base_dir / "research" / "p1-brief.md"
        if not brief_path.exists():
            brief_path.write_text(
                f"# {episode.title}\n\n{episode.description}\n",
                encoding="utf-8",
            )

        # ── 4. logs/prompts.md ──────────────────────────────────────
        prompts_path = base_dir / "logs" / "prompts.md"
        if not prompts_path.exists():
            ep_number = (
                episode.episode_number if episode.episode_number is not None else "TBD"
            )
            prompts_path.write_text(
                "# Episode Prompts Log\n"
                "\n"
                f"- **Podcast**: {episode.podcast.title}\n"
                f"- **Episode**: {ep_number} - {episode.title}\n"
                f"- **Date**: {date.today().isoformat()}\n",
                encoding="utf-8",
            )

        # ── 5. sources.md ───────────────────────────────────────────
        sources_path = base_dir / "sources.md"
        if not sources_path.exists():
            sources_path.write_text(
                "# Sources\n\n<!-- Add sources as they are found during research -->\n",
                encoding="utf-8",
            )

        # ── 6. Transition to in_progress ────────────────────────────
        episode.status = "in_progress"
        episode.save(update_fields=["status", "modified_at"])

        # ── 7. Enqueue the production task pipeline ──────────────────
        from apps.podcast.tasks import produce_episode

        result = produce_episode.enqueue(episode_id=episode.id)
        self.stdout.write(
            self.style.SUCCESS(
                f"Enqueued produce_episode task (result id: {result.id})"
            )
        )

        # ── 8. Summary ──────────────────────────────────────────────
        ep_number = (
            episode.episode_number if episode.episode_number is not None else "TBD"
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"\nEpisode started:\n"
                f"  Number : {ep_number}\n"
                f"  Title  : {episode.title}\n"
                f"  Dir    : {base_dir}\n"
            )
        )
