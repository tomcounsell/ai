"""
Tests for the transcribe tool.

Tests the dual-backend transcription: SuperWhisper (primary) + OpenAI Whisper API (fallback).
Run with: pytest tools/transcribe/tests/ -v
"""

import json
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch


class TestConfiguration:
    """Test tool configuration and setup."""

    def test_manifest_exists(self):
        """manifest.json should exist."""
        manifest_path = Path(__file__).parent.parent / "manifest.json"
        assert manifest_path.exists()

    def test_manifest_valid_json(self):
        """manifest.json should be valid JSON with dual-backend config."""
        manifest_path = Path(__file__).parent.parent / "manifest.json"
        with open(manifest_path) as f:
            manifest = json.load(f)

        assert manifest["name"] == "transcribe"
        assert manifest["type"] == "api"
        assert "transcribe" in manifest["capabilities"]
        # Verify dual-backend is documented
        assert "backends" in manifest
        backend_names = [b["name"] for b in manifest["backends"]]
        assert "superwhisper" in backend_names
        assert "openai-whisper" in backend_names

    def test_readme_exists(self):
        """README.md should exist."""
        readme_path = Path(__file__).parent.parent / "README.md"
        assert readme_path.exists()


class TestTranscribeFunction:
    """Test the Python transcribe() function."""

    def test_import(self):
        """transcribe function should be importable."""
        from tools.transcribe import transcribe

        assert callable(transcribe)

    def test_missing_file(self):
        """Should return error for missing file."""
        from tools.transcribe import transcribe

        result = transcribe("/nonexistent/audio.mp3")
        assert "error" in result
        assert "not found" in result["error"]

    def test_unsupported_format(self):
        """Should return error for unsupported format."""
        from tools.transcribe import transcribe

        with tempfile.NamedTemporaryFile(suffix=".xyz", delete=False) as f:
            f.write(b"fake audio data")
            tmp_path = f.name
        try:
            result = transcribe(tmp_path)
            assert "error" in result
            assert "Unsupported" in result["error"]
        finally:
            os.unlink(tmp_path)

    def test_empty_file(self):
        """Should return error for empty file."""
        from tools.transcribe import transcribe

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            tmp_path = f.name
        try:
            result = transcribe(tmp_path)
            assert "error" in result
            assert "empty" in result["error"].lower()
        finally:
            os.unlink(tmp_path)

    def test_file_too_large(self):
        """Should return error for files exceeding 25MB."""
        from tools.transcribe import transcribe

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            # Write just enough to make stat report > 25MB without actually allocating
            f.seek(26 * 1024 * 1024)
            f.write(b"\0")
            tmp_path = f.name
        try:
            result = transcribe(tmp_path)
            assert "error" in result
            assert "too large" in result["error"].lower() or "Max" in result["error"]
        finally:
            os.unlink(tmp_path)


class TestSuperWhisperAvailability:
    """Test SuperWhisper availability detection."""

    def test_is_superwhisper_available_returns_bool(self):
        """_is_superwhisper_available should return a boolean."""
        from tools.transcribe import _is_superwhisper_available, _superwhisper_available_cache

        # Reset cache to force fresh check
        _superwhisper_available_cache["timestamp"] = 0.0
        result = _is_superwhisper_available()
        assert isinstance(result, bool)

    def test_availability_caching(self):
        """Availability check should be cached for 60s."""
        from tools.transcribe import (
            _is_superwhisper_available,
            _superwhisper_available_cache,
        )

        # Prime the cache
        _superwhisper_available_cache["timestamp"] = time.time()
        _superwhisper_available_cache["available"] = True

        # Should return cached value without running pgrep
        with patch("tools.transcribe.subprocess.run") as mock_run:
            result = _is_superwhisper_available()
            assert result is True
            mock_run.assert_not_called()

    def test_cache_expires(self):
        """Cache should expire after TTL."""
        from tools.transcribe import (
            _is_superwhisper_available,
            _superwhisper_available_cache,
        )

        # Set cache to expired (more than 60s ago)
        _superwhisper_available_cache["timestamp"] = time.time() - 61
        _superwhisper_available_cache["available"] = True

        # Should call pgrep
        with patch("tools.transcribe.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)  # not running
            result = _is_superwhisper_available()
            assert result is False
            mock_run.assert_called_once()


class TestSuperWhisperBackend:
    """Test SuperWhisper transcription backend."""

    def test_missing_recordings_dir(self):
        """Should return None when recordings directory doesn't exist."""
        from tools.transcribe import _transcribe_superwhisper

        with patch("tools.transcribe.SUPERWHISPER_RECORDINGS_DIR", Path("/nonexistent/path")):
            result = _transcribe_superwhisper("/some/audio.ogg")
            assert result is None

    def test_successful_transcription(self):
        """Should return transcription when meta.json appears."""
        from tools.transcribe import _transcribe_superwhisper

        with tempfile.TemporaryDirectory() as tmpdir:
            recordings_dir = Path(tmpdir)

            # Create a fake audio file
            audio_file = Path(tmpdir) / "test.ogg"
            audio_file.write_bytes(b"fake audio data")

            def mock_open_app(*args, **kwargs):
                # Simulate SuperWhisper creating a recording folder with meta.json
                folder = recordings_dir / "1773979999"
                folder.mkdir()
                meta = {
                    "result": "Hello, this is a test transcription.",
                    "duration": 3500,
                    "processingTime": 1200,
                    "modelName": "Ultra (Cloud)",
                }
                (folder / "meta.json").write_text(json.dumps(meta))
                return MagicMock(returncode=0)

            with (
                patch("tools.transcribe.SUPERWHISPER_RECORDINGS_DIR", recordings_dir),
                patch("tools.transcribe.subprocess.run", side_effect=mock_open_app),
                patch("tools.transcribe.SUPERWHISPER_POLL_INTERVAL", 0.01),
            ):
                result = _transcribe_superwhisper(str(audio_file))

            assert result is not None
            assert result["text"] == "Hello, this is a test transcription."
            assert result["duration"] == 3.5  # 3500ms -> 3.5s
            assert result["language"] is None

    def test_empty_result_field(self):
        """Should return None when result field is empty."""
        from tools.transcribe import _transcribe_superwhisper

        with tempfile.TemporaryDirectory() as tmpdir:
            recordings_dir = Path(tmpdir)
            audio_file = Path(tmpdir) / "test.ogg"
            audio_file.write_bytes(b"fake audio data")

            def mock_open_app(*args, **kwargs):
                folder = recordings_dir / "1773979999"
                folder.mkdir()
                meta = {"result": "", "duration": 0}
                (folder / "meta.json").write_text(json.dumps(meta))
                return MagicMock(returncode=0)

            with (
                patch("tools.transcribe.SUPERWHISPER_RECORDINGS_DIR", recordings_dir),
                patch("tools.transcribe.subprocess.run", side_effect=mock_open_app),
                patch("tools.transcribe.SUPERWHISPER_POLL_INTERVAL", 0.01),
            ):
                result = _transcribe_superwhisper(str(audio_file))

            assert result is None

    def test_timeout(self):
        """Should return None after timeout."""
        from tools.transcribe import _transcribe_superwhisper

        with tempfile.TemporaryDirectory() as tmpdir:
            recordings_dir = Path(tmpdir)
            audio_file = Path(tmpdir) / "test.ogg"
            audio_file.write_bytes(b"fake audio data")

            with (
                patch("tools.transcribe.SUPERWHISPER_RECORDINGS_DIR", recordings_dir),
                patch(
                    "tools.transcribe.subprocess.run",
                    return_value=MagicMock(returncode=0),
                ),
                patch("tools.transcribe.SUPERWHISPER_POLL_INTERVAL", 0.01),
                patch("tools.transcribe.SUPERWHISPER_TIMEOUT", 0.05),
            ):
                result = _transcribe_superwhisper(str(audio_file))

            assert result is None


class TestFallbackChain:
    """Test that transcribe() falls back from SuperWhisper to OpenAI."""

    def test_uses_superwhisper_when_available(self):
        """Should use SuperWhisper when it's available and working."""
        from tools.transcribe import transcribe

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            f.write(b"fake audio data")
            tmp_path = f.name

        try:
            sw_result = {"text": "SuperWhisper result", "language": None}
            with (
                patch("tools.transcribe._is_superwhisper_available", return_value=True),
                patch("tools.transcribe._transcribe_superwhisper", return_value=sw_result),
            ):
                result = transcribe(tmp_path)

            assert result["text"] == "SuperWhisper result"
        finally:
            os.unlink(tmp_path)

    def test_falls_back_when_superwhisper_unavailable(self):
        """Should fall back to OpenAI when SuperWhisper is not running."""
        from tools.transcribe import transcribe

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            f.write(b"fake audio data")
            tmp_path = f.name

        try:
            openai_result = {"text": "OpenAI result", "language": "en"}
            with (
                patch("tools.transcribe._is_superwhisper_available", return_value=False),
                patch("tools.transcribe._transcribe_openai", return_value=openai_result),
            ):
                result = transcribe(tmp_path)

            assert result["text"] == "OpenAI result"
        finally:
            os.unlink(tmp_path)

    def test_falls_back_when_superwhisper_fails(self):
        """Should fall back to OpenAI when SuperWhisper returns None."""
        from tools.transcribe import transcribe

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            f.write(b"fake audio data")
            tmp_path = f.name

        try:
            openai_result = {"text": "OpenAI fallback", "language": "en"}
            with (
                patch("tools.transcribe._is_superwhisper_available", return_value=True),
                patch("tools.transcribe._transcribe_superwhisper", return_value=None),
                patch("tools.transcribe._transcribe_openai", return_value=openai_result),
            ):
                result = transcribe(tmp_path)

            assert result["text"] == "OpenAI fallback"
        finally:
            os.unlink(tmp_path)

    def test_superwhisper_skipped_for_timestamps(self):
        """SuperWhisper should be skipped when timestamps are requested."""
        from tools.transcribe import transcribe

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            f.write(b"fake audio data")
            tmp_path = f.name

        try:
            openai_result = {"text": "result", "language": "en", "words": []}
            with (
                patch("tools.transcribe._is_superwhisper_available", return_value=True),
                patch("tools.transcribe._transcribe_superwhisper") as mock_sw,
                patch("tools.transcribe._transcribe_openai", return_value=openai_result),
            ):
                transcribe(tmp_path, timestamps=True)

            mock_sw.assert_not_called()
        finally:
            os.unlink(tmp_path)


class TestBridgeIntegration:
    """Test that bridge/media.py uses tools.transcribe."""

    def test_bridge_imports_tools_transcribe(self):
        """bridge/media.py transcribe_voice should use tools.transcribe."""
        import inspect

        from bridge.media import transcribe_voice

        source = inspect.getsource(transcribe_voice)
        assert "tools.transcribe" in source
        # Should NOT contain inline OpenAI API call
        assert "api.openai.com" not in source


class TestNotInstalled:
    """Tests that work even when backends are not installed."""

    def test_supported_formats_documented(self):
        """README should document supported formats."""
        readme_path = Path(__file__).parent.parent / "README.md"
        content = readme_path.read_text()

        assert "mp3" in content.lower()
        assert "ogg" in content.lower()

    def test_installation_instructions_in_readme(self):
        """README should contain relevant installation/setup info."""
        readme_path = Path(__file__).parent.parent / "README.md"
        content = readme_path.read_text()

        # Should mention SuperWhisper
        assert "SuperWhisper" in content or "superwhisper" in content
