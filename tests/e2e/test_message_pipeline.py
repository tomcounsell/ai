"""E2E tests for the full message pipeline.

Tests: event -> routing -> context -> response
Mocks at boundaries only: Telegram API, Claude CLI subprocess.
Uses real Redis (via conftest redis_test_db fixture).
"""

import pytest

from bridge.context import build_context_prefix, is_status_question
from bridge.dedup import is_duplicate_message, record_message_processed
from bridge.markdown import escape_markdown
from bridge.response import clean_message, extract_files_from_response, filter_tool_logs
from bridge.routing import (
    build_group_to_project_map,
    classify_needs_response,
    classify_work_request,
    extract_at_mentions,
    find_project_for_chat,
    is_message_for_others,
    is_message_for_valor,
    should_respond_sync,
)


@pytest.mark.e2e
class TestMessageRouting:
    """Test message routing from chat to project config."""

    def test_known_group_routes_to_project(self, sample_config, valor_project):
        import bridge.routing as routing_mod

        old_active = routing_mod.ACTIVE_PROJECTS
        old_map = routing_mod.GROUP_TO_PROJECT
        try:
            routing_mod.ACTIVE_PROJECTS = ["valor", "popoto", "django-project-template"]
            routing_mod.GROUP_TO_PROJECT = build_group_to_project_map(sample_config)

            project = find_project_for_chat("Dev: Valor")
            assert project is not None
            assert project["_key"] == "valor"
        finally:
            routing_mod.ACTIVE_PROJECTS = old_active
            routing_mod.GROUP_TO_PROJECT = old_map

    def test_unknown_chat_returns_none(self):
        import bridge.routing as routing_mod

        old_map = routing_mod.GROUP_TO_PROJECT
        try:
            routing_mod.GROUP_TO_PROJECT = {}
            project = find_project_for_chat("Random Chat Group")
            assert project is None
        finally:
            routing_mod.GROUP_TO_PROJECT = old_map

    def test_none_chat_title_returns_none(self):
        assert find_project_for_chat(None) is None

    def test_group_map_builds_lowercase_keys(self, sample_config):
        import bridge.routing as routing_mod

        old_active = routing_mod.ACTIVE_PROJECTS
        try:
            routing_mod.ACTIVE_PROJECTS = ["valor"]
            result = build_group_to_project_map(sample_config)
            assert "dev: valor" in result
            assert "Dev: Valor" not in result
        finally:
            routing_mod.ACTIVE_PROJECTS = old_active


@pytest.mark.e2e
class TestMentionDetection:
    """Test @mention routing for group messages."""

    def test_valor_mention_detected(self, valor_project):
        assert is_message_for_valor("Hey @valor can you help?", valor_project)

    def test_other_mention_ignored(self, valor_project):
        assert is_message_for_others("@someoneelse please review", valor_project)

    def test_no_mention_not_for_valor(self, valor_project):
        assert not is_message_for_valor("Just a regular message", valor_project)

    def test_extract_multiple_mentions(self):
        mentions = extract_at_mentions("@alice and @bob please review @valor")
        assert "alice" in mentions
        assert "bob" in mentions
        assert "valor" in mentions


@pytest.mark.e2e
class TestResponseDecision:
    """Test should_respond_sync logic across DM and group scenarios."""

    def test_dm_responds_when_enabled(self, valor_project):
        import bridge.routing as routing_mod

        old_val = routing_mod.RESPOND_TO_DMS
        old_wl = routing_mod.DM_WHITELIST
        try:
            routing_mod.RESPOND_TO_DMS = True
            routing_mod.DM_WHITELIST = set()
            assert should_respond_sync("hello", is_dm=True, project=None) is True
        finally:
            routing_mod.RESPOND_TO_DMS = old_val
            routing_mod.DM_WHITELIST = old_wl

    def test_dm_blocked_when_disabled(self):
        import bridge.routing as routing_mod

        old_val = routing_mod.RESPOND_TO_DMS
        try:
            routing_mod.RESPOND_TO_DMS = False
            assert should_respond_sync("hello", is_dm=True, project=None) is False
        finally:
            routing_mod.RESPOND_TO_DMS = old_val

    def test_group_without_project_rejected(self):
        assert should_respond_sync("hello", is_dm=False, project=None) is False

    def test_respond_to_all_group(self, django_project):
        assert should_respond_sync("random msg", is_dm=False, project=django_project) is True

    def test_mention_trigger_in_group(self, valor_project):
        import bridge.routing as routing_mod

        old_mentions = routing_mod.DEFAULT_MENTIONS
        try:
            routing_mod.DEFAULT_MENTIONS = ["@valor", "valor"]
            result = should_respond_sync("hey @valor help me", is_dm=False, project=valor_project)
            assert result is True
        finally:
            routing_mod.DEFAULT_MENTIONS = old_mentions


@pytest.mark.e2e
class TestDeduplication:
    """Test Redis-backed message deduplication."""

    @pytest.mark.asyncio
    async def test_new_message_not_duplicate(self):
        is_dup = await is_duplicate_message(chat_id=99999, message_id=1)
        assert not is_dup

    @pytest.mark.asyncio
    async def test_recorded_message_is_duplicate(self):
        await record_message_processed(chat_id=99999, message_id=2)
        is_dup = await is_duplicate_message(chat_id=99999, message_id=2)
        assert is_dup

    @pytest.mark.asyncio
    async def test_different_chat_not_duplicate(self):
        await record_message_processed(chat_id=88888, message_id=3)
        is_dup = await is_duplicate_message(chat_id=77777, message_id=3)
        assert not is_dup


@pytest.mark.e2e
class TestWorkRequestClassification:
    """Test the fast-path classification in classify_work_request."""

    def test_slash_commands_are_passthrough(self):
        assert classify_work_request("/sdlc do something") == "passthrough"
        assert classify_work_request("/do-plan my feature") == "passthrough"
        assert classify_work_request("/update") == "passthrough"

    def test_acknowledgments_are_passthrough(self):
        assert classify_work_request("ok") == "passthrough"
        assert classify_work_request("continue") == "passthrough"
        assert classify_work_request("yes") == "passthrough"

    def test_empty_is_passthrough(self):
        assert classify_work_request("") == "passthrough"
        assert classify_work_request("   ") == "passthrough"

    def test_issue_reference_is_sdlc(self):
        assert classify_work_request("issue 123") == "sdlc"
        assert classify_work_request("pr 42") == "sdlc"

    def test_bare_hash_is_question(self):
        assert classify_work_request("#123") == "question"


@pytest.mark.e2e
class TestNeedsResponseClassification:
    """Test fast-path classify_needs_response (bool: True/False)."""

    def test_short_messages_ignored(self):
        assert classify_needs_response("ok") is False
        assert classify_needs_response("hi") is False

    def test_common_acknowledgments_ignored(self):
        assert classify_needs_response("thanks") is False
        assert classify_needs_response("gotcha") is False

    def test_social_banter_ignored(self):
        assert classify_needs_response("nice") is False
        assert classify_needs_response("lol") is False
        assert classify_needs_response("haha") is False

    def test_emoji_acknowledgments_ignored(self):
        assert classify_needs_response("\U0001f44d") is False


@pytest.mark.e2e
class TestContextBuilding:
    """Test context prefix building from project config."""

    def test_project_context_includes_name(self, valor_project):
        ctx = build_context_prefix(valor_project, session_type=None)
        assert "Valor AI" in ctx

    def test_project_context_includes_tech(self, valor_project):
        ctx = build_context_prefix(valor_project, session_type=None)
        assert "Python" in ctx

    def test_teammate_without_project(self):
        ctx = build_context_prefix(None, session_type="teammate")
        assert "RESTRICTION" in ctx

    def test_no_project_no_session_type(self):
        ctx = build_context_prefix(None, session_type=None)
        assert ctx == ""

    def test_status_question_detection(self):
        assert is_status_question("what are you working on?") is True
        assert is_status_question("any updates?") is True
        assert is_status_question("deploy the feature") is False


@pytest.mark.e2e
class TestResponseCleaning:
    """Test response cleaning and file extraction."""

    def test_tool_logs_filtered(self):
        raw = "Here is the result\n\U0001f6e0\ufe0f exec: ls -la\nDone."
        filtered = filter_tool_logs(raw)
        assert "exec:" not in filtered
        assert "result" in filtered

    def test_empty_response_stays_empty(self):
        assert filter_tool_logs("") == ""

    def test_file_marker_extraction(self):
        text = "Here is your image <<FILE:/tmp/test.png>> enjoy"
        cleaned, files = extract_files_from_response(text)
        assert "<<FILE:" not in cleaned
        # File won't exist in test env so files list is empty
        assert isinstance(files, list)

    def test_clean_message_strips_mentions(self, valor_project):
        import bridge.routing as routing_mod

        old_mentions = routing_mod.DEFAULT_MENTIONS
        try:
            routing_mod.DEFAULT_MENTIONS = ["@valor", "valor"]
            result = clean_message("@valor what is Python?", valor_project)
            assert "what is Python?" in result
        finally:
            routing_mod.DEFAULT_MENTIONS = old_mentions


@pytest.mark.e2e
class TestMarkdownEscaping:
    """Test Telegram markdown escaping utility."""

    def test_underscores_escaped(self):
        assert "\\_" in escape_markdown("some_variable_name")

    def test_code_blocks_preserved(self):
        result = escape_markdown("Use `my_func()` here")
        assert "`my_func()`" in result

    def test_links_preserved(self):
        result = escape_markdown("See [my_link](http://example.com)")
        assert "[my_link](http://example.com)" in result
