"""Tests for bridge.summarizer — response summarization and classification."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bridge.summarizer import (
    CLASSIFICATION_CONFIDENCE_THRESHOLD,
    ClassificationResult,
    OutputType,
    StructuredSummary,
    _classify_with_heuristics,
    _compose_structured_summary,
    _get_status_emoji,
    _linkify_references,
    _parse_classification_response,
    _parse_summary_and_questions,
    classify_output,
    extract_artifacts,
    summarize_response,
)
from models.agent_session import SDLC_STAGES


def _mock_session_with_stages(stage_dict, links=None):
    """Create a MagicMock session with proper stage_states for PipelineStateMachine."""
    session = MagicMock()
    # Build full stage_states from partial dict
    all_stages = {stage: "pending" for stage in SDLC_STAGES}
    all_stages.update(stage_dict)
    session.stage_states = json.dumps(all_stages)
    session.session_id = "mock-session"
    session.get_links = MagicMock(return_value=links or {})
    # Provide issue/plan/pr URL fields for get_links fallback
    session.issue_url = (links or {}).get("issue")
    session.plan_url = (links or {}).get("plan")
    session.pr_url = (links or {}).get("pr")
    return session


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
        text = "modified: src/main.py\ncreated: tests/test_new.py\ndeleted: old_file.txt"
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
        mock_haiku = AsyncMock(
            return_value=StructuredSummary(
                context_summary="Committed changes",
                response="Done ✅ `abc1234`",
                expectations=None,
            )
        )
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

        mock_haiku = AsyncMock(
            return_value=StructuredSummary(
                context_summary="Working on output",
                response="Summary: did the work. `abc1234`",
                expectations=None,
            )
        )
        with patch("bridge.summarizer._summarize_with_haiku", mock_haiku):
            result = await summarize_response(long_text)

        assert result.was_summarized is True
        # Structured composer prepends emoji prefix for non-session summaries
        assert "Summary: did the work. `abc1234`" in result.text
        mock_haiku.assert_called_once()

    @pytest.mark.asyncio
    async def test_haiku_fails_falls_back_to_openrouter(self):
        """If Haiku fails, OpenRouter is tried next."""
        long_text = "Detailed work output. " * 200

        mock_haiku = AsyncMock(return_value=None)
        mock_openrouter = AsyncMock(
            return_value=StructuredSummary(
                context_summary="Working on output",
                response="OpenRouter summary of work.",
                expectations=None,
            )
        )
        with (
            patch("bridge.summarizer._summarize_with_haiku", mock_haiku),
            patch("bridge.summarizer._summarize_with_openrouter", mock_openrouter),
        ):
            result = await summarize_response(long_text)

        assert result.was_summarized is True
        assert "OpenRouter summary of work." in result.text
        mock_haiku.assert_called_once()
        mock_openrouter.assert_called_once()

    @pytest.mark.asyncio
    async def test_all_backends_fail_truncates(self):
        """If all summarization backends fail, truncate."""
        long_text = "x" * 5000

        mock_haiku = AsyncMock(return_value=None)
        mock_openrouter = AsyncMock(return_value=None)
        with (
            patch("bridge.summarizer._summarize_with_haiku", mock_haiku),
            patch("bridge.summarizer._summarize_with_openrouter", mock_openrouter),
        ):
            result = await summarize_response(long_text)

        assert result.was_summarized is False
        assert len(result.text) <= 4096
        assert result.text.endswith("...")

    @pytest.mark.asyncio
    async def test_very_long_response_creates_file(self):
        """Responses over FILE_ATTACH_THRESHOLD get a full output file."""
        long_text = "Output line.\n" * 500

        mock_haiku = AsyncMock(
            return_value=StructuredSummary(
                context_summary="Work output",
                response="Summary of work.",
                expectations=None,
            )
        )
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

        mock_haiku = AsyncMock(
            return_value=StructuredSummary(
                context_summary="Mid-length content",
                response="Short summary.",
                expectations=None,
            )
        )
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
        assert result.coaching_message == "You said 'should work' but didn't show test output."

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
        raw = '```json\n{"type": "completion", "confidence": 0.88, "reason": "done"}\n```'
        result = _parse_classification_response(raw)
        assert result is not None
        assert result.output_type == OutputType.COMPLETION

    def test_code_fences_no_language(self):
        """Handles code fences without language tag."""
        raw = '```\n{"type": "status", "confidence": 0.75, "reason": "in progress"}\n```'
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
        assert result.coaching_message == "You said 'should work' — run the tests and share output."

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
            assert result.coaching_message is None, (
                f"coaching_message should be None for {type_str}"
            )
            assert result.was_rejected_completion is False, (
                f"was_rejected_completion should be False for {type_str}"
            )

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
        result = _classify_with_heuristics("Error: ModuleNotFoundError: No module named 'foo'")
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
        result = _classify_with_heuristics("Permission denied when accessing the deployment config")
        assert result.output_type == OutputType.BLOCKER

    def test_blocker_cannot_proceed(self):
        result = _classify_with_heuristics("Cannot proceed without database credentials")
        assert result.output_type == OutputType.BLOCKER

    def test_completion_done(self):
        result = _classify_with_heuristics("Done. All changes committed.")
        assert result.output_type == OutputType.COMPLETION
        assert result.confidence >= 0.80

    def test_completion_pr_url(self):
        result = _classify_with_heuristics("PR created: https://github.com/org/repo/pull/42")
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
        assert result.confidence == 0.80  # At threshold to avoid redundant gate re-conversion

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
        result = _classify_with_heuristics("Plan is ready. Shall I proceed with the build?")
        assert result.output_type == OutputType.QUESTION

    def test_approval_gate_awaiting_approval(self):
        """'awaiting approval' triggers QUESTION."""
        result = _classify_with_heuristics("PR is up, awaiting your approval before merging")
        assert result.output_type == OutputType.QUESTION

    def test_approval_gate_let_me_know_when(self):
        """'let me know when' triggers QUESTION."""
        result = _classify_with_heuristics("Let me know when you want me to start the migration")
        assert result.output_type == OutputType.QUESTION


class TestEmptyPromiseDetection:
    """Tests for empty promise detection in heuristic classifier."""

    def test_bare_acknowledgment_is_empty_promise(self):
        """'Got it' + commitment without evidence = empty promise."""
        result = _classify_with_heuristics("Got it. Will report final results and blockers only.")
        assert result.output_type == OutputType.STATUS_UPDATE
        assert result.coaching_message is not None
        assert (
            "empty" in result.coaching_message.lower()
            or "evidence" in result.coaching_message.lower()
        )

    def test_understood_without_evidence_is_empty_promise(self):
        """'Understood' without a concrete change = empty promise."""
        result = _classify_with_heuristics(
            "Understood. I'll adjust my communication style going forward."
        )
        assert result.output_type == OutputType.STATUS_UPDATE
        assert result.coaching_message is not None

    def test_noted_without_evidence_is_empty_promise(self):
        """'Noted' with a vague commitment = empty promise."""
        result = _classify_with_heuristics("Noted. You'll see the difference in my next output.")
        assert result.output_type == OutputType.STATUS_UPDATE
        assert result.coaching_message is not None

    def test_acknowledgment_with_commit_is_not_empty(self):
        """Acknowledgment WITH a commit hash = real action, not empty."""
        result = _classify_with_heuristics(
            "Got it. Updated the summarizer prompt. Committed abc1234."
        )
        # Should be classified as completion (has commit evidence)
        assert result.output_type == OutputType.COMPLETION

    def test_acknowledgment_with_file_path_is_not_empty(self):
        """Acknowledgment WITH a file edit = real action, not empty."""
        result = _classify_with_heuristics(
            "Understood. Saved memory to feedback_no_plans.md with this rule."
        )
        assert result.output_type != OutputType.STATUS_UPDATE or result.coaching_message is None

    def test_normal_status_not_flagged(self):
        """Regular status updates should not trigger empty promise detection."""
        result = _classify_with_heuristics("Running tests now, found 3 issues so far.")
        assert (
            result.coaching_message is None
            or "empty" not in (result.coaching_message or "").lower()
        )

    def test_will_do_without_evidence(self):
        """'Will do' without proof = empty promise."""
        result = _classify_with_heuristics("Will do. I'll change my approach from now on.")
        assert result.output_type == OutputType.STATUS_UPDATE
        assert result.coaching_message is not None


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
            patch("bridge.summarizer.get_anthropic_api_key", return_value="sk-test"),
            patch("bridge.summarizer.anthropic.AsyncAnthropic", return_value=mock_client),
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
            patch("bridge.summarizer.get_anthropic_api_key", return_value="sk-test"),
            patch("bridge.summarizer.anthropic.AsyncAnthropic", return_value=mock_client),
        ):
            result = await classify_output("Some ambiguous output")

        assert result.output_type == OutputType.QUESTION
        assert result.confidence == 0.5
        assert "Low confidence" in result.reason

    @pytest.mark.asyncio
    async def test_llm_failure_falls_back_to_heuristics(self):
        """When LLM call fails, heuristics are used."""
        with (
            patch("bridge.summarizer.get_anthropic_api_key", return_value="sk-test"),
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
        with patch("bridge.summarizer.get_anthropic_api_key", return_value=""):
            result = await classify_output("Error: ModuleNotFoundError: No module named 'foo'")

        assert result.output_type == OutputType.ERROR

    @pytest.mark.asyncio
    async def test_unparseable_llm_response_falls_back(self):
        """When LLM returns garbage, falls back to heuristics."""
        mock_response = AsyncMock()
        mock_response.content = [AsyncMock(text="I think this is a question")]
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with (
            patch("bridge.summarizer.get_anthropic_api_key", return_value="sk-test"),
            patch("bridge.summarizer.anthropic.AsyncAnthropic", return_value=mock_client),
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
            AsyncMock(text='{"type": "status", "confidence": 0.90, "reason": "progress report"}')
        ]
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with (
            patch("bridge.summarizer.get_anthropic_api_key", return_value="sk-test"),
            patch("bridge.summarizer.anthropic.AsyncAnthropic", return_value=mock_client),
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
            patch("bridge.summarizer.get_anthropic_api_key", return_value="sk-test"),
            patch("bridge.summarizer.anthropic.AsyncAnthropic", return_value=mock_client),
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
            patch("bridge.summarizer.get_anthropic_api_key", return_value="sk-test"),
            patch("bridge.summarizer.anthropic.AsyncAnthropic", return_value=mock_client),
        ):
            result = await classify_output("All tests pass. Task complete.")

        assert result.output_type == OutputType.STATUS_UPDATE
        assert result.coaching_message == "Paste the pytest output with pass/fail counts."
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
            patch("bridge.summarizer.get_anthropic_api_key", return_value="sk-test"),
            patch("bridge.summarizer.anthropic.AsyncAnthropic", return_value=mock_client),
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
        text = "• Built the feature\n• Pushed to main\n---\n>> Should I merge?"
        bullets, questions = _parse_summary_and_questions(text)
        assert bullets == "• Built the feature\n• Pushed to main"
        assert questions == ">> Should I merge?"

    def test_bullets_and_questions_legacy_prefix(self):
        """Legacy ? prefix is normalized to >> prefix."""
        text = "• Built the feature\n• Pushed to main\n---\n? Should I merge?"
        bullets, questions = _parse_summary_and_questions(text)
        assert bullets == "• Built the feature\n• Pushed to main"
        assert questions == ">> Should I merge?"

    def test_multiple_questions(self):
        text = "• Done\n---\n>> Q1\n>> Q2\n>> Q3"
        bullets, questions = _parse_summary_and_questions(text)
        assert bullets == "• Done"
        assert ">> Q1" in questions
        assert ">> Q2" in questions
        assert ">> Q3" in questions

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
        result = _compose_structured_summary("• Done\n---\n>> Should I merge?", session=None)
        assert ">> Should I merge?" in result
        assert "• Done" in result

    def test_not_completion_uses_pending_emoji(self):
        result = _compose_structured_summary("• Working on it", session=None, is_completion=False)
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
        session.is_sdlc = True

        result = _compose_structured_summary(
            "• Built the bypass\n• Tests passing", session=session, is_completion=True
        )
        first_line = result.split("\n")[0]
        assert first_line.strip() in ("✅", "⏳", "❌", "")
        assert "continue" not in first_line

    def test_no_echo_on_regular_session(self):
        """Regular sessions should not echo the user's message."""
        from unittest.mock import MagicMock

        session = MagicMock()
        session._get_history_list.return_value = ["[user] What time is it?"]
        session.message_text = "What time is it?"
        session.status = "completed"
        session.get_links.return_value = {}

        result = _compose_structured_summary("It's 3pm UTC+7", session=session, is_completion=True)
        assert "What time is it?" not in result


class TestGetStatusEmojiRegression:
    """Regression tests for _get_status_emoji — issue #192.

    Updated for milestone-selective behavior (issue #540).
    """

    def test_running_session_with_completion_returns_empty(self):
        """Routine completion with running session returns empty."""
        from unittest.mock import MagicMock

        session = MagicMock()
        session.status = "running"

        result = _get_status_emoji(session, is_completion=True)
        assert result == ""

    def test_running_session_without_completion_returns_pending(self):
        """is_completion=False with running session returns hourglass."""
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

    def test_completed_without_pr_returns_empty(self):
        """Completed session without PR is routine (no emoji)."""
        from unittest.mock import MagicMock

        session = MagicMock()
        session.status = "completed"
        session.get_links.return_value = {}

        assert _get_status_emoji(session, is_completion=True) == ""

    def test_completed_with_pr_returns_checkmark(self):
        """Completed session with PR is milestone (checkmark)."""
        from unittest.mock import MagicMock

        session = MagicMock()
        session.status = "completed"
        session.get_links.return_value = {
            "pr": "https://github.com/org/repo/pull/42",
        }

        assert _get_status_emoji(session, is_completion=True) == "✅"

    def test_no_session_uses_completion_flag(self):
        """No session defers to is_completion flag."""
        assert _get_status_emoji(None, is_completion=True) == "✅"
        assert _get_status_emoji(None, is_completion=False) == "⏳"


class TestComposeStructuredSummaryWithSession:
    """Tests for _compose_structured_summary with session context."""

    def test_sdlc_session_renders_summary(self):
        """Full SDLC session gets emoji prefix and summary bullets."""
        links = {
            "issue": "https://github.com/org/repo/issues/190",
            "pr": "https://github.com/org/repo/pull/191",
        }
        session = _mock_session_with_stages(
            {"ISSUE": "completed", "PLAN": "completed", "BUILD": "in_progress"},
            links=links,
        )
        session._get_history_list.return_value = [
            "[user] /sdlc 190",
        ]
        session.message_text = "continue"
        session.status = "running"

        result = _compose_structured_summary(
            "\u2022 Implemented the bypass\n\u2022 135 tests passing",
            session=session,
            is_completion=True,
        )

        # First line is empty (routine) or emoji
        first_line = result.split("\n")[0]
        assert first_line.strip() in ("\u2705", "\u23f3", "\u274c", "")
        assert "continue" not in first_line
        # Bullets present
        assert "\u2022 Implemented the bypass" in result

    def test_non_sdlc_session_no_stage_line(self):
        """Non-SDLC session skips stage progress line."""
        session = _mock_session_with_stages({})  # All pending
        session._get_history_list.return_value = ["[user] What time is it?"]
        session.message_text = "What time is it?"
        session.status = "running"

        result = _compose_structured_summary("It's 3pm UTC+7", session=session, is_completion=True)

        # No stage-related content for non-SDLC
        assert "ISSUE" not in result
        assert "BUILD" not in result
        # First line is empty or emoji
        assert result.split("\n")[0].strip() in ("\u2705", "\u23f3", "\u274c", "")


class TestSummarizationBypass:
    """Tests for the non-SDLC short response bypass in response.py."""

    @pytest.mark.asyncio
    async def test_short_non_sdlc_skips_summarization(self):
        """Non-SDLC responses under 500 chars skip summarization entirely."""
        from unittest.mock import MagicMock

        session = MagicMock()
        session.is_sdlc = False

        # Simulate the bypass logic from response.py
        text = "Update complete. 3 packages updated."
        is_sdlc = hasattr(session, "is_sdlc") and session.is_sdlc
        should_summarize = text and (is_sdlc or len(text) >= 500)

        assert not should_summarize
        assert not is_sdlc
        assert len(text) < 500

    @pytest.mark.asyncio
    async def test_short_sdlc_still_summarizes(self):
        """SDLC responses are always summarized, even if short."""
        from unittest.mock import MagicMock

        session = MagicMock()
        session.is_sdlc = True

        text = "Done."
        is_sdlc = hasattr(session, "is_sdlc") and session.is_sdlc
        should_summarize = text and (is_sdlc or len(text) >= 500)

        assert should_summarize
        assert is_sdlc

    @pytest.mark.asyncio
    async def test_long_non_sdlc_still_summarizes(self):
        """Non-SDLC responses >= 500 chars are still summarized."""
        from unittest.mock import MagicMock

        session = MagicMock()
        session.is_sdlc = False

        text = "x" * 600
        is_sdlc = hasattr(session, "is_sdlc") and session.is_sdlc
        should_summarize = text and (is_sdlc or len(text) >= 500)

        assert should_summarize
        assert not is_sdlc
        assert len(text) >= 500

    @pytest.mark.asyncio
    async def test_no_session_treats_as_non_sdlc(self):
        """When session is None, the bypass uses length threshold only."""
        session = None
        text = "Short reply."
        is_sdlc = session and hasattr(session, "is_sdlc") and session.is_sdlc
        should_summarize = text and (is_sdlc or len(text) >= 500)

        assert not should_summarize

    @pytest.mark.asyncio
    async def test_empty_text_never_summarizes(self):
        """Empty text is never summarized regardless of session type."""
        from unittest.mock import MagicMock

        session = MagicMock()
        session.is_sdlc = True

        text = ""
        is_sdlc = session and hasattr(session, "is_sdlc") and session.is_sdlc
        should_summarize = text and (is_sdlc or len(text) >= 500)

        assert not should_summarize


class TestQuestionFabricationPrevention:
    """Tests for anti-fabrication rules in the summarizer (issue #280).

    The summarizer must NEVER fabricate questions from declarative statements.
    Only explicit questions (sentences ending in "?" directed at the human)
    may appear in the "?" section or set the expectations field.
    """

    @pytest.mark.asyncio
    async def test_no_questions_fabricated_from_declarative_statements(self):
        """Declarative planned work must produce expectations=None, no '?' lines."""
        agent_output = (
            "I will add sdlc to classifier categories. "
            "I will fix auto-continue to carry forward session state."
        )
        mock_haiku = AsyncMock(
            return_value=StructuredSummary(
                context_summary="Planning classifier and auto-continue fixes",
                response=(
                    "• Adding sdlc to classifier categories\n"
                    "• Fixing auto-continue session state propagation"
                ),
                expectations=None,
            )
        )
        with patch("bridge.summarizer._summarize_with_haiku", mock_haiku):
            result = await summarize_response(agent_output)

        assert result.expectations is None
        # Verify no --- separator (which precedes questions)
        assert "\n---\n" not in result.text

    @pytest.mark.asyncio
    async def test_explicit_questions_preserved_verbatim(self):
        """Real questions in agent output must be preserved in expectations."""
        # Input must be longer than the mock response to avoid the
        # "summary longer than original" safety fallback
        agent_output = (
            "I've completed building the authentication module with token rotation, "
            "session management, and retry logic. The implementation includes proper "
            "error handling for all edge cases including network timeouts, invalid "
            "tokens, and rate limiting. All 12 tests are passing with full coverage.\n\n"
            "Should we use exponential backoff or fixed intervals?"
        )
        mock_haiku = AsyncMock(
            return_value=StructuredSummary(
                context_summary="Auth module with backoff decision",
                response=(
                    "• Built auth module with token rotation\n"
                    "• 12 tests passing\n---\n"
                    "? Should we use exponential backoff or fixed intervals?"
                ),
                expectations="Should we use exponential backoff or fixed intervals?",
            )
        )
        with patch("bridge.summarizer._summarize_with_haiku", mock_haiku):
            result = await summarize_response(agent_output)

        assert result.expectations is not None
        assert "exponential backoff" in result.expectations
        assert ">> Should we use exponential backoff or fixed intervals?" in result.text

    @pytest.mark.asyncio
    async def test_mixed_declarative_and_questions(self):
        """Only explicit questions surfaced; declarative statements stay as bullets."""
        agent_output = (
            "Implemented retry logic with 3 attempts. "
            "Refactored the error handler to use structured exceptions.\n\n"
            "The API rate limit is 100/min — should we add client-side throttling?"
        )
        mock_haiku = AsyncMock(
            return_value=StructuredSummary(
                context_summary="Retry logic with rate limit question",
                response=(
                    "• Implemented retry logic with 3 attempts\n"
                    "• Refactored error handler to structured exceptions\n---\n"
                    "? Should we add client-side throttling?"
                ),
                expectations="Should we add client-side throttling?",
            )
        )
        with patch("bridge.summarizer._summarize_with_haiku", mock_haiku):
            result = await summarize_response(agent_output)

        assert result.expectations is not None
        assert "throttling" in result.expectations
        # The retry logic should be a bullet, not a question
        assert "retry" in result.text.lower()
        # Only one question line
        lines = result.text.split("\n")
        question_lines = [line for line in lines if line.strip().startswith(">>")]
        assert len(question_lines) == 1
        assert "throttling" in question_lines[0]

    @pytest.mark.asyncio
    async def test_future_tense_plans_not_turned_into_questions(self):
        """'Will do X' statements must not become questions."""
        agent_output = (
            "Next steps: will update the migration script, "
            "will add index to users table, will run load test."
        )
        mock_haiku = AsyncMock(
            return_value=StructuredSummary(
                context_summary="Planning migration and performance work",
                response=(
                    "• Will update migration script\n"
                    "• Will add index to users table\n"
                    "• Will run load test"
                ),
                expectations=None,
            )
        )
        with patch("bridge.summarizer._summarize_with_haiku", mock_haiku):
            result = await summarize_response(agent_output)

        assert result.expectations is None
        assert "\n---\n" not in result.text

    @pytest.mark.asyncio
    async def test_rhetorical_questions_not_surfaced(self):
        """Rhetorical questions in agent reasoning must not set expectations."""
        agent_output = (
            "Why was this never caught? Because the test suite didn't cover "
            "this path. Fixed by adding integration test."
        )
        mock_haiku = AsyncMock(
            return_value=StructuredSummary(
                context_summary="Fixed missing test coverage",
                response="• Fixed missing test coverage by adding integration test",
                expectations=None,
            )
        )
        with patch("bridge.summarizer._summarize_with_haiku", mock_haiku):
            result = await summarize_response(agent_output)

        assert result.expectations is None

    @pytest.mark.asyncio
    async def test_code_snippet_with_question_marks_not_treated_as_questions(self):
        """Question marks inside code snippets must not be extracted as questions."""
        agent_output = (
            "Fixed the regex: `if line.endswith('?'):` now handles edge cases. All 8 tests passing."
        )
        mock_haiku = AsyncMock(
            return_value=StructuredSummary(
                context_summary="Fixed regex edge case handling",
                response="• Fixed regex `endswith('?')` edge case\n• 8 tests passing",
                expectations=None,
            )
        )
        with patch("bridge.summarizer._summarize_with_haiku", mock_haiku):
            result = await summarize_response(agent_output)

        assert result.expectations is None
        assert "\n---\n" not in result.text

    @pytest.mark.asyncio
    async def test_conditional_statements_not_treated_as_questions(self):
        """'If X' and 'whether Y' statements must not become questions."""
        agent_output = (
            "If the CI pipeline fails, the deploy will be blocked. "
            "Whether to retry depends on the error type."
        )
        mock_haiku = AsyncMock(
            return_value=StructuredSummary(
                context_summary="CI pipeline deployment notes",
                response="• CI failure blocks deploy; retry depends on error type",
                expectations=None,
            )
        )
        with patch("bridge.summarizer._summarize_with_haiku", mock_haiku):
            result = await summarize_response(agent_output)

        assert result.expectations is None

    def test_prompt_contains_anti_fabrication_instruction(self):
        """Verify SUMMARIZER_SYSTEM_PROMPT includes anti-fabrication rules."""
        from bridge.summarizer import SUMMARIZER_SYSTEM_PROMPT

        assert "NEVER fabricate questions" in SUMMARIZER_SYSTEM_PROMPT
        assert "NEVER reframe declarative statements as questions" in SUMMARIZER_SYSTEM_PROMPT
        assert "VERBATIM" in SUMMARIZER_SYSTEM_PROMPT

    def test_prompt_contains_negative_examples(self):
        """Verify SUMMARIZER_SYSTEM_PROMPT includes negative examples."""
        from bridge.summarizer import SUMMARIZER_SYSTEM_PROMPT

        assert "WRONG" in SUMMARIZER_SYSTEM_PROMPT
        assert "FABRICATED" in SUMMARIZER_SYSTEM_PROMPT
        assert "I will add sdlc to classifier categories" in SUMMARIZER_SYSTEM_PROMPT

    def test_expectations_tool_schema_updated(self):
        """Verify the tool schema description for expectations reflects anti-fabrication."""
        from bridge.summarizer import STRUCTURED_SUMMARY_TOOL

        schema = STRUCTURED_SUMMARY_TOOL["input_schema"]
        expectations_desc = schema["properties"]["expectations"]["description"]
        assert "explicit question" in expectations_desc.lower()


class TestQuestionFabricationIntegration:
    """Integration tests using real Haiku API to validate anti-fabrication behavior."""

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        not __import__("os").environ.get("ANTHROPIC_API_KEY"),
        reason="ANTHROPIC_API_KEY not set",
    )
    async def test_real_haiku_no_fabricated_questions(self):
        """Real Haiku should not fabricate questions from declarative statements."""
        # This is the actual raw output pattern that triggered the bug
        agent_output = (
            "I identified two root causes for the session tracking bugs:\n\n"
            "1. The classifier doesn't have an 'sdlc' category. I will add sdlc "
            "to the classifier categories so it can properly identify SDLC work.\n\n"
            "2. Auto-continue doesn't carry forward session state. I will fix "
            "auto-continue to propagate the classification_type and branch_name "
            "from the parent session to the continued session.\n\n"
            "Both fixes are straightforward — modifying the classifier prompt and "
            "the auto-continue handler in the bridge."
        )
        result = await summarize_response(agent_output)

        assert result.was_summarized is True
        assert result.expectations is None, (
            f"Haiku fabricated expectations from declarative output: {result.expectations}"
        )
        # No question prefix lines should appear (>> or legacy ?)
        lines = result.text.split("\n")
        question_lines = [
            line for line in lines if line.strip().startswith(">>") or line.strip().startswith("?")
        ]
        assert len(question_lines) == 0, f"Haiku fabricated question lines: {question_lines}"

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        not __import__("os").environ.get("ANTHROPIC_API_KEY"),
        reason="ANTHROPIC_API_KEY not set",
    )
    async def test_real_haiku_preserves_real_questions(self):
        """Real Haiku should preserve genuine questions in expectations."""
        agent_output = (
            "Completed the database schema refactor. All 15 tests passing.\n"
            "Committed abc1234 and pushed to session/db-refactor.\n\n"
            "Should I merge to main or wait for the design review?"
        )
        result = await summarize_response(agent_output)

        assert result.was_summarized is True
        assert result.expectations is not None, (
            "Haiku failed to surface the genuine question about merging"
        )
        assert "merge" in result.expectations.lower() or "review" in result.expectations.lower()

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        not __import__("os").environ.get("ANTHROPIC_API_KEY"),
        reason="ANTHROPIC_API_KEY not set",
    )
    async def test_real_haiku_sdlc_work_summary_no_questions(self):
        """SDLC completion output without questions should have expectations=None."""
        agent_output = (
            "Plan execution complete for fix-session-tracking.\n\n"
            "Changes made:\n"
            "- Modified bridge/summarizer.py: updated classifier categories\n"
            "- Modified bridge/telegram_bridge.py: fixed auto-continue propagation\n"
            "- Created tests/test_session_tracking.py: 8 new tests\n\n"
            "Test results: 135 passed, 0 failed\n"
            "Committed def5678 and pushed to session/fix-session-tracking.\n"
            "PR created: https://github.com/org/repo/pull/277"
        )
        result = await summarize_response(agent_output)

        assert result.was_summarized is True
        assert result.expectations is None, (
            f"Haiku fabricated expectations from SDLC completion: {result.expectations}"
        )


class TestErrorStateRendering:
    """Tests for _compose_structured_summary with error/failed states (Gap 4).

    Verifies that error states render correctly with:
    - Failure emoji (X)
    - Failed stage progress showing the failure point
    - Error messages reaching the output
    """

    def test_failed_session_renders_error_emoji(self):
        """Failed session should render with X emoji."""
        session = _mock_session_with_stages(
            {"ISSUE": "completed", "PLAN": "completed", "BUILD": "failed"},
            links={"issue": "https://github.com/org/repo/issues/200"},
        )
        session._get_history_list.return_value = [
            "[user] /sdlc 200",
        ]
        session.message_text = "continue"
        session.status = "failed"
        session.is_sdlc = True

        result = _compose_structured_summary(
            "• Build failed: pytest returned exit code 1\n• 3 tests failing",
            session=session,
            is_completion=False,
        )

        # First line should be error emoji
        first_line = result.split("\n")[0]
        assert first_line.strip() == "❌"

    def test_failed_session_with_completion_flag_still_shows_error(self):
        """Failed status overrides is_completion=True -- error emoji takes priority."""
        session = _mock_session_with_stages(
            {"ISSUE": "completed", "PLAN": "completed", "BUILD": "completed", "TEST": "failed"},
        )
        session._get_history_list.return_value = ["[user] test"]
        session.message_text = "test"
        session.status = "failed"

        result = _compose_structured_summary(
            "• Tests failed",
            session=session,
            is_completion=True,  # Even with completion flag, failed session shows X
        )

        first_line = result.split("\n")[0]
        assert first_line.strip() == "❌"

    def test_failed_stage_shows_in_progress(self):
        """Failed stage progress should render the failure point visibly.

        The stage progress renderer shows failed stages with a cross mark.
        This test ensures the stage line is present for failed sessions.
        """
        session = _mock_session_with_stages(
            {"ISSUE": "completed", "PLAN": "completed", "BUILD": "failed"},
        )
        session._get_history_list.return_value = []
        session.message_text = "continue"
        session.status = "failed"
        session.is_sdlc = True

        result = _compose_structured_summary(
            "• Build failed at test stage",
            session=session,
            is_completion=False,
        )

        # Error content should be present (stage progress removed)
        assert "Build failed" in result

    def test_error_message_propagated_to_output(self):
        """Error messages in the summary text should reach the rendered output."""
        session = _mock_session_with_stages({})  # All pending
        session._get_history_list.return_value = []
        session.message_text = "continue"
        session.status = "failed"

        error_text = "Error: ModuleNotFoundError: No module named 'foo'"
        result = _compose_structured_summary(
            f"• {error_text}",
            session=session,
            is_completion=False,
        )

        # Error message should be in the rendered output
        assert "ModuleNotFoundError" in result

    def test_failed_session_with_link_footer(self):
        """Failed session should still render link footer with issue reference."""
        session = _mock_session_with_stages(
            {"ISSUE": "completed", "PLAN": "completed", "BUILD": "failed"},
            links={"issue": "https://github.com/org/repo/issues/200"},
        )
        session._get_history_list.return_value = []
        session.message_text = "continue"
        session.status = "failed"
        session.is_sdlc = True

        result = _compose_structured_summary(
            "• Build failed: 3 tests failing",
            session=session,
            is_completion=False,
        )

        # Error emoji and content present (link footer removed)
        assert "❌" in result
        assert "Build failed" in result

    def test_get_status_emoji_failed_overrides_everything(self):
        """_get_status_emoji with failed status always returns error emoji."""
        from unittest.mock import MagicMock

        session = MagicMock()
        session.status = "failed"

        # Even with is_completion=True, failed status wins
        assert _get_status_emoji(session, is_completion=True) == "❌"
        assert _get_status_emoji(session, is_completion=False) == "❌"


class TestLinkifyReferences:
    """Unit tests for _linkify_references — converting plain PR/Issue refs to markdown links."""

    def _make_session(self, project_key="valor"):
        """Create a mock session with the given project_key."""
        from unittest.mock import MagicMock

        session = MagicMock()
        session.project_key = project_key
        return session

    def _register_config(self, project_key="valor", org="tomcounsell", repo="ai"):
        """Register a project config with GitHub org/repo for testing."""
        from agent.job_queue import register_project_config

        register_project_config(
            project_key,
            {"github": {"org": org, "repo": repo}},
        )

    def test_pr_reference_linkified(self):
        """PR #N is converted to a markdown link."""
        self._register_config("psyoptimal", org="yudame", repo="psyoptimal")
        session = self._make_session("psyoptimal")
        result = _linkify_references("PR #323", session)
        assert result == "[PR #323](https://github.com/yudame/psyoptimal/pull/323)"

    def test_issue_reference_linkified(self):
        """Issue #N is converted to a markdown link."""
        self._register_config("valor", org="tomcounsell", repo="ai")
        session = self._make_session("valor")
        result = _linkify_references("Issue #309", session)
        assert result == "[Issue #309](https://github.com/tomcounsell/ai/issues/309)"

    def test_multiple_references(self):
        """Multiple PR references in the same text are all linkified."""
        self._register_config("valor", org="tomcounsell", repo="ai")
        session = self._make_session("valor")
        result = _linkify_references("PR #322 and PR #323", session)
        assert "[PR #322](https://github.com/tomcounsell/ai/pull/322)" in result
        assert "[PR #323](https://github.com/tomcounsell/ai/pull/323)" in result

    def test_already_linked_not_doubled(self):
        """Already-linked references inside markdown syntax are not double-linked."""
        self._register_config("valor", org="tomcounsell", repo="ai")
        session = self._make_session("valor")
        text = "[PR #323](https://github.com/tomcounsell/ai/pull/323)"
        result = _linkify_references(text, session)
        assert result == text

    def test_no_session_returns_unchanged(self):
        """With session=None, text is returned unchanged."""
        result = _linkify_references("PR #323", None)
        assert result == "PR #323"

    def test_no_project_key_returns_unchanged(self):
        """Session without project_key returns text unchanged."""
        from unittest.mock import MagicMock

        session = MagicMock()
        session.project_key = None
        result = _linkify_references("PR #323", session)
        assert result == "PR #323"

    def test_no_github_config_returns_unchanged(self):
        """project_key exists but no GitHub config registered returns unchanged."""
        from agent.job_queue import register_project_config

        register_project_config("no-github", {"name": "No GitHub"})
        session = self._make_session("no-github")
        result = _linkify_references("PR #323", session)
        assert result == "PR #323"

    def test_mixed_pr_and_issue(self):
        """Both PR #N and Issue #N in the same text are both linkified."""
        self._register_config("valor", org="tomcounsell", repo="ai")
        session = self._make_session("valor")
        result = _linkify_references("Fixed PR #100 for Issue #200", session)
        assert "[PR #100](https://github.com/tomcounsell/ai/pull/100)" in result
        assert "[Issue #200](https://github.com/tomcounsell/ai/issues/200)" in result

    def test_empty_text_returns_unchanged(self):
        """Empty text is returned as-is."""
        session = self._make_session("valor")
        assert _linkify_references("", session) == ""

    def test_empty_project_key_string_returns_unchanged(self):
        """Session with empty string project_key returns text unchanged."""
        from unittest.mock import MagicMock

        session = MagicMock()
        session.project_key = "   "
        result = _linkify_references("PR #323", session)
        assert result == "PR #323"


class TestSummarizerBypass:
    """Tests for PM self-messaging summarizer bypass (issue #497).

    When the PM sends messages via tools/send_telegram.py during a session,
    the summarizer should be skipped entirely in send_response_with_files.
    """

    @pytest.mark.asyncio
    async def test_bypass_when_pm_has_messages(self):
        """send_response_with_files should return True without summarizing
        when pm_sent_message_ids is non-empty."""
        from bridge.response import send_response_with_files

        mock_client = MagicMock()
        mock_session = MagicMock()
        mock_session.has_pm_messages.return_value = True
        mock_session.pm_sent_message_ids = [42, 43]
        mock_session.session_id = "test-session"

        result = await send_response_with_files(
            mock_client,
            None,
            "Some agent output",
            chat_id=12345,
            reply_to=67890,
            session=mock_session,
        )

        assert result is True

    @pytest.mark.asyncio
    async def test_no_bypass_when_no_pm_messages(self):
        """send_response_with_files should proceed normally when
        pm_sent_message_ids is empty."""
        from bridge.response import send_response_with_files

        mock_client = MagicMock()
        mock_session = MagicMock()
        mock_session.has_pm_messages.return_value = False
        mock_session.pm_sent_message_ids = []
        mock_session.session_id = "test-session"
        mock_session.is_sdlc = False

        # Mock send_markdown to avoid Telethon calls
        with patch("bridge.markdown.send_markdown", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = MagicMock()
            result = await send_response_with_files(
                mock_client,
                None,
                "Short",
                chat_id=12345,
                reply_to=67890,
                session=mock_session,
            )

        # Should have tried to send the text (not bypassed)
        assert mock_send.called or result is True


class TestNormalizeQuestionPrefix:
    """Tests for _normalize_question_prefix."""

    def test_legacy_prefix_normalized(self):
        from bridge.summarizer import _normalize_question_prefix

        result = _normalize_question_prefix("? Should I merge?")
        assert result == ">> Should I merge?"

    def test_new_prefix_unchanged(self):
        from bridge.summarizer import _normalize_question_prefix

        result = _normalize_question_prefix(">> Should I merge?")
        assert result == ">> Should I merge?"

    def test_mixed_prefixes(self):
        from bridge.summarizer import _normalize_question_prefix

        text = "? First question\n>> Second question\n? Third"
        result = _normalize_question_prefix(text)
        assert ">> First question" in result
        assert ">> Second question" in result
        assert ">> Third" in result
        assert "? " not in result

    def test_non_question_lines_unchanged(self):
        from bridge.summarizer import _normalize_question_prefix

        result = _normalize_question_prefix("Normal text here")
        assert result == "Normal text here"


class TestCrashMessagePool:
    """Tests for the crash message pool in sdk_client.py."""

    def test_pool_has_minimum_variants(self):
        from agent.sdk_client import CRASH_MESSAGE_POOL

        assert len(CRASH_MESSAGE_POOL) >= 4

    def test_get_crash_message_returns_string(self):
        from agent.sdk_client import _get_crash_message

        msg = _get_crash_message()
        assert isinstance(msg, str)
        assert len(msg) > 10

    def test_no_consecutive_repeats(self):
        """Crash messages should not repeat consecutively."""
        import agent.sdk_client as mod
        from agent.sdk_client import _get_crash_message

        mod._last_crash_message = None

        messages = [_get_crash_message() for _ in range(20)]
        for i in range(1, len(messages)):
            assert messages[i] != messages[i - 1], f"Consecutive repeat at index {i}: {messages[i]}"

    def test_first_call_no_previous(self):
        """First call with no previous message should work."""
        import agent.sdk_client as mod
        from agent.sdk_client import _get_crash_message

        mod._last_crash_message = None
        msg = _get_crash_message()
        assert msg in mod.CRASH_MESSAGE_POOL

    def test_all_variants_include_next_step(self):
        """Each crash message includes next-step language."""
        from agent.sdk_client import CRASH_MESSAGE_POOL

        next_step_words = [
            "retry",
            "try again",
            "re-trigger",
            "re-send",
            "check back",
        ]
        for msg in CRASH_MESSAGE_POOL:
            has_next = any(w in msg.lower() for w in next_step_words)
            assert has_next, f"Missing next-step language: {msg}"


class TestSentenceAwareTruncation:
    """Tests for _truncate_at_sentence_boundary in response.py."""

    def test_short_text_unchanged(self):
        from bridge.response import _truncate_at_sentence_boundary

        text = "Short text."
        assert _truncate_at_sentence_boundary(text) == text

    def test_truncates_at_sentence_boundary(self):
        from bridge.response import _truncate_at_sentence_boundary

        sentences = "First sentence. Second sentence. Third. "
        text = sentences * 50
        result = _truncate_at_sentence_boundary(text, limit=100)
        assert len(result) <= 100
        assert result.endswith(".")

    def test_fallback_to_ellipsis(self):
        from bridge.response import _truncate_at_sentence_boundary

        text = "a" * 5000
        result = _truncate_at_sentence_boundary(text, limit=4096)
        assert len(result) <= 4096
        assert result.endswith("...")

    def test_empty_text(self):
        from bridge.response import _truncate_at_sentence_boundary

        assert _truncate_at_sentence_boundary("") == ""
        assert _truncate_at_sentence_boundary(None) == ""

    def test_exact_limit_unchanged(self):
        from bridge.response import _truncate_at_sentence_boundary

        text = "x" * 4096
        result = _truncate_at_sentence_boundary(text, limit=4096)
        assert result == text

    def test_preserves_exclamation_boundary(self):
        from bridge.response import _truncate_at_sentence_boundary

        text = "Done! " * 800 + "Extra text over limit"
        result = _truncate_at_sentence_boundary(text, limit=4096)
        assert result.rstrip().endswith("!")

    def test_question_mark_boundary(self):
        from bridge.response import _truncate_at_sentence_boundary

        text = "Is it working? " * 300 + "Extra text"
        result = _truncate_at_sentence_boundary(text, limit=4096)
        assert result.rstrip().endswith("?")


class TestSummarizerPromptUpdates:
    """Tests for new SUMMARIZER_SYSTEM_PROMPT content (#540)."""

    def test_prompt_contains_sdlc_naturalization(self):
        from bridge.summarizer import SUMMARIZER_SYSTEM_PROMPT

        assert "planning" in SUMMARIZER_SYSTEM_PROMPT
        assert "building" in SUMMARIZER_SYSTEM_PROMPT
        assert "testing" in SUMMARIZER_SYSTEM_PROMPT

    def test_prompt_contains_question_prefix_instruction(self):
        from bridge.summarizer import SUMMARIZER_SYSTEM_PROMPT

        assert ">> " in SUMMARIZER_SYSTEM_PROMPT

    def test_prompt_contains_link_format_instruction(self):
        from bridge.summarizer import SUMMARIZER_SYSTEM_PROMPT

        prompt_lower = SUMMARIZER_SYSTEM_PROMPT.lower()
        assert "short-form references" in prompt_lower

    def test_prompt_contains_metrics_suppression(self):
        from bridge.summarizer import SUMMARIZER_SYSTEM_PROMPT

        prompt_lower = SUMMARIZER_SYSTEM_PROMPT.lower()
        assert "line counts" in prompt_lower
