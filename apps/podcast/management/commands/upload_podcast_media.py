"""
Upload podcast audio files and cover images to Supabase storage.

Walks episodes in the database, finds matching local files in the source
directory, uploads them directly to Supabase (bypassing STORAGE_BACKEND
setting), and updates the Episode record with the new URL, file size,
and audio duration.

Usage:
    # Preview (no uploads)
    uv run python manage.py upload_podcast_media \\
        --source-dir /path/to/research/podcast/episodes --dry-run

    # Upload for real (uses SUPABASE_* settings from Django config)
    uv run python manage.py upload_podcast_media \\
        --source-dir /path/to/research/podcast/episodes

    # Only upload audio (skip cover images)
    uv run python manage.py upload_podcast_media --source-dir ... --audio-only

    # Also upload podcast-level cover images
    uv run python manage.py upload_podcast_media --source-dir ... --podcast-covers

Idempotent: skips episodes that already have a Supabase URL.
"""

import mimetypes
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.podcast.models import Podcast

# Old URL prefix that needs replacing
OLD_URL_PREFIX = "https://research.bwforce.ai/"

# Mapping from podcast slug -> series directory name(s) in the source repo
PODCAST_TO_SERIES = {
    "yudame-research": [
        "active-recovery",
        "algorithms-for-life",
        "building-a-micro-school",
        "cardiovascular-health",
        "kindergarten-first-principles",
    ],
    "satsol": ["solomon-islands-telecom-series"],
    "soul-world-bank": ["stablecoin-series"],
}


def _find_audio_file(episode_dir: Path) -> Path | None:
    """Find the primary audio file in an episode directory."""
    # Prefer .mp3 over .m4a
    for ext in (".mp3", ".m4a"):
        candidates = list(episode_dir.glob(f"*{ext}"))
        if candidates:
            # If there are multiple, prefer the one matching the episode slug
            # or the largest file
            candidates.sort(key=lambda p: p.stat().st_size, reverse=True)
            return candidates[0]
    return None


def _find_cover_image(episode_dir: Path) -> Path | None:
    """Find cover image in an episode directory."""
    for name in ("cover.png", "cover.jpg", "cover.jpeg", "cover.webp"):
        path = episode_dir / name
        if path.exists():
            return path
    return None


def _get_audio_duration(file_path: Path) -> int | None:
    """Get audio duration in seconds using mutagen. Returns None if unavailable."""
    try:
        from mutagen import File as MutagenFile

        audio = MutagenFile(str(file_path))
        if audio and audio.info:
            return int(audio.info.length)
    except Exception:
        pass
    return None


def _needs_upload(url: str | None) -> bool:
    """Check if a URL is missing, local, or points to the old broken host."""
    if not url:
        return True
    if url.startswith(OLD_URL_PREFIX):
        return True
    if url.startswith("/media/"):
        return True
    return False


class Command(BaseCommand):
    help = "Upload podcast audio and cover images to Supabase storage"

    def add_arguments(self, parser):
        parser.add_argument(
            "--source-dir",
            required=True,
            help="Path to research/podcast/episodes/ directory",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview uploads without actually uploading",
        )
        parser.add_argument(
            "--audio-only",
            action="store_true",
            help="Only upload audio files, skip cover images",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-upload even if URL looks valid",
        )
        parser.add_argument(
            "--podcast-covers",
            action="store_true",
            help="Also upload podcast-level cover images",
        )

    def handle(self, **options):
        source_dir = Path(options["source_dir"])
        dry_run = options["dry_run"]
        audio_only = options["audio_only"]
        force = options["force"]
        podcast_covers = options.get("podcast_covers", False)

        if not source_dir.is_dir():
            raise CommandError(f"Source directory not found: {source_dir}")

        if dry_run:
            self.stdout.write("[DRY RUN] No uploads will be made\n")

        # Validate Supabase config before starting
        bucket_name = settings.SUPABASE_PUBLIC_BUCKET_NAME
        if not dry_run:
            for key in (
                "SUPABASE_PROJECT_URL",
                "SUPABASE_SERVICE_ROLE_KEY",
                "SUPABASE_PUBLIC_BUCKET_NAME",
            ):
                if not getattr(settings, key, None):
                    raise CommandError(f"Missing setting: {key}")

            from supabase import create_client

            supabase = create_client(
                settings.SUPABASE_PROJECT_URL,
                settings.SUPABASE_SERVICE_ROLE_KEY,
            )
            storage = supabase.storage.from_(bucket_name)
            self.stdout.write(f"Using Supabase bucket: {bucket_name}")

        # Build a lookup: (series_dir, episode_slug) -> episode_dir path
        dir_lookup = self._build_dir_lookup(source_dir)

        stats = {
            "audio_uploaded": 0,
            "audio_skipped": 0,
            "cover_uploaded": 0,
            "cover_skipped": 0,
            "audio_not_found": 0,
            "cover_not_found": 0,
            "duration_populated": 0,
            "podcast_cover_uploaded": 0,
            "podcast_cover_skipped": 0,
            "errors": 0,
        }

        for podcast in Podcast.objects.all():
            series_dirs = PODCAST_TO_SERIES.get(podcast.slug, [])
            if not series_dirs:
                self.stdout.write(
                    f"  Skipping podcast {podcast.slug}: no series mapping"
                )
                continue

            self.stdout.write(f"\n--- Podcast: {podcast.title} ({podcast.slug}) ---")

            for episode in podcast.episodes.order_by("episode_number"):
                self.stdout.write(f"  Episode {episode.episode_number}: {episode.slug}")

                # Find the episode directory
                ep_dir = self._find_episode_dir(
                    episode.slug, series_dirs, dir_lookup, source_dir
                )
                if not ep_dir:
                    self.stdout.write(
                        f"    WARNING: No directory found for {episode.slug}"
                    )
                    stats["audio_not_found"] += 1
                    stats["cover_not_found"] += 1
                    continue

                # Upload audio
                if force or _needs_upload(episode.audio_url):
                    audio_path = _find_audio_file(ep_dir)
                    if audio_path:
                        ext = audio_path.suffix
                        storage_key = (
                            f"podcast/{podcast.slug}/{episode.slug}/audio{ext}"
                        )
                        content_type = (
                            mimetypes.guess_type(str(audio_path))[0] or "audio/mpeg"
                        )
                        file_size = audio_path.stat().st_size
                        duration = _get_audio_duration(audio_path)

                        if dry_run:
                            self.stdout.write(
                                f"    [DRY] Would upload audio: {audio_path.name} "
                                f"({file_size / 1024 / 1024:.1f} MB) -> {storage_key}"
                            )
                        else:
                            self.stdout.write(
                                f"    Uploading audio: {audio_path.name} "
                                f"({file_size / 1024 / 1024:.1f} MB)..."
                            )
                            try:
                                audio_bytes = audio_path.read_bytes()
                                storage.upload(
                                    storage_key,
                                    file=audio_bytes,
                                    file_options={
                                        "content-type": content_type,
                                        "upsert": "true",
                                    },
                                )
                                url = storage.get_public_url(storage_key)
                                episode.audio_url = url
                                episode.audio_file_size_bytes = file_size
                                if duration:
                                    episode.audio_duration_seconds = duration
                                    stats["duration_populated"] += 1
                                episode.save(
                                    update_fields=[
                                        "audio_url",
                                        "audio_file_size_bytes",
                                        "audio_duration_seconds",
                                        "modified_at",
                                    ]
                                )
                                self.stdout.write(f"    -> {url}")
                            except Exception as e:
                                self.stderr.write(f"    ERROR uploading audio: {e}")
                                stats["errors"] += 1
                                continue

                        stats["audio_uploaded"] += 1
                    else:
                        self.stdout.write(f"    No audio file found in {ep_dir}")
                        stats["audio_not_found"] += 1
                else:
                    self.stdout.write("    Audio URL OK, skipping")
                    stats["audio_skipped"] += 1

                # Upload cover image
                if not audio_only:
                    if force or _needs_upload(episode.cover_image_url):
                        cover_path = _find_cover_image(ep_dir)
                        if cover_path:
                            ext = cover_path.suffix
                            storage_key = (
                                f"podcast/{podcast.slug}/{episode.slug}/cover{ext}"
                            )
                            content_type = (
                                mimetypes.guess_type(str(cover_path))[0] or "image/png"
                            )

                            if dry_run:
                                self.stdout.write(
                                    f"    [DRY] Would upload cover: {cover_path.name} "
                                    f"-> {storage_key}"
                                )
                            else:
                                try:
                                    cover_bytes = cover_path.read_bytes()
                                    storage.upload(
                                        storage_key,
                                        file=cover_bytes,
                                        file_options={
                                            "content-type": content_type,
                                            "upsert": "true",
                                        },
                                    )
                                    url = storage.get_public_url(storage_key)
                                    episode.cover_image_url = url
                                    episode.save(
                                        update_fields=[
                                            "cover_image_url",
                                            "modified_at",
                                        ]
                                    )
                                    self.stdout.write(f"    Cover -> {url}")
                                except Exception as e:
                                    self.stderr.write(f"    ERROR uploading cover: {e}")
                                    stats["errors"] += 1

                            stats["cover_uploaded"] += 1
                        else:
                            stats["cover_not_found"] += 1
                    else:
                        stats["cover_skipped"] += 1

        # Upload podcast-level cover images
        if podcast_covers:
            storage_client = None if dry_run else storage
            self._upload_podcast_covers(
                source_dir, dry_run, force, storage_client, stats
            )

        # Summary
        self.stdout.write("\n=== Upload Summary ===")
        self.stdout.write(
            f"Audio:    {stats['audio_uploaded']} uploaded, "
            f"{stats['audio_skipped']} skipped, "
            f"{stats['audio_not_found']} not found"
        )
        self.stdout.write(
            f"Covers:   {stats['cover_uploaded']} uploaded, "
            f"{stats['cover_skipped']} skipped, "
            f"{stats['cover_not_found']} not found"
        )
        if podcast_covers:
            self.stdout.write(
                f"Podcast covers: {stats['podcast_cover_uploaded']} uploaded, "
                f"{stats['podcast_cover_skipped']} skipped"
            )
        self.stdout.write(f"Duration: {stats['duration_populated']} populated")
        if stats["errors"]:
            self.stderr.write(f"Errors:   {stats['errors']}")
        if dry_run:
            self.stdout.write("\n[DRY RUN] No changes were made")

    def _build_dir_lookup(self, source_dir: Path) -> dict[str, Path]:
        """Build a lookup of episode_slug -> directory path."""
        lookup = {}
        for series_dir in source_dir.iterdir():
            if not series_dir.is_dir():
                continue
            for ep_dir in series_dir.iterdir():
                if not ep_dir.is_dir():
                    continue
                lookup[ep_dir.name] = ep_dir
        return lookup

    def _find_episode_dir(
        self,
        episode_slug: str,
        series_dirs: list[str],
        dir_lookup: dict[str, Path],
        source_dir: Path,
    ) -> Path | None:
        """Find the episode directory by trying various naming patterns."""
        # Direct match by slug
        if episode_slug in dir_lookup:
            return dir_lookup[episode_slug]

        # Try with series prefix removed (e.g., "ep1-foundations" in "active-recovery/")
        for series_name in series_dirs:
            series_path = source_dir / series_name
            if not series_path.is_dir():
                continue
            for ep_dir in series_path.iterdir():
                if not ep_dir.is_dir():
                    continue
                # Match by slug
                if ep_dir.name == episode_slug:
                    return ep_dir
                # Match by slug contained in dir name
                if episode_slug in ep_dir.name or ep_dir.name in episode_slug:
                    return ep_dir
        return None

    def _find_podcast_cover(self, podcast_slug: str, source_dir: Path) -> Path | None:
        """Find podcast-level cover image using priority-based lookup.

        Priority:
        1. {source_dir}/../cover.png -- global cover (yudame-research)
        2. {source_dir}/../{podcast_slug}/cover.png -- per-podcast cover
        """
        parent = source_dir.parent

        # Priority 1: global cover in parent directory
        global_cover = parent / "cover.png"
        if global_cover.exists():
            return global_cover

        # Priority 2: per-podcast cover by slug
        slug_cover = parent / podcast_slug / "cover.png"
        if slug_cover.exists():
            return slug_cover

        return None

    def _upload_podcast_covers(
        self,
        source_dir: Path,
        dry_run: bool,
        force: bool,
        storage,
        stats: dict,
    ) -> None:
        """Upload podcast-level cover images to Supabase."""
        self.stdout.write("\n--- Podcast Covers ---")

        for podcast in Podcast.objects.all():
            existing_url = podcast.cover_image_url or ""
            if existing_url.startswith(("http://", "https://")) and not force:
                self.stdout.write(
                    f"  {podcast.title}: Already uploaded: {existing_url}"
                )
                stats["podcast_cover_skipped"] += 1
                continue

            cover_path = self._find_podcast_cover(podcast.slug, source_dir)
            if not cover_path:
                self.stdout.write(f"  {podcast.title}: No cover file found")
                stats["podcast_cover_skipped"] += 1
                continue

            storage_key = f"podcast/{podcast.slug}/cover.png"

            if dry_run:
                self.stdout.write(
                    f"  {podcast.title}: [DRY] Would upload "
                    f"{cover_path} -> {storage_key}"
                )
            else:
                try:
                    content_type = (
                        mimetypes.guess_type(str(cover_path))[0] or "image/png"
                    )
                    cover_bytes = cover_path.read_bytes()
                    storage.upload(
                        storage_key,
                        file=cover_bytes,
                        file_options={
                            "content-type": content_type,
                            "upsert": "true",
                        },
                    )
                    url = storage.get_public_url(storage_key)
                    podcast.cover_image_url = url
                    podcast.save(update_fields=["cover_image_url", "modified_at"])
                    self.stdout.write(f"  {podcast.title}: Cover -> {url}")
                except Exception as e:
                    self.stderr.write(f"  {podcast.title}: ERROR uploading cover: {e}")
                    stats["errors"] += 1
                    continue

            stats["podcast_cover_uploaded"] += 1
