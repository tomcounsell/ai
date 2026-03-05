"""
Local audio worker that polls production for pending audio generation jobs.

Usage:
    # Poll every 5 seconds with up to 3 concurrent jobs
    uv run python manage.py local_audio_worker --base-url https://ai.yuda.me

    # Custom interval and concurrency
    uv run python manage.py local_audio_worker --base-url https://ai.yuda.me --interval 10 --max-concurrent 1

The worker:
  1. Polls GET {base_url}/api/podcast/pending-audio/ for episodes needing audio
  2. For each pending episode, generates audio via notebooklm-py
  3. Uploads the resulting MP3 to storage via store_file()
  4. Calls POST {base_url}/api/podcast/episodes/{id}/audio-callback/ with the audio URL
  5. Repeats until interrupted (Ctrl+C)

Requires:
  - LOCAL_WORKER_API_KEY in environment/settings (must match production)
  - notebooklm-py installed and authenticated (notebooklm login)
  - Storage backend configured (Supabase for production uploads)
"""

import asyncio
import logging
import re
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
        # Support both new 'privacy'/'uses_private_bucket' keys and legacy 'is_public'
        if "uses_private_bucket" in episode_data:
            is_private = episode_data["uses_private_bucket"]
        elif "privacy" in episode_data:
            is_private = episode_data["privacy"] == "restricted"
        else:
            # Legacy API response: is_public boolean
            is_private = not episode_data.get("is_public", True)
        sources = episode_data.get("sources", {})

        logger.info("Processing episode %d: %s", episode_id, title)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Write source files
            for filename, content in sources.items():
                (tmpdir_path / filename).write_text(content, encoding="utf-8")

            # Extract content plan for NotebookLM instructions
            instructions = self._extract_instructions(sources)

            # Generate audio via notebooklm-py
            output_path = tmpdir_path / f"{slug}.mp3"
            self._generate_audio_nlm(tmpdir_path, title, output_path, instructions)

            # Read the audio bytes
            audio_bytes = output_path.read_bytes()

            # Upload to storage (public or private based on podcast visibility)
            storage_key = f"podcast/{podcast_slug}/{slug}/audio.mp3"
            audio_url = store_file(
                storage_key, audio_bytes, "audio/mpeg", public=not is_private
            )
            logger.info("Uploaded audio for episode %d: %s", episode_id, audio_url)

            # Callback to production
            self._send_callback(
                base_url, api_key, episode_id, audio_url, len(audio_bytes)
            )

    @staticmethod
    def _extract_instructions(sources: dict[str, str]) -> str | None:
        """Extract NotebookLM instructions from the content plan.

        Looks for content_plan.md in the sources dict and extracts the
        NotebookLM Guidance section if present. Falls back to the full
        content plan text if the guidance section can't be isolated.

        Returns None if no content plan is available.
        """
        content_plan = sources.get("content_plan.md")
        if not content_plan:
            return None

        # Try to extract just the NotebookLM Guidance section
        # The content plan is Markdown with a "## NotebookLM Guidance" or
        # "## notebooklm_guidance" section header
        match = re.search(
            r"^##\s+(?:NotebookLM[_ ]Guidance|notebooklm_guidance)\s*\n(.*?)(?=\n##\s|\Z)",
            content_plan,
            re.MULTILINE | re.DOTALL | re.IGNORECASE,
        )
        if match:
            return match.group(0).strip()

        # Fall back to the full content plan as instructions
        return content_plan

    def _generate_audio_nlm(
        self,
        source_dir: Path,
        title: str,
        output_path: Path,
        instructions: str | None = None,
    ) -> None:
        """Generate audio using notebooklm-py library.

        Uses asyncio.run() to bridge the sync management command with the
        async notebooklm-py client API. Each call creates its own event loop,
        which is safe when called from ThreadPoolExecutor threads.

        Args:
            source_dir: Directory containing .md source files to upload.
            title: Episode title for the NotebookLM notebook.
            output_path: Where to save the generated MP3 file.
            instructions: Optional episode focus instructions for NotebookLM.
                Extracted from the content plan's NotebookLM Guidance section.
        """
        try:
            import notebooklm  # noqa: F401
        except ImportError:
            raise CommandError(
                "notebooklm-py not installed. Install with: uv add notebooklm-py"
            )

        asyncio.run(
            self._generate_audio_async(source_dir, title, output_path, instructions)
        )

    @staticmethod
    async def _generate_audio_async(
        source_dir: Path,
        title: str,
        output_path: Path,
        instructions: str | None = None,
    ) -> None:
        """Async implementation of audio generation via notebooklm-py.

        Args:
            source_dir: Directory containing .md source files to upload.
            title: Episode title for the NotebookLM notebook.
            output_path: Where to save the generated MP3 file.
            instructions: Optional episode focus instructions passed to
                NotebookLM's generate_audio(). Guides the two-host
                conversation structure and content emphasis.
        """
        from notebooklm import NotebookLMClient

        async with await NotebookLMClient.from_storage() as client:
            nb = await client.notebooks.create(f"Yudame Research: {title}")

            try:
                # Upload all source files from the temp directory
                for source_file in sorted(source_dir.iterdir()):
                    if source_file.is_file() and source_file.suffix == ".md":
                        await client.sources.add_text(
                            nb.id,
                            source_file.name,
                            source_file.read_text(encoding="utf-8"),
                            wait=True,
                        )

                # Generate audio overview with optional episode focus instructions
                generate_kwargs: dict = {"notebook_id": nb.id}
                if instructions:
                    generate_kwargs["instructions"] = instructions
                    logger.info(
                        "Generating audio with instructions (%d chars)",
                        len(instructions),
                    )
                status = await client.artifacts.generate_audio(**generate_kwargs)

                # Wait for completion (30 minute timeout = 1800 seconds)
                await client.artifacts.wait_for_completion(
                    nb.id, status.task_id, timeout=1800.0
                )

                # Download the generated audio
                await client.artifacts.download_audio(nb.id, str(output_path))
            finally:
                try:
                    await client.notebooks.delete(nb.id)
                except Exception:
                    logger.warning(
                        "Failed to clean up notebook %s",
                        nb.id,
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
