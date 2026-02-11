"""Tests for deep research tools (Perplexity, Gemini, GPT-Researcher)."""

import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import gemini_deep_research
import gpt_researcher_run
import perplexity_deep_research


class TestPerplexityDeepResearch:
    """Tests for Perplexity Deep Research tool."""

    def test_get_api_key_from_env(self, monkeypatch):
        """Test API key retrieval from environment."""
        monkeypatch.setenv("PERPLEXITY_API_KEY", "test-key-123")
        key = perplexity_deep_research.get_api_key()
        assert key == "test-key-123"

    def test_get_api_key_missing(self, monkeypatch, tmp_path):
        """Test API key when not set."""
        monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
        monkeypatch.chdir(tmp_path)  # Change to temp dir with no .env
        key = perplexity_deep_research.get_api_key()
        assert key is None or key == ""

    def test_cli_help_works(self):
        """Test that --help flag works."""
        with pytest.raises(SystemExit) as exc_info:
            with patch("sys.argv", ["perplexity_deep_research.py", "--help"]):
                perplexity_deep_research.main()
        assert exc_info.value.code == 0

    @patch("perplexity_deep_research.run_perplexity_research")
    def test_auto_save_creates_files(self, mock_research):
        """Test that auto-save creates output and log files."""
        mock_research.return_value = "Test research result"

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch(
                "sys.argv",
                [
                    "perplexity_deep_research.py",
                    "--log-dir",
                    tmpdir,
                    "--quiet",
                    "Test prompt",
                ],
            ):
                with pytest.raises(SystemExit) as exc_info:
                    perplexity_deep_research.main()
                assert exc_info.value.code == 0

            # Check that files were created
            files = list(Path(tmpdir).glob("*"))
            assert len(files) == 2, f"Expected 2 files, found {len(files)}"

            md_files = list(Path(tmpdir).glob("*.md"))
            txt_files = list(Path(tmpdir).glob("*.txt"))
            assert len(md_files) == 1, "Should create one .md file"
            assert len(txt_files) == 1, "Should create one .txt log file"

    @patch("perplexity_deep_research.run_perplexity_research")
    def test_custom_output_path(self, mock_research):
        """Test custom output file path."""
        mock_research.return_value = "Test research result"

        with tempfile.TemporaryDirectory() as tmpdir:
            output_file = Path(tmpdir) / "custom_output.md"

            with patch(
                "sys.argv",
                [
                    "perplexity_deep_research.py",
                    "--output",
                    str(output_file),
                    "--quiet",
                    "Test prompt",
                ],
            ):
                with pytest.raises(SystemExit) as exc_info:
                    perplexity_deep_research.main()
                assert exc_info.value.code == 0

            assert output_file.exists(), "Output file should exist"
            log_file = Path(tmpdir) / "custom_output_log.txt"
            assert log_file.exists(), "Log file should exist"

    @patch("perplexity_deep_research.run_perplexity_research")
    def test_no_auto_save(self, mock_research, capsys):
        """Test that --no-auto-save prints to stdout."""
        mock_research.return_value = "Test research result"

        with patch(
            "sys.argv", ["perplexity_deep_research.py", "--no-auto-save", "Test prompt"]
        ):
            with pytest.raises(SystemExit) as exc_info:
                perplexity_deep_research.main()
            assert exc_info.value.code == 0

        captured = capsys.readouterr()
        assert "Test research result" in captured.out


class TestGeminiDeepResearch:
    """Tests for Gemini Deep Research tool."""

    def test_get_api_key_from_env(self, monkeypatch):
        """Test API key retrieval from environment."""
        monkeypatch.setenv("GOOGLE_AI_API_KEY", "test-key-456")
        key = gemini_deep_research.get_api_key()
        assert key == "test-key-456"

    def test_get_api_key_missing(self, monkeypatch, tmp_path):
        """Test API key when not set."""
        monkeypatch.delenv("GOOGLE_AI_API_KEY", raising=False)
        monkeypatch.chdir(tmp_path)  # Change to temp dir with no .env
        key = gemini_deep_research.get_api_key()
        assert key is None or key == ""

    def test_cli_help_works(self):
        """Test that --help flag works."""
        with pytest.raises(SystemExit) as exc_info:
            with patch("sys.argv", ["gemini_deep_research.py", "--help"]):
                gemini_deep_research.main()
        assert exc_info.value.code == 0

    @patch("gemini_deep_research.run_gemini_research")
    def test_auto_save_creates_files(self, mock_research):
        """Test that auto-save creates output and log files."""
        mock_research.return_value = "Test research result"

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch(
                "sys.argv",
                [
                    "gemini_deep_research.py",
                    "--log-dir",
                    tmpdir,
                    "--quiet",
                    "Test prompt",
                ],
            ):
                with pytest.raises(SystemExit) as exc_info:
                    gemini_deep_research.main()
                assert exc_info.value.code == 0

            # Check that files were created
            md_files = list(Path(tmpdir).glob("*.md"))
            txt_files = list(Path(tmpdir).glob("*.txt"))
            assert len(md_files) == 1, "Should create one .md file"
            assert len(txt_files) == 1, "Should create one .txt log file"

    def test_extract_output(self):
        """Test extracting text from result."""
        result = {
            "outputs": [
                {"type": "text", "text": "Part 1"},
                {"type": "text", "text": "Part 2"},
            ]
        }
        text = gemini_deep_research.extract_output(result)
        assert text == "Part 1\nPart 2"


class TestGPTResearcher:
    """Tests for GPT-Researcher tool."""

    def test_get_api_keys(self, monkeypatch):
        """Test API key retrieval."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-123")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-456")

        keys = gpt_researcher_run.get_api_keys()

        assert keys["openai"] == "sk-test-123"
        assert keys["anthropic"] == "sk-ant-test-456"

    def test_configure_model_openai(self):
        """Test model configuration for OpenAI."""
        fast, smart = gpt_researcher_run.configure_model("openai:gpt-4o")
        assert fast == "openai:gpt-4o"
        assert smart == "openai:gpt-4o"

    def test_configure_model_anthropic(self):
        """Test model configuration for Anthropic."""
        fast, smart = gpt_researcher_run.configure_model("anthropic:claude-opus-4")
        assert fast == "anthropic:claude-opus-4"
        assert smart == "anthropic:claude-opus-4"

    def test_configure_model_openrouter(self):
        """Test model configuration for OpenRouter."""
        fast, smart = gpt_researcher_run.configure_model(
            "openrouter/anthropic/claude-opus-4"
        )
        assert fast == "openrouter:anthropic/claude-opus-4"
        assert smart == "openrouter:anthropic/claude-opus-4"

    def test_cli_help_works(self):
        """Test that --help flag works."""
        with pytest.raises(SystemExit) as exc_info:
            with patch("sys.argv", ["gpt_researcher_run.py", "--help"]):
                gpt_researcher_run.main()
        assert exc_info.value.code == 0


class TestLogDirectory:
    """Tests for --log-dir functionality across all tools."""

    @patch("perplexity_deep_research.run_perplexity_research")
    def test_perplexity_log_dir_creates_subdirectory(self, mock_research):
        """Test that --log-dir creates subdirectory for Perplexity."""
        mock_research.return_value = "Test result"

        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir) / "logs"

            with patch(
                "sys.argv",
                [
                    "perplexity_deep_research.py",
                    "--log-dir",
                    str(log_dir),
                    "--quiet",
                    "Test prompt",
                ],
            ):
                with pytest.raises(SystemExit) as exc_info:
                    perplexity_deep_research.main()
                assert exc_info.value.code == 0

            assert log_dir.exists(), "Log directory should be created"
            assert log_dir.is_dir(), "Should be a directory"
            assert len(list(log_dir.glob("*.md"))) == 1
            assert len(list(log_dir.glob("*.txt"))) == 1

    @patch("gemini_deep_research.run_gemini_research")
    def test_gemini_log_dir_creates_subdirectory(self, mock_research):
        """Test that --log-dir creates subdirectory for Gemini."""
        mock_research.return_value = "Test result"

        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir) / "logs"

            with patch(
                "sys.argv",
                [
                    "gemini_deep_research.py",
                    "--log-dir",
                    str(log_dir),
                    "--quiet",
                    "Test prompt",
                ],
            ):
                with pytest.raises(SystemExit) as exc_info:
                    gemini_deep_research.main()
                assert exc_info.value.code == 0

            assert log_dir.exists(), "Log directory should be created"
            assert log_dir.is_dir(), "Should be a directory"
            assert len(list(log_dir.glob("*.md"))) == 1
            assert len(list(log_dir.glob("*.txt"))) == 1


class TestFileOutputFormat:
    """Test output file format for all tools."""

    @patch("perplexity_deep_research.run_perplexity_research")
    def test_perplexity_output_contains_metadata(self, mock_research):
        """Test that Perplexity output file contains metadata."""
        mock_research.return_value = "Research content here"

        with tempfile.TemporaryDirectory() as tmpdir:
            output_file = Path(tmpdir) / "output.md"

            with patch(
                "sys.argv",
                [
                    "perplexity_deep_research.py",
                    "--output",
                    str(output_file),
                    "--quiet",
                    "Test prompt",
                ],
            ):
                with pytest.raises(SystemExit) as exc_info:
                    perplexity_deep_research.main()
                assert exc_info.value.code == 0

            content = output_file.read_text()
            assert "# Perplexity Deep Research Results" in content
            assert "Date:" in content
            assert "Model:" in content
            assert "Prompt:" in content
            assert "Research content here" in content

    @patch("gemini_deep_research.run_gemini_research")
    def test_gemini_output_contains_metadata(self, mock_research):
        """Test that Gemini output file contains metadata."""
        mock_research.return_value = "Research content here"

        with tempfile.TemporaryDirectory() as tmpdir:
            output_file = Path(tmpdir) / "output.md"

            with patch(
                "sys.argv",
                [
                    "gemini_deep_research.py",
                    "--output",
                    str(output_file),
                    "--quiet",
                    "Test prompt",
                ],
            ):
                with pytest.raises(SystemExit) as exc_info:
                    gemini_deep_research.main()
                assert exc_info.value.code == 0

            content = output_file.read_text()
            assert "# Gemini Deep Research Results" in content
            assert "Date:" in content
            assert "Prompt:" in content
            assert "Research content here" in content
