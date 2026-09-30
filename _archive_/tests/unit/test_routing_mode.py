"""Unit tests for resolve_persona() in bridge/routing.py."""

from bridge.routing import resolve_persona
from config.enums import PersonaType

# =============================================================================
# DM tests -- always Teammate regardless of project config
# =============================================================================


class TestDMAlwaysTeammate:
    def test_dm_with_no_project(self):
        assert resolve_persona(None, None, is_dm=True) == PersonaType.TEAMMATE

    def test_dm_with_project(self):
        project = {"telegram": {"groups": {}}}
        assert resolve_persona(project, None, is_dm=True) == PersonaType.TEAMMATE

    def test_dm_with_engineer_project(self):
        """DM overrides any project-level config."""
        project = {
            "telegram": {
                "groups": {"Eng: MyProject": {"persona": "engineer"}},
            }
        }
        assert resolve_persona(project, None, is_dm=True) == PersonaType.TEAMMATE


# =============================================================================
# Persona resolution from projects.json
# =============================================================================


class TestPersonaMapping:
    def test_teammate_persona_resolves(self):
        project = {
            "telegram": {
                "groups": {"Team Chat": {"persona": "teammate"}},
            }
        }
        assert resolve_persona(project, "Team Chat", is_dm=False) == PersonaType.TEAMMATE

    def test_engineer_persona_resolves(self):
        project = {
            "telegram": {
                "groups": {"Eng: MyProject": {"persona": "engineer"}},
            }
        }
        assert resolve_persona(project, "Eng: MyProject", is_dm=False) == PersonaType.ENGINEER

    def test_case_insensitive_group_matching(self):
        project = {
            "telegram": {
                "groups": {"team chat": {"persona": "teammate"}},
            }
        }
        assert resolve_persona(project, "Team Chat", is_dm=False) == PersonaType.TEAMMATE

    def test_partial_group_name_matching(self):
        """Group name is a substring of chat title."""
        project = {
            "telegram": {
                "groups": {"MyProject": {"persona": "teammate"}},
            }
        }
        assert (
            resolve_persona(project, "Team: MyProject Discussion", is_dm=False)
            == PersonaType.TEAMMATE
        )


# =============================================================================
# Title prefix fallback (Eng: is the canonical prefix post-consolidation)
# =============================================================================


class TestTitlePrefixFallback:
    def test_eng_prefix_returns_engineer(self):
        assert resolve_persona(None, "Eng: MyProject", is_dm=False) == PersonaType.ENGINEER

    def test_no_prefix_no_config_returns_none(self):
        assert resolve_persona(None, "Random Group", is_dm=False) is None

    def test_eng_prefix_with_empty_project(self):
        project = {"telegram": {"groups": {}}}
        assert resolve_persona(project, "Eng: MyProject", is_dm=False) == PersonaType.ENGINEER

    def test_legacy_dev_prefix_returns_none(self):
        """Legacy 'Dev:' prefix is no longer recognized -- returns None."""
        assert resolve_persona(None, "Dev: MyProject", is_dm=False) is None

    def test_legacy_pm_prefix_returns_none(self):
        """Legacy 'PM:' prefix is no longer recognized -- returns None."""
        assert resolve_persona(None, "PM: MyProject", is_dm=False) is None


# =============================================================================
# Unconfigured groups fall through to None
# =============================================================================


class TestUnconfigured:
    def test_no_project_no_prefix(self):
        assert resolve_persona(None, "General Chat", is_dm=False) is None

    def test_project_without_matching_group(self):
        project = {
            "telegram": {
                "groups": {"Other Group": {"persona": "engineer"}},
            }
        }
        assert resolve_persona(project, "Unrelated Chat", is_dm=False) is None

    def test_no_chat_title_not_dm(self):
        assert resolve_persona(None, None, is_dm=False) is None


# =============================================================================
# Edge cases -- invalid/empty persona values
# =============================================================================


class TestEdgeCases:
    def test_empty_persona_falls_through_to_prefix(self):
        """Empty persona string should not match, fall through to title prefix."""
        project = {
            "telegram": {
                "groups": {"Eng: MyProject": {"persona": ""}},
            }
        }
        # Empty persona -> ValueError -> falls through to title prefix
        assert resolve_persona(project, "Eng: MyProject", is_dm=False) == PersonaType.ENGINEER

    def test_unknown_persona_falls_through(self):
        """Unknown persona value not in PersonaType -> fall through."""
        project = {
            "telegram": {
                "groups": {"SomeGroup": {"persona": "unknown-role"}},
            }
        }
        assert resolve_persona(project, "SomeGroup", is_dm=False) is None

    def test_group_config_is_not_dict(self):
        """Legacy format where group config is just a string."""
        project = {
            "telegram": {
                "groups": {"SomeGroup": "developer"},
            }
        }
        assert resolve_persona(project, "SomeGroup", is_dm=False) is None

    def test_groups_is_list_not_dict(self):
        """Groups as list (old format) -- no persona lookup possible."""
        project = {
            "telegram": {
                "groups": ["Group A", "Group B"],
            }
        }
        assert resolve_persona(project, "Group A", is_dm=False) is None

    def test_no_telegram_config(self):
        project = {}
        assert resolve_persona(project, "SomeGroup", is_dm=False) is None

    def test_persona_config_takes_priority_over_title_prefix(self):
        """If an Eng: group has teammate persona, persona wins."""
        project = {
            "telegram": {
                "groups": {"Eng: MyProject": {"persona": "teammate"}},
            }
        }
        assert resolve_persona(project, "Eng: MyProject", is_dm=False) == PersonaType.TEAMMATE
