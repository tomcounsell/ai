"""
Inspect and update a single episode from the command line.

Usage:
    manage.py ep <slug>                        # show episode summary
    manage.py ep <slug> set field=value ...    # update whitelisted fields
    manage.py ep <slug> brief                  # print p1-brief artifact content
    manage.py ep <slug> setup                  # run setup_episode() service call
"""

import os
import urllib.parse

from django.core.management.base import BaseCommand, CommandError

from apps.podcast.models import Episode, EpisodeArtifact

EDITABLE_FIELDS = {
    "title",
    "slug",
    "description",
    "status",
    "tags",
    "show_notes",
    "episode_number",
}

SUBCOMMANDS = ("show", "set", "brief", "setup")


class Command(BaseCommand):
    help = "Inspect and update a podcast episode by slug"

    def add_arguments(self, parser) -> None:
        parser.add_argument("slug", help="Episode slug")
        parser.add_argument(
            "subcommand",
            nargs="?",
            default="show",
            choices=SUBCOMMANDS,
            help="Action to perform (default: show)",
        )
        parser.add_argument(
            "fields",
            nargs="*",
            help="field=value pairs for the set subcommand",
        )

    def handle(self, *args, **options) -> None:
        self._warn_if_remote_db()

        slug = options["slug"]
        subcommand = options["subcommand"]

        episode = self._get_episode(slug)

        if subcommand == "show":
            self._show(episode)
        elif subcommand == "set":
            self._set(episode, options["fields"])
        elif subcommand == "brief":
            self._brief(episode)
        elif subcommand == "setup":
            self._setup(episode)

    # ── helpers ────────────────────────────────────────────────────────────────

    def _warn_if_remote_db(self) -> None:
        database_url = os.environ.get("DATABASE_URL", "")
        if not database_url:
            return
        host = urllib.parse.urlparse(database_url).hostname or ""
        if host and host not in ("localhost", "127.0.0.1", "::1"):
            self.stderr.write(
                self.style.WARNING(f"WARNING: PRODUCTION DATABASE ({host})")
            )

    def _get_episode(self, slug: str) -> Episode:
        try:
            return Episode.objects.select_related("podcast").get(slug=slug)
        except Episode.DoesNotExist:
            raise CommandError(f"No episode found with slug '{slug}'")

    # ── subcommand handlers ────────────────────────────────────────────────────

    def _show(self, episode: Episode) -> None:
        desc = episode.description or ""
        if len(desc) > 120:
            desc = desc[:120] + "..."

        try:
            wf = episode.workflow
            wf_info = f"{wf.current_step} / {wf.status}"
        except Exception:
            wf_info = "(no workflow)"

        artifact_titles = list(
            episode.artifacts.values_list("title", flat=True).order_by("title")
        )

        self.stdout.write(f"Title         : {episode.title}")
        self.stdout.write(f"Slug          : {episode.slug}")
        self.stdout.write(f"Podcast       : {episode.podcast.title}")
        self.stdout.write(f"Episode #     : {episode.episode_number}")
        self.stdout.write(f"Status        : {episode.status}")
        self.stdout.write(f"Description   : {desc}")
        self.stdout.write(f"Workflow      : {wf_info}")
        self.stdout.write(f"Artifacts     : {artifact_titles or '(none)'}")

    def _set(self, episode: Episode, fields: list[str]) -> None:
        if not fields:
            raise CommandError("set requires at least one field=value argument")

        updates = {}
        for token in fields:
            if "=" not in token:
                raise CommandError(
                    f"Invalid argument: '{token}' — expected field=value"
                )
            field, _, value = token.partition("=")
            if field not in EDITABLE_FIELDS:
                raise CommandError(
                    f"Unknown or non-editable field: '{field}'. "
                    f"Allowed: {sorted(EDITABLE_FIELDS)}"
                )
            updates[field] = value

        for field, value in updates.items():
            setattr(episode, field, value)

        episode.save(update_fields=list(updates.keys()) + ["modified_at"])

        for field, value in updates.items():
            self.stdout.write(self.style.SUCCESS(f"Updated {field} = {value!r}"))

    def _brief(self, episode: Episode) -> None:
        try:
            artifact = episode.artifacts.get(title="p1-brief")
            self.stdout.write(artifact.content)
        except EpisodeArtifact.DoesNotExist:
            self.stdout.write("p1-brief artifact not found for this episode")

    def _setup(self, episode: Episode) -> None:
        from apps.podcast.services.setup import setup_episode

        artifact = setup_episode(episode.pk)
        self.stdout.write(
            self.style.SUCCESS(
                f"Artifact: {artifact.title} ({artifact.word_count} words)"
            )
        )
