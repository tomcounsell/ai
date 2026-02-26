"""Tests for AgentSession lifecycle, summarizer composition, and markdown send.

Covers the gaps identified in PR #180 review:
1. AgentSession history tracking (append_history, cap at 20, get_stage_progress)
2. AgentSession link tracking (set_link, get_links)
3. Summarizer composition (_compose_structured_summary, _render_stage_progress, _render_link_footer)
4. Summarizer with session context (summarize_response with session param)
5. Markdown send (send_markdown fallback behavior)
6. Backward compat (SessionLog shim, RedisJob alias, sender property)
7. Full lifecycle simulations (SDLC, Q&A, chit-chat)
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models.agent_session import HISTORY_MAX_ENTRIES, SDLC_STAGES, AgentSession

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def session(redis_test_db):
    """Create a basic AgentSession for testing."""
    return AgentSession.create(
        session_id="lifecycle-test-1",
        project_key="test",
        status="active",
        chat_id="100",
        sender_name="Tom",
        created_at=time.time(),
        started_at=time.time(),
        last_activity=time.time(),
        message_text="SDLC 177",
        turn_count=0,
        tool_call_count=0,
    )


@pytest.fixture
def sdlc_session(redis_test_db):
    """Create an AgentSession with SDLC stage history and links."""
    s = AgentSession.create(
        session_id="sdlc-lifecycle-1",
        project_key="test",
        status="completed",
        chat_id="200",
        sender_name="Tom",
        created_at=time.time(),
        started_at=time.time(),
        last_activity=time.time(),
        message_text="SDLC 177",
        classification_type="feature",
        branch_name="session/summarizer-bullet-format",
        turn_count=15,
        tool_call_count=42,
    )
    s.append_history("user", "SDLC 177")
    s.append_history("stage", "ISSUE completed ☑")
    s.append_history("stage", "PLAN completed ☑")
    s.append_history("stage", "BUILD completed ☑")
    s.append_history("stage", "TEST completed ☑")
    s.append_history("stage", "REVIEW completed ☑")
    s.append_history("stage", "DOCS completed ☑")
    s.set_link("issue", "https://github.com/tomcounsell/ai/issues/177")
    s.set_link(
        "plan", "https://github.com/tomcounsell/ai/blob/main/docs/plans/summarizer.md"
    )
    s.set_link("pr", "https://github.com/tomcounsell/ai/pull/180")
    return s


@pytest.fixture
def qa_session(redis_test_db):
    """Create an AgentSession for a Q&A interaction."""
    return AgentSession.create(
        session_id="qa-test-1",
        project_key="test",
        status="completed",
        chat_id="300",
        sender_name="Kevin",
        created_at=time.time(),
        started_at=time.time(),
        last_activity=time.time(),
        message_text="How does the job queue work?",
        turn_count=3,
        tool_call_count=5,
    )


@pytest.fixture
def chat_session(redis_test_db):
    """Create an AgentSession for casual chit-chat."""
    return AgentSession.create(
        session_id="chat-test-1",
        project_key="test",
        status="completed",
        chat_id="400",
        sender_name="Tom",
        created_at=time.time(),
        started_at=time.time(),
        last_activity=time.time(),
        message_text="Hey, how's it going?",
        turn_count=2,
        tool_call_count=0,
    )


# ── History Tracking ──────────────────────────────────────────────────────────


class TestHistoryTracking:
    """Tests for append_history, _get_history_list, and history cap."""

    def test_append_history_single(self, session):
        session.append_history("user", "SDLC 177")
        history = session._get_history_list()
        assert len(history) == 1
        assert history[0] == "[user] SDLC 177"

    def test_append_history_multiple(self, session):
        session.append_history("user", "SDLC 177")
        session.append_history("classify", "feature")
        session.append_history("stage", "ISSUE completed ☑")
        history = session._get_history_list()
        assert len(history) == 3
        assert "[classify] feature" in history

    def test_history_capped_at_max(self, session):
        for i in range(HISTORY_MAX_ENTRIES + 10):
            session.append_history("test", f"entry {i}")
        history = session._get_history_list()
        assert len(history) == HISTORY_MAX_ENTRIES
        # Should keep the most recent entries
        assert f"entry {HISTORY_MAX_ENTRIES + 9}" in history[-1]
        # Oldest entries should be gone
        assert not any("entry 0" in h for h in history)

    def test_get_history_list_empty(self, session):
        assert session._get_history_list() == []

    def test_get_history_list_none_safe(self, redis_test_db):
        s = AgentSession.create(
            session_id="no-history",
            project_key="test",
            status="active",
            created_at=time.time(),
        )
        # history field is None by default
        assert s._get_history_list() == []


# ── Stage Progress ────────────────────────────────────────────────────────────


class TestStageProgress:
    """Tests for get_stage_progress parsing history into SDLC stages."""

    def test_no_history_all_pending(self, session):
        progress = session.get_stage_progress()
        assert all(v == "pending" for v in progress.values())
        assert set(progress.keys()) == set(SDLC_STAGES)

    def test_completed_stages(self, sdlc_session):
        progress = sdlc_session.get_stage_progress()
        for stage in SDLC_STAGES:
            assert progress[stage] == "completed", f"{stage} should be completed"

    def test_partial_progress(self, session):
        session.append_history("stage", "ISSUE completed ☑")
        session.append_history("stage", "PLAN completed ☑")
        session.append_history("stage", "BUILD in_progress ▶")
        progress = session.get_stage_progress()
        assert progress["ISSUE"] == "completed"
        assert progress["PLAN"] == "completed"
        assert progress["BUILD"] == "in_progress"
        assert progress["TEST"] == "pending"
        assert progress["REVIEW"] == "pending"
        assert progress["DOCS"] == "pending"

    def test_non_stage_entries_ignored(self, session):
        session.append_history("user", "SDLC 177")
        session.append_history("classify", "feature")
        session.append_history("summary", "Did some BUILD work")
        progress = session.get_stage_progress()
        # "summary" entries don't have [stage] so BUILD stays pending
        assert progress["BUILD"] == "pending"

    def test_stage_overwrite_latest_wins(self, session):
        session.append_history("stage", "BUILD in_progress ▶")
        session.append_history("stage", "BUILD completed ☑")
        progress = session.get_stage_progress()
        assert progress["BUILD"] == "completed"


# ── Link Tracking ─────────────────────────────────────────────────────────────


class TestLinkTracking:
    """Tests for set_link, get_links."""

    def test_set_and_get_issue_link(self, session):
        session.set_link("issue", "https://github.com/org/repo/issues/42")
        links = session.get_links()
        assert links["issue"] == "https://github.com/org/repo/issues/42"

    def test_set_and_get_all_links(self, session):
        session.set_link("issue", "https://github.com/org/repo/issues/1")
        session.set_link("plan", "https://github.com/org/repo/blob/main/plan.md")
        session.set_link("pr", "https://github.com/org/repo/pull/5")
        links = session.get_links()
        assert len(links) == 3
        assert "issue" in links
        assert "plan" in links
        assert "pr" in links

    def test_get_links_empty(self, session):
        assert session.get_links() == {}

    def test_set_link_unknown_kind_ignored(self, session):
        session.set_link("unknown", "https://example.com")
        assert session.get_links() == {}

    def test_link_overwrite(self, session):
        session.set_link("pr", "https://github.com/org/repo/pull/1")
        session.set_link("pr", "https://github.com/org/repo/pull/2")
        links = session.get_links()
        assert links["pr"] == "https://github.com/org/repo/pull/2"


# ── Summarizer Composition ────────────────────────────────────────────────────


class TestRenderStageProgress:
    """Tests for _render_stage_progress."""

    def test_no_session(self):
        from bridge.summarizer import _render_stage_progress

        assert _render_stage_progress(None) is None

    def test_no_progress(self, session):
        from bridge.summarizer import _render_stage_progress

        assert _render_stage_progress(session) is None

    def test_full_completion(self, sdlc_session):
        from bridge.summarizer import _render_stage_progress

        line = _render_stage_progress(sdlc_session)
        assert line is not None
        assert "☑ ISSUE" in line
        assert "☑ DOCS" in line
        assert "→" in line

    def test_partial_progress(self, session):
        from bridge.summarizer import _render_stage_progress

        session.append_history("stage", "ISSUE completed ☑")
        session.append_history("stage", "PLAN completed ☑")
        session.append_history("stage", "BUILD in_progress ▶")
        line = _render_stage_progress(session)
        assert "☑ ISSUE" in line
        assert "☑ PLAN" in line
        assert "▶ BUILD" in line
        assert "☐ TEST" in line


class TestRenderLinkFooter:
    """Tests for _render_link_footer."""

    def test_no_session(self):
        from bridge.summarizer import _render_link_footer

        assert _render_link_footer(None) is None

    def test_no_links(self, session):
        from bridge.summarizer import _render_link_footer

        assert _render_link_footer(session) is None

    def test_issue_link_extracts_number(self, session):
        from bridge.summarizer import _render_link_footer

        session.set_link("issue", "https://github.com/org/repo/issues/177")
        footer = _render_link_footer(session)
        assert "Issue #177" in footer
        assert "[Issue #177]" in footer

    def test_pr_link_extracts_number(self, session):
        from bridge.summarizer import _render_link_footer

        session.set_link("pr", "https://github.com/org/repo/pull/180")
        footer = _render_link_footer(session)
        assert "PR #180" in footer

    def test_all_links_pipe_separated(self, sdlc_session):
        from bridge.summarizer import _render_link_footer

        footer = _render_link_footer(sdlc_session)
        assert " | " in footer
        assert "Issue #177" in footer
        assert "PR #180" in footer
        assert "Plan" in footer


class TestGetStatusEmoji:
    """Tests for _get_status_emoji."""

    def test_no_session_completion(self):
        from bridge.summarizer import _get_status_emoji

        assert _get_status_emoji(None, is_completion=True) == "✅"

    def test_no_session_non_completion(self):
        from bridge.summarizer import _get_status_emoji

        assert _get_status_emoji(None, is_completion=False) == "⏳"

    def test_completed_session(self, sdlc_session):
        from bridge.summarizer import _get_status_emoji

        assert _get_status_emoji(sdlc_session) == "✅"

    def test_active_session_completion(self, session):
        """Active session with is_completion=True (default) returns ✅."""
        from bridge.summarizer import _get_status_emoji

        assert _get_status_emoji(session) == "✅"

    def test_active_session_non_completion(self, session):
        """Active session with is_completion=False returns ⏳."""
        from bridge.summarizer import _get_status_emoji

        assert _get_status_emoji(session, is_completion=False) == "⏳"

    def test_failed_session(self, redis_test_db):
        from bridge.summarizer import _get_status_emoji

        s = AgentSession.create(
            session_id="failed-1",
            project_key="test",
            status="failed",
            created_at=time.time(),
        )
        assert _get_status_emoji(s) == "❌"


class TestComposeStructuredSummary:
    """Tests for _compose_structured_summary."""

    def test_no_session_plain_text(self):
        from bridge.summarizer import _compose_structured_summary

        result = _compose_structured_summary("Done.", session=None, is_completion=True)
        assert "✅" in result
        assert "Done." in result

    def test_sdlc_session_full_structure(self, sdlc_session):
        from bridge.summarizer import _compose_structured_summary

        result = _compose_structured_summary(
            "• Unified AgentSession model\n• Bullet-point summarizer",
            session=sdlc_session,
            is_completion=True,
        )
        lines = result.split("\n")
        # Line 1: emoji + label from message_text
        assert "✅" in lines[0]
        assert "177" in lines[0]
        # Line 2: stage progress
        assert "☑ ISSUE" in result
        assert "☑ DOCS" in result
        # Bullets present
        assert "• Unified AgentSession model" in result
        # Link footer
        assert "Issue #177" in result
        assert "PR #180" in result

    def test_qa_session_no_stages(self, qa_session):
        from bridge.summarizer import _compose_structured_summary

        result = _compose_structured_summary(
            "The job queue uses FILO ordering per project.",
            session=qa_session,
            is_completion=True,
        )
        # No stage progress (no history)
        assert "☑" not in result
        assert "☐" not in result
        # Has emoji and text
        assert "✅" in result
        assert "FILO" in result

    def test_chat_session_minimal(self, chat_session):
        from bridge.summarizer import _compose_structured_summary

        result = _compose_structured_summary(
            "Hey! All good here.",
            session=chat_session,
            is_completion=True,
        )
        assert "Hey! All good here." in result

    def test_strips_sdlc_prefix_from_label(self, sdlc_session):
        from bridge.summarizer import _compose_structured_summary

        # message_text is "SDLC 177" — the "SDLC " prefix should be stripped
        result = _compose_structured_summary("• Work done", session=sdlc_session)
        first_line = result.split("\n")[0]
        assert not first_line.startswith("✅ SDLC")
        assert "177" in first_line

    def test_emoji_not_doubled(self):
        from bridge.summarizer import _compose_structured_summary

        result = _compose_structured_summary("✅ Already has emoji")
        # Should NOT have two ✅
        assert result.count("✅") == 1


# ── Summarizer with Session Context ──────────────────────────────────────────


class TestSummarizeWithSession:
    """Tests for summarize_response passing session context."""

    @pytest.mark.asyncio
    async def test_short_response_still_summarized(self):
        """All non-empty responses are now summarized (no threshold)."""
        from bridge.summarizer import summarize_response

        mock_haiku = AsyncMock(return_value="Done ✅")
        with patch("bridge.summarizer._summarize_with_haiku", mock_haiku):
            result = await summarize_response("Done.", session=None)
        assert result.was_summarized is True
        mock_haiku.assert_called_once()

    @pytest.mark.asyncio
    async def test_long_response_with_sdlc_session(self, sdlc_session):
        from bridge.summarizer import summarize_response

        long_text = "Detailed implementation work. " * 200
        mock_haiku = AsyncMock(return_value="• Built the feature\n• Tests passing")

        with patch("bridge.summarizer._summarize_with_haiku", mock_haiku):
            result = await summarize_response(long_text, session=sdlc_session)

        assert result.was_summarized is True
        # Structured composition adds stage progress and links
        assert "☑ ISSUE" in result.text
        assert "Issue #177" in result.text
        assert "PR #180" in result.text
        # The haiku output is included
        assert "Built the feature" in result.text

    @pytest.mark.asyncio
    async def test_long_response_with_qa_session(self, qa_session):
        from bridge.summarizer import summarize_response

        long_text = "Here is a very long explanation. " * 200
        mock_haiku = AsyncMock(return_value="The job queue uses FILO ordering.")

        with patch("bridge.summarizer._summarize_with_haiku", mock_haiku):
            result = await summarize_response(long_text, session=qa_session)

        assert result.was_summarized is True
        # No stage progress for Q&A
        assert "☑" not in result.text
        assert "FILO" in result.text

    @pytest.mark.asyncio
    async def test_build_prompt_includes_session_context(self, sdlc_session):
        from bridge.summarizer import _build_summary_prompt

        prompt = _build_summary_prompt(
            "some output text",
            {"commits": ["abc1234"]},
            session=sdlc_session,
        )
        assert "SDLC 177" in prompt
        assert "feature" in prompt
        assert "session/summarizer-bullet-format" in prompt


# ── Markdown Send ─────────────────────────────────────────────────────────────


class TestSendMarkdown:
    """Tests for send_markdown with fallback behavior."""

    @pytest.mark.asyncio
    async def test_successful_markdown_send(self):
        from bridge.markdown import send_markdown

        mock_client = AsyncMock()
        mock_client.send_message = AsyncMock(return_value="sent")

        result = await send_markdown(mock_client, 123, "**bold** text")
        assert result == "sent"
        mock_client.send_message.assert_called_once_with(
            123, "**bold** text", reply_to=None, parse_mode="md"
        )

    @pytest.mark.asyncio
    async def test_fallback_on_parse_error(self):
        from telethon import errors

        from bridge.markdown import send_markdown

        mock_client = AsyncMock()
        # First call (with parse_mode) raises, second (plain) succeeds
        mock_client.send_message = AsyncMock(
            side_effect=[
                errors.BadRequestError(request=MagicMock(), message="parse error"),
                "sent_plain",
            ]
        )

        result = await send_markdown(mock_client, 123, "broken *md")
        assert result == "sent_plain"
        assert mock_client.send_message.call_count == 2
        # Second call should be without parse_mode
        second_call = mock_client.send_message.call_args_list[1]
        assert "parse_mode" not in second_call.kwargs

    @pytest.mark.asyncio
    async def test_with_reply_to(self):
        from bridge.markdown import send_markdown

        mock_client = AsyncMock()
        mock_client.send_message = AsyncMock(return_value="sent")

        await send_markdown(mock_client, 123, "text", reply_to=456)
        mock_client.send_message.assert_called_once_with(
            123, "text", reply_to=456, parse_mode="md"
        )


# ── Escape Markdown ──────────────────────────────────────────────────────────


class TestEscapeMarkdown:
    """Tests for escape_markdown utility."""

    def test_escapes_underscores(self):
        from bridge.markdown import escape_markdown

        assert escape_markdown("hello_world") == r"hello\_world"

    def test_preserves_bold(self):
        from bridge.markdown import escape_markdown

        assert "*bold*" in escape_markdown("*bold*")

    def test_preserves_code(self):
        from bridge.markdown import escape_markdown

        result = escape_markdown("use `my_func` here")
        # Underscore inside code should NOT be escaped
        assert "`my_func`" in result

    def test_preserves_links(self):
        from bridge.markdown import escape_markdown

        result = escape_markdown("[my_link](https://example.com/path_here)")
        # Underscores inside links should NOT be escaped
        assert "[my_link](https://example.com/path_here)" in result

    def test_escapes_outside_protected(self):
        from bridge.markdown import escape_markdown

        result = escape_markdown("my_var and `my_func` end")
        assert r"my\_var" in result
        assert "`my_func`" in result


# ── Backward Compatibility ────────────────────────────────────────────────────


class TestBackwardCompatibility:
    """Tests for SessionLog shim, RedisJob alias, and sender property."""

    def test_session_log_is_agent_session(self):
        from models.session_log import SessionLog

        assert SessionLog is AgentSession

    def test_redis_job_is_agent_session(self):
        from agent.job_queue import RedisJob

        assert RedisJob is AgentSession

    def test_models_init_exports_both(self):
        from models import AgentSession as AgentSessionAlias
        from models import SessionLog as SessionLogAlias

        assert AgentSessionAlias is SessionLogAlias

    def test_sender_property(self, session):
        assert session.sender == session.sender_name
        assert session.sender == "Tom"

    def test_create_via_session_log_shim(self, redis_test_db):
        from models.session_log import SessionLog

        s = SessionLog.create(
            session_id="shim-test",
            project_key="test",
            status="active",
            created_at=time.time(),
            sender_name="Test",
        )
        assert s.session_id == "shim-test"
        assert isinstance(s, AgentSession)


# ── Full Lifecycle Simulations ────────────────────────────────────────────────


class TestSDLCLifecycle:
    """Simulate a full SDLC session from enqueue to summarized output."""

    @pytest.mark.asyncio
    async def test_full_sdlc_flow(self, redis_test_db):
        from bridge.summarizer import _compose_structured_summary

        # 1. Create session (enqueue)
        s = AgentSession.create(
            session_id="sdlc-full-1",
            project_key="test",
            status="pending",
            chat_id="500",
            sender_name="Tom",
            created_at=time.time(),
            message_text="SDLC 177",
            priority="high",
        )
        assert s.status == "pending"

        # 2. Simulate job pickup — delete and recreate as running
        fields = {
            "session_id": s.session_id,
            "project_key": s.project_key,
            "chat_id": s.chat_id,
            "sender_name": s.sender_name,
            "created_at": s.created_at,
            "message_text": s.message_text,
            "priority": s.priority,
        }
        s.delete()
        s = AgentSession.create(status="running", started_at=time.time(), **fields)
        assert s.status == "running"

        # 3. Track SDLC stages
        s.append_history("user", "SDLC 177")
        s.append_history("stage", "ISSUE completed ☑")
        s.append_history("stage", "PLAN completed ☑")
        s.append_history("stage", "BUILD in_progress ▶")

        progress = s.get_stage_progress()
        assert progress["ISSUE"] == "completed"
        assert progress["PLAN"] == "completed"
        assert progress["BUILD"] == "in_progress"
        assert progress["TEST"] == "pending"

        # 4. Track links
        s.set_link("issue", "https://github.com/org/repo/issues/177")
        s.set_link("plan", "https://example.com/plan.md")

        # 5. Complete build, test, review, docs
        s.append_history("stage", "BUILD completed ☑")
        s.append_history("stage", "TEST completed ☑")
        s.append_history("stage", "REVIEW completed ☑")
        s.append_history("stage", "DOCS completed ☑")
        s.set_link("pr", "https://github.com/org/repo/pull/180")

        # 6. Compose structured summary
        result = _compose_structured_summary(
            "• Unified AgentSession model\n• Tests passing",
            session=s,
            is_completion=True,
        )

        # Verify full structured output
        assert "177" in result
        assert "☑ ISSUE" in result
        assert "☑ DOCS" in result
        assert "• Unified AgentSession model" in result
        assert "Issue #177" in result
        assert "PR #180" in result


class TestQALifecycle:
    """Simulate a Q&A session."""

    @pytest.mark.asyncio
    async def test_qa_flow(self, redis_test_db):
        from bridge.summarizer import _compose_structured_summary

        s = AgentSession.create(
            session_id="qa-full-1",
            project_key="test",
            status="completed",
            chat_id="600",
            sender_name="Kevin",
            created_at=time.time(),
            message_text="How does the job queue work?",
        )
        s.append_history("user", "How does the job queue work?")

        result = _compose_structured_summary(
            "The job queue uses a FILO stack with per-project sequential workers.",
            session=s,
            is_completion=True,
        )

        # Should have emoji + label + answer, no stages, no links
        assert "✅" in result
        assert "job queue" in result.lower()
        assert "FILO" in result
        assert "☑" not in result
        assert "☐" not in result


class TestChitChatLifecycle:
    """Simulate a casual conversation session."""

    @pytest.mark.asyncio
    async def test_chat_flow(self, redis_test_db):
        from bridge.summarizer import _compose_structured_summary

        s = AgentSession.create(
            session_id="chat-full-1",
            project_key="test",
            status="completed",
            chat_id="700",
            sender_name="Tom",
            created_at=time.time(),
            message_text="Hey, how's it going?",
        )

        result = _compose_structured_summary(
            "All good! Working through the backlog.",
            session=s,
            is_completion=True,
        )

        # Minimal output — emoji, label, prose
        assert "✅" in result
        assert "Working through the backlog" in result
        # No SDLC artifacts
        assert "☑" not in result
        assert " | " not in result
