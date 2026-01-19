"""
Integration tests for transcribe.

These tests verify insanely-fast-whisper installation and functionality.
Run with: pytest tools/transcribe/tests/ -v
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


def is_whisper_installed() -> bool:
    """Check if insanely-fast-whisper is installed."""
    return shutil.which("insanely-fast-whisper") is not None


def run_whisper(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    """Run insanely-fast-whisper command."""
    cmd = ["insanely-fast-whisper", *args]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


class TestConfiguration:
    """Test tool configuration and setup."""

    def test_manifest_exists(self):
        """manifest.json should exist."""
        manifest_path = Path(__file__).parent.parent / "manifest.json"
        assert manifest_path.exists()

    def test_manifest_valid_json(self):
        """manifest.json should be valid JSON."""
        manifest_path = Path(__file__).parent.parent / "manifest.json"
        with open(manifest_path) as f:
            manifest = json.load(f)

        assert manifest["name"] == "transcribe"
        assert manifest["type"] == "cli"
        assert "transcribe" in manifest["capabilities"]

    def test_readme_exists(self):
        """README.md should exist."""
        readme_path = Path(__file__).parent.parent / "README.md"
        assert readme_path.exists()


@pytest.mark.skipif(
    not is_whisper_installed(),
    reason="insanely-fast-whisper not installed",
)
class TestInstallation:
    """Verify insanely-fast-whisper is properly installed."""

    def test_help_command(self):
        """insanely-fast-whisper should respond to --help."""
        result = run_whisper("--help")
        assert result.returncode == 0
        assert "file-name" in result.stdout or "file_name" in result.stdout

    def test_binary_exists(self):
        """Binary should be in PATH."""
        assert shutil.which("insanely-fast-whisper") is not None


@pytest.mark.skipif(
    not is_whisper_installed(),
    reason="insanely-fast-whisper not installed",
)
class TestErrorHandling:
    """Test error handling scenarios."""

    def test_missing_file(self):
        """Should error on missing file."""
        result = run_whisper("--file-name", "nonexistent_file.mp3")
        # Should fail with non-zero exit code or error in stderr
        assert result.returncode != 0 or "error" in result.stderr.lower()

    def test_invalid_option(self):
        """Should handle invalid options gracefully."""
        result = run_whisper("--invalid-option-xyz")
        assert result.returncode != 0


class TestNotInstalled:
    """Tests that work even when whisper is not installed."""

    def test_installation_instructions_in_readme(self):
        """README should contain installation instructions."""
        readme_path = Path(__file__).parent.parent / "README.md"
        content = readme_path.read_text()

        assert "pipx install" in content or "pip install" in content
        assert "insanely-fast-whisper" in content

    def test_supported_formats_documented(self):
        """README should document supported formats."""
        readme_path = Path(__file__).parent.parent / "README.md"
        content = readme_path.read_text()

        assert ".mp3" in content or "mp3" in content
        assert ".ogg" in content or "ogg" in content  # Telegram voice


class TestPythonIntegration:
    """Test the Python wrapper function documented in README."""

    def test_transcribe_function_pattern(self):
        """The documented Python pattern should be valid."""
        # This tests that the pattern shown in README would work
        code = '''
import subprocess
import json

def transcribe(audio_path: str, model: str = "openai/whisper-large-v3") -> str:
    """Transcribe audio file to text."""
    result = subprocess.run(
        [
            "insanely-fast-whisper",
            "--file-name", audio_path,
            "--model-name", model,
            "--transcript-path", "/tmp/transcript.json"
        ],
        capture_output=True,
        text=True
    )
    return result
'''
        # Should compile without syntax errors
        compile(code, "<string>", "exec")
