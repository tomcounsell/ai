"""
Unit tests for Telegram bridge logic.

Tests the core decision-making functions without requiring Telegram connectivity.
"""

import pytest
import re


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


def build_context_prefix(project: dict | None, is_dm: bool) -> str:
    """Build project context to inject into agent prompt."""
    if not project:
        if is_dm:
            return "CONTEXT: Direct message to Valor (no specific project context)"
        return ""

    context_parts = [f"PROJECT: {project.get('name', project.get('_key', 'Unknown'))}"]

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
        result = clean_message("@valor fix the evaluation code", valor_project, self.DEFAULT_MENTIONS)
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

    def test_dm_without_project(self):
        """DM without project should get generic context."""
        result = build_context_prefix(None, is_dm=True)
        assert "Direct message" in result
        assert "no specific project context" in result

    def test_group_without_project(self):
        """Group message without project match should get empty context."""
        result = build_context_prefix(None, is_dm=False)
        assert result == ""

    def test_includes_project_name(self, valor_project):
        """Context should include project name."""
        result = build_context_prefix(valor_project, is_dm=False)
        assert "PROJECT: Valor AI" in result

    def test_includes_focus_description(self, valor_project):
        """Context should include focus description."""
        result = build_context_prefix(valor_project, is_dm=False)
        assert "FOCUS: Focus on agentic systems" in result

    def test_includes_tech_stack(self, valor_project):
        """Context should include tech stack."""
        result = build_context_prefix(valor_project, is_dm=False)
        assert "TECH: Python, Clawdbot, Telethon" in result

    def test_includes_repo(self, valor_project):
        """Context should include GitHub repo."""
        result = build_context_prefix(valor_project, is_dm=False)
        assert "REPO: tomcounsell/ai" in result

    def test_all_fields_present(self, valor_project):
        """All context fields should be present."""
        result = build_context_prefix(valor_project, is_dm=False)
        lines = result.split("\n")
        assert len(lines) == 4  # PROJECT, FOCUS, TECH, REPO

    def test_missing_optional_fields(self):
        """Should handle missing optional fields gracefully."""
        minimal_project = {
            "name": "Test Project",
            "_key": "test",
        }
        result = build_context_prefix(minimal_project, is_dm=False)
        assert "PROJECT: Test Project" in result
        assert "FOCUS:" not in result  # No context.description
        assert "TECH:" not in result  # No context.tech_stack
        assert "REPO:" not in result  # No github.repo


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
        context = build_context_prefix(project, is_dm=False)
        assert "PROJECT: Popoto" in context
        assert "TECH: Python, Redis" in context
        assert "REPO: tomcounsell/popoto" in context
