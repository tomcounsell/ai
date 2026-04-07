"""Unit tests for resolve_chat_mode() in bridge/routing.py."""

from bridge.routing import resolve_chat_mode

# =============================================================================
# DM tests -- always Q&A regardless of project config
# =============================================================================


class TestDMAlwaysQA:
    def test_dm_with_no_project(self):
        assert resolve_chat_mode(None, None, is_dm=True) == "qa"

    def test_dm_with_project(self):
        project = {"telegram": {"groups": {}}}
        assert resolve_chat_mode(project, None, is_dm=True) == "qa"

    def test_dm_with_developer_project(self):
        """DM overrides any project-level config."""
        project = {
            "telegram": {
                "groups": {"Dev: MyProject": {"persona": "developer"}},
            }
        }
        assert resolve_chat_mode(project, None, is_dm=True) == "qa"


# =============================================================================
# Persona-to-mode mapping from projects.json
# =============================================================================


class TestPersonaMapping:
    def test_teammate_persona_maps_to_qa(self):
        project = {
            "telegram": {
                "groups": {"Team Chat": {"persona": "teammate"}},
            }
        }
        assert resolve_chat_mode(project, "Team Chat", is_dm=False) == "qa"

    def test_project_manager_persona_maps_to_pm(self):
        project = {
            "telegram": {
                "groups": {"PM: MyProject": {"persona": "project-manager"}},
            }
        }
        assert resolve_chat_mode(project, "PM: MyProject", is_dm=False) == "pm"

    def test_developer_persona_maps_to_dev(self):
        project = {
            "telegram": {
                "groups": {"Dev: MyProject": {"persona": "developer"}},
            }
        }
        assert resolve_chat_mode(project, "Dev: MyProject", is_dm=False) == "dev"

    def test_case_insensitive_group_matching(self):
        project = {
            "telegram": {
                "groups": {"team chat": {"persona": "teammate"}},
            }
        }
        assert resolve_chat_mode(project, "Team Chat", is_dm=False) == "qa"

    def test_partial_group_name_matching(self):
        """Group name is a substring of chat title."""
        project = {
            "telegram": {
                "groups": {"MyProject": {"persona": "teammate"}},
            }
        }
        assert resolve_chat_mode(project, "Team: MyProject Discussion", is_dm=False) == "qa"


# =============================================================================
# Title prefix fallback (backward compatibility)
# =============================================================================


class TestTitlePrefixFallback:
    def test_dev_prefix_returns_dev(self):
        assert resolve_chat_mode(None, "Dev: MyProject", is_dm=False) == "dev"

    def test_pm_prefix_returns_pm(self):
        assert resolve_chat_mode(None, "PM: MyProject", is_dm=False) == "pm"

    def test_no_prefix_no_config_returns_none(self):
        assert resolve_chat_mode(None, "Random Group", is_dm=False) is None

    def test_dev_prefix_with_empty_project(self):
        project = {"telegram": {"groups": {}}}
        assert resolve_chat_mode(project, "Dev: MyProject", is_dm=False) == "dev"

    def test_pm_prefix_with_empty_project(self):
        project = {"telegram": {"groups": {}}}
        assert resolve_chat_mode(project, "PM: MyProject", is_dm=False) == "pm"


# =============================================================================
# Unconfigured groups fall through to None
# =============================================================================


class TestUnconfigured:
    def test_no_project_no_prefix(self):
        assert resolve_chat_mode(None, "General Chat", is_dm=False) is None

    def test_project_without_matching_group(self):
        project = {
            "telegram": {
                "groups": {"Other Group": {"persona": "developer"}},
            }
        }
        assert resolve_chat_mode(project, "Unrelated Chat", is_dm=False) is None

    def test_no_chat_title_not_dm(self):
        assert resolve_chat_mode(None, None, is_dm=False) is None


# =============================================================================
# Edge cases -- invalid/empty persona values
# =============================================================================


class TestEdgeCases:
    def test_empty_persona_falls_through_to_prefix(self):
        """Empty persona string should not match, fall through to title prefix."""
        project = {
            "telegram": {
                "groups": {"Dev: MyProject": {"persona": ""}},
            }
        }
        # Empty persona -> no PERSONA_TO_MODE match -> falls through to title prefix
        assert resolve_chat_mode(project, "Dev: MyProject", is_dm=False) == "dev"

    def test_unknown_persona_falls_through(self):
        """Unknown persona value not in PERSONA_TO_MODE -> fall through."""
        project = {
            "telegram": {
                "groups": {"SomeGroup": {"persona": "unknown-role"}},
            }
        }
        assert resolve_chat_mode(project, "SomeGroup", is_dm=False) is None

    def test_group_config_is_not_dict(self):
        """Legacy format where group config is just a string."""
        project = {
            "telegram": {
                "groups": {"SomeGroup": "developer"},
            }
        }
        assert resolve_chat_mode(project, "SomeGroup", is_dm=False) is None

    def test_groups_is_list_not_dict(self):
        """Groups as list (old format) -- no persona lookup possible."""
        project = {
            "telegram": {
                "groups": ["Group A", "Group B"],
            }
        }
        assert resolve_chat_mode(project, "Group A", is_dm=False) is None

    def test_no_telegram_config(self):
        project = {}
        assert resolve_chat_mode(project, "SomeGroup", is_dm=False) is None

    def test_persona_config_takes_priority_over_title_prefix(self):
        """If a Dev: group has teammate persona, persona wins."""
        project = {
            "telegram": {
                "groups": {"Dev: MyProject": {"persona": "teammate"}},
            }
        }
        assert resolve_chat_mode(project, "Dev: MyProject", is_dm=False) == "qa"
