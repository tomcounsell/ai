"""
Tests for the local_audio_worker management command.

Tests cover:
- Command fails without LOCAL_WORKER_API_KEY
- _poll_pending returns correct data
- _send_callback sends correct payload
- _process_episode orchestrates generation, upload, and callback
- Signal handling sets _shutdown flag
"""

import signal
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from apps.podcast.management.commands.local_audio_worker import Command


class LocalAudioWorkerAPIKeyTestCase(TestCase):
    """Test that the command requires LOCAL_WORKER_API_KEY."""

    @override_settings(LOCAL_WORKER_API_KEY="")
    def test_fails_without_api_key_empty(self):
        """Command raises CommandError when LOCAL_WORKER_API_KEY is empty."""
        with self.assertRaises(CommandError) as ctx:
            call_command(
                "local_audio_worker",
                "--base-url",
                "https://app.bwforce.ai",
                stdout=StringIO(),
                stderr=StringIO(),
            )
        self.assertIn("LOCAL_WORKER_API_KEY", str(ctx.exception))

    def test_fails_without_api_key_missing(self):
        """Command raises CommandError when LOCAL_WORKER_API_KEY is not set."""
        # Ensure the setting does not exist
        with self.settings(**{"LOCAL_WORKER_API_KEY": ""}):
            with self.assertRaises(CommandError) as ctx:
                call_command(
                    "local_audio_worker",
                    "--base-url",
                    "https://app.bwforce.ai",
                    stdout=StringIO(),
                    stderr=StringIO(),
                )
            self.assertIn("LOCAL_WORKER_API_KEY", str(ctx.exception))


class PollPendingTestCase(TestCase):
    """Test the _poll_pending method."""

    @patch("apps.podcast.management.commands.local_audio_worker.httpx.get")
    def test_poll_pending_returns_episodes(self, mock_get):
        """_poll_pending parses the response and returns episodes list."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "episodes": [
                {"id": 1, "title": "Test Episode", "slug": "test-ep"},
                {"id": 2, "title": "Another Episode", "slug": "another-ep"},
            ]
        }
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        cmd = Command()
        episodes = cmd._poll_pending("https://app.bwforce.ai", "test-key")

        self.assertEqual(len(episodes), 2)
        self.assertEqual(episodes[0]["id"], 1)
        self.assertEqual(episodes[1]["title"], "Another Episode")

        mock_get.assert_called_once_with(
            "https://app.bwforce.ai/api/podcast/pending-audio/",
            headers={"Authorization": "Bearer test-key"},
            timeout=30,
        )

    @patch("apps.podcast.management.commands.local_audio_worker.httpx.get")
    def test_poll_pending_returns_empty_on_no_episodes(self, mock_get):
        """_poll_pending returns empty list when no episodes pending."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"episodes": []}
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        cmd = Command()
        episodes = cmd._poll_pending("https://app.bwforce.ai", "test-key")

        self.assertEqual(episodes, [])

    @patch("apps.podcast.management.commands.local_audio_worker.httpx.get")
    def test_poll_pending_raises_on_http_error(self, mock_get):
        """_poll_pending raises when the server returns an error."""
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Server Error",
            request=MagicMock(),
            response=MagicMock(status_code=500),
        )
        mock_get.return_value = mock_response

        cmd = Command()
        with self.assertRaises(httpx.HTTPStatusError):
            cmd._poll_pending("https://app.bwforce.ai", "test-key")


class SendCallbackTestCase(TestCase):
    """Test the _send_callback method."""

    @patch("apps.podcast.management.commands.local_audio_worker.httpx.post")
    def test_send_callback_correct_payload(self, mock_post):
        """_send_callback sends the correct URL and payload."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"status": "ok"}
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        cmd = Command()
        cmd._send_callback(
            "https://app.bwforce.ai",
            "test-key",
            episode_id=42,
            audio_url="https://storage.example.com/podcast/audio.mp3",
            file_size=1024000,
        )

        mock_post.assert_called_once_with(
            "https://app.bwforce.ai/api/podcast/episodes/42/audio-callback/",
            headers={
                "Authorization": "Bearer test-key",
                "Content-Type": "application/json",
            },
            json={
                "audio_url": "https://storage.example.com/podcast/audio.mp3",
                "audio_file_size_bytes": 1024000,
            },
            timeout=30,
        )

    @patch("apps.podcast.management.commands.local_audio_worker.httpx.post")
    def test_send_callback_raises_on_error(self, mock_post):
        """_send_callback raises when the server returns an error."""
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Not Found",
            request=MagicMock(),
            response=MagicMock(status_code=404),
        )
        mock_post.return_value = mock_response

        cmd = Command()
        with self.assertRaises(httpx.HTTPStatusError):
            cmd._send_callback(
                "https://app.bwforce.ai",
                "test-key",
                episode_id=999,
                audio_url="https://storage.example.com/audio.mp3",
                file_size=100,
            )


class ProcessEpisodeTestCase(TestCase):
    """Test the _process_episode method end-to-end with mocks."""

    @patch("apps.podcast.management.commands.local_audio_worker.Command._send_callback")
    @patch(
        "apps.podcast.management.commands.local_audio_worker.store_file",
        return_value="https://storage.example.com/podcast/test-podcast/test-ep/audio.mp3",
    )
    @patch(
        "apps.podcast.management.commands.local_audio_worker.Command._generate_audio_nlm"
    )
    def test_process_episode_full_flow(self, mock_generate, mock_store, mock_callback):
        """_process_episode writes sources, generates audio, uploads, and calls back."""

        # Make _generate_audio_nlm create a fake audio file
        def fake_generate(source_dir, title, output_path, instructions=None):
            output_path.write_bytes(b"fake-audio-content")

        mock_generate.side_effect = fake_generate

        episode_data = {
            "id": 42,
            "title": "Test Episode",
            "slug": "test-ep",
            "podcast_slug": "test-podcast",
            "sources": {
                "briefing.md": "# Briefing\n\nContent here.",
                "research.md": "# Research\n\nFindings here.",
            },
        }

        cmd = Command()
        cmd._process_episode("https://app.bwforce.ai", "test-key", episode_data)

        # Verify generate was called with None instructions (no content_plan.md)
        mock_generate.assert_called_once()
        call_args = mock_generate.call_args
        self.assertEqual(call_args[0][1], "Test Episode")
        self.assertTrue(str(call_args[0][2]).endswith("test-ep.mp3"))
        self.assertIsNone(call_args[0][3])  # no content_plan.md = None instructions

        # Verify store_file was called with correct key and content type
        mock_store.assert_called_once()
        store_args = mock_store.call_args
        self.assertEqual(store_args[0][0], "podcast/test-podcast/test-ep/audio.mp3")
        self.assertEqual(store_args[0][1], b"fake-audio-content")
        self.assertEqual(store_args[0][2], "audio/mpeg")

        # Verify callback was called
        mock_callback.assert_called_once_with(
            "https://app.bwforce.ai",
            "test-key",
            42,
            "https://storage.example.com/podcast/test-podcast/test-ep/audio.mp3",
            len(b"fake-audio-content"),
        )

    @patch("apps.podcast.management.commands.local_audio_worker.Command._send_callback")
    @patch(
        "apps.podcast.management.commands.local_audio_worker.store_file",
        return_value="https://storage.example.com/audio.mp3",
    )
    @patch(
        "apps.podcast.management.commands.local_audio_worker.Command._generate_audio_nlm"
    )
    def test_process_episode_writes_source_files(
        self, mock_generate, mock_store, mock_callback
    ):
        """_process_episode writes all source files to temp directory."""
        written_files = {}

        def fake_generate(source_dir, title, output_path, instructions=None):
            # Capture which files were written to the temp dir
            for f in source_dir.iterdir():
                if f.is_file():
                    written_files[f.name] = f.read_text(encoding="utf-8")
            output_path.write_bytes(b"audio")

        mock_generate.side_effect = fake_generate

        episode_data = {
            "id": 1,
            "title": "Source Test",
            "slug": "source-test",
            "podcast_slug": "pod",
            "sources": {
                "brief.md": "Brief content",
                "notes.md": "Notes content",
            },
        }

        cmd = Command()
        cmd._process_episode("https://app.bwforce.ai", "key", episode_data)

        self.assertIn("brief.md", written_files)
        self.assertEqual(written_files["brief.md"], "Brief content")
        self.assertIn("notes.md", written_files)
        self.assertEqual(written_files["notes.md"], "Notes content")


class SignalHandlerTestCase(TestCase):
    """Test that signal handling sets the _shutdown flag."""

    def test_signal_handler_sets_shutdown(self):
        """_signal_handler sets _shutdown to True."""
        cmd = Command()
        self.assertFalse(cmd._shutdown)

        cmd._signal_handler(signal.SIGINT, None)

        self.assertTrue(cmd._shutdown)

    def test_signal_handler_with_sigterm(self):
        """_signal_handler works with SIGTERM as well."""
        cmd = Command()
        self.assertFalse(cmd._shutdown)

        cmd._signal_handler(signal.SIGTERM, None)

        self.assertTrue(cmd._shutdown)


class ExtractInstructionsTestCase(TestCase):
    """Test the _extract_instructions static method."""

    def test_returns_none_without_content_plan(self):
        """Returns None when sources dict has no content_plan.md."""
        result = Command._extract_instructions({"report.md": "# Report"})
        self.assertIsNone(result)

    def test_returns_none_for_empty_sources(self):
        """Returns None for empty sources dict."""
        result = Command._extract_instructions({})
        self.assertIsNone(result)

    def test_extracts_guidance_section(self):
        """Extracts the NotebookLM Guidance section from content plan."""
        content_plan = (
            "## Episode Structure\n\nStructure details.\n\n"
            "## NotebookLM Guidance\n\n"
            "Opening instructions here.\n"
            "Key terms to define.\n\n"
            "## Episode Arc\n\nArc details."
        )
        result = Command._extract_instructions({"content_plan.md": content_plan})
        self.assertIn("NotebookLM Guidance", result)
        self.assertIn("Opening instructions here.", result)
        self.assertNotIn("Episode Arc", result)
        self.assertNotIn("Structure details", result)

    def test_falls_back_to_full_plan(self):
        """Falls back to full content plan when no guidance section found."""
        content_plan = "# Episode Plan\n\nSome plan without guidance section."
        result = Command._extract_instructions({"content_plan.md": content_plan})
        self.assertEqual(result, content_plan)

    def test_handles_underscore_variant(self):
        """Handles notebooklm_guidance section header variant."""
        content_plan = (
            "## notebooklm_guidance\n\nGuidance content.\n\n## Next Section\n\nOther."
        )
        result = Command._extract_instructions({"content_plan.md": content_plan})
        self.assertIn("Guidance content.", result)
        self.assertNotIn("Next Section", result)


class GenerateAudioNLMTestCase(TestCase):
    """Test the _generate_audio_nlm method."""

    def test_raises_command_error_if_nlm_not_installed(self):
        """_generate_audio_nlm raises CommandError when notebooklm-py is missing."""
        cmd = Command()

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            with (  # noqa: SIM117
                patch.dict(
                    "sys.modules",
                    {"notebooklm": None},
                ),
                self.assertRaises((CommandError, ImportError)),
            ):
                cmd._generate_audio_nlm(
                    tmpdir_path,
                    "Test Title",
                    tmpdir_path / "output.mp3",
                )
