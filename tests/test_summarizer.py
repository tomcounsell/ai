"""Tests for bridge.summarizer — response summarization and classification."""

from unittest.mock import AsyncMock, patch

import pytest

from bridge.summarizer import (
    CLASSIFICATION_CONFIDENCE_THRESHOLD,
    SUMMARIZE_THRESHOLD,
    ClassificationResult,
    OutputType,
    _classify_with_heuristics,
    _parse_classification_response,
    classify_output,
    extract_artifacts,
    summarize_response,
)


class TestExtractArtifacts:
    """Unit tests for artifact extraction from agent output."""

    def test_extracts_commit_hashes(self):
        text = (
            "Created commit abc1234 and pushed to remote.\n" "Commit def5678901 merged."
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

        mock_haiku = AsyncMock(return_value="Summary: did the work. `abc1234`")
        with patch("bridge.summarizer._summarize_with_haiku", mock_haiku):
            result = await summarize_response(long_text)

        assert result.was_summarized is True
        assert result.text == "Summary: did the work. `abc1234`"
        mock_haiku.assert_called_once()

    @pytest.mark.asyncio
    async def test_haiku_fails_falls_back_to_ollama(self):
        """If Haiku fails, Ollama is tried next."""
        long_text = "Detailed work output. " * 200

        mock_haiku = AsyncMock(return_value=None)
        mock_ollama = AsyncMock(return_value="Ollama summary of work.")
        with (
            patch("bridge.summarizer._summarize_with_haiku", mock_haiku),
            patch("bridge.summarizer._summarize_with_ollama", mock_ollama),
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
        with (
            patch("bridge.summarizer._summarize_with_haiku", mock_haiku),
            patch("bridge.summarizer._summarize_with_ollama", mock_ollama),
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
        with patch("bridge.summarizer._summarize_with_haiku", mock_haiku):
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
        with patch("bridge.summarizer._summarize_with_haiku", mock_haiku):
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
        agent_output = "\n\n".join(
            [
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
            ]
        )

        result = await summarize_response(agent_output)

        assert result.was_summarized is True
        assert len(result.text) < len(agent_output)
        # Summary should be concise — a few sentences, not a wall
        assert len(result.text) <= 800


class TestOutputType:
    """Tests for the OutputType enum."""

    def test_enum_values(self):
        assert OutputType.QUESTION.value == "question"
        assert OutputType.STATUS_UPDATE.value == "status"
        assert OutputType.COMPLETION.value == "completion"
        assert OutputType.BLOCKER.value == "blocker"
        assert OutputType.ERROR.value == "error"

    def test_all_types_exist(self):
        """Verify all expected types are defined."""
        assert len(OutputType) == 5


class TestClassificationResult:
    """Tests for the ClassificationResult dataclass."""

    def test_creation(self):
        result = ClassificationResult(
            output_type=OutputType.QUESTION,
            confidence=0.95,
            reason="Direct question detected",
        )
        assert result.output_type == OutputType.QUESTION
        assert result.confidence == 0.95
        assert result.reason == "Direct question detected"

    def test_confidence_range(self):
        """Confidence is a float between 0.0 and 1.0."""
        result = ClassificationResult(
            output_type=OutputType.STATUS_UPDATE,
            confidence=0.0,
            reason="Test",
        )
        assert result.confidence == 0.0

        result2 = ClassificationResult(
            output_type=OutputType.COMPLETION,
            confidence=1.0,
            reason="Test",
        )
        assert result2.confidence == 1.0


class TestParseClassificationResponse:
    """Tests for _parse_classification_response."""

    def test_valid_json(self):
        raw = '{"type": "question", "confidence": 0.95, "reason": "asks user"}'
        result = _parse_classification_response(raw)
        assert result is not None
        assert result.output_type == OutputType.QUESTION
        assert result.confidence == 0.95
        assert result.reason == "asks user"

    def test_all_types(self):
        """Every valid type string parses correctly."""
        for type_str, expected in [
            ("question", OutputType.QUESTION),
            ("status", OutputType.STATUS_UPDATE),
            ("completion", OutputType.COMPLETION),
            ("blocker", OutputType.BLOCKER),
            ("error", OutputType.ERROR),
        ]:
            raw = f'{{"type": "{type_str}", "confidence": 0.9, "reason": "test"}}'
            result = _parse_classification_response(raw)
            assert result is not None
            assert result.output_type == expected

    def test_markdown_code_fences(self):
        """Handles JSON wrapped in markdown code fences."""
        raw = (
            "```json\n"
            '{"type": "completion", "confidence": 0.88, "reason": "done"}\n'
            "```"
        )
        result = _parse_classification_response(raw)
        assert result is not None
        assert result.output_type == OutputType.COMPLETION

    def test_code_fences_no_language(self):
        """Handles code fences without language tag."""
        raw = (
            "```\n"
            '{"type": "status", "confidence": 0.75, "reason": "in progress"}\n'
            "```"
        )
        result = _parse_classification_response(raw)
        assert result is not None
        assert result.output_type == OutputType.STATUS_UPDATE

    def test_invalid_json(self):
        assert _parse_classification_response("not json at all") is None

    def test_invalid_type(self):
        raw = '{"type": "unknown", "confidence": 0.9, "reason": "test"}'
        assert _parse_classification_response(raw) is None

    def test_missing_type(self):
        raw = '{"confidence": 0.9, "reason": "test"}'
        assert _parse_classification_response(raw) is None

    def test_confidence_clamped_high(self):
        raw = '{"type": "error", "confidence": 1.5, "reason": "test"}'
        result = _parse_classification_response(raw)
        assert result is not None
        assert result.confidence == 1.0

    def test_confidence_clamped_low(self):
        raw = '{"type": "error", "confidence": -0.5, "reason": "test"}'
        result = _parse_classification_response(raw)
        assert result is not None
        assert result.confidence == 0.0

    def test_non_numeric_confidence(self):
        raw = '{"type": "error", "confidence": "high", "reason": "test"}'
        result = _parse_classification_response(raw)
        assert result is not None
        assert result.confidence == 0.5

    def test_not_a_dict(self):
        assert _parse_classification_response("[1, 2, 3]") is None

    def test_empty_string(self):
        assert _parse_classification_response("") is None


class TestClassifyWithHeuristics:
    """Tests for the keyword-based heuristic fallback classifier."""

    def test_question_should_i(self):
        result = _classify_with_heuristics("Should I proceed with the refactor?")
        assert result.output_type == OutputType.QUESTION
        assert result.confidence >= 0.80

    def test_question_do_you_want(self):
        result = _classify_with_heuristics("Do you want me to fix the failing test?")
        assert result.output_type == OutputType.QUESTION

    def test_question_would_you_like(self):
        result = _classify_with_heuristics("Would you like me to push these changes?")
        assert result.output_type == OutputType.QUESTION

    def test_question_what_should(self):
        result = _classify_with_heuristics("What should I do about the deprecated API?")
        assert result.output_type == OutputType.QUESTION

    def test_question_please_confirm(self):
        result = _classify_with_heuristics("Please confirm this is the right approach.")
        assert result.output_type == OutputType.QUESTION

    def test_error_pattern(self):
        result = _classify_with_heuristics(
            "Error: ModuleNotFoundError: No module named 'foo'"
        )
        assert result.output_type == OutputType.ERROR
        assert result.confidence >= 0.80

    def test_error_failed(self):
        result = _classify_with_heuristics("Failed: connection timeout")
        assert result.output_type == OutputType.ERROR

    def test_error_exit_code(self):
        result = _classify_with_heuristics("Build failed with exit code 1")
        assert result.output_type == OutputType.ERROR

    def test_blocker_blocked(self):
        result = _classify_with_heuristics("Blocked on waiting for API access")
        assert result.output_type == OutputType.BLOCKER
        assert result.confidence >= 0.80

    def test_blocker_permission(self):
        result = _classify_with_heuristics(
            "Permission denied when accessing the deployment config"
        )
        assert result.output_type == OutputType.BLOCKER

    def test_blocker_cannot_proceed(self):
        result = _classify_with_heuristics(
            "Cannot proceed without database credentials"
        )
        assert result.output_type == OutputType.BLOCKER

    def test_completion_done(self):
        result = _classify_with_heuristics("Done. All changes committed.")
        assert result.output_type == OutputType.COMPLETION
        assert result.confidence >= 0.80

    def test_completion_pr_url(self):
        result = _classify_with_heuristics(
            "PR created: https://github.com/org/repo/pull/42"
        )
        assert result.output_type == OutputType.COMPLETION

    def test_completion_pushed(self):
        result = _classify_with_heuristics("Pushed changes to origin/main")
        assert result.output_type == OutputType.COMPLETION

    def test_completion_finished(self):
        result = _classify_with_heuristics("Finished the implementation.")
        assert result.output_type == OutputType.COMPLETION

    def test_status_default(self):
        """Unrecognized patterns default to STATUS_UPDATE."""
        result = _classify_with_heuristics("Analyzing the codebase structure now")
        assert result.output_type == OutputType.STATUS_UPDATE
        assert result.confidence < 0.80

    def test_status_running_tests(self):
        result = _classify_with_heuristics("Running tests now...")
        assert result.output_type == OutputType.STATUS_UPDATE

    def test_empty_text(self):
        """Empty text still returns a valid classification."""
        result = _classify_with_heuristics("")
        assert result.output_type == OutputType.STATUS_UPDATE


class TestClassifyOutput:
    """Tests for the main classify_output async function."""

    @pytest.mark.asyncio
    async def test_empty_text(self):
        result = await classify_output("")
        assert result.output_type == OutputType.STATUS_UPDATE
        assert result.confidence == 1.0
        assert result.reason == "Empty output"

    @pytest.mark.asyncio
    async def test_none_text(self):
        result = await classify_output(None)
        assert result.output_type == OutputType.STATUS_UPDATE

    @pytest.mark.asyncio
    async def test_whitespace_only(self):
        result = await classify_output("   \n\t  ")
        assert result.output_type == OutputType.STATUS_UPDATE
        assert result.confidence == 1.0

    @pytest.mark.asyncio
    async def test_llm_success(self):
        """When LLM returns valid JSON, classification is used."""
        mock_response = AsyncMock()
        mock_response.content = [
            AsyncMock(
                text='{"type": "question", "confidence": 0.95, '
                '"reason": "asks about approach"}'
            )
        ]
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with (
            patch("utils.api_keys.get_anthropic_api_key", return_value="sk-test"),
            patch(
                "bridge.summarizer.anthropic.AsyncAnthropic", return_value=mock_client
            ),
        ):
            result = await classify_output("Should I use approach A or B?")

        assert result.output_type == OutputType.QUESTION
        assert result.confidence == 0.95

    @pytest.mark.asyncio
    async def test_llm_low_confidence_defaults_to_question(self):
        """Below confidence threshold, defaults to QUESTION."""
        mock_response = AsyncMock()
        mock_response.content = [
            AsyncMock(
                text='{"type": "status", "confidence": 0.5, ' '"reason": "unclear"}'
            )
        ]
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with (
            patch("utils.api_keys.get_anthropic_api_key", return_value="sk-test"),
            patch(
                "bridge.summarizer.anthropic.AsyncAnthropic", return_value=mock_client
            ),
        ):
            result = await classify_output("Some ambiguous output")

        assert result.output_type == OutputType.QUESTION
        assert result.confidence == 0.5
        assert "Low confidence" in result.reason

    @pytest.mark.asyncio
    async def test_llm_failure_falls_back_to_heuristics(self):
        """When LLM call fails, heuristics are used."""
        with (
            patch("utils.api_keys.get_anthropic_api_key", return_value="sk-test"),
            patch(
                "bridge.summarizer.anthropic.AsyncAnthropic",
                side_effect=Exception("API error"),
            ),
        ):
            result = await classify_output("Should I proceed?")

        # Heuristics should still detect the question
        assert result.output_type == OutputType.QUESTION

    @pytest.mark.asyncio
    async def test_no_api_key_falls_back_to_heuristics(self):
        """When no API key, heuristics are used."""
        with patch("utils.api_keys.get_anthropic_api_key", return_value=""):
            result = await classify_output(
                "Error: ModuleNotFoundError: No module named 'foo'"
            )

        assert result.output_type == OutputType.ERROR

    @pytest.mark.asyncio
    async def test_unparseable_llm_response_falls_back(self):
        """When LLM returns garbage, falls back to heuristics."""
        mock_response = AsyncMock()
        mock_response.content = [AsyncMock(text="I think this is a question")]
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with (
            patch("utils.api_keys.get_anthropic_api_key", return_value="sk-test"),
            patch(
                "bridge.summarizer.anthropic.AsyncAnthropic", return_value=mock_client
            ),
        ):
            result = await classify_output("Done. Committed abc1234.")

        # Heuristics should detect completion
        assert result.output_type == OutputType.COMPLETION

    @pytest.mark.asyncio
    async def test_long_text_truncated_for_classification(self):
        """Very long text is truncated before sending to LLM."""
        long_text = "x" * 5000
        mock_response = AsyncMock()
        mock_response.content = [
            AsyncMock(
                text='{"type": "status", "confidence": 0.90, '
                '"reason": "progress report"}'
            )
        ]
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with (
            patch("utils.api_keys.get_anthropic_api_key", return_value="sk-test"),
            patch(
                "bridge.summarizer.anthropic.AsyncAnthropic", return_value=mock_client
            ),
        ):
            result = await classify_output(long_text)

        # Verify the text sent to LLM was truncated
        call_args = mock_client.messages.create.call_args
        user_msg = call_args.kwargs["messages"][0]["content"]
        assert len(user_msg) < len(long_text)
        assert "[...truncated...]" in user_msg
        assert result.output_type == OutputType.STATUS_UPDATE

    @pytest.mark.asyncio
    async def test_confidence_threshold_constant(self):
        """Verify the confidence threshold is set correctly."""
        assert CLASSIFICATION_CONFIDENCE_THRESHOLD == 0.80
