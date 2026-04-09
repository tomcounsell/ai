"""Tests for config/enums.py StrEnum definitions.

Verifies string equality, membership, iteration, and backward compatibility
with existing string-based comparisons throughout the codebase.
"""

from config.enums import ClassificationType, PersonaType, SessionType


class TestSessionType:
    def test_string_equality(self):
        assert SessionType.PM == "pm"
        assert SessionType.TEAMMATE == "teammate"
        assert SessionType.DEV == "dev"

    def test_str_conversion(self):
        assert str(SessionType.PM) == "pm"
        assert str(SessionType.TEAMMATE) == "teammate"
        assert str(SessionType.DEV) == "dev"

    def test_membership(self):
        assert "pm" in [SessionType.PM, SessionType.TEAMMATE, SessionType.DEV]
        assert "teammate" in [SessionType.PM, SessionType.TEAMMATE, SessionType.DEV]
        assert "dev" in [SessionType.PM, SessionType.TEAMMATE, SessionType.DEV]
        assert "invalid" not in [SessionType.PM, SessionType.TEAMMATE, SessionType.DEV]

    def test_iteration(self):
        members = list(SessionType)
        assert len(members) == 3
        assert SessionType.PM in members
        assert SessionType.TEAMMATE in members
        assert SessionType.DEV in members

    def test_value_access(self):
        assert SessionType.PM.value == "pm"
        assert SessionType.TEAMMATE.value == "teammate"
        assert SessionType.DEV.value == "dev"

    def test_construction_from_string(self):
        assert SessionType("pm") == SessionType.PM
        assert SessionType("teammate") == SessionType.TEAMMATE
        assert SessionType("dev") == SessionType.DEV

    def test_invalid_construction_raises(self):
        import pytest

        with pytest.raises(ValueError):
            SessionType("invalid")

        with pytest.raises(ValueError):
            SessionType("chat")  # Old value no longer valid

    def test_backward_compat_with_constants(self):
        """SESSION_TYPE_PM/DEV aliases in agent_session.py should match."""
        from models.agent_session import SESSION_TYPE_DEV, SESSION_TYPE_PM

        assert SESSION_TYPE_PM == SessionType.PM
        assert SESSION_TYPE_DEV == SessionType.DEV
        assert SESSION_TYPE_PM is SessionType.PM
        assert SESSION_TYPE_DEV is SessionType.DEV


class TestPersonaType:
    def test_string_equality(self):
        assert PersonaType.DEVELOPER == "developer"
        assert PersonaType.PROJECT_MANAGER == "project-manager"
        assert PersonaType.TEAMMATE == "teammate"

    def test_all_members(self):
        assert len(list(PersonaType)) == 3


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
        env_value = "pm"  # Simulates os.environ.get("SESSION_TYPE")
        assert env_value == SessionType.PM

    def test_str_enum_in_dict_key(self):
        """StrEnum members work as dict keys matching string keys."""
        d = {"pm": "pm_persona", "teammate": "teammate_persona", "dev": "dev_persona"}
        assert d[SessionType.PM] == "pm_persona"
        assert d[SessionType.TEAMMATE] == "teammate_persona"
        assert d[SessionType.DEV] == "dev_persona"

    def test_persona_in_config_lookup(self):
        """PersonaType members match string keys from projects.json config."""
        persona_map = {
            PersonaType.TEAMMATE: "teammate",
            PersonaType.PROJECT_MANAGER: "project-manager",
            PersonaType.DEVELOPER: "developer",
        }
        # Lookup with string (simulating config value)
        assert persona_map.get("teammate") == "teammate"
        assert persona_map.get("project-manager") == "project-manager"
        assert persona_map.get("developer") == "developer"
