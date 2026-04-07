"""Tests for config-driven routing paths.

Covers:
- should_respond_async() behavior for Q&A-persona groups (passive listener)
- Session type derivation from resolve_chat_mode()
- Classifier bypass logic in sdk_client.py
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from bridge.routing import resolve_chat_mode, should_respond_async

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
# Q&A-persona group: passive listener behavior
# =============================================================================


class TestQAGroupPassiveListener:
    """Q&A-persona groups should only respond on @mention or reply-to-Valor."""

    def _qa_project(self):
        return {
            "telegram": {
                "groups": {"Team Chat": {"persona": "teammate"}},
                "mention_triggers": ["@valor", "valor"],
                "respond_to_all": True,
            }
        }

    @pytest.mark.asyncio
    async def test_unaddressed_message_silent(self):
        """Unaddressed messages in Q&A groups get no response."""
        project = self._qa_project()
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
        """@valor mention in Q&A group triggers a response."""
        project = self._qa_project()
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
        """Reply to Valor's message in Q&A group triggers a response."""
        project = self._qa_project()
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
        # resolve_chat_mode returns "dev" from title prefix
        # is_team_chat returns False for "Dev:" prefix
        # respond_to_all=True so it responds
        assert should is True

    def test_dev_prefix_session_type(self):
        """Dev: prefix without persona still resolves to dev mode."""
        project = {"telegram": {"groups": {}}}
        mode = resolve_chat_mode(project, "Dev: MyProject", is_dm=False)
        assert mode == "dev"

    def test_pm_prefix_session_type(self):
        """PM: prefix without persona still resolves to pm mode."""
        project = {"telegram": {"groups": {}}}
        mode = resolve_chat_mode(project, "PM: MyProject", is_dm=False)
        assert mode == "pm"


# =============================================================================
# Session type derivation
# =============================================================================


class TestSessionTypeDerivation:
    """Verify mode-to-session-type mapping logic."""

    def test_dev_mode_gives_dev_session(self):
        mode = resolve_chat_mode(None, "Dev: X", is_dm=False)
        assert mode == "dev"
        # In telegram_bridge.py: dev mode -> session_type = "dev"

    def test_pm_mode_gives_chat_session(self):
        mode = resolve_chat_mode(None, "PM: X", is_dm=False)
        assert mode == "pm"
        # In telegram_bridge.py: pm mode -> session_type = "chat"

    def test_qa_mode_gives_chat_session(self):
        mode = resolve_chat_mode(None, None, is_dm=True)
        assert mode == "qa"
        # In telegram_bridge.py: qa mode -> session_type = "chat"

    def test_none_mode_gives_chat_session(self):
        mode = resolve_chat_mode(None, "Random Group", is_dm=False)
        assert mode is None
        # In telegram_bridge.py: None mode -> session_type = "chat"


# =============================================================================
# Classifier bypass verification
# =============================================================================


class TestClassifierBypass:
    """Verify that config-determined modes skip the intent classifier."""

    def test_dm_should_bypass_classifier(self):
        """DMs resolve to qa mode, which should bypass classifier."""
        mode = resolve_chat_mode(None, None, is_dm=True)
        assert mode == "qa"  # qa mode triggers classifier bypass in sdk_client

    def test_configured_qa_group_should_bypass(self):
        project = {
            "telegram": {
                "groups": {"Team: Project": {"persona": "teammate"}},
            }
        }
        mode = resolve_chat_mode(project, "Team: Project", is_dm=False)
        assert mode == "qa"  # qa mode triggers classifier bypass

    def test_configured_pm_group_should_bypass(self):
        project = {
            "telegram": {
                "groups": {"PM: Project": {"persona": "project-manager"}},
            }
        }
        mode = resolve_chat_mode(project, "PM: Project", is_dm=False)
        assert mode == "pm"  # pm mode triggers classifier bypass

    def test_configured_dev_group_should_bypass(self):
        project = {
            "telegram": {
                "groups": {"Dev: Project": {"persona": "developer"}},
            }
        }
        mode = resolve_chat_mode(project, "Dev: Project", is_dm=False)
        assert mode == "dev"  # dev mode triggers classifier bypass

    def test_unconfigured_group_should_not_bypass(self):
        """Groups with no config should NOT bypass -- classifier runs."""
        mode = resolve_chat_mode(None, "Random Group", is_dm=False)
        assert mode is None  # None means classifier should run
