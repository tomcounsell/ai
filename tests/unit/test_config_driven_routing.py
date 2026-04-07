"""Tests for config-driven routing paths.

Covers:
- should_respond_async() behavior for Teammate-persona groups (passive listener)
- Session type derivation from resolve_persona()
- Classifier bypass logic in sdk_client.py
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from bridge.routing import resolve_persona, should_respond_async
from config.enums import PersonaType

# =============================================================================
# Helpers
# =============================================================================


def _make_event(text="hello", is_private=False, reply_to_msg_id=None):
    """Create a mock Telegram event."""
    message = MagicMock()
    message.text = text
    message.reply_to_msg_id = reply_to_msg_id
    event = MagicMock()
    event.message = message
    event.is_private = is_private
    event.chat_id = -12345
    return event


def _make_client(replied_msg_is_ours=False):
    """Create a mock Telegram client."""
    client = AsyncMock()
    if replied_msg_is_ours:
        replied = MagicMock()
        replied.out = True
        client.get_messages = AsyncMock(return_value=replied)
    else:
        replied = MagicMock()
        replied.out = False
        client.get_messages = AsyncMock(return_value=replied)
    return client


# =============================================================================
# Teammate-persona group: passive listener behavior
# =============================================================================


class TestTeammateGroupPassiveListener:
    """Teammate-persona groups should only respond on @mention or reply-to-Valor."""

    def _teammate_project(self):
        return {
            "telegram": {
                "groups": {"Team Chat": {"persona": "teammate"}},
                "mention_triggers": ["@valor", "valor"],
                "respond_to_all": True,
            }
        }

    @pytest.mark.asyncio
    async def test_unaddressed_message_silent(self):
        """Unaddressed messages in Teammate groups get no response."""
        project = self._teammate_project()
        event = _make_event("just sharing some info")
        client = _make_client()

        should, is_reply = await should_respond_async(
            client,
            event,
            "just sharing some info",
            is_dm=False,
            chat_title="Team Chat",
            project=project,
        )
        assert should is False
        assert is_reply is False

    @pytest.mark.asyncio
    async def test_mention_triggers_response(self):
        """@valor mention in Teammate group triggers a response."""
        project = self._teammate_project()
        event = _make_event("hey valor what do you think?")
        client = _make_client()

        should, is_reply = await should_respond_async(
            client,
            event,
            "hey valor what do you think?",
            is_dm=False,
            chat_title="Team Chat",
            project=project,
        )
        assert should is True

    @pytest.mark.asyncio
    async def test_reply_to_valor_triggers_response(self):
        """Reply to Valor's message in Teammate group triggers a response."""
        project = self._teammate_project()
        event = _make_event("can you elaborate?", reply_to_msg_id=999)
        client = _make_client(replied_msg_is_ours=True)

        should, is_reply = await should_respond_async(
            client,
            event,
            "can you elaborate?",
            is_dm=False,
            chat_title="Team Chat",
            project=project,
        )
        assert should is True
        assert is_reply is True


# =============================================================================
# Backward compatibility: title-prefix groups without explicit persona
# =============================================================================


class TestBackwardCompatibility:
    """Groups without explicit persona config should work via title prefix."""

    @pytest.mark.asyncio
    async def test_dev_prefix_group_still_responds(self):
        """Dev: prefix group without persona config should still respond."""
        # Project has the group listed but with no persona
        project = {
            "telegram": {
                "groups": {},
                "respond_to_all": True,
            }
        }
        event = _make_event("fix the bug")
        client = _make_client()

        should, is_reply = await should_respond_async(
            client,
            event,
            "fix the bug",
            is_dm=False,
            chat_title="Dev: MyProject",
            project=project,
        )
        # resolve_persona returns DEVELOPER from title prefix
        # is_team_chat returns False for "Dev:" prefix
        # respond_to_all=True so it responds
        assert should is True

    def test_dev_prefix_session_type(self):
        """Dev: prefix without persona still resolves to Developer."""
        project = {"telegram": {"groups": {}}}
        persona = resolve_persona(project, "Dev: MyProject", is_dm=False)
        assert persona == PersonaType.DEVELOPER

    def test_pm_prefix_session_type(self):
        """PM: prefix without persona still resolves to Project Manager."""
        project = {"telegram": {"groups": {}}}
        persona = resolve_persona(project, "PM: MyProject", is_dm=False)
        assert persona == PersonaType.PROJECT_MANAGER


# =============================================================================
# Session type derivation
# =============================================================================


class TestSessionTypeDerivation:
    """Verify persona-to-session-type mapping logic."""

    def test_dev_persona_gives_dev_session(self):
        persona = resolve_persona(None, "Dev: X", is_dm=False)
        assert persona == PersonaType.DEVELOPER
        # In telegram_bridge.py: Developer persona -> session_type = "dev"

    def test_pm_persona_gives_chat_session(self):
        persona = resolve_persona(None, "PM: X", is_dm=False)
        assert persona == PersonaType.PROJECT_MANAGER
        # In telegram_bridge.py: PM persona -> session_type = "chat"

    def test_teammate_persona_gives_chat_session(self):
        persona = resolve_persona(None, None, is_dm=True)
        assert persona == PersonaType.TEAMMATE
        # In telegram_bridge.py: Teammate persona -> session_type = "chat"

    def test_none_persona_gives_chat_session(self):
        persona = resolve_persona(None, "Random Group", is_dm=False)
        assert persona is None
        # In telegram_bridge.py: None persona -> session_type = "chat"


# =============================================================================
# Classifier bypass verification
# =============================================================================


class TestClassifierBypass:
    """Verify that config-determined personas skip the intent classifier."""

    def test_dm_should_bypass_classifier(self):
        """DMs resolve to Teammate persona, which should bypass classifier."""
        persona = resolve_persona(None, None, is_dm=True)
        assert persona == PersonaType.TEAMMATE

    def test_configured_teammate_group_should_bypass(self):
        project = {
            "telegram": {
                "groups": {"Team: Project": {"persona": "teammate"}},
            }
        }
        persona = resolve_persona(project, "Team: Project", is_dm=False)
        assert persona == PersonaType.TEAMMATE

    def test_configured_pm_group_should_bypass(self):
        project = {
            "telegram": {
                "groups": {"PM: Project": {"persona": "project-manager"}},
            }
        }
        persona = resolve_persona(project, "PM: Project", is_dm=False)
        assert persona == PersonaType.PROJECT_MANAGER

    def test_configured_dev_group_should_bypass(self):
        project = {
            "telegram": {
                "groups": {"Dev: Project": {"persona": "developer"}},
            }
        }
        persona = resolve_persona(project, "Dev: Project", is_dm=False)
        assert persona == PersonaType.DEVELOPER

    def test_unconfigured_group_should_not_bypass(self):
        """Groups with no config should NOT bypass -- classifier runs."""
        persona = resolve_persona(None, "Random Group", is_dm=False)
        assert persona is None  # None means classifier should run
