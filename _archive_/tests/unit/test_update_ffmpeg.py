"""Unit tests for scripts/update/kokoro.py::ensure_ffmpeg().

ffmpeg is Kokoro's WAV->OGG/Opus encode dependency; without it on PATH the
local TTS backend reports unavailable and synthesis falls back to the paid
OpenAI tts-1 path. The /update run installs it on macOS via Homebrew.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from scripts.update.kokoro import FfmpegResult, ensure_ffmpeg

FFMPEG_BIN = "/opt/homebrew/bin/ffmpeg"
BREW_BIN = "/opt/homebrew/bin/brew"


class TestEnsureFfmpeg:
    """Tests for ensure_ffmpeg()."""

    def test_already_present(self):
        """Returns 'present' without shelling out when ffmpeg is on PATH."""
        with (
            patch("scripts.update.kokoro.shutil.which", return_value=FFMPEG_BIN),
            patch("scripts.update.kokoro.subprocess.run") as mock_run,
        ):
            result = ensure_ffmpeg()
        assert isinstance(result, FfmpegResult)
        assert result.success
        assert result.action == "present"
        assert result.path == FFMPEG_BIN
        mock_run.assert_not_called()

    def test_non_darwin_no_autoinstall(self):
        """On non-macOS, returns failed with guidance (no brew attempt)."""
        with (
            patch("scripts.update.kokoro.shutil.which", return_value=None),
            patch("scripts.update.kokoro.platform.system", return_value="Linux"),
            patch("scripts.update.kokoro.subprocess.run") as mock_run,
        ):
            result = ensure_ffmpeg()
        assert not result.success
        assert result.action == "failed"
        assert "package manager" in result.error
        mock_run.assert_not_called()

    def test_missing_brew(self):
        """On macOS without Homebrew, returns failed with manual guidance."""

        def which(name: str):
            return None  # neither ffmpeg nor brew

        with (
            patch("scripts.update.kokoro.shutil.which", side_effect=which),
            patch("scripts.update.kokoro.platform.system", return_value="Darwin"),
            patch("scripts.update.kokoro.subprocess.run") as mock_run,
        ):
            result = ensure_ffmpeg()
        assert not result.success
        assert result.action == "failed"
        assert "Homebrew not found" in result.error
        mock_run.assert_not_called()

    def test_installs_via_brew(self):
        """On macOS, installs via brew and reports 'installed'."""
        # which(): ffmpeg absent first, brew present, ffmpeg present after install.
        which_returns = iter([None, BREW_BIN, FFMPEG_BIN])

        mock_run = MagicMock()
        mock_run.returncode = 0
        mock_run.stdout = ""
        mock_run.stderr = ""

        with (
            patch("scripts.update.kokoro.shutil.which", side_effect=lambda _: next(which_returns)),
            patch("scripts.update.kokoro.platform.system", return_value="Darwin"),
            patch("scripts.update.kokoro.subprocess.run", return_value=mock_run) as run_patch,
        ):
            result = ensure_ffmpeg()
        assert result.success
        assert result.action == "installed"
        assert result.path == FFMPEG_BIN
        # Verify it actually invoked `brew install ffmpeg`.
        args = run_patch.call_args[0][0]
        assert args[0] == BREW_BIN
        assert args[1:] == ["install", "ffmpeg"]

    def test_brew_install_failure(self):
        """A non-zero brew exit surfaces as failed with stderr."""
        which_returns = iter([None, BREW_BIN, None])
        mock_run = MagicMock()
        mock_run.returncode = 1
        mock_run.stdout = ""
        mock_run.stderr = "Error: download failed"

        with (
            patch("scripts.update.kokoro.shutil.which", side_effect=lambda _: next(which_returns)),
            patch("scripts.update.kokoro.platform.system", return_value="Darwin"),
            patch("scripts.update.kokoro.subprocess.run", return_value=mock_run),
        ):
            result = ensure_ffmpeg()
        assert not result.success
        assert result.action == "failed"
        assert "download failed" in result.error
