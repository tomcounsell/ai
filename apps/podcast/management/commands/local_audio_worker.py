"""
Local audio worker that polls production for pending audio generation jobs.

Usage:
    # Poll every 5 seconds with up to 3 concurrent jobs
    uv run python manage.py local_audio_worker --base-url https://ai.yuda.me

    # Custom interval and concurrency
    uv run python manage.py local_audio_worker --base-url https://ai.yuda.me --interval 10 --max-concurrent 1

The worker:
  1. Polls GET {base_url}/api/podcast/pending-audio/ for episodes needing audio
  2. For each pending episode, generates audio via notebooklm-mcp-cli
  3. Uploads the resulting MP3 to storage via store_file()
  4. Calls POST {base_url}/api/podcast/episodes/{id}/audio-callback/ with the audio URL
  5. Repeats until interrupted (Ctrl+C)

Requires:
  - LOCAL_WORKER_API_KEY in environment/settings (must match production)
  - notebooklm-mcp-cli installed and authenticated (nlm login)
  - Storage backend configured (Supabase for production uploads)
"""

import logging
import signal
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.common.services.storage import store_file

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Poll production for pending audio jobs and generate locally"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._shutdown = False

    def add_arguments(self, parser):
        parser.add_argument(
            "--base-url",
            required=True,
            help="Base URL of the production server (e.g. https://ai.yuda.me)",
        )
        parser.add_argument(
            "--interval",
            type=int,
            default=5,
            help="Poll interval in seconds (default: 5)",
        )
        parser.add_argument(
            "--max-concurrent",
            type=int,
            default=3,
            help="Maximum concurrent audio generation jobs (default: 3)",
        )

    def handle(self, *args, **options):
        base_url = options["base_url"].rstrip("/")
        interval = options["interval"]
        max_concurrent = options["max_concurrent"]

        api_key = getattr(settings, "LOCAL_WORKER_API_KEY", "")
        if not api_key:
            raise CommandError(
                "LOCAL_WORKER_API_KEY not configured. "
                "Add it to .env.local and settings."
            )

        # Graceful shutdown on SIGINT/SIGTERM
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        self.stdout.write(
            self.style.SUCCESS(
                f"Local audio worker started\n"
                f"  Server: {base_url}\n"
                f"  Interval: {interval}s\n"
                f"  Max concurrent: {max_concurrent}\n"
            )
        )

        # Track in-progress episode IDs to avoid double-processing
        in_progress: set[int] = set()

        with ThreadPoolExecutor(max_workers=max_concurrent) as executor:
            futures: dict = {}

            while not self._shutdown:
                # Check completed futures
                done_futures = [f for f in futures if f.done()]
                for future in done_futures:
                    ep_id = futures.pop(future)
                    in_progress.discard(ep_id)
                    try:
                        future.result()
                        self.stdout.write(
                            self.style.SUCCESS(f"  Completed audio for episode {ep_id}")
                        )
                    except Exception as exc:
                        self.stderr.write(
                            self.style.ERROR(
                                f"  Failed audio for episode {ep_id}: {exc}"
                            )
                        )

                # Poll for new work if we have capacity
                if len(futures) < max_concurrent:
                    try:
                        episodes = self._poll_pending(base_url, api_key)
                    except Exception as exc:
                        self.stderr.write(self.style.ERROR(f"  Poll error: {exc}"))
                        episodes = []

                    for ep in episodes:
                        ep_id = ep["id"]
                        if ep_id in in_progress:
                            continue
                        if len(futures) >= max_concurrent:
                            break

                        in_progress.add(ep_id)
                        future = executor.submit(
                            self._process_episode, base_url, api_key, ep
                        )
                        futures[future] = ep_id
                        self.stdout.write(
                            f"  Started audio generation for episode {ep_id}: "
                            f"{ep.get('title', 'unknown')}"
                        )

                # Sleep in small increments for responsiveness
                for _ in range(interval * 10):
                    if self._shutdown:
                        break
                    time.sleep(0.1)

        self.stdout.write(self.style.WARNING("Worker shutting down..."))

    def _signal_handler(self, signum, frame):
        self._shutdown = True

    def _poll_pending(self, base_url: str, api_key: str) -> list[dict]:
        """Poll the server for pending audio jobs."""
        response = httpx.get(
            f"{base_url}/api/podcast/pending-audio/",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        return data.get("episodes", [])

    def _process_episode(self, base_url: str, api_key: str, episode_data: dict) -> None:
        """Generate audio for one episode and call back."""
        episode_id = episode_data["id"]
        title = episode_data.get("title", "unknown")
        slug = episode_data.get("slug", f"episode-{episode_id}")
        podcast_slug = episode_data.get("podcast_slug", "podcast")
        is_public = episode_data.get("is_public", True)
        sources = episode_data.get("sources", {})

        logger.info("Processing episode %d: %s", episode_id, title)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Write source files
            for filename, content in sources.items():
                (tmpdir_path / filename).write_text(content, encoding="utf-8")

            # Generate audio via notebooklm-mcp-cli
            output_path = tmpdir_path / f"{slug}.mp3"
            self._generate_audio_nlm(tmpdir_path, title, output_path)

            # Read the audio bytes
            audio_bytes = output_path.read_bytes()

            # Upload to storage (public or private based on podcast visibility)
            storage_key = f"podcast/{podcast_slug}/{slug}/audio.mp3"
            audio_url = store_file(
                storage_key, audio_bytes, "audio/mpeg", public=is_public
            )
            logger.info("Uploaded audio for episode %d: %s", episode_id, audio_url)

            # Callback to production
            self._send_callback(
                base_url, api_key, episode_id, audio_url, len(audio_bytes)
            )

    def _generate_audio_nlm(
        self, source_dir: Path, title: str, output_path: Path
    ) -> None:
        """Generate audio using notebooklm-mcp-cli library."""
        try:
            from notebooklm_mcp_cli.core import NotebookLMClient
        except ImportError:
            raise CommandError(
                "notebooklm-mcp-cli not installed. "
                "Install with: pip install notebooklm-mcp-cli"
            )

        client = NotebookLMClient()

        # Create notebook and upload sources
        notebook_id = client.create_notebook(f"Yudame Research: {title}")

        try:
            # Upload all source files from the temp directory
            for source_file in source_dir.iterdir():
                if source_file.is_file() and source_file.suffix == ".md":
                    client.upload_source(
                        notebook_id,
                        source_file.name,
                        source_file.read_text(encoding="utf-8"),
                    )

            # Generate audio
            client.generate_audio(notebook_id)
            client.wait_for_audio(notebook_id, timeout_minutes=30)

            # Download
            client.download_audio(notebook_id, output_path)
        finally:
            try:
                client.delete_notebook(notebook_id)
            except Exception:
                logger.warning(
                    "Failed to clean up notebook %s",
                    notebook_id,
                    exc_info=True,
                )

    def _send_callback(
        self,
        base_url: str,
        api_key: str,
        episode_id: int,
        audio_url: str,
        file_size: int,
    ) -> None:
        """POST the audio result back to production."""
        response = httpx.post(
            f"{base_url}/api/podcast/episodes/{episode_id}/audio-callback/",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "audio_url": audio_url,
                "audio_file_size_bytes": file_size,
            },
            timeout=30,
        )
        response.raise_for_status()
        logger.info(
            "Callback success for episode %d: %s",
            episode_id,
            response.json(),
        )
