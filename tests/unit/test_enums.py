"""Tests for config/enums.py StrEnum definitions.

Verifies string equality, membership, iteration, and backward compatibility
with existing string-based comparisons throughout the codebase.
"""

import pytest

from config.enums import ClassificationType, PersonaType, SessionType


class TestSessionType:
    def test_string_equality(self):
        assert SessionType.ENG == "eng"
        assert SessionType.TEAMMATE == "teammate"
        assert SessionType.GRANITE == "granite"

    def test_str_conversion(self):
        assert str(SessionType.ENG) == "eng"
        assert str(SessionType.TEAMMATE) == "teammate"
        assert str(SessionType.GRANITE) == "granite"

    def test_membership(self):
        assert "eng" in [SessionType.ENG, SessionType.TEAMMATE, SessionType.GRANITE]
        assert "teammate" in [SessionType.ENG, SessionType.TEAMMATE, SessionType.GRANITE]
        assert "granite" in [SessionType.ENG, SessionType.TEAMMATE, SessionType.GRANITE]
        assert "invalid" not in [SessionType.ENG, SessionType.TEAMMATE, SessionType.GRANITE]

    def test_iteration(self):
        members = list(SessionType)
        assert len(members) == 3
        assert SessionType.ENG in members
        assert SessionType.TEAMMATE in members
        assert SessionType.GRANITE in members

    def test_value_access(self):
        assert SessionType.ENG.value == "eng"
        assert SessionType.TEAMMATE.value == "teammate"
        assert SessionType.GRANITE.value == "granite"

    def test_construction_from_string(self):
        assert SessionType("eng") == SessionType.ENG
        assert SessionType("teammate") == SessionType.TEAMMATE
        assert SessionType("granite") == SessionType.GRANITE

    def test_invalid_construction_raises(self):
        with pytest.raises(ValueError):
            SessionType("invalid")

        with pytest.raises(ValueError):
            SessionType("pm")  # Old PM value no longer valid

        with pytest.raises(ValueError):
            SessionType("dev")  # Old DEV value no longer valid

    def test_pm_and_dev_no_longer_exist(self):
        """SessionType.PM and SessionType.DEV must not exist after the ENG consolidation."""
        assert not hasattr(SessionType, "PM"), "SessionType.PM must have been removed"
        assert not hasattr(SessionType, "DEV"), "SessionType.DEV must have been removed"

    def test_backward_compat_with_constants(self):
        """SESSION_TYPE_ENG alias in agent_session.py should match."""
        from models.agent_session import SESSION_TYPE_ENG

        assert SESSION_TYPE_ENG == SessionType.ENG
        assert SESSION_TYPE_ENG is SessionType.ENG

    def test_old_pm_dev_aliases_do_not_exist(self):
        """SESSION_TYPE_PM and SESSION_TYPE_DEV must not exist after the ENG consolidation."""
        import models.agent_session as m

        assert not hasattr(m, "SESSION_TYPE_PM"), "SESSION_TYPE_PM must have been removed"
        assert not hasattr(m, "SESSION_TYPE_DEV"), "SESSION_TYPE_DEV must have been removed"


class TestPersonaType:
    def test_string_equality(self):
        assert PersonaType.ENGINEER == "engineer"
        assert PersonaType.TEAMMATE == "teammate"
        assert PersonaType.CUSTOMER_SERVICE == "customer-service"

    def test_all_members(self):
        assert len(list(PersonaType)) == 3

    def test_developer_and_project_manager_no_longer_exist(self):
        """PersonaType.DEVELOPER and PersonaType.PROJECT_MANAGER must not exist."""
        assert not hasattr(PersonaType, "DEVELOPER"), "PersonaType.DEVELOPER must have been removed"
        assert not hasattr(PersonaType, "PROJECT_MANAGER"), (
            "PersonaType.PROJECT_MANAGER must have been removed"
        )


class TestClassificationType:
    def test_string_equality(self):
        assert ClassificationType.SDLC == "sdlc"
        assert ClassificationType.COLLABORATION == "collaboration"
        assert ClassificationType.OTHER == "other"
        assert ClassificationType.QUESTION == "question"

    def test_all_members(self):
        assert len(list(ClassificationType)) == 4

    def test_construction_from_string(self):
        assert ClassificationType("collaboration") == ClassificationType.COLLABORATION
        assert ClassificationType("other") == ClassificationType.OTHER


class TestEnvVarCompatibility:
    """Verify enums work correctly when used in env var contexts."""

    def test_session_type_in_env_var_comparison(self):
        """os.environ values are strings; enum must compare equal."""
        env_value = "eng"  # Simulates os.environ.get("SESSION_TYPE")
        assert env_value == SessionType.ENG

    def test_str_enum_in_dict_key(self):
        """StrEnum members work as dict keys matching string keys."""
        d = {"eng": "eng_persona", "teammate": "teammate_persona", "granite": "granite_persona"}
        assert d[SessionType.ENG] == "eng_persona"
        assert d[SessionType.TEAMMATE] == "teammate_persona"
        assert d[SessionType.GRANITE] == "granite_persona"

    def test_persona_in_config_lookup(self):
        """PersonaType members match string keys from projects.json config."""
        persona_map = {
            PersonaType.TEAMMATE: "teammate",
            PersonaType.ENGINEER: "engineer",
        }
        # Lookup with string (simulating config value)
        assert persona_map.get("teammate") == "teammate"
        assert persona_map.get("engineer") == "engineer"
