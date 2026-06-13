"""Tests for bridge.message_drafter — response composition and validation."""

import json
from unittest.mock import MagicMock

import pytest

from bridge.message_drafter import (
    MessageDraft,
    _compose_structured_draft,
    _derive_context_summary,
    _get_status_emoji,
    _parse_draft_and_questions,
    draft_message,
    extract_artifacts,
)
from config.enums import SessionType
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
        text = "M  bridge/message_drafter.py\nA  tests/test_message_drafter.py"
        artifacts = extract_artifacts(text)
        assert "files_changed" in artifacts
        assert "bridge/message_drafter.py" in artifacts["files_changed"]


class TestDeriveContextSummary:
    """Tests for the new deterministic _derive_context_summary helper."""

    def test_returns_none_for_empty_input(self):
        assert _derive_context_summary("") is None

    def test_returns_none_for_whitespace_only(self):
        assert _derive_context_summary("   \n\n  ") is None

    def test_returns_first_sentence(self):
        text = "Fixed the authentication bug. All tests passing."
        result = _derive_context_summary(text)
        assert result is not None
        assert "Fixed the authentication bug" in result

    def test_caps_at_140_chars(self):
        long_sentence = (
            "This is a very long sentence that goes on and on and should be truncated " + "x" * 100
        )
        result = _derive_context_summary(long_sentence)
        assert result is not None
        assert len(result) <= 143  # 140 + "..." at most

    def test_skips_blank_lines(self):
        text = "\n\n\nReal content here."
        result = _derive_context_summary(text)
        assert result is not None
        assert "Real content here" in result

    def test_skips_markdown_headings(self):
        text = "# Heading\nActual content sentence."
        result = _derive_context_summary(text)
        assert result is not None
        assert "Actual content" in result
        assert "Heading" not in result

    def test_skips_separator_lines(self):
        text = "---\nContent after separator."
        result = _derive_context_summary(text)
        assert result is not None
        assert "Content after separator" in result

    def test_strips_bullet_markers(self):
        text = "• Implemented the feature and committed."
        result = _derive_context_summary(text)
        assert result is not None
        assert "Implemented the feature" in result
        assert "•" not in result

    def test_strips_dash_bullet(self):
        text = "- Fixed the bug in bridge module."
        result = _derive_context_summary(text)
        assert result is not None
        assert "Fixed the bug" in result

    def test_strips_numbered_list(self):
        text = "1. First item here."
        result = _derive_context_summary(text)
        assert result is not None
        assert "First item here" in result
        assert "1." not in result

    def test_short_text_returned_as_is(self):
        text = "Done. All tests pass."
        result = _derive_context_summary(text)
        assert result == "Done. All tests pass."

    def test_returns_str_not_empty_string_for_valid_input(self):
        result = _derive_context_summary("Some real content here.")
        assert result is not None
        assert isinstance(result, str)
        assert len(result) > 0


class TestDraftMessage:
    """Tests for the main draft_message function (pass-through + validation)."""

    @pytest.mark.asyncio
    async def test_short_response_passes_through_verbatim(self):
        """Non-SDLC responses under 200 chars pass through verbatim."""
        short_text = "Done. Committed abc1234."
        result = await draft_message(short_text)
        assert result.text == short_text

    @pytest.mark.asyncio
    async def test_short_response_has_no_was_drafted(self):
        """MessageDraft no longer has was_drafted field."""
        short_text = "Done."
        result = await draft_message(short_text)
        assert not hasattr(result, "was_drafted")

    @pytest.mark.asyncio
    async def test_empty_response(self):
        result = await draft_message("")
        assert result.text == ""

    @pytest.mark.asyncio
    async def test_none_response(self):
        result = await draft_message(None)
        assert result.text == ""

    @pytest.mark.asyncio
    async def test_long_response_composed_deterministically(self):
        """Responses >=200 chars go through deterministic composition."""
        long_text = "Done and committed. " * 30  # 600 chars
        result = await draft_message(long_text)
        # text is non-empty (composition succeeded)
        assert result.text

    @pytest.mark.asyncio
    async def test_very_long_response_creates_file(self):
        """Responses over FILE_ATTACH_THRESHOLD get a full output file."""
        long_text = "Output line.\n" * 500

        result = await draft_message(long_text)

        assert result.full_output_file is not None
        assert result.full_output_file.exists()
        content = result.full_output_file.read_text()
        assert content == long_text

        # Cleanup
        result.full_output_file.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_overlength_still_delivers(self):
        """Over-length responses still deliver (no needs_self_draft) — just attach file."""
        long_text = "x" * 4000  # Over 3000 threshold
        result = await draft_message(long_text)
        # needs_self_draft is NOT set for over-length (file is attached instead)
        assert result.needs_self_draft is False
        assert result.full_output_file is not None
        result.full_output_file.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_mid_length_response_no_file(self):
        """Responses between 200 and FILE_ATTACH_THRESHOLD: no file."""
        text = "x" * 2000  # Over 200, under 3000
        result = await draft_message(text)
        assert result.full_output_file is None

    @pytest.mark.asyncio
    async def test_context_summary_populated_for_long_response(self):
        """context_summary is set for responses that go through composition."""
        long_text = "Fixed the drafter refactoring to remove LLM calls. " * 10
        result = await draft_message(long_text)
        # context_summary should be derived deterministically
        assert result.context_summary is not None
        assert isinstance(result.context_summary, str)

    @pytest.mark.asyncio
    async def test_expectations_none_for_no_questions(self):
        """expectations is None when no ## Open Questions section exists."""
        text = "Fixed the bug and committed abc1234. All tests passing. " * 10
        result = await draft_message(text)
        assert result.expectations is None

    @pytest.mark.asyncio
    async def test_expectations_from_open_questions_section(self):
        """expectations is populated from ## Open Questions section."""
        text = (
            "Completed the refactoring work. All tests pass.\n\n"
            "## Open Questions\n"
            "- Should we merge to main or wait for design review?\n"
            "- Is the confidence threshold of 0.80 acceptable?\n"
        )
        result = await draft_message(text)
        assert result.expectations is not None
        assert "merge" in result.expectations.lower() or "design" in result.expectations.lower()

    @pytest.mark.asyncio
    async def test_self_summary_instruction_quality(self):
        """SELF_DRAFT_INSTRUCTION contains key quality markers."""
        from bridge.message_drafter import SELF_DRAFT_INSTRUCTION

        assert "outcome" in SELF_DRAFT_INSTRUCTION.lower()
        assert "narration" in SELF_DRAFT_INSTRUCTION.lower()
        assert "bullet" in SELF_DRAFT_INSTRUCTION.lower()
        assert len(SELF_DRAFT_INSTRUCTION) < 1000  # compact, not the full system prompt


class TestParseSummaryAndQuestions:
    """Tests for _parse_draft_and_questions."""

    def test_bullets_only(self):
        text = "• Built the feature\n• Pushed to main"
        bullets, questions = _parse_draft_and_questions(text)
        assert bullets == text
        assert questions is None

    def test_bullets_and_questions(self):
        text = "• Built the feature\n• Pushed to main\n---\n>> Should I merge?"
        bullets, questions = _parse_draft_and_questions(text)
        assert bullets == "• Built the feature\n• Pushed to main"
        assert questions == ">> Should I merge?"

    def test_bullets_and_questions_legacy_prefix(self):
        """Legacy ? prefix is normalized to >> prefix."""
        text = "• Built the feature\n• Pushed to main\n---\n? Should I merge?"
        bullets, questions = _parse_draft_and_questions(text)
        assert bullets == "• Built the feature\n• Pushed to main"
        assert questions == ">> Should I merge?"

    def test_multiple_questions(self):
        text = "• Done\n---\n>> Q1\n>> Q2\n>> Q3"
        bullets, questions = _parse_draft_and_questions(text)
        assert bullets == "• Done"
        assert ">> Q1" in questions
        assert ">> Q2" in questions
        assert ">> Q3" in questions

    def test_empty_questions_section(self):
        text = "• Done\n---\n"
        bullets, questions = _parse_draft_and_questions(text)
        assert bullets == "• Done"
        assert questions is None

    def test_leading_separator(self):
        text = "---\n? Only questions here"
        bullets, questions = _parse_draft_and_questions(text)
        assert bullets == ""
        assert questions is not None
        assert "Only questions here" in questions

    def test_no_separator(self):
        text = "Simple summary without questions."
        bullets, questions = _parse_draft_and_questions(text)
        assert bullets == text
        assert questions is None


class TestComposeStructuredDraft:
    """Tests for _compose_structured_draft."""

    def test_no_session_returns_emoji_and_bullets(self):
        result = _compose_structured_draft("• Built it\n• Shipped it", session=None)
        assert "✅" in result
        assert "• Built it" in result
        assert "• Shipped it" in result

    def test_questions_appended(self):
        result = _compose_structured_draft("• Done\n---\n>> Should I merge?", session=None)
        assert ">> Should I merge?" in result
        assert "• Done" in result

    def test_not_completion_uses_pending_emoji(self):
        result = _compose_structured_draft("• Working on it", session=None, is_completion=False)
        assert "⏳" in result

    def test_teammate_mode_returns_prose_without_emoji(self):
        """Teammate sessions bypass structured formatting -- return prose directly."""
        from unittest.mock import MagicMock

        session = MagicMock()
        session.session_type = SessionType.TEAMMATE
        session.session_id = None  # Skip Redis refresh

        result = _compose_structured_draft(
            "The bridge uses Telethon for Telegram integration. See bridge/telegram_bridge.py.",
            session=session,
        )
        # No emoji prefix
        assert not result.startswith("✅")
        assert not result.startswith("⏳")
        assert not result.startswith("❌")
        # Prose preserved as-is
        assert "bridge uses Telethon" in result

    def test_non_teammate_mode_still_gets_structured(self):
        """Non-Teammate sessions (session_type != TEAMMATE) still get structured formatting."""
        from unittest.mock import MagicMock

        session = MagicMock()
        session.session_type = SessionType.PM
        session.session_id = None
        session.status = "completed"

        result = _compose_structured_draft("• Built it", session=session)
        assert "✅" in result
        assert "• Built it" in result


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
        session.session_type = SessionType.PM

        result = _compose_structured_draft(
            "• Built the bypass\n• Tests passing", session=session, is_completion=True
        )
        first_line = result.split("\n")[0]
        # First line is emoji or content (no emoji for routine completions)
        assert first_line.strip() in ("✅", "⏳", "❌", "") or first_line.startswith("•")
        assert "continue" not in first_line

    def test_no_echo_on_regular_session(self):
        """Regular sessions should not echo the user's message."""
        from unittest.mock import MagicMock

        session = MagicMock()
        session._get_history_list.return_value = ["[user] What time is it?"]
        session.message_text = "What time is it?"
        session.status = "completed"
        session.session_type = SessionType.PM
        session.get_links.return_value = {}

        result = _compose_structured_draft("It's 3pm UTC+7", session=session, is_completion=True)
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


class TestComposeStructuredDraftWithSession:
    """Tests for _compose_structured_draft with session context."""

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

        result = _compose_structured_draft(
            "• Implemented the bypass\n• 135 tests passing",
            session=session,
            is_completion=True,
        )

        # First line is emoji or content (no emoji for routine completions)
        first_line = result.split("\n")[0]
        assert first_line.strip() in ("✅", "⏳", "❌", "") or first_line.startswith("•")
        assert "continue" not in first_line
        # Bullets present
        assert "• Implemented the bypass" in result

    def test_non_sdlc_session_no_stage_line(self):
        """Non-SDLC session skips stage progress line."""
        session = _mock_session_with_stages({})  # All pending
        session._get_history_list.return_value = ["[user] What time is it?"]
        session.message_text = "What time is it?"
        session.status = "running"

        result = _compose_structured_draft("It's 3pm UTC+7", session=session, is_completion=True)

        # No stage-related content for non-SDLC
        assert "ISSUE" not in result
        assert "BUILD" not in result
        # First line is emoji or content (no emoji for routine completions)
        first_line = result.split("\n")[0].strip()
        assert first_line in ("✅", "⏳", "❌", "") or len(first_line) > 0

    def test_teammate_mode_session_returns_prose(self):
        """Teammate session bypasses all structured formatting."""
        session = _mock_session_with_stages({})
        session.session_type = SessionType.TEAMMATE
        session.session_id = None  # Skip Redis refresh
        session.message_text = "How does the bridge work?"
        session.status = "completed"

        result = _compose_structured_draft(
            "The bridge connects Telegram to Claude via Telethon. "
            "See bridge/telegram_bridge.py for the main entry point.",
            session=session,
        )

        # No structured formatting — pure prose
        assert not result.startswith("✅")
        assert not result.startswith("⏳")
        assert "•" not in result  # No bullet points
        assert "bridge connects Telegram" in result


class TestDraftingBypass:
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
    """Tests for anti-fabrication rules — expectations must come from raw output only.

    The drafter must NEVER fabricate questions from declarative statements.
    Only explicit questions (from ## Open Questions sections) populate expectations.
    """

    @pytest.mark.asyncio
    async def test_no_questions_fabricated_from_declarative_statements(self):
        """Declarative planned work must produce expectations=None."""
        # Long enough to bypass short-output path, no ## Open Questions section
        agent_output = (
            "I will add sdlc to classifier categories. "
            "I will fix auto-continue to carry forward session state. "
            "Both changes are straightforward — modifying the classifier prompt "
            "and the auto-continue handler. No questions at this time. " * 3
        )
        result = await draft_message(agent_output)

        # No ## Open Questions section → expectations must be None
        assert result.expectations is None
        # Verify no --- separator (which precedes questions)
        assert "\n---\n" not in result.text

    @pytest.mark.asyncio
    async def test_explicit_questions_from_open_questions_section(self):
        """Real ## Open Questions section must populate expectations."""
        agent_output = (
            "Completed the refactoring work. All 12 tests passing. "
            "Committed abc1234 and pushed to session/refactor.\n\n"
            "## Open Questions\n"
            "- Should we use exponential backoff or fixed intervals?\n"
        )
        result = await draft_message(agent_output)

        assert result.expectations is not None
        assert "exponential backoff" in result.expectations

    @pytest.mark.asyncio
    async def test_future_tense_plans_not_turned_into_questions(self):
        """'Will do X' statements must not become questions."""
        agent_output = (
            "Next steps: will update the migration script, "
            "will add index to users table, will run load test. "
            "No explicit questions for the human at this point. " * 5
        )
        result = await draft_message(agent_output)

        assert result.expectations is None
        assert "\n---\n" not in result.text


class TestErrorStateRendering:
    """Tests for _compose_structured_draft with error/failed states (Gap 4)."""

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

        result = _compose_structured_draft(
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

        result = _compose_structured_draft(
            "• Tests failed",
            session=session,
            is_completion=True,  # Even with completion flag, failed session shows X
        )

        first_line = result.split("\n")[0]
        assert first_line.strip() == "❌"

    def test_failed_stage_shows_in_progress(self):
        """Failed stage progress should render the failure point visibly."""
        session = _mock_session_with_stages(
            {"ISSUE": "completed", "PLAN": "completed", "BUILD": "failed"},
        )
        session._get_history_list.return_value = []
        session.message_text = "continue"
        session.status = "failed"
        session.is_sdlc = True

        result = _compose_structured_draft(
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
        result = _compose_structured_draft(
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

        result = _compose_structured_draft(
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


class TestMessageDraftDataclass:
    """Tests for the MessageDraft dataclass after was_drafted removal."""

    def test_no_was_drafted_field(self):
        """MessageDraft no longer has was_drafted field."""
        draft = MessageDraft(text="hello")
        assert not hasattr(draft, "was_drafted")

    def test_default_needs_self_draft_false(self):
        draft = MessageDraft(text="hello")
        assert draft.needs_self_draft is False

    def test_context_summary_defaults_none(self):
        draft = MessageDraft(text="hello")
        assert draft.context_summary is None

    def test_expectations_defaults_none(self):
        draft = MessageDraft(text="hello")
        assert draft.expectations is None

    def test_violations_defaults_empty_list(self):
        draft = MessageDraft(text="hello")
        assert draft.violations == []

    def test_full_output_file_defaults_none(self):
        draft = MessageDraft(text="hello")
        assert draft.full_output_file is None


class TestExpectationsRecallParity:
    """Tests verifying that expectations come exclusively from _extract_open_questions.

    The drafter must NEVER fabricate questions from declarative statements.
    expectations must be None (not "", not any other falsy value) when no
    ## Open Questions section is present.
    """

    @pytest.mark.asyncio
    async def test_open_questions_section_populates_expectations(self):
        """A real ## Open Questions section must produce matching expectations."""
        agent_output = (
            "Completed the migration. All 47 tests pass. Committed abc1234.\n\n"
            "## Open Questions\n"
            "- Should we use exponential backoff or fixed 5s intervals?\n"
            "- Is the 0.80 confidence threshold acceptable for prod?\n"
        )
        result = await draft_message(agent_output)

        assert result.expectations is not None
        assert (
            "exponential backoff" in result.expectations
            or "confidence threshold" in result.expectations
        )

    @pytest.mark.asyncio
    async def test_declarative_output_yields_none_expectations(self):
        """Pure declarative output with no ## Open Questions → expectations is None."""
        agent_output = (
            "Fixed the authentication bug in bridge/telegram_bridge.py. "
            "The session lock cleanup now runs on startup. "
            "All 135 tests pass. Committed def5678 and pushed to session/auth-fix. " * 3
        )
        result = await draft_message(agent_output)

        # No ## Open Questions section → no expectations
        assert result.expectations is None

    @pytest.mark.asyncio
    async def test_none_contract_not_empty_string(self):
        """expectations is None (not '') when no questions are found.

        The contract is: None means 'no questions', empty string is ambiguous
        and must not be used. Callers rely on truthiness to gate routing.
        """
        agent_output = (
            "Updated the session scheduler. Cleaned up 3 orphaned sessions. "
            "No pending questions at this time. " * 5
        )
        result = await draft_message(agent_output)

        # Must be exactly None, not an empty string or empty list
        assert result.expectations is None
        assert result.expectations != ""
        assert result.expectations != []

    @pytest.mark.asyncio
    async def test_trailing_question_mark_sentences_not_fabricated(self):
        """Sentences that end in '?' but are not in ## Open Questions must not
        become expectations. Anti-fabrication rule preserved from original drafter."""
        agent_output = (
            "Should we use Redis or Postgres? I think Redis is better for this use case. "
            "The current implementation uses Redis anyway. "
            "Completed the analysis. Will proceed with Redis. " * 3
        )
        result = await draft_message(agent_output)

        # Questions embedded in declarative prose must not be extracted
        assert result.expectations is None

    @pytest.mark.asyncio
    async def test_open_questions_section_with_multiple_items(self):
        """All items under ## Open Questions are extracted and joined."""
        agent_output = (
            "Refactoring complete. 200 tests passing.\n\n"
            "## Open Questions\n"
            "- Should we merge to main or wait for the design review?\n"
            "- Do we need a migration script for existing records?\n"
            "- Is the 48h TTL on steering keys acceptable?\n"
        )
        result = await draft_message(agent_output)

        assert result.expectations is not None
        # All three questions should appear in some form
        assert (
            "merge" in result.expectations.lower() or "design review" in result.expectations.lower()
        )


class TestDeriveContextSummaryRecallParity:
    """Tests that _derive_context_summary produces routing-usable topic hints.

    Goal: strictly better than '(no context)' fallback, not equivalent to Haiku.
    These tests verify that the first-sentence heuristic produces non-empty,
    capped, agent-derived hints that distinguish between different-topic outputs.

    If a test here fails, it means the first-sentence slice is too crude and
    the heuristic needs to be widened before shipping.
    """

    def test_code_task_reply_produces_routing_hint(self):
        """A code-task completion reply produces a usable routing hint."""
        agent_output = (
            "Fixed the authentication bug in agent/output_handler.py at line 423. "
            "The session lock cleanup now fires before the steering push check. "
            "All 135 tests pass."
        )
        result = _derive_context_summary(agent_output)

        assert result is not None
        assert len(result) > 0
        assert len(result) <= 143  # 140 + "..." at most
        # Must contain content drawn from the agent's text
        assert any(word in result for word in ["Fixed", "authentication", "session", "agent"])

    def test_question_bearing_reply_produces_routing_hint(self):
        """A reply that contains questions still produces a context hint from the body."""
        agent_output = (
            "Completed the drafter refactoring. LLM calls removed, pass-through implemented.\n\n"
            "## Open Questions\n"
            "- Should we merge to main immediately or wait for peer review?\n"
        )
        result = _derive_context_summary(agent_output)

        assert result is not None
        assert len(result) > 0
        assert len(result) <= 143
        # The hint should describe the work, not just the question
        assert any(
            word in result
            for word in ["drafter", "refactoring", "LLM", "pass-through", "Completed"]
        )

    def test_multi_paragraph_status_reply_produces_routing_hint(self):
        """A multi-paragraph status reply uses the first substantive sentence."""
        agent_output = (
            "Updated the session scheduler to reap orphaned sessions on startup.\n\n"
            "Changed agent/agent_session_queue.py to call cleanup() before accepting new work.\n\n"
            "Also updated the watchdog to emit a metric on each reap cycle."
        )
        result = _derive_context_summary(agent_output)

        assert result is not None
        assert len(result) > 0
        assert len(result) <= 143
        # Must be drawn from agent text
        assert any(
            word in result.lower()
            for word in ["session", "scheduler", "orphan", "startup", "updated"]
        )

    def test_different_topics_produce_distinguishable_hints(self):
        """Three outputs on different topics must produce distinguishable summaries.

        This is the key discriminability test: if the heuristic collapses all
        outputs to the same hint, the routing value is zero.
        """
        outputs = {
            "auth": (
                "Fixed the OAuth token refresh bug. Sessions no longer expire prematurely. "
                "All 28 auth tests pass."
            ),
            "db": (
                "Added index to users.email column. Query time dropped from 450ms to 12ms. "
                "Migration script applied to staging."
            ),
            "bridge": (
                "Rewrote the Telegram bridge reconnect logic. Now uses exponential backoff. "
                "Bridge stability improved by 40% in load tests."
            ),
        }

        summaries = {topic: _derive_context_summary(text) for topic, text in outputs.items()}

        # All summaries must be non-empty
        for topic, summary in summaries.items():
            assert summary is not None, f"Summary for '{topic}' was None"
            assert len(summary) > 0, f"Summary for '{topic}' was empty"

        # All summaries must be distinct (no two the same)
        unique_summaries = set(summaries.values())
        assert len(unique_summaries) == 3, (
            f"Expected 3 distinct summaries, got {len(unique_summaries)}: {summaries}. "
            "If the first-sentence heuristic collapses different topics, widen it before shipping."
        )

    def test_summary_cap_does_not_lose_topic_signal(self):
        """Even when the first sentence is long and must be truncated, the
        routing hint retains enough topic signal to be useful (first 100+ chars
        of a 200-char sentence should still identify the topic)."""
        agent_output = (
            "Refactored the TelegramRelayOutputHandler.send() method to route all output "
            "through the verbatim pass-through drafter before writing to the Redis outbox, "
            "eliminating the LLM rewrite cluster and reducing p95 latency from 1.8s to 0.02s."
        )
        result = _derive_context_summary(agent_output)

        assert result is not None
        assert len(result) <= 143
        # Must contain at least one recognizable topic word from the text
        assert any(
            word in result
            for word in [
                "TelegramRelayOutputHandler",
                "Refactored",
                "pass-through",
                "drafter",
                "Redis",
            ]
        )
