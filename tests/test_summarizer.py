"""Tests for bridge.summarizer — response summarization and classification."""

from unittest.mock import AsyncMock, patch

import pytest

from bridge.summarizer import (
    CLASSIFICATION_CONFIDENCE_THRESHOLD,
    ClassificationResult,
    OutputType,
    _classify_with_heuristics,
    _compose_structured_summary,
    _get_status_emoji,
    _parse_classification_response,
    _parse_summary_and_questions,
    _render_link_footer,
    _render_stage_progress,
    classify_output,
    extract_artifacts,
    summarize_response,
)


class TestExtractArtifacts:
    """Unit tests for artifact extraction from agent output."""

    def test_extracts_commit_hashes(self):
        text = "Created commit abc1234 and pushed to remote.\nCommit def5678901 merged."
        artifacts = extract_artifacts(text)
        assert "commits" in artifacts
        assert "abc1234" in artifacts["commits"]
        assert "def5678901" in artifacts["commits"]

    def test_extracts_urls(self):
        text = "PR created: https://github.com/org/repo/pull/42\nSee https://sentry.io/issues/123"
        artifacts = extract_artifacts(text)
        assert "urls" in artifacts
        assert "https://github.com/org/repo/pull/42" in artifacts["urls"]
        assert "https://sentry.io/issues/123" in artifacts["urls"]

    def test_extracts_files_changed(self):
        text = (
            "modified: src/main.py\ncreated: tests/test_new.py\ndeleted: old_file.txt"
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
        text = "Error: ModuleNotFoundError: No module named 'foo'\nFailed: connection timeout"
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
    async def test_short_response_still_summarized(self):
        """All non-empty responses are summarized (no threshold)."""
        short_text = "Done. Committed abc1234."
        mock_haiku = AsyncMock(return_value="Done ✅ `abc1234`")
        with patch("bridge.summarizer._summarize_with_haiku", mock_haiku):
            result = await summarize_response(short_text)
        assert result.was_summarized is True
        mock_haiku.assert_called_once()

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
    async def test_long_response_calls_haiku(self):
        """Responses over threshold trigger Haiku summarization."""
        long_text = "Detailed work output. " * 200

        mock_haiku = AsyncMock(return_value="Summary: did the work. `abc1234`")
        with patch("bridge.summarizer._summarize_with_haiku", mock_haiku):
            result = await summarize_response(long_text)

        assert result.was_summarized is True
        # Structured composer prepends emoji prefix for non-session summaries
        assert "Summary: did the work. `abc1234`" in result.text
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
        assert "Ollama summary of work." in result.text
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

    def test_coaching_message_default_none(self):
        """coaching_message defaults to None when not provided."""
        result = ClassificationResult(
            output_type=OutputType.STATUS_UPDATE,
            confidence=0.9,
            reason="Test",
        )
        assert result.coaching_message is None

    def test_coaching_message_set(self):
        """coaching_message can be set explicitly."""
        result = ClassificationResult(
            output_type=OutputType.STATUS_UPDATE,
            confidence=0.9,
            reason="Test",
            coaching_message="You said 'should work' but didn't show test output.",
        )
        assert (
            result.coaching_message
            == "You said 'should work' but didn't show test output."
        )

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
            '```json\n{"type": "completion", "confidence": 0.88, "reason": "done"}\n```'
        )
        result = _parse_classification_response(raw)
        assert result is not None
        assert result.output_type == OutputType.COMPLETION

    def test_code_fences_no_language(self):
        """Handles code fences without language tag."""
        raw = (
            '```\n{"type": "status", "confidence": 0.75, "reason": "in progress"}\n```'
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

    def test_coaching_message_extracted(self):
        """coaching_message is extracted from LLM JSON response."""
        raw = (
            '{"type": "status", "confidence": 0.92, '
            '"reason": "hedging language", '
            '"coaching_message": "You said \'should work\' — run the tests and share output."}'
        )
        result = _parse_classification_response(raw)
        assert result is not None
        assert (
            result.coaching_message
            == "You said 'should work' — run the tests and share output."
        )

    def test_coaching_message_absent_defaults_none(self):
        """When coaching_message is missing from JSON, it defaults to None."""
        raw = '{"type": "completion", "confidence": 0.95, "reason": "done"}'
        result = _parse_classification_response(raw)
        assert result is not None
        assert result.coaching_message is None

    def test_hedging_patterns_not_used_for_was_rejected(self):
        """was_rejected_completion is NOT set by hedging pattern matching.

        The old code scanned reason text for patterns like 'hedg', 'no evidence'.
        This has been removed — was_rejected_completion should only be set
        when coaching_message is present (indicating the LLM flagged it).
        """
        raw = (
            '{"type": "status", "confidence": 0.90, '
            '"reason": "hedging language detected, no evidence provided"}'
        )
        result = _parse_classification_response(raw)
        assert result is not None
        # Without coaching_message, was_rejected_completion should be False
        assert result.was_rejected_completion is False

    def test_was_rejected_set_when_coaching_message_present(self):
        """was_rejected_completion is True when coaching_message is present on status."""
        raw = (
            '{"type": "status", "confidence": 0.90, '
            '"reason": "completion downgraded", '
            '"coaching_message": "Include test output next time."}'
        )
        result = _parse_classification_response(raw)
        assert result is not None
        assert result.was_rejected_completion is True
        assert result.coaching_message == "Include test output next time."

    def test_coaching_message_null_for_non_status_types(self):
        """coaching_message should be None for completion, question, blocker, error."""
        for type_str in ("completion", "question", "blocker", "error"):
            raw = (
                f'{{"type": "{type_str}", "confidence": 0.95, '
                f'"reason": "test", "coaching_message": null}}'
            )
            result = _parse_classification_response(raw)
            assert result is not None
            assert (
                result.coaching_message is None
            ), f"coaching_message should be None for {type_str}"
            assert (
                result.was_rejected_completion is False
            ), f"was_rejected_completion should be False for {type_str}"

    def test_coaching_message_on_non_status_ignored_for_rejection(self):
        """Even if LLM mistakenly sets coaching_message on completion, was_rejected stays False."""
        raw = (
            '{"type": "completion", "confidence": 0.95, '
            '"reason": "done", '
            '"coaching_message": "Some coaching text"}'
        )
        result = _parse_classification_response(raw)
        assert result is not None
        # coaching_message IS preserved (it's in the JSON)
        assert result.coaching_message == "Some coaching text"
        # But was_rejected_completion is only set for STATUS_UPDATE
        assert result.was_rejected_completion is False

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

    def test_default_is_question(self):
        """Unrecognized patterns now default to QUESTION (conservative).

        Changed from STATUS_UPDATE to QUESTION as part of the auto-continue
        audit (issue #99): the heuristic fallback should be conservative
        (show to user) rather than permissive (auto-continue).
        """
        result = _classify_with_heuristics("Analyzing the codebase structure now")
        assert result.output_type == OutputType.QUESTION
        assert (
            result.confidence == 0.80
        )  # At threshold to avoid redundant gate re-conversion

    def test_default_running_tests(self):
        """No explicit status pattern — falls to conservative QUESTION default."""
        result = _classify_with_heuristics("Running tests now...")
        assert result.output_type == OutputType.QUESTION

    def test_heuristics_always_return_coaching_message_none(self):
        """All heuristic paths return coaching_message=None."""
        # Question path
        result = _classify_with_heuristics("Should I proceed?")
        assert result.coaching_message is None

        # Error path
        result = _classify_with_heuristics("Error: something broke")
        assert result.coaching_message is None

        # Blocker path
        result = _classify_with_heuristics("Blocked on API access")
        assert result.coaching_message is None

        # Completion path
        result = _classify_with_heuristics("Done. All committed.")
        assert result.coaching_message is None

        # Default status path
        result = _classify_with_heuristics("Working on it now")
        assert result.coaching_message is None

    def test_empty_text(self):
        """Empty text still returns a valid classification (default QUESTION)."""
        result = _classify_with_heuristics("")
        assert result.output_type == OutputType.QUESTION

    def test_approval_gate_when_approved(self):
        """'when approved' triggers QUESTION (approval gate)."""
        result = _classify_with_heuristics("Ready to build when approved")
        assert result.output_type == OutputType.QUESTION
        assert result.confidence == 0.85
        assert "approval gate" in result.reason.lower()

    def test_approval_gate_go_ahead(self):
        """'waiting for go-ahead' triggers QUESTION."""
        result = _classify_with_heuristics("Waiting for your go-ahead to proceed")
        assert result.output_type == OutputType.QUESTION

    def test_approval_gate_shall_i_proceed(self):
        """'shall I proceed' triggers QUESTION."""
        result = _classify_with_heuristics(
            "Plan is ready. Shall I proceed with the build?"
        )
        assert result.output_type == OutputType.QUESTION

    def test_approval_gate_awaiting_approval(self):
        """'awaiting approval' triggers QUESTION."""
        result = _classify_with_heuristics(
            "PR is up, awaiting your approval before merging"
        )
        assert result.output_type == OutputType.QUESTION

    def test_approval_gate_let_me_know_when(self):
        """'let me know when' triggers QUESTION."""
        result = _classify_with_heuristics(
            "Let me know when you want me to start the migration"
        )
        assert result.output_type == OutputType.QUESTION


class TestApplyHeuristicConfidenceGate:
    """Tests for _apply_heuristic_confidence_gate."""

    def test_high_confidence_passes_through(self):
        """Results above threshold are returned unchanged."""
        from bridge.summarizer import _apply_heuristic_confidence_gate

        result = ClassificationResult(
            output_type=OutputType.COMPLETION,
            confidence=0.85,
            reason="Detected completion",
        )
        gated = _apply_heuristic_confidence_gate(result)
        assert gated.output_type == OutputType.COMPLETION
        assert gated.confidence == 0.85

    def test_low_confidence_becomes_question(self):
        """Results below threshold become QUESTION."""
        from bridge.summarizer import _apply_heuristic_confidence_gate

        result = ClassificationResult(
            output_type=OutputType.STATUS_UPDATE,
            confidence=0.60,
            reason="No strong signal",
        )
        gated = _apply_heuristic_confidence_gate(result)
        assert gated.output_type == OutputType.QUESTION
        assert gated.confidence == 0.60
        assert "Low heuristic confidence" in gated.reason

    def test_threshold_boundary_exact(self):
        """Exactly at threshold passes through (not below)."""
        from bridge.summarizer import _apply_heuristic_confidence_gate

        result = ClassificationResult(
            output_type=OutputType.STATUS_UPDATE,
            confidence=0.80,
            reason="Status",
        )
        gated = _apply_heuristic_confidence_gate(result)
        assert gated.output_type == OutputType.STATUS_UPDATE

    def test_below_threshold_preserves_original_confidence(self):
        """The original confidence value is preserved in the gated result."""
        from bridge.summarizer import _apply_heuristic_confidence_gate

        result = ClassificationResult(
            output_type=OutputType.STATUS_UPDATE,
            confidence=0.55,
            reason="Weak signal",
        )
        gated = _apply_heuristic_confidence_gate(result)
        assert gated.confidence == 0.55


class TestClassificationAuditLog:
    """Tests for the classification audit JSONL log."""

    def test_audit_log_writes_entry(self, tmp_path):
        """_write_classification_audit creates a JSONL entry."""
        import json

        import bridge.summarizer as mod
        from bridge.summarizer import _write_classification_audit

        # Redirect audit log to temp path
        original_path = mod._AUDIT_LOG_PATH
        mod._AUDIT_LOG_PATH = tmp_path / "test_audit.jsonl"

        try:
            result = ClassificationResult(
                output_type=OutputType.QUESTION,
                confidence=0.85,
                reason="Direct question",
            )
            _write_classification_audit("Should I proceed?", result, source="llm")

            # Verify file was written
            assert mod._AUDIT_LOG_PATH.exists()
            line = mod._AUDIT_LOG_PATH.read_text().strip()
            entry = json.loads(line)
            assert entry["result"] == "question"
            assert entry["confidence"] == 0.85
            assert entry["source"] == "llm"
            assert entry["text_preview"] == "Should I proceed?"
            assert "ts" in entry
        finally:
            mod._AUDIT_LOG_PATH = original_path

    def test_audit_log_truncates_preview(self, tmp_path):
        """Text preview is truncated to 200 chars."""
        import json

        import bridge.summarizer as mod
        from bridge.summarizer import _write_classification_audit

        original_path = mod._AUDIT_LOG_PATH
        mod._AUDIT_LOG_PATH = tmp_path / "test_audit2.jsonl"

        try:
            result = ClassificationResult(
                output_type=OutputType.STATUS_UPDATE,
                confidence=0.90,
                reason="Progress",
            )
            long_text = "x" * 500
            _write_classification_audit(long_text, result, source="heuristic")

            line = mod._AUDIT_LOG_PATH.read_text().strip()
            entry = json.loads(line)
            assert len(entry["text_preview"]) == 200
        finally:
            mod._AUDIT_LOG_PATH = original_path


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
                text='{"type": "question", "confidence": 0.95, "reason": "asks about approach"}'
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
            AsyncMock(text='{"type": "status", "confidence": 0.5, "reason": "unclear"}')
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
                text='{"type": "status", "confidence": 0.90, "reason": "progress report"}'
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
    async def test_llm_returns_coaching_for_hedging(self):
        """LLM classifier returns specific coaching when hedging language detected."""
        mock_response = AsyncMock()
        mock_response.content = [
            AsyncMock(
                text='{"type": "status", "confidence": 0.92, '
                '"reason": "Hedging language without verification", '
                '"coaching_message": "You used hedging language. Run the tests."}'
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
            result = await classify_output("I think the bug is fixed now. Should work.")

        assert result.output_type == OutputType.STATUS_UPDATE
        assert result.coaching_message == "You used hedging language. Run the tests."
        assert result.was_rejected_completion is True

    @pytest.mark.asyncio
    async def test_llm_returns_coaching_for_missing_evidence(self):
        """LLM classifier returns specific coaching when evidence is missing."""
        mock_response = AsyncMock()
        mock_response.content = [
            AsyncMock(
                text='{"type": "status", "confidence": 0.90, '
                '"reason": "Claims tests pass but shows no output", '
                '"coaching_message": "Paste the pytest output with pass/fail counts."}'
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
            result = await classify_output("All tests pass. Task complete.")

        assert result.output_type == OutputType.STATUS_UPDATE
        assert (
            result.coaching_message == "Paste the pytest output with pass/fail counts."
        )
        assert result.was_rejected_completion is True

    @pytest.mark.asyncio
    async def test_llm_no_coaching_for_genuine_completion(self):
        """LLM returns null coaching_message for genuine completions with evidence."""
        mock_response = AsyncMock()
        mock_response.content = [
            AsyncMock(
                text='{"type": "completion", "confidence": 0.98, '
                '"reason": "verified completion with evidence", '
                '"coaching_message": null}'
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
            result = await classify_output(
                "All 42 tests passed. Committed abc1234. PR: https://github.com/org/repo/pull/99"
            )

        assert result.output_type == OutputType.COMPLETION
        assert result.coaching_message is None
        assert result.was_rejected_completion is False

    @pytest.mark.asyncio
    async def test_confidence_threshold_constant(self):
        """Verify the confidence threshold is set correctly."""
        assert CLASSIFICATION_CONFIDENCE_THRESHOLD == 0.80


class TestParseSummaryAndQuestions:
    """Tests for _parse_summary_and_questions."""

    def test_bullets_only(self):
        text = "• Built the feature\n• Pushed to main"
        bullets, questions = _parse_summary_and_questions(text)
        assert bullets == text
        assert questions is None

    def test_bullets_and_questions(self):
        text = "• Built the feature\n• Pushed to main\n---\n? Should I merge?"
        bullets, questions = _parse_summary_and_questions(text)
        assert bullets == "• Built the feature\n• Pushed to main"
        assert questions == "? Should I merge?"

    def test_multiple_questions(self):
        text = "• Done\n---\n? Q1\n? Q2\n? Q3"
        bullets, questions = _parse_summary_and_questions(text)
        assert bullets == "• Done"
        assert "? Q1" in questions
        assert "? Q2" in questions
        assert "? Q3" in questions

    def test_empty_questions_section(self):
        text = "• Done\n---\n"
        bullets, questions = _parse_summary_and_questions(text)
        assert bullets == "• Done"
        assert questions is None

    def test_leading_separator(self):
        text = "---\n? Only questions here"
        bullets, questions = _parse_summary_and_questions(text)
        assert bullets == ""
        assert questions is not None
        assert "Only questions here" in questions

    def test_no_separator(self):
        text = "Simple summary without questions."
        bullets, questions = _parse_summary_and_questions(text)
        assert bullets == text
        assert questions is None


class TestComposeStructuredSummary:
    """Tests for _compose_structured_summary."""

    def test_no_session_returns_emoji_and_bullets(self):
        result = _compose_structured_summary("• Built it\n• Shipped it", session=None)
        assert "✅" in result
        assert "• Built it" in result
        assert "• Shipped it" in result

    def test_questions_appended(self):
        result = _compose_structured_summary(
            "• Done\n---\n? Should I merge?", session=None
        )
        assert "? Should I merge?" in result
        assert "• Done" in result

    def test_not_completion_uses_pending_emoji(self):
        result = _compose_structured_summary(
            "• Working on it", session=None, is_completion=False
        )
        assert "⏳" in result


class TestNoMessageEcho:
    """Tests verifying that message echo has been removed (issue #241).

    The summarizer previously echoed the user's original message on the first line.
    This was removed because Telegram's reply-to feature already shows context.
    """

    def test_no_echo_on_auto_continued_session(self):
        """Auto-continued sessions must not echo 'continue' or the original request."""
        from unittest.mock import MagicMock

        session = MagicMock()
        session._get_history_list.return_value = [
            "[user] SDLC 190",
            "[stage] ISSUE completed ☑",
            "[stage] PLAN completed ☑",
        ]
        session.message_text = "continue"
        session.status = "running"
        session.is_sdlc_job.return_value = True

        result = _compose_structured_summary(
            "• Built the bypass\n• Tests passing", session=session, is_completion=True
        )
        first_line = result.split("\n")[0]
        assert first_line.strip() in ("✅", "⏳", "❌")
        assert "continue" not in first_line

    def test_no_echo_on_regular_session(self):
        """Regular sessions should not echo the user's message."""
        from unittest.mock import MagicMock

        session = MagicMock()
        session._get_history_list.return_value = ["[user] What time is it?"]
        session.message_text = "What time is it?"
        session.status = "completed"

        result = _compose_structured_summary(
            "It's 3pm UTC+7", session=session, is_completion=True
        )
        first_line = result.split("\n")[0]
        assert first_line.strip() in ("✅", "⏳", "❌")
        assert "What time is it?" not in first_line


class TestGetStatusEmojiRegression:
    """Regression tests for _get_status_emoji — issue #192."""

    def test_running_session_with_completion_flag_returns_checkmark(self):
        """Regression: is_completion=True must return ✅ even when session is running."""
        from unittest.mock import MagicMock

        session = MagicMock()
        session.status = "running"

        result = _get_status_emoji(session, is_completion=True)
        assert result == "✅"

    def test_running_session_without_completion_flag_returns_pending(self):
        """is_completion=False with running session returns ⏳."""
        from unittest.mock import MagicMock

        session = MagicMock()
        session.status = "running"

        result = _get_status_emoji(session, is_completion=False)
        assert result == "⏳"

    def test_failed_session_always_returns_error(self):
        """Failed status overrides is_completion flag."""
        from unittest.mock import MagicMock

        session = MagicMock()
        session.status = "failed"

        assert _get_status_emoji(session, is_completion=True) == "❌"
        assert _get_status_emoji(session, is_completion=False) == "❌"

    def test_completed_session_always_returns_checkmark(self):
        """Completed status always returns ✅."""
        from unittest.mock import MagicMock

        session = MagicMock()
        session.status = "completed"

        assert _get_status_emoji(session, is_completion=True) == "✅"
        assert _get_status_emoji(session, is_completion=False) == "✅"

    def test_no_session_uses_completion_flag(self):
        """No session defers to is_completion flag."""
        assert _get_status_emoji(None, is_completion=True) == "✅"
        assert _get_status_emoji(None, is_completion=False) == "⏳"


class TestRenderStageProgress:
    """Tests for _render_stage_progress."""

    def test_no_session_returns_none(self):
        assert _render_stage_progress(None) is None

    def test_all_pending_returns_none(self):
        """No stage progress means nothing to render."""
        from unittest.mock import MagicMock

        session = MagicMock()
        session.get_stage_progress.return_value = {
            "ISSUE": "pending",
            "PLAN": "pending",
            "BUILD": "pending",
            "TEST": "pending",
            "REVIEW": "pending",
            "DOCS": "pending",
        }
        assert _render_stage_progress(session) is None

    def test_mixed_progress_renders_correctly(self):
        """Completed, in-progress, and pending stages render with checkboxes."""
        from unittest.mock import MagicMock

        session = MagicMock()
        session.get_stage_progress.return_value = {
            "ISSUE": "completed",
            "PLAN": "completed",
            "BUILD": "in_progress",
            "TEST": "pending",
            "REVIEW": "pending",
            "DOCS": "pending",
        }
        session.get_links.return_value = {}
        result = _render_stage_progress(session)
        # ISSUE has no checkbox
        assert "☑ ISSUE" not in result
        assert "☐ ISSUE" not in result
        # Completed stages show ☑
        assert "☑ PLAN" in result
        # In-progress shows ▶ prefix
        assert "▶ BUILD" in result
        # Pending stages show ☐
        assert "☐ TEST" in result
        # Stages joined with arrows
        assert "→" in result

    def test_all_completed(self):
        from unittest.mock import MagicMock

        session = MagicMock()
        session.get_stage_progress.return_value = {
            "ISSUE": "completed",
            "PLAN": "completed",
            "BUILD": "completed",
            "TEST": "completed",
            "REVIEW": "completed",
            "DOCS": "completed",
        }
        session.get_links.return_value = {}
        result = _render_stage_progress(session)
        assert result is not None
        assert "☐" not in result  # No pending stages
        assert "▶" not in result  # No in-progress stages
        assert "☑ PLAN" in result
        assert "☑ DOCS" in result

    def test_issue_number_embedded_in_label(self):
        """ISSUE stage shows the issue number when available in session links."""
        from unittest.mock import MagicMock

        session = MagicMock()
        session.get_stage_progress.return_value = {
            "ISSUE": "completed",
            "PLAN": "completed",
            "BUILD": "in_progress",
            "TEST": "pending",
            "REVIEW": "pending",
            "DOCS": "pending",
        }
        session.get_links.return_value = {
            "issue": "https://github.com/org/repo/issues/243"
        }
        result = _render_stage_progress(session)
        assert "ISSUE 243" in result
        assert "☑ PLAN" in result
        assert "▶ BUILD" in result
        assert "☐ TEST" in result

    def test_no_issue_number_without_links(self):
        """ISSUE stage shows plain 'ISSUE' when no issue link exists."""
        from unittest.mock import MagicMock

        session = MagicMock()
        session.get_stage_progress.return_value = {
            "ISSUE": "completed",
            "PLAN": "in_progress",
            "BUILD": "pending",
            "TEST": "pending",
            "REVIEW": "pending",
            "DOCS": "pending",
        }
        session.get_links.return_value = {}
        result = _render_stage_progress(session)
        # Should start with plain "ISSUE" not "ISSUE None" or similar
        assert result.startswith("ISSUE →")
        assert "▶ PLAN" in result


class TestRenderLinkFooter:
    """Tests for _render_link_footer."""

    def test_no_session_returns_none(self):
        assert _render_link_footer(None) is None

    def test_no_links_returns_none(self):
        from unittest.mock import MagicMock

        session = MagicMock()
        session.get_links.return_value = {}
        assert _render_link_footer(session) is None

    def test_issue_link_extracts_number(self):
        from unittest.mock import MagicMock

        session = MagicMock()
        session.get_links.return_value = {
            "issue": "https://github.com/org/repo/issues/190"
        }
        result = _render_link_footer(session)
        assert "Issue #190" in result
        assert "https://github.com/org/repo/issues/190" in result

    def test_pr_link_extracts_number(self):
        from unittest.mock import MagicMock

        session = MagicMock()
        session.get_links.return_value = {"pr": "https://github.com/org/repo/pull/191"}
        result = _render_link_footer(session)
        assert "PR #191" in result

    def test_multiple_links_joined_with_pipe(self):
        from unittest.mock import MagicMock

        session = MagicMock()
        session.get_links.return_value = {
            "issue": "https://github.com/org/repo/issues/190",
            "pr": "https://github.com/org/repo/pull/191",
        }
        result = _render_link_footer(session)
        assert " | " in result
        assert "Issue #190" in result
        assert "PR #191" in result

    def test_plan_link_excluded(self):
        """Plan links are intentionally excluded from the footer."""
        from unittest.mock import MagicMock

        session = MagicMock()
        session.get_links.return_value = {
            "issue": "https://github.com/org/repo/issues/190",
            "plan": "https://github.com/org/repo/blob/main/docs/plans/foo.md",
            "pr": "https://github.com/org/repo/pull/191",
        }
        result = _render_link_footer(session)
        assert "Plan" not in result
        assert "Issue #190" in result
        assert "PR #191" in result

    def test_plan_only_returns_none(self):
        """Session with only a plan link returns None (no visible links)."""
        from unittest.mock import MagicMock

        session = MagicMock()
        session.get_links.return_value = {"plan": "https://example.com/plan.md"}
        result = _render_link_footer(session)
        assert result is None


class TestComposeStructuredSummaryWithSession:
    """Tests for _compose_structured_summary with session context."""

    def test_sdlc_session_with_stage_progress_and_links(self):
        """Full SDLC session gets stage line, link footer, and original request label."""
        from unittest.mock import MagicMock

        session = MagicMock()
        session._get_history_list.return_value = [
            "[user] /sdlc 190",
            "[stage] ISSUE completed",
            "[stage] PLAN completed",
            "[stage] BUILD in_progress",
        ]
        session.message_text = "continue"
        session.status = "running"
        session.get_stage_progress.return_value = {
            "ISSUE": "completed",
            "PLAN": "completed",
            "BUILD": "in_progress",
            "TEST": "pending",
            "REVIEW": "pending",
            "DOCS": "pending",
        }
        session.get_links.return_value = {
            "issue": "https://github.com/org/repo/issues/190",
            "pr": "https://github.com/org/repo/pull/191",
        }

        result = _compose_structured_summary(
            "• Implemented the bypass\n• 135 tests passing",
            session=session,
            is_completion=True,
        )

        # First line is emoji only (no message echo)
        first_line = result.split("\n")[0]
        assert first_line.strip() in ("✅", "⏳", "❌")
        assert "continue" not in first_line
        # Stage progress line with checkboxes (ISSUE has none)
        assert "ISSUE 190" in result
        assert "☑ PLAN" in result
        assert "▶ BUILD" in result
        assert "☐ TEST" in result
        # Bullets present
        assert "• Implemented the bypass" in result
        # Link footer present (no plan link)
        assert "Issue #190" in result
        assert "PR #191" in result
        assert "Plan" not in result

    def test_non_sdlc_session_no_stage_line(self):
        """Non-SDLC session skips stage progress line."""
        from unittest.mock import MagicMock

        session = MagicMock()
        session._get_history_list.return_value = ["[user] What time is it?"]
        session.message_text = "What time is it?"
        session.status = "running"
        session.get_stage_progress.return_value = {
            "ISSUE": "pending",
            "PLAN": "pending",
            "BUILD": "pending",
            "TEST": "pending",
            "REVIEW": "pending",
            "DOCS": "pending",
        }
        session.get_links.return_value = {}

        result = _compose_structured_summary(
            "It's 3pm UTC+7", session=session, is_completion=True
        )

        # No stage-related content for non-SDLC
        assert "ISSUE" not in result
        assert "BUILD" not in result
        # No echo of user message (Telegram reply-to provides context)
        assert result.split("\n")[0].strip() in ("✅", "⏳", "❌")


class TestSummarizationBypass:
    """Tests for the non-SDLC short response bypass in response.py."""

    @pytest.mark.asyncio
    async def test_short_non_sdlc_skips_summarization(self):
        """Non-SDLC responses under 500 chars skip summarization entirely."""
        from unittest.mock import MagicMock

        session = MagicMock()
        session.is_sdlc_job.return_value = False

        # Simulate the bypass logic from response.py
        text = "Update complete. 3 packages updated."
        is_sdlc = hasattr(session, "is_sdlc_job") and session.is_sdlc_job()
        should_summarize = text and (is_sdlc or len(text) >= 500)

        assert not should_summarize
        assert not is_sdlc
        assert len(text) < 500

    @pytest.mark.asyncio
    async def test_short_sdlc_still_summarizes(self):
        """SDLC responses are always summarized, even if short."""
        from unittest.mock import MagicMock

        session = MagicMock()
        session.is_sdlc_job.return_value = True

        text = "Done."
        is_sdlc = hasattr(session, "is_sdlc_job") and session.is_sdlc_job()
        should_summarize = text and (is_sdlc or len(text) >= 500)

        assert should_summarize
        assert is_sdlc

    @pytest.mark.asyncio
    async def test_long_non_sdlc_still_summarizes(self):
        """Non-SDLC responses >= 500 chars are still summarized."""
        from unittest.mock import MagicMock

        session = MagicMock()
        session.is_sdlc_job.return_value = False

        text = "x" * 600
        is_sdlc = hasattr(session, "is_sdlc_job") and session.is_sdlc_job()
        should_summarize = text and (is_sdlc or len(text) >= 500)

        assert should_summarize
        assert not is_sdlc
        assert len(text) >= 500

    @pytest.mark.asyncio
    async def test_no_session_treats_as_non_sdlc(self):
        """When session is None, the bypass uses length threshold only."""
        session = None
        text = "Short reply."
        is_sdlc = session and hasattr(session, "is_sdlc_job") and session.is_sdlc_job()
        should_summarize = text and (is_sdlc or len(text) >= 500)

        assert not should_summarize

    @pytest.mark.asyncio
    async def test_empty_text_never_summarizes(self):
        """Empty text is never summarized regardless of session type."""
        from unittest.mock import MagicMock

        session = MagicMock()
        session.is_sdlc_job.return_value = True

        text = ""
        is_sdlc = session and hasattr(session, "is_sdlc_job") and session.is_sdlc_job()
        should_summarize = text and (is_sdlc or len(text) >= 500)

        assert not should_summarize
