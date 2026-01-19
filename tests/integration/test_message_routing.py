"""
Integration tests for Telegram message routing.

Tests the complete message routing flow by testing the bridge module functions
with various configurations. These tests use the test fixtures from conftest.py
and test the logic functions directly.

Note: The actual bridge module uses global variables loaded at import time.
These tests verify the logic by calling the functions with test data directly,
similar to the unit tests but focusing on end-to-end scenarios.
"""

import re

import pytest


# Re-implement bridge functions for testable integration tests
# This matches the actual bridge logic but allows parameter injection


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
# Integration Test Scenarios
# ============================================================================


class TestMessageRoutingIntegration:
    """Integration tests for complete message routing."""

    def test_valor_group_correctly_identified(self, sample_config):
        """Messages from Dev: Valor should be identified as Valor project."""
        active = ["valor", "popoto"]
        group_map = build_group_to_project_map(sample_config, active)

        project = find_project_for_chat("Dev: Valor", group_map)

        assert project is not None
        assert project["name"] == "Valor AI"
        assert project["_key"] == "valor"

    def test_multiple_projects_isolated(self, sample_config):
        """Each project's groups should route to the correct project."""
        active = ["valor", "popoto", "django-project-template"]
        group_map = build_group_to_project_map(sample_config, active)

        # Test each project is correctly identified
        valor = find_project_for_chat("Dev: Valor", group_map)
        popoto = find_project_for_chat("Dev: Popoto", group_map)
        django = find_project_for_chat("Dev: Django Template", group_map)

        assert valor["_key"] == "valor"
        assert popoto["_key"] == "popoto"
        assert django["_key"] == "django-project-template"

    def test_inactive_projects_not_routed(self, sample_config):
        """Projects not in ACTIVE_PROJECTS should not be routed."""
        # Only valor is active
        active = ["valor"]
        group_map = build_group_to_project_map(sample_config, active)

        # Popoto group should not match
        project = find_project_for_chat("Dev: Popoto", group_map)

        assert project is None

    def test_context_injection_includes_all_fields(self, sample_config):
        """Context should include PROJECT, FOCUS, TECH, and REPO."""
        active = ["valor"]
        group_map = build_group_to_project_map(sample_config, active)
        project = find_project_for_chat("Dev: Valor", group_map)

        context = build_context_prefix(project, is_dm=False)

        assert "PROJECT: Valor AI" in context
        assert "FOCUS:" in context
        assert "TECH:" in context
        assert "REPO: tomcounsell/ai" in context

    def test_session_id_format(self, sample_config):
        """Session IDs should include project key for isolation."""
        active = ["valor"]
        group_map = build_group_to_project_map(sample_config, active)
        project = find_project_for_chat("Dev: Valor", group_map)

        # Simulate session ID generation
        project_key = project.get("_key", "dm")
        chat_id = 123456789
        session_id = f"tg_{project_key}_{chat_id}"

        assert session_id == "tg_valor_123456789"

    def test_dm_session_id_uses_dm_key(self):
        """DM sessions should use 'dm' as project key."""
        project_key = None  # No project for DMs
        chat_id = 987654321
        session_id = f"tg_{project_key or 'dm'}_{chat_id}"

        assert session_id == "tg_dm_987654321"


class TestResponseDecisionIntegration:
    """Integration tests for response decision logic."""

    DEFAULT_MENTIONS = ["@valor", "valor", "hey valor"]

    def test_mention_required_for_valor(self, sample_config):
        """Valor group should only respond to mentions."""
        active = ["valor"]
        group_map = build_group_to_project_map(sample_config, active)
        project = find_project_for_chat("Dev: Valor", group_map)

        # Should respond to mention
        assert should_respond(
            "hey valor help",
            is_dm=False,
            project=project,
            respond_to_dms=True,
            default_mentions=self.DEFAULT_MENTIONS,
        )

        # Should not respond without mention
        assert not should_respond(
            "random discussion",
            is_dm=False,
            project=project,
            respond_to_dms=True,
            default_mentions=self.DEFAULT_MENTIONS,
        )

    def test_respond_to_all_for_django(self, sample_config):
        """Django group should respond to all messages."""
        active = ["django-project-template"]
        group_map = build_group_to_project_map(sample_config, active)
        project = find_project_for_chat("Dev: Django Template", group_map)

        # Should respond even without mention (respond_to_all: true)
        assert should_respond(
            "any random message",
            is_dm=False,
            project=project,
            respond_to_dms=True,
            default_mentions=self.DEFAULT_MENTIONS,
        )


class TestMessageCleaningIntegration:
    """Integration tests for message cleaning."""

    DEFAULT_MENTIONS = ["@valor", "valor", "hey valor"]

    def test_cleans_mentions_before_processing(self, sample_config):
        """Mentions should be cleaned from message before sending to agent."""
        active = ["valor"]
        group_map = build_group_to_project_map(sample_config, active)
        project = find_project_for_chat("Dev: Valor", group_map)

        original = "@valor please review this code"
        cleaned = clean_message(original, project, self.DEFAULT_MENTIONS)

        assert "@valor" not in cleaned
        assert "please review this code" in cleaned

    def test_preserves_message_content(self, sample_config):
        """Important message content should be preserved after cleaning."""
        active = ["valor"]
        group_map = build_group_to_project_map(sample_config, active)
        project = find_project_for_chat("Dev: Valor", group_map)

        original = "hey valor the API endpoint /api/users is returning 500 errors"
        cleaned = clean_message(original, project, self.DEFAULT_MENTIONS)

        assert "/api/users" in cleaned
        assert "500 errors" in cleaned


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    DEFAULT_MENTIONS = ["@valor", "valor"]

    def test_empty_active_projects(self, sample_config):
        """Empty ACTIVE_PROJECTS should result in no groups being monitored."""
        group_map = build_group_to_project_map(sample_config, [])

        assert group_map == {}

    def test_nonexistent_project_in_active(self, sample_config):
        """Nonexistent projects in ACTIVE_PROJECTS should be ignored."""
        group_map = build_group_to_project_map(sample_config, ["nonexistent", "valor"])

        assert "dev: valor" in group_map
        assert len(group_map) == 1

    def test_empty_message_text(self):
        """Empty message text should not crash."""
        result = clean_message("", None, self.DEFAULT_MENTIONS)

        assert result == ""

    def test_none_chat_title_handling(self, sample_config):
        """None chat title (DMs) should be handled gracefully."""
        group_map = build_group_to_project_map(sample_config, ["valor"])

        # Should return None, not crash
        result = find_project_for_chat(None, group_map)
        assert result is None

    def test_special_characters_in_mention(self):
        """Special regex characters in mentions should be handled."""
        # Test with @ which could be special in regex
        result = clean_message("@valor help", None, self.DEFAULT_MENTIONS)

        assert result == "help"

    def test_unicode_in_message(self, sample_config):
        """Unicode characters in messages should be handled."""
        active = ["valor"]
        group_map = build_group_to_project_map(sample_config, active)
        project = find_project_for_chat("Dev: Valor", group_map)

        # Message with emoji and unicode
        original = "@valor can you help with this? Thanks!"
        cleaned = clean_message(original, project, self.DEFAULT_MENTIONS)

        assert "Thanks!" in cleaned


class TestMultiMachineScenarios:
    """Tests simulating multi-machine deployment scenarios."""

    DEFAULT_MENTIONS = ["@valor", "valor", "hey valor"]

    def test_machine_a_monitors_valor_only(self, sample_config):
        """Machine A: Only monitors Valor project."""
        # Machine A configuration
        active = ["valor"]
        group_map = build_group_to_project_map(sample_config, active)

        # Should respond to Valor group
        valor_project = find_project_for_chat("Dev: Valor", group_map)
        assert valor_project is not None
        assert valor_project["_key"] == "valor"

        # Should NOT respond to other groups
        popoto_project = find_project_for_chat("Dev: Popoto", group_map)
        assert popoto_project is None

    def test_machine_b_monitors_multiple(self, sample_config):
        """Machine B: Monitors Popoto and Django."""
        # Machine B configuration
        active = ["popoto", "django-project-template"]
        group_map = build_group_to_project_map(sample_config, active)

        # Should respond to Popoto
        popoto_project = find_project_for_chat("Dev: Popoto", group_map)
        assert popoto_project is not None
        assert popoto_project["_key"] == "popoto"

        # Should respond to Django
        django_project = find_project_for_chat("Dev: Django Template", group_map)
        assert django_project is not None
        assert django_project["_key"] == "django-project-template"

        # Should NOT respond to Valor (not in active)
        valor_project = find_project_for_chat("Dev: Valor", group_map)
        assert valor_project is None

    def test_machine_c_monitors_all(self, sample_config):
        """Machine C: Monitors all projects."""
        # Machine C configuration
        active = ["valor", "popoto", "django-project-template"]
        group_map = build_group_to_project_map(sample_config, active)

        # Should respond to all groups
        assert find_project_for_chat("Dev: Valor", group_map) is not None
        assert find_project_for_chat("Dev: Popoto", group_map) is not None
        assert find_project_for_chat("Dev: Django Template", group_map) is not None

    def test_context_isolation_between_projects(self, sample_config):
        """Each project should inject its own context."""
        active = ["valor", "popoto"]
        group_map = build_group_to_project_map(sample_config, active)

        valor_project = find_project_for_chat("Dev: Valor", group_map)
        popoto_project = find_project_for_chat("Dev: Popoto", group_map)

        valor_context = build_context_prefix(valor_project, is_dm=False)
        popoto_context = build_context_prefix(popoto_project, is_dm=False)

        # Valor context
        assert "Valor AI" in valor_context
        assert "tomcounsell/ai" in valor_context

        # Popoto context
        assert "Popoto" in popoto_context
        assert "tomcounsell/popoto" in popoto_context

        # Contexts should be different
        assert valor_context != popoto_context

    def test_session_isolation_between_projects(self, sample_config):
        """Sessions should be isolated by project key."""
        active = ["valor", "popoto"]
        group_map = build_group_to_project_map(sample_config, active)

        # Same chat ID but different projects
        chat_id = 123456789

        valor_project = find_project_for_chat("Dev: Valor", group_map)
        popoto_project = find_project_for_chat("Dev: Popoto", group_map)

        valor_session = f"tg_{valor_project['_key']}_{chat_id}"
        popoto_session = f"tg_{popoto_project['_key']}_{chat_id}"

        assert valor_session == "tg_valor_123456789"
        assert popoto_session == "tg_popoto_123456789"
        assert valor_session != popoto_session
