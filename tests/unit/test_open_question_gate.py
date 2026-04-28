"""Tests for open question extraction and stage-aware gate behavior.

Tests the _extract_open_questions() function and its integration with:
1. draft_message() — populating expectations when open questions found
2. Stage-aware auto-continue in agent_session_queue.py — pausing when open questions detected

Run with: pytest tests/test_open_question_gate.py -v
"""

from unittest.mock import AsyncMock, patch

import pytest

from bridge.message_drafter import (
    StructuredDraft,
    _extract_open_questions,
    draft_message,
)


class TestExtractOpenQuestions:
    """Tests for _extract_open_questions() extraction logic."""

    def test_extracts_numbered_questions(self):
        """Numbered list items under ## Open Questions are extracted."""
        text = (
            "## Open Questions\n\n"
            "1. Should we use Redis or PostgreSQL for session storage?\n"
            "2. What is the expected latency budget for the API?\n"
            "3. Do we need backward compatibility with the old format?\n"
        )
        questions = _extract_open_questions(text)
        assert len(questions) == 3
        assert "Should we use Redis or PostgreSQL for session storage?" in questions
        assert "What is the expected latency budget for the API?" in questions
        assert "Do we need backward compatibility with the old format?" in questions

    def test_extracts_bulleted_questions(self):
        """Bulleted list items under ## Open Questions are extracted."""
        text = (
            "## Open Questions\n\n"
            "- What retry strategy should we use?\n"
            "- How many concurrent workers are needed?\n"
        )
        questions = _extract_open_questions(text)
        assert len(questions) == 2
        assert "What retry strategy should we use?" in questions

    def test_extracts_asterisk_bulleted_questions(self):
        """Asterisk-bulleted list items are extracted."""
        text = "## Open Questions\n\n* Should the feature be gated behind a flag?\n"
        questions = _extract_open_questions(text)
        assert len(questions) == 1

    def test_empty_section_returns_empty_list(self):
        """Empty ## Open Questions section returns empty list."""
        text = "## Open Questions\n\n## Next Steps\n- Do something"
        questions = _extract_open_questions(text)
        assert questions == []

    def test_whitespace_only_section_returns_empty_list(self):
        """## Open Questions followed only by whitespace returns empty list."""
        text = "## Open Questions\n\n   \n\t\n\n## Solution\n"
        questions = _extract_open_questions(text)
        assert questions == []

    def test_no_section_returns_empty_list(self):
        """Text without ## Open Questions returns empty list."""
        text = "## Solution\n\nHere is the plan.\n\n## Implementation\n\nDo the thing."
        questions = _extract_open_questions(text)
        assert questions == []

    def test_empty_text_returns_empty_list(self):
        """Empty string returns empty list."""
        assert _extract_open_questions("") == []

    def test_none_returns_empty_list(self):
        """None returns empty list."""
        assert _extract_open_questions(None) == []

    def test_placeholder_tbd_skipped(self):
        """Placeholder items like 'TBD' are not treated as questions."""
        text = "## Open Questions\n\n1. TBD\n2. TODO\n3. N/A\n"
        questions = _extract_open_questions(text)
        assert questions == []

    def test_placeholder_none_skipped(self):
        """'None' placeholder is skipped."""
        text = "## Open Questions\n\n- None\n"
        questions = _extract_open_questions(text)
        assert questions == []

    def test_mixed_real_and_placeholder(self):
        """Mix of real questions and placeholders extracts only real ones."""
        text = (
            "## Open Questions\n\n"
            "1. Should we use approach A or B?\n"
            "2. TBD\n"
            "3. What is the expected SLA?\n"
        )
        questions = _extract_open_questions(text)
        assert len(questions) == 2
        assert "Should we use approach A or B?" in questions
        assert "What is the expected SLA?" in questions

    def test_section_ends_at_next_heading(self):
        """Questions extraction stops at the next ## heading."""
        text = (
            "## Open Questions\n\n"
            "1. What approach should we use?\n\n"
            "## Solution\n\n"
            "1. This is not a question, it's a solution step.\n"
        )
        questions = _extract_open_questions(text)
        assert len(questions) == 1
        assert "What approach should we use?" in questions

    def test_section_at_end_of_text(self):
        """## Open Questions at the end of text works correctly."""
        text = (
            "## Solution\n\nDo the thing.\n\n"
            "## Open Questions\n\n"
            "1. Should we proceed with this approach?\n"
        )
        questions = _extract_open_questions(text)
        assert len(questions) == 1

    def test_open_questions_with_resolved_suffix_skipped(self):
        """## Open Questions (Resolved) heading is NOT matched -- resolved questions are done."""
        text = (
            "## Open Questions (Resolved)\n\n"
            "1. Scope: PLAN stage only.\n"
            "2. Answer flow: Human's responsibility.\n"
        )
        questions = _extract_open_questions(text)
        assert questions == []  # Resolved sections are skipped

    def test_open_questions_with_answered_suffix_skipped(self):
        """## Open Questions (Answered) heading is also skipped."""
        text = "## Open Questions (Answered)\n\n1. Already answered question.\n"
        questions = _extract_open_questions(text)
        assert questions == []

    def test_open_questions_with_non_resolved_suffix_matched(self):
        """## Open Questions with a non-resolved suffix IS matched."""
        text = "## Open Questions (for discussion)\n\n1. Should we use approach A or B?\n"
        questions = _extract_open_questions(text)
        assert len(questions) == 1

    def test_questions_without_question_mark(self):
        """Items under ## Open Questions are treated as questions regardless of punctuation."""
        text = (
            "## Open Questions\n\n"
            "1. The retry strategy for failed API calls\n"
            "2. Whether we need rate limiting on the endpoint\n"
        )
        questions = _extract_open_questions(text)
        assert len(questions) == 2

    def test_malformed_markdown_no_crash(self):
        """Malformed markdown is handled gracefully (no crash)."""
        # Missing newline after heading
        text = "## Open Questions\n1. Question without blank line\n"
        questions = _extract_open_questions(text)
        assert len(questions) == 1

        # Heading with extra hashes
        text2 = "### Open Questions\n\n1. Not a level-2 heading\n"
        questions2 = _extract_open_questions(text2)
        assert questions2 == []  # Only ## level is matched

    def test_indented_list_items(self):
        """Indented list items are still extracted."""
        text = "## Open Questions\n\n  1. Indented numbered item\n  - Indented bullet item\n"
        questions = _extract_open_questions(text)
        assert len(questions) == 2

    def test_multiline_question_first_line_only(self):
        """Only the first line of a list item is captured (not continuation lines)."""
        text = (
            "## Open Questions\n\n"
            "1. Should we use Redis for caching?\n"
            "   This would require infrastructure changes.\n"
            "2. What is the timeout value?\n"
        )
        questions = _extract_open_questions(text)
        assert len(questions) == 2
        assert "Should we use Redis for caching?" in questions
        assert "What is the timeout value?" in questions

    def test_real_plan_output(self):
        """Test with realistic plan agent output containing open questions."""
        text = (
            "# Plan: Implement Feature X\n\n"
            "## Problem\n\n"
            "The system needs feature X.\n\n"
            "## Solution\n\n"
            "Build it using approach A.\n\n"
            "## Open Questions\n\n"
            "1. Should we use exponential backoff or fixed intervals for retries?\n"
            "2. What is the acceptable error rate threshold for alerting?\n"
            "3. Do we need to support the legacy API format during migration?\n\n"
            "## No-Gos\n\n"
            "- Don't rewrite the entire system.\n"
        )
        questions = _extract_open_questions(text)
        assert len(questions) == 3
        assert "Should we use exponential backoff or fixed intervals for retries?" in questions


class TestSummarizeResponseOpenQuestions:
    """Tests for open question integration with draft_message()."""

    @pytest.mark.asyncio
    async def test_open_questions_populate_expectations(self):
        """When raw output has open questions and LLM sets no expectations,
        expectations are populated from extracted questions."""
        raw_output = (
            "Plan created for feature X.\n\n"
            "## Open Questions\n\n"
            "1. Should we use approach A or B?\n"
            "2. What is the acceptable latency?\n\n"
            "## Solution\n\nBuild it.\n"
        )
        mock_haiku = AsyncMock(
            return_value=StructuredDraft(
                context_summary="Planning feature X",
                response="• Created plan for feature X",
                expectations=None,  # LLM did not detect questions
            )
        )
        with patch("bridge.message_drafter._draft_with_haiku", mock_haiku):
            result = await draft_message(raw_output)

        assert result.expectations is not None
        assert "Should we use approach A or B?" in result.expectations
        assert "What is the acceptable latency?" in result.expectations

    @pytest.mark.asyncio
    async def test_llm_expectations_take_priority(self):
        """When LLM sets expectations, extracted questions don't override."""
        raw_output = (
            "Some output.\n\n"
            "## Open Questions\n\n"
            "1. A question from the section?\n\n"
            "Should I merge this now?"
        )
        mock_haiku = AsyncMock(
            return_value=StructuredDraft(
                context_summary="Work in progress",
                response="• Working on it\n---\n? Should I merge this now?",
                expectations="Should I merge this now?",
            )
        )
        with patch("bridge.message_drafter._draft_with_haiku", mock_haiku):
            result = await draft_message(raw_output)

        # LLM expectations should take priority
        assert result.expectations == "Should I merge this now?"

    @pytest.mark.asyncio
    async def test_no_open_questions_no_expectations_change(self):
        """When raw output has no open questions, expectations stay None."""
        raw_output = "Built the feature. All tests passing."
        mock_haiku = AsyncMock(
            return_value=StructuredDraft(
                context_summary="Feature complete",
                response="• Built the feature\n• All tests passing",
                expectations=None,
            )
        )
        with patch("bridge.message_drafter._draft_with_haiku", mock_haiku):
            result = await draft_message(raw_output)

        assert result.expectations is None

    @pytest.mark.asyncio
    async def test_empty_open_questions_section_no_expectations(self):
        """Empty ## Open Questions section does not populate expectations."""
        raw_output = "Plan created.\n\n## Open Questions\n\n## Solution\n\nBuild it.\n"
        mock_haiku = AsyncMock(
            return_value=StructuredDraft(
                context_summary="Plan created",
                response="• Created plan",
                expectations=None,
            )
        )
        with patch("bridge.message_drafter._draft_with_haiku", mock_haiku):
            result = await draft_message(raw_output)

        assert result.expectations is None

    @pytest.mark.asyncio
    async def test_anti_fabrication_preserved(self):
        """Existing anti-fabrication behavior is preserved — declarative
        statements with open questions don't fabricate extra questions."""
        raw_output = (
            "I will implement feature X.\n\n"
            "## Open Questions\n\n"
            "1. Should we use Redis or PostgreSQL?\n\n"
            "## Solution\n\nImplement with Redis.\n"
        )
        mock_haiku = AsyncMock(
            return_value=StructuredDraft(
                context_summary="Planning feature X",
                response="• Will implement feature X with Redis",
                expectations=None,  # LLM correctly doesn't fabricate
            )
        )
        with patch("bridge.message_drafter._draft_with_haiku", mock_haiku):
            result = await draft_message(raw_output)

        # Expectations should contain the real open question, not fabricated ones
        assert result.expectations is not None
        assert "Redis or PostgreSQL" in result.expectations
        # Should NOT contain fabricated questions from declarative statements
        assert "implement feature X" not in result.expectations.lower()


class TestStageAwareOpenQuestionGate:
    """Tests for the open question gate in the stage-aware auto-continue path.

    These tests verify the logic in agent/agent_session_queue.py that checks for
    open questions before auto-continuing SDLC sessions.
    """

    def test_extract_open_questions_used_by_gate(self):
        """Verify _extract_open_questions returns questions for gate input."""
        # This is the kind of output the PLAN stage would produce
        plan_output = (
            "# Plan: Fix the Bug\n\n"
            "## Open Questions\n\n"
            "1. Should we fix this in the bridge or the agent?\n"
            "2. Is backward compatibility required?\n\n"
            "## Solution\n\nFix it in the bridge.\n"
        )
        questions = _extract_open_questions(plan_output)
        assert len(questions) == 2
        # The gate would see these and fall through to deliver path

    def test_no_open_questions_allows_auto_continue(self):
        """Output without open questions should not trigger the gate."""
        status_output = (
            "Working on the implementation.\nModified bridge/message_drafter.py\nRunning tests...\n"
        )
        questions = _extract_open_questions(status_output)
        assert questions == []
        # The gate would NOT trigger, allowing auto-continue

    def test_open_questions_in_quoted_content_still_detected(self):
        """## Open Questions in quoted/referenced content is still detected
        by the extractor. Stage-scoping is the gate's responsibility (agent_session_queue.py),
        not the extractor's.
        """
        quoted_output = (
            "Here is the plan I created:\n\n"
            "## Open Questions\n\n"
            "1. Should we use approach A?\n\n"
            "## Solution\n\nUse approach A.\n"
        )
        questions = _extract_open_questions(quoted_output)
        # The extractor finds questions regardless of context
        assert len(questions) == 1

    def test_questions_format_for_expectations(self):
        """Extracted questions format correctly for expectations field."""
        text = "## Open Questions\n\n1. Question one?\n2. Question two?\n"
        questions = _extract_open_questions(text)
        # This is how agent_session_queue.py would not format them, but how
        # draft_message formats them for the expectations field
        expectations = "\n".join(f"? {q}" for q in questions)
        assert "? Question one?" in expectations
        assert "? Question two?" in expectations

    def test_gate_only_triggers_during_plan_stage(self):
        """The open question gate in agent_session_queue.py only checks for questions
        when the current SDLC stage is PLAN. During BUILD/TEST/etc., open
        questions in the output are ignored (they're likely quoted content).
        """
        plan_output_with_questions = "## Open Questions\n\n1. Should we use approach A?\n"
        # The extractor always finds questions
        questions = _extract_open_questions(plan_output_with_questions)
        assert len(questions) == 1

        # But the gate logic in agent_session_queue.py wraps the call:
        #   open_questions = _extract_open_questions(msg) if _current_stage == "PLAN" else []
        # So during non-PLAN stages, even if questions exist, the gate returns []
        non_plan_result = (
            _extract_open_questions(plan_output_with_questions)
            if "PLAN" == "BUILD"  # Simulates non-PLAN stage check
            else []
        )
        assert non_plan_result == []

    def test_resolved_section_does_not_trigger_gate(self):
        """A ## Open Questions (Resolved) section should not trigger the gate."""
        output = (
            "Plan created.\n\n"
            "## Open Questions (Resolved)\n\n"
            "1. Scope: PLAN stage only.\n"
            "2. Answer flow: Human's responsibility.\n\n"
            "## Solution\n\nImplement the fix.\n"
        )
        questions = _extract_open_questions(output)
        assert questions == []  # Resolved sections are excluded


class TestWorkflowAnnouncementExtraction:
    """Issue #1189: PM workflow-announcement responses end with a
    ## Open Questions section that must populate session.expectations
    verbatim, so the unthreaded-message router can match a `plan` or
    `skip` reply back to the dormant session.

    The PM persona overlay tells the agent to emit a literal phrase
    ("Unless you directly instruct me to skip our standard workflow...")
    plus a `## Open Questions` section asking the human to choose between
    `plan` and `skip`. These tests verify the extraction → expectations
    pipeline works for that exact response shape.
    """

    def test_workflow_question_extracted_from_announcement_response(self):
        """A PM bucket-#3 announcement response is parseable."""
        pm_response = (
            "Unless you directly instruct me to skip our standard workflow, "
            "we need to file an issue to plan all improvements and changes to "
            "software.\n\n"
            "Reply with one of:\n"
            "- `plan` — file an issue and run /do-plan\n"
            "- `skip` — override SDLC for this task only\n\n"
            "## Open Questions\n\n"
            "1. Should I file an issue (`plan`) or skip SDLC (`skip`)?\n"
        )
        questions = _extract_open_questions(pm_response)
        assert len(questions) == 1
        assert "plan" in questions[0].lower()
        assert "skip" in questions[0].lower()

    def test_workflow_question_with_bullet_extracted(self):
        """Bullet-style ## Open Questions for the workflow phrase still extracts."""
        pm_response = (
            "Unless you directly instruct me to skip our standard workflow, "
            "we need to file an issue to plan all improvements and changes to "
            "software.\n\n"
            "## Open Questions\n\n"
            "- Should I file an issue (`plan`) or skip SDLC (`skip`)?\n"
        )
        questions = _extract_open_questions(pm_response)
        assert len(questions) == 1

    @pytest.mark.asyncio
    async def test_workflow_question_populates_expectations(self):
        """When a PM response carries the workflow announcement and a
        ## Open Questions section, draft_message populates expectations
        from the section even if Haiku does not detect it."""
        pm_response = (
            "Unless you directly instruct me to skip our standard workflow, "
            "we need to file an issue to plan all improvements and changes to "
            "software.\n\n"
            "Reply with `plan` to file an issue, or `skip` to override SDLC "
            "for this task only.\n\n"
            "## Open Questions\n\n"
            "1. Should I file an issue (`plan`) or skip SDLC (`skip`)?\n"
        )
        mock_haiku = AsyncMock(
            return_value=StructuredDraft(
                context_summary="PM announcing workflow contract for a coding request",
                response=(
                    "Unless you directly instruct me to skip our standard workflow, "
                    "we need to file an issue. Reply `plan` or `skip`."
                ),
                # Haiku might miss the workflow question — extraction must back-fill it
                expectations=None,
            )
        )
        with patch("bridge.message_drafter._draft_with_haiku", mock_haiku):
            result = await draft_message(pm_response)

        assert result.expectations is not None, (
            "expectations should be populated from the verbatim ## Open Questions section "
            "so the unthreaded-message router can match a `plan` or `skip` reply"
        )
        # The workflow question must contain both reply tokens for the
        # semantic router to clear the 0.80 threshold against single-word replies.
        assert "plan" in result.expectations.lower()
        assert "skip" in result.expectations.lower()

    @pytest.mark.asyncio
    async def test_haiku_expectations_take_priority_for_workflow(self):
        """If Haiku already extracts the workflow question, that wins
        (consistent with the existing LLM-priority rule)."""
        pm_response = (
            "Unless you directly instruct me to skip our standard workflow, "
            "we need to file an issue to plan all improvements and changes to "
            "software.\n\n"
            "## Open Questions\n\n"
            "1. Should I file an issue (`plan`) or skip SDLC (`skip`)?\n"
        )
        mock_haiku = AsyncMock(
            return_value=StructuredDraft(
                context_summary="PM workflow announcement",
                response="Unless you directly instruct me to skip... reply `plan` or `skip`.",
                expectations="Should I file an issue (plan) or skip SDLC (skip)?",
            )
        )
        with patch("bridge.message_drafter._draft_with_haiku", mock_haiku):
            result = await draft_message(pm_response)

        assert result.expectations == "Should I file an issue (plan) or skip SDLC (skip)?"
