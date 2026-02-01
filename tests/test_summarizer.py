"""Tests for bridge.summarizer — response summarization for Telegram."""

from unittest.mock import AsyncMock, patch

import pytest

from bridge.summarizer import (
    SUMMARIZE_THRESHOLD,
    extract_artifacts,
    summarize_response,
)


class TestExtractArtifacts:
    """Unit tests for artifact extraction from agent output."""

    def test_extracts_commit_hashes(self):
        text = (
            "Created commit abc1234 and pushed to remote.\n"
            "Commit def5678901 merged."
        )
        artifacts = extract_artifacts(text)
        assert "commits" in artifacts
        assert "abc1234" in artifacts["commits"]
        assert "def5678901" in artifacts["commits"]

    def test_extracts_urls(self):
        text = (
            "PR created: https://github.com/org/repo/pull/42\n"
            "See https://sentry.io/issues/123"
        )
        artifacts = extract_artifacts(text)
        assert "urls" in artifacts
        assert "https://github.com/org/repo/pull/42" in artifacts["urls"]
        assert "https://sentry.io/issues/123" in artifacts["urls"]

    def test_extracts_files_changed(self):
        text = (
            "modified: src/main.py\n"
            "created: tests/test_new.py\n"
            "deleted: old_file.txt"
        )
        artifacts = extract_artifacts(text)
        assert "files_changed" in artifacts
        assert "src/main.py" in artifacts["files_changed"]
        assert "tests/test_new.py" in artifacts["files_changed"]

    def test_extracts_test_results(self):
        text = "Results: 15 passed, 2 failed, 1 skipped"
        artifacts = extract_artifacts(text)
        assert "test_results" in artifacts

    def test_extracts_errors(self):
        text = (
            "Error: ModuleNotFoundError: No module named 'foo'\n"
            "Failed: connection timeout"
        )
        artifacts = extract_artifacts(text)
        assert "errors" in artifacts
        assert len(artifacts["errors"]) >= 1

    def test_empty_text(self):
        assert extract_artifacts("") == {}

    def test_no_artifacts(self):
        text = "Everything looks good. The task is complete."
        artifacts = extract_artifacts(text)
        assert isinstance(artifacts, dict)

    def test_deduplicates_artifacts(self):
        text = "Commit abc1234 done.\nPushed commit abc1234 to origin."
        artifacts = extract_artifacts(text)
        assert artifacts["commits"].count("abc1234") == 1

    def test_git_status_file_patterns(self):
        text = "M  bridge/summarizer.py\nA  tests/test_summarizer.py"
        artifacts = extract_artifacts(text)
        assert "files_changed" in artifacts
        assert "bridge/summarizer.py" in artifacts["files_changed"]


class TestSummarizeResponse:
    """Tests for the main summarize_response function."""

    @pytest.mark.asyncio
    async def test_short_response_not_summarized(self):
        """Responses under threshold are returned as-is."""
        short_text = "Done. Committed abc1234."
        result = await summarize_response(short_text)
        assert result.text == short_text
        assert result.was_summarized is False
        assert result.full_output_file is None

    @pytest.mark.asyncio
    async def test_empty_response(self):
        result = await summarize_response("")
        assert result.text == ""
        assert result.was_summarized is False

    @pytest.mark.asyncio
    async def test_none_response(self):
        result = await summarize_response(None)
        assert result.text == ""

    @pytest.mark.asyncio
    async def test_exactly_at_threshold(self):
        """Response exactly at threshold is not summarized."""
        text = "x" * SUMMARIZE_THRESHOLD
        result = await summarize_response(text)
        assert result.was_summarized is False

    @pytest.mark.asyncio
    async def test_long_response_calls_haiku(self):
        """Responses over threshold trigger Haiku summarization."""
        long_text = "Detailed work output. " * 200

        mock_haiku = AsyncMock(
            return_value="Summary: did the work. `abc1234`"
        )
        with patch(
            "bridge.summarizer._summarize_with_haiku", mock_haiku
        ):
            result = await summarize_response(long_text)

        assert result.was_summarized is True
        assert result.text == "Summary: did the work. `abc1234`"
        mock_haiku.assert_called_once()

    @pytest.mark.asyncio
    async def test_haiku_fails_falls_back_to_ollama(self):
        """If Haiku fails, Ollama is tried next."""
        long_text = "Detailed work output. " * 200

        mock_haiku = AsyncMock(return_value=None)
        mock_ollama = AsyncMock(
            return_value="Ollama summary of work."
        )
        with patch(
            "bridge.summarizer._summarize_with_haiku", mock_haiku
        ), patch(
            "bridge.summarizer._summarize_with_ollama", mock_ollama
        ):
            result = await summarize_response(long_text)

        assert result.was_summarized is True
        assert result.text == "Ollama summary of work."
        mock_haiku.assert_called_once()
        mock_ollama.assert_called_once()

    @pytest.mark.asyncio
    async def test_all_backends_fail_truncates(self):
        """If all summarization backends fail, truncate."""
        long_text = "x" * 5000

        mock_haiku = AsyncMock(return_value=None)
        mock_ollama = AsyncMock(return_value=None)
        with patch(
            "bridge.summarizer._summarize_with_haiku", mock_haiku
        ), patch(
            "bridge.summarizer._summarize_with_ollama", mock_ollama
        ):
            result = await summarize_response(long_text)

        assert result.was_summarized is False
        assert len(result.text) <= 4096
        assert result.text.endswith("...")

    @pytest.mark.asyncio
    async def test_very_long_response_creates_file(self):
        """Responses over FILE_ATTACH_THRESHOLD get a full output file."""
        long_text = "Output line.\n" * 500

        mock_haiku = AsyncMock(return_value="Summary of work.")
        with patch(
            "bridge.summarizer._summarize_with_haiku", mock_haiku
        ):
            result = await summarize_response(long_text)

        assert result.full_output_file is not None
        assert result.full_output_file.exists()
        content = result.full_output_file.read_text()
        assert content == long_text

        # Cleanup
        result.full_output_file.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_mid_length_response_no_file(self):
        """Responses between summarize and file thresholds: no file."""
        text = "x" * 2000  # Over 1500, under 3000

        mock_haiku = AsyncMock(return_value="Short summary.")
        with patch(
            "bridge.summarizer._summarize_with_haiku", mock_haiku
        ):
            result = await summarize_response(text)

        assert result.full_output_file is None
        assert result.was_summarized is True


class TestSummarizeResponseIntegration:
    """Integration test with real Haiku API call."""

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        not __import__("os").environ.get("ANTHROPIC_API_KEY"),
        reason="ANTHROPIC_API_KEY not set",
    )
    async def test_real_haiku_summarization(self):
        """Test actual Haiku produces a concise PM-style summary."""
        agent_output = "\n\n".join([
            "I've completed the requested changes to the response "
            "summarization system. Here's a detailed breakdown:",
            "First, I analyzed the existing codebase. I read through "
            "bridge/telegram_bridge.py, paying special attention to "
            "send_response_with_files() at line 1499. The current "
            "flow hard-truncates at 4000 chars, losing commit "
            "hashes, file lists, and test results.",
            "I reviewed agent/sdk_client.py to understand how raw "
            "responses are collected from the Claude Agent SDK. The "
            "query() method concatenates all AssistantMessage text "
            "blocks, producing very long outputs.",
            "I examined agent/completion.py and its format_summary() "
            "method. While useful, it's a separate concern.",
            "I created bridge/summarizer.py with components:\n"
            "1. extract_artifacts() - Regex extraction of commits, "
            "URLs, file paths, test results, errors\n"
            "2. summarize_response() - Async Haiku summarization\n"
            "3. SummarizedResponse dataclass",
            "The summarizer follows a tiered approach:\n"
            "- Under 1500 chars: passed through unchanged\n"
            "- 1500-3000 chars: summarized via Haiku\n"
            "- Over 3000 chars: summarized + full output as file",
            "Changes made:\nmodified: bridge/telegram_bridge.py\n"
            "created: bridge/summarizer.py\n"
            "created: tests/test_summarizer.py",
            "Test suite results: 17 passed, 0 failed",
            "Created commit a1b2c3d and pushed to origin/main.\n"
            "PR: https://github.com/org/repo/pull/99",
            "The implementation handles all edge cases including "
            "API failures, empty responses, and concise responses.",
            "I also verified the existing test suite still passes "
            "after the changes. No regressions were introduced. "
            "The bridge restart was tested manually and confirmed "
            "working. Logs show the summarizer activating correctly "
            "for responses exceeding the threshold.",
        ])

        result = await summarize_response(agent_output)

        assert result.was_summarized is True
        assert len(result.text) < len(agent_output)
        # Summary should be concise — a few sentences, not a wall
        assert len(result.text) <= 800
