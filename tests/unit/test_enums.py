"""Tests for config/enums.py StrEnum definitions.

Verifies string equality, membership, iteration, and backward compatibility
with existing string-based comparisons throughout the codebase.
"""

from config.enums import ClassificationType, PersonaType, SessionType


class TestSessionType:
    def test_string_equality(self):
        assert SessionType.CHAT == "chat"
        assert SessionType.DEV == "dev"

    def test_str_conversion(self):
        assert str(SessionType.CHAT) == "chat"
        assert str(SessionType.DEV) == "dev"

    def test_membership(self):
        assert "chat" in [SessionType.CHAT, SessionType.DEV]
        assert "dev" in [SessionType.CHAT, SessionType.DEV]
        assert "invalid" not in [SessionType.CHAT, SessionType.DEV]

    def test_iteration(self):
        members = list(SessionType)
        assert len(members) == 2
        assert SessionType.CHAT in members
        assert SessionType.DEV in members

    def test_value_access(self):
        assert SessionType.CHAT.value == "chat"
        assert SessionType.DEV.value == "dev"

    def test_construction_from_string(self):
        assert SessionType("chat") == SessionType.CHAT
        assert SessionType("dev") == SessionType.DEV

    def test_invalid_construction_raises(self):
        import pytest

        with pytest.raises(ValueError):
            SessionType("invalid")

    def test_backward_compat_with_constants(self):
        """SESSION_TYPE_CHAT/DEV aliases in agent_session.py should match."""
        from models.agent_session import SESSION_TYPE_CHAT, SESSION_TYPE_DEV

        assert SESSION_TYPE_CHAT == SessionType.CHAT
        assert SESSION_TYPE_DEV == SessionType.DEV
        assert SESSION_TYPE_CHAT is SessionType.CHAT
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
        assert ClassificationType.QUESTION == "question"

    def test_all_members(self):
        assert len(list(ClassificationType)) == 2


class TestEnvVarCompatibility:
    """Verify enums work correctly when used in env var contexts."""

    def test_session_type_in_env_var_comparison(self):
        """os.environ values are strings; enum must compare equal."""
        env_value = "chat"  # Simulates os.environ.get("SESSION_TYPE")
        assert env_value == SessionType.CHAT

    def test_str_enum_in_dict_key(self):
        """StrEnum members work as dict keys matching string keys."""
        d = {"chat": "pm_persona", "dev": "dev_persona"}
        assert d[SessionType.CHAT] == "pm_persona"
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
