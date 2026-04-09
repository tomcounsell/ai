"""
Unit tests for Telegram bridge logic.

Tests the core decision-making functions without requiring Telegram connectivity.
"""

import asyncio
import re
from unittest.mock import AsyncMock, MagicMock, patch

# Import the functions we're testing (we'll test them in isolation)
# These are re-implemented here to test the logic without importing the module
# (which has side effects at import time)


def build_group_to_project_map(config: dict, active_projects: list[str]) -> dict:
    """Build a mapping from group names (lowercase) to project configs."""
    group_map = {}
    projects = config.get("projects", {})

    for project_key in active_projects:
        if project_key not in projects:
            continue

        project = projects[project_key].copy()
        project["_key"] = project_key

        telegram_config = project.get("telegram", {})
        groups = telegram_config.get("groups", [])

        for group in groups:
            group_lower = group.lower()
            if group_lower in group_map:
                continue
            group_map[group_lower] = project

    return group_map


def find_project_for_chat(chat_title: str | None, group_to_project: dict) -> dict | None:
    """Find which project a chat belongs to."""
    if not chat_title:
        return None

    chat_lower = chat_title.lower()
    for group_name, project in group_to_project.items():
        if group_name in chat_lower:
            return project

    return None


def should_respond(
    text: str,
    is_dm: bool,
    project: dict | None,
    respond_to_dms: bool,
    default_mentions: list[str],
) -> bool:
    """Determine if we should respond to this message."""
    if is_dm:
        return respond_to_dms

    if not project:
        return False

    telegram_config = project.get("telegram", {})

    if telegram_config.get("respond_to_all", False):
        return True

    if telegram_config.get("respond_to_mentions", True):
        mentions = telegram_config.get("mention_triggers", default_mentions)
        text_lower = text.lower()
        return any(mention.lower() in text_lower for mention in mentions)

    return False


def clean_message(text: str, project: dict | None, default_mentions: list[str]) -> str:
    """Remove mention triggers from message for cleaner processing."""
    mentions = default_mentions
    if project:
        telegram_config = project.get("telegram", {})
        mentions = telegram_config.get("mention_triggers", default_mentions)

    result = text
    for mention in mentions:
        result = re.sub(re.escape(mention), "", result, flags=re.IGNORECASE)
    return result.strip()


def build_context_prefix(project: dict | None, session_type: str | None = None) -> str:
    """Build project context to inject into agent prompt."""
    context_parts = []

    if session_type == "teammate":
        context_parts.append(
            "RESTRICTION: This user has read-only Teammate access. "
            "Do NOT make any code changes, file edits, git commits, or run destructive commands. "
            "Answer questions, explain code, and provide guidance only. "
            "If they ask you to make changes, politely explain you can only help with "
            "informational queries for them."
        )

    if not project:
        return "\n".join(context_parts) if context_parts else ""

    context_parts.append(f"PROJECT: {project.get('name', project.get('_key', 'Unknown'))}")

    project_context = project.get("context", {})
    if project_context.get("description"):
        context_parts.append(f"FOCUS: {project_context['description']}")

    if project_context.get("tech_stack"):
        context_parts.append(f"TECH: {', '.join(project_context['tech_stack'])}")

    github = project.get("github", {})
    if github.get("repo"):
        context_parts.append(f"REPO: {github.get('org', '')}/{github['repo']}")

    return "\n".join(context_parts)


# ============================================================================
# Tests for build_group_to_project_map
# ============================================================================


class TestBuildGroupToProjectMap:
    """Tests for group-to-project mapping function."""

    def test_maps_single_project(self, sample_config):
        """Single active project should map its groups."""
        result = build_group_to_project_map(sample_config, ["valor"])

        assert "dev: valor" in result
        assert result["dev: valor"]["name"] == "Valor AI"
        assert result["dev: valor"]["_key"] == "valor"

    def test_maps_multiple_projects(self, sample_config):
        """Multiple active projects should all be mapped."""
        result = build_group_to_project_map(
            sample_config, ["valor", "popoto", "django-project-template"]
        )

        assert "dev: valor" in result
        assert "dev: popoto" in result
        assert "dev: django template" in result
        assert len(result) == 3

    def test_ignores_unknown_projects(self, sample_config):
        """Unknown project keys should be silently ignored."""
        result = build_group_to_project_map(sample_config, ["valor", "nonexistent"])

        assert "dev: valor" in result
        assert len(result) == 1

    def test_empty_active_projects(self, sample_config):
        """Empty active projects should return empty map."""
        result = build_group_to_project_map(sample_config, [])

        assert result == {}

    def test_project_key_added(self, sample_config):
        """Each project should have _key field added."""
        result = build_group_to_project_map(sample_config, ["popoto"])

        assert result["dev: popoto"]["_key"] == "popoto"


# ============================================================================
# Tests for find_project_for_chat
# ============================================================================


class TestFindProjectForChat:
    """Tests for chat-to-project matching."""

    def test_finds_exact_match(self, sample_config):
        """Exact group name match should find project."""
        group_map = build_group_to_project_map(sample_config, ["valor", "popoto"])

        result = find_project_for_chat("Dev: Valor", group_map)
        assert result is not None
        assert result["name"] == "Valor AI"

    def test_finds_partial_match(self, sample_config):
        """Partial match (group name in chat title) should find project."""
        group_map = build_group_to_project_map(sample_config, ["valor"])

        # Chat title contains the group name
        result = find_project_for_chat("Dev: Valor - Main Channel", group_map)
        assert result is not None
        assert result["name"] == "Valor AI"

    def test_case_insensitive(self, sample_config):
        """Matching should be case-insensitive."""
        group_map = build_group_to_project_map(sample_config, ["valor"])

        result = find_project_for_chat("DEV: VALOR", group_map)
        assert result is not None
        assert result["name"] == "Valor AI"

    def test_no_match_returns_none(self, sample_config):
        """Unmatched chat should return None."""
        group_map = build_group_to_project_map(sample_config, ["valor"])

        result = find_project_for_chat("Random Chat Group", group_map)
        assert result is None

    def test_none_chat_title_returns_none(self, sample_config):
        """None chat title (DMs) should return None."""
        group_map = build_group_to_project_map(sample_config, ["valor"])

        result = find_project_for_chat(None, group_map)
        assert result is None

    def test_empty_chat_title_returns_none(self, sample_config):
        """Empty chat title should return None."""
        group_map = build_group_to_project_map(sample_config, ["valor"])

        result = find_project_for_chat("", group_map)
        assert result is None


# ============================================================================
# Tests for should_respond
# ============================================================================


class TestShouldRespond:
    """Tests for response decision logic."""

    DEFAULT_MENTIONS = ["@valor", "valor", "hey valor"]

    def test_dm_respects_setting_true(self, valor_project):
        """DMs should respond when respond_to_dms is True."""
        result = should_respond(
            "hello there",
            is_dm=True,
            project=None,
            respond_to_dms=True,
            default_mentions=self.DEFAULT_MENTIONS,
        )
        assert result is True

    def test_dm_respects_setting_false(self, valor_project):
        """DMs should not respond when respond_to_dms is False."""
        result = should_respond(
            "hello there",
            is_dm=True,
            project=None,
            respond_to_dms=False,
            default_mentions=self.DEFAULT_MENTIONS,
        )
        assert result is False

    def test_no_project_no_response(self):
        """Messages with no matching project should not get response."""
        result = should_respond(
            "hello valor",
            is_dm=False,
            project=None,
            respond_to_dms=True,
            default_mentions=self.DEFAULT_MENTIONS,
        )
        assert result is False

    def test_respond_to_all_true(self, django_project):
        """Projects with respond_to_all should respond to any message."""
        # Django project has respond_to_all: True
        result = should_respond(
            "random message without mention",
            is_dm=False,
            project=django_project,
            respond_to_dms=True,
            default_mentions=self.DEFAULT_MENTIONS,
        )
        assert result is True

    def test_mention_triggers_response(self, valor_project):
        """Messages with mention should trigger response."""
        result = should_respond(
            "hey valor, can you help?",
            is_dm=False,
            project=valor_project,
            respond_to_dms=True,
            default_mentions=self.DEFAULT_MENTIONS,
        )
        assert result is True

    def test_no_mention_no_response(self, valor_project):
        """Messages without mention should not trigger response."""
        result = should_respond(
            "random message about something",
            is_dm=False,
            project=valor_project,
            respond_to_dms=True,
            default_mentions=self.DEFAULT_MENTIONS,
        )
        assert result is False

    def test_mention_case_insensitive(self, valor_project):
        """Mention detection should be case-insensitive."""
        result = should_respond(
            "HEY VALOR please help",
            is_dm=False,
            project=valor_project,
            respond_to_dms=True,
            default_mentions=self.DEFAULT_MENTIONS,
        )
        assert result is True

    def test_at_mention(self, valor_project):
        """@mention should trigger response."""
        result = should_respond(
            "@valor check this out",
            is_dm=False,
            project=valor_project,
            respond_to_dms=True,
            default_mentions=self.DEFAULT_MENTIONS,
        )
        assert result is True


# ============================================================================
# Tests for clean_message
# ============================================================================


class TestCleanMessage:
    """Tests for message cleaning (mention removal)."""

    DEFAULT_MENTIONS = ["@valor", "valor", "hey valor"]

    def test_removes_at_mention(self, valor_project):
        """Should remove @valor mention."""
        result = clean_message("@valor please help me", valor_project, self.DEFAULT_MENTIONS)
        assert result == "please help me"

    def test_removes_hey_mention(self, valor_project):
        """Should remove 'hey valor' mention."""
        result = clean_message("hey valor can you help?", valor_project, self.DEFAULT_MENTIONS)
        # "valor" gets removed first, leaving "hey  can you help?"
        # This is expected - the important thing is "valor" is gone
        assert "valor" not in result.lower()

    def test_removes_plain_mention(self, valor_project):
        """Should remove plain 'valor' mention."""
        result = clean_message("valor, what is this?", valor_project, self.DEFAULT_MENTIONS)
        assert result == ", what is this?"

    def test_case_insensitive_removal(self, valor_project):
        """Mention removal should be case-insensitive."""
        result = clean_message("HEY VALOR can you help?", valor_project, self.DEFAULT_MENTIONS)
        # "VALOR" gets removed (case-insensitive), the important thing is it's gone
        assert "valor" not in result.lower()

    def test_removes_multiple_mentions(self, valor_project):
        """Should remove multiple mentions in one message."""
        result = clean_message(
            "@valor hey valor please valor help", valor_project, self.DEFAULT_MENTIONS
        )
        assert "valor" not in result.lower()

    def test_preserves_non_mention_text(self, valor_project):
        """Should preserve text that isn't a mention."""
        result = clean_message(
            "@valor fix the evaluation code", valor_project, self.DEFAULT_MENTIONS
        )
        assert "fix the evaluation code" in result

    def test_no_project_uses_defaults(self):
        """Without project, should use default mentions."""
        result = clean_message("@valor help me", None, self.DEFAULT_MENTIONS)
        assert result == "help me"


# ============================================================================
# Tests for build_context_prefix
# ============================================================================


class TestBuildContextPrefix:
    """Tests for context prefix generation."""

    def test_teammate_without_project(self):
        """Teammate session without project should get restriction but no project context."""
        result = build_context_prefix(None, session_type="teammate")
        assert "RESTRICTION" in result
        assert "read-only Teammate access" in result

    def test_no_session_type_without_project(self):
        """No session type without project match should get empty context."""
        result = build_context_prefix(None, session_type=None)
        assert result == ""

    def test_pm_session_without_project(self):
        """PM session without project should get no restriction."""
        result = build_context_prefix(None, session_type="pm")
        assert result == ""
        assert "RESTRICTION" not in result

    def test_dev_session_without_project(self):
        """Dev session without project should get no restriction."""
        result = build_context_prefix(None, session_type="dev")
        assert result == ""
        assert "RESTRICTION" not in result

    def test_includes_project_name(self, valor_project):
        """Context should include project name."""
        result = build_context_prefix(valor_project, session_type=None)
        assert "PROJECT: Valor AI" in result

    def test_includes_focus_description(self, valor_project):
        """Context should include focus description."""
        result = build_context_prefix(valor_project, session_type=None)
        assert "FOCUS: Focus on agentic systems" in result

    def test_includes_tech_stack(self, valor_project):
        """Context should include tech stack."""
        result = build_context_prefix(valor_project, session_type=None)
        assert "TECH: Python, Claude Agent SDK, Telethon" in result

    def test_includes_repo(self, valor_project):
        """Context should include GitHub repo."""
        result = build_context_prefix(valor_project, session_type=None)
        assert "REPO: tomcounsell/ai" in result

    def test_all_fields_present(self, valor_project):
        """All context fields should be present."""
        result = build_context_prefix(valor_project, session_type=None)
        lines = result.split("\n")
        assert len(lines) == 4  # PROJECT, FOCUS, TECH, REPO

    def test_missing_optional_fields(self):
        """Should handle missing optional fields gracefully."""
        minimal_project = {
            "name": "Test Project",
            "_key": "test",
        }
        result = build_context_prefix(minimal_project, session_type=None)
        assert "PROJECT: Test Project" in result
        assert "FOCUS:" not in result  # No context.description
        assert "TECH:" not in result  # No context.tech_stack
        assert "REPO:" not in result  # No github.repo

    def test_pm_session_no_restriction(self, valor_project):
        """PM session should never receive Teammate read-only restriction."""
        result = build_context_prefix(valor_project, session_type="pm")
        assert "RESTRICTION" not in result
        assert "PROJECT: Valor AI" in result

    def test_teammate_session_restriction_present(self):
        """Teammate session should receive read-only restriction."""
        result = build_context_prefix(None, session_type="teammate")
        assert "RESTRICTION" in result
        assert "read-only Teammate access" in result

    def test_teammate_dm_restriction_regression(self):
        """Regression: Teammate DM sessions (real Telegram users) still get restriction.

        Risk 1 guard: Telegram DM messages set session_type="teammate" via bridge routing.
        Passing session_type="teammate" must correctly inject the restriction, so real DM
        users never gain unrestricted access.
        """
        result = build_context_prefix(None, session_type="teammate")
        assert "RESTRICTION" in result
        assert "read-only Teammate access" in result
        assert "Do NOT make any code changes" in result


# ============================================================================
# Tests for message routing (end-to-end logic flow)
# ============================================================================


class TestMessageRouting:
    """Tests for complete message routing scenarios."""

    DEFAULT_MENTIONS = ["@valor", "valor", "hey valor"]

    def test_valor_group_mention_responds(self, sample_config, valor_project):
        """Message mentioning Valor in Valor group should respond."""
        group_map = build_group_to_project_map(sample_config, ["valor"])
        project = find_project_for_chat("Dev: Valor", group_map)

        assert project is not None
        assert should_respond(
            "hey valor help me",
            is_dm=False,
            project=project,
            respond_to_dms=True,
            default_mentions=self.DEFAULT_MENTIONS,
        )

    def test_valor_group_no_mention_ignores(self, sample_config, valor_project):
        """Message without mention in Valor group should be ignored."""
        group_map = build_group_to_project_map(sample_config, ["valor"])
        project = find_project_for_chat("Dev: Valor", group_map)

        assert project is not None
        assert not should_respond(
            "random discussion",
            is_dm=False,
            project=project,
            respond_to_dms=True,
            default_mentions=self.DEFAULT_MENTIONS,
        )

    def test_django_group_any_message_responds(self, sample_config, django_project):
        """Any message in Django group (respond_to_all) should respond."""
        group_map = build_group_to_project_map(sample_config, ["django-project-template"])
        project = find_project_for_chat("Dev: Django Template", group_map)

        assert project is not None
        assert should_respond(
            "random message no mention",
            is_dm=False,
            project=project,
            respond_to_dms=True,
            default_mentions=self.DEFAULT_MENTIONS,
        )

    def test_unmonitored_group_ignored(self, sample_config):
        """Messages in unmonitored groups should be ignored."""
        # Only monitoring valor, not popoto
        group_map = build_group_to_project_map(sample_config, ["valor"])
        project = find_project_for_chat("Dev: Popoto", group_map)

        assert project is None
        assert not should_respond(
            "@valor help",
            is_dm=False,
            project=project,
            respond_to_dms=True,
            default_mentions=self.DEFAULT_MENTIONS,
        )

    def test_dm_responds_when_any_project_allows(self, sample_config):
        """DMs should respond if any active project allows DMs."""
        # valor has respond_to_dms: True, popoto has respond_to_dms: False
        # If valor is active, DMs should respond
        result = should_respond(
            "hello",
            is_dm=True,
            project=None,
            respond_to_dms=True,  # Any project allows
            default_mentions=self.DEFAULT_MENTIONS,
        )
        assert result is True

    def test_correct_context_injected(self, sample_config, popoto_project):
        """Correct project context should be injected for matching group."""
        group_map = build_group_to_project_map(sample_config, ["popoto"])
        project = find_project_for_chat("Dev: Popoto", group_map)

        assert project is not None
        context = build_context_prefix(project, session_type=None)
        assert "PROJECT: Popoto" in context
        assert "TECH: Python, Redis" in context
        assert "REPO: tomcounsell/popoto" in context


# ============================================================================
# Tests for connect_with_retry() hibernation behavior
#
# The retry loop lives inside bridge/telegram_bridge.py:main() and cannot be
# imported directly (import has side effects). We replicate the core retry
# logic here — exactly as test_bridge_logic.py does for all other bridge
# functions — and verify that the hibernation contract is upheld:
#
#   1. is_user_authorized() == False → enter_hibernation() called, SystemExit(2) raised
#   2. Permanent auth exception (e.g. AuthKeyUnregisteredError) → same
#   3. Transient exception (e.g. ConnectionError) → enter_hibernation() NOT called
#
# Mocks: bridge.hibernation.enter_hibernation is patched to avoid flag-file
# writes and osascript calls. Telethon error classes are imported from
# telethon.errors directly.
# ============================================================================


async def _connect_with_retry(client, enter_hibernation_fn, is_auth_error_fn, max_attempts=8):
    """Minimal replica of the connect-with-retry loop from telegram_bridge.main().

    This mirrors lines 1664-1707 of bridge/telegram_bridge.py exactly, with
    asyncio.sleep replaced by a no-op so tests run instantly. It is the basis
    for the hibernation contract tests below.
    """
    for _attempt in range(1, max_attempts + 1):
        try:
            await client.connect()
            if not await client.is_user_authorized():
                enter_hibernation_fn()
                raise SystemExit(2)
            break
        except SystemExit:
            raise
        except KeyboardInterrupt:
            raise
        except Exception as e:
            if is_auth_error_fn(e):
                enter_hibernation_fn()
                raise SystemExit(2)
            if _attempt >= max_attempts:
                raise
            # No sleep in tests — just continue
            continue


class TestConnectWithRetryHibernation:
    """Tests for the hibernation contract inside the bridge retry loop.

    Verifies that:
    - Unauthorized state triggers enter_hibernation() and SystemExit(2)
    - Permanent auth exceptions trigger enter_hibernation() and SystemExit(2)
    - Transient exceptions do NOT trigger enter_hibernation()
    """

    def _run(self, coro):
        return asyncio.run(coro)

    def test_unauthorized_calls_enter_hibernation_and_exits_2(self):
        """When is_user_authorized() returns False, enter_hibernation() is called
        and SystemExit(2) is raised.
        """
        client = AsyncMock()
        client.connect = AsyncMock()
        client.is_user_authorized = AsyncMock(return_value=False)

        mock_enter = MagicMock()

        from bridge.hibernation import is_auth_error

        with patch("bridge.hibernation.enter_hibernation", mock_enter):
            import pytest

            with pytest.raises(SystemExit) as exc_info:
                self._run(
                    _connect_with_retry(
                        client,
                        enter_hibernation_fn=mock_enter,
                        is_auth_error_fn=is_auth_error,
                    )
                )

        assert exc_info.value.code == 2
        mock_enter.assert_called_once()

    def test_permanent_auth_exception_calls_enter_hibernation_and_exits_2(self):
        """When a permanent auth exception (AuthKeyUnregisteredError) is raised during
        connect(), enter_hibernation() is called and SystemExit(2) is raised.
        """
        from telethon.errors import AuthKeyUnregisteredError

        client = AsyncMock()
        client.connect = AsyncMock(side_effect=AuthKeyUnregisteredError(None))
        client.is_user_authorized = AsyncMock()

        mock_enter = MagicMock()

        import pytest

        from bridge.hibernation import is_auth_error

        with pytest.raises(SystemExit) as exc_info:
            self._run(
                _connect_with_retry(
                    client,
                    enter_hibernation_fn=mock_enter,
                    is_auth_error_fn=is_auth_error,
                )
            )

        assert exc_info.value.code == 2
        mock_enter.assert_called_once()

    def test_transient_error_does_not_call_enter_hibernation(self):
        """When a transient error (ConnectionError) occurs, enter_hibernation() is
        NOT called — the retry loop continues as normal.
        """
        client = AsyncMock()
        # Fail on first attempt with transient error, succeed on second
        call_count = 0

        async def flaky_connect():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("temporary network failure")

        client.connect = flaky_connect
        client.is_user_authorized = AsyncMock(return_value=True)

        mock_enter = MagicMock()

        from bridge.hibernation import is_auth_error

        # Should complete without raising
        self._run(
            _connect_with_retry(
                client,
                enter_hibernation_fn=mock_enter,
                is_auth_error_fn=is_auth_error,
                max_attempts=3,
            )
        )

        mock_enter.assert_not_called()
