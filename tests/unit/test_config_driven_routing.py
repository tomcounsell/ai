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

    def test_pm_persona_gives_pm_session(self):
        persona = resolve_persona(None, "PM: X", is_dm=False)
        assert persona == PersonaType.PROJECT_MANAGER
        # In telegram_bridge.py: PM persona -> session_type = "pm"

    def test_teammate_persona_gives_teammate_session(self):
        persona = resolve_persona(None, None, is_dm=True)
        assert persona == PersonaType.TEAMMATE
        # In telegram_bridge.py: Teammate persona -> session_type = "teammate"

    def test_none_persona_gives_pm_session(self):
        persona = resolve_persona(None, "Random Group", is_dm=False)
        assert persona is None
        # In telegram_bridge.py: None persona -> session_type = "pm"


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


# =============================================================================
# #996: Reply to any thread message should set is_reply_to_valor=True
# =============================================================================


class TestReplyToAnyThreadMessage:
    """Reply to any message in a thread (not just Valor's) should trigger session continuation."""

    def _project(self):
        return {
            "telegram": {
                "respond_to_all": True,
            }
        }

    @pytest.mark.asyncio
    async def test_reply_to_own_message_sets_is_reply_flag(self):
        """Reply to a non-Valor (own) message should return is_reply=True (#996)."""
        project = self._project()
        event = _make_event("follow-up thought", reply_to_msg_id=555)
        # replied_msg.out = False → message was NOT sent by Valor
        client = _make_client(replied_msg_is_ours=False)

        should, is_reply = await should_respond_async(
            client,
            event,
            "follow-up thought",
            is_dm=False,
            chat_title="Dev: Project",
            project=project,
        )
        assert should is True
        assert is_reply is True, "Reply to own message must set is_reply_to_valor=True"

    @pytest.mark.asyncio
    async def test_dm_reply_sets_is_reply_flag(self):
        """Reply in a DM thread should return is_reply=True (#996)."""
        project = self._project()
        event = _make_event("continued thought", reply_to_msg_id=777)
        client = _make_client(replied_msg_is_ours=False)

        should, is_reply = await should_respond_async(
            client,
            event,
            "continued thought",
            is_dm=True,
            chat_title=None,
            project=project,
        )
        # DM always responds; reply flag should now be True
        assert is_reply is True, "DM reply must set is_reply_to_valor=True"

    @pytest.mark.asyncio
    async def test_dm_without_reply_does_not_set_flag(self):
        """A fresh DM (no reply_to_msg_id) should return is_reply=False."""
        project = self._project()
        event = _make_event("brand new message", reply_to_msg_id=None)
        client = _make_client(replied_msg_is_ours=False)

        should, is_reply = await should_respond_async(
            client,
            event,
            "brand new message",
            is_dm=True,
            chat_title=None,
            project=project,
        )
        assert is_reply is False, "Fresh DM with no reply_to must not set is_reply_to_valor"

    @pytest.mark.asyncio
    async def test_reply_to_non_valor_in_team_chat_is_silent(self):
        """Reply to a non-Valor message in a team chat without mention must not respond.

        Regression guard: the `elif replied_msg` branch used to short-circuit with
        `return True, True`, bypassing the mention-only gate for team chats
        (no Dev:/PM: prefix). That caused Valor to reply to unrelated threads.
        """
        project = self._project()
        event = _make_event("replying to my own note", reply_to_msg_id=555)
        client = _make_client(replied_msg_is_ours=False)

        should, _is_reply = await should_respond_async(
            client,
            event,
            "replying to my own note",
            is_dm=False,
            chat_title="Agent Builders Chat",  # no Dev:/PM: prefix → team chat
            project=project,
        )
        assert should is False, (
            "Reply to non-Valor message in a team chat must not trigger a response "
            "without an @mention"
        )

    @pytest.mark.asyncio
    async def test_reply_to_non_valor_in_team_chat_with_mention_continues_session(self):
        """@mention + reply-to-non-Valor in team chat should respond AND flag continuation."""
        project = {
            "telegram": {
                "respond_to_all": True,
                "mention_triggers": ["@valor", "valor"],
            }
        }
        event = _make_event("hey valor what about this?", reply_to_msg_id=555)
        client = _make_client(replied_msg_is_ours=False)

        should, is_reply = await should_respond_async(
            client,
            event,
            "hey valor what about this?",
            is_dm=False,
            chat_title="Agent Builders Chat",
            project=project,
        )
        assert should is True
        assert is_reply is True, (
            "Mention-triggered response to a non-Valor reply must preserve session continuation"
        )

    @pytest.mark.asyncio
    async def test_reply_to_non_valor_in_teammate_group_is_silent(self):
        """Reply-to-non-Valor in a Teammate-persona group without mention must be silent."""
        project = {
            "telegram": {
                "groups": {"Team Chat": {"persona": "teammate"}},
                "mention_triggers": ["@valor", "valor"],
                "respond_to_all": True,
            }
        }
        event = _make_event("replying to someone else", reply_to_msg_id=555)
        client = _make_client(replied_msg_is_ours=False)

        should, _is_reply = await should_respond_async(
            client,
            event,
            "replying to someone else",
            is_dm=False,
            chat_title="Team Chat",
            project=project,
        )
        assert should is False

    @pytest.mark.asyncio
    async def test_reply_to_valor_still_works(self):
        """Reply to Valor's own message continues to work as before (#996 non-regression)."""
        project = self._project()
        event = _make_event("can you elaborate?", reply_to_msg_id=888)
        client = _make_client(replied_msg_is_ours=True)

        # We need classify_conversation_terminus to return RESPOND — mock it
        import bridge.routing as routing_mod

        original = routing_mod.classify_conversation_terminus

        async def _mock_terminus(**kwargs):
            return "RESPOND"

        routing_mod.classify_conversation_terminus = _mock_terminus
        try:
            should, is_reply = await should_respond_async(
                client,
                event,
                "can you elaborate?",
                is_dm=False,
                chat_title="Dev: Project",
                project=project,
            )
        finally:
            routing_mod.classify_conversation_terminus = original

        assert should is True
        assert is_reply is True


# =============================================================================
# #997: Sentinel guard prevents double-dispatch on steering-check exception
# =============================================================================


class TestSteeringCheckSentinel:
    """_steering_session_enqueued sentinel must be present in the steering check block."""

    def test_sentinel_variable_initialized_in_steering_block(self):
        """Verify the _steering_session_enqueued sentinel is present in telegram_bridge.py."""
        import ast
        from pathlib import Path

        bridge_src = Path("bridge/telegram_bridge.py").read_text()
        tree = ast.parse(bridge_src)

        sentinel_found = False
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in targets:
                    if isinstance(t, ast.Name) and t.id == "_steering_session_enqueued":
                        sentinel_found = True
                        break

        assert sentinel_found, (
            "_steering_session_enqueued sentinel not found in bridge/telegram_bridge.py. "
            "This guard prevents duplicate session enqueue (#997)."
        )

    def test_sentinel_set_to_true_after_dispatch(self):
        """After dispatch_telegram_session, sentinel must be set True before return."""
        from pathlib import Path

        bridge_src = Path("bridge/telegram_bridge.py").read_text()
        # _steering_session_enqueued = True must appear after dispatch_telegram_session
        dispatch_pos = bridge_src.find(
            "dispatch_telegram_session(", bridge_src.find("completed_sessions")
        )
        sentinel_pos = bridge_src.find("_steering_session_enqueued = True", dispatch_pos)
        assert sentinel_pos != -1, (
            "_steering_session_enqueued must be set to True after dispatch_telegram_session "
            "in the completed-session resume branch (#997)."
        )

    def test_exception_handlers_guard_against_fallthrough(self):
        """Both exception handlers must check _steering_session_enqueued before falling through."""
        from pathlib import Path

        bridge_src = Path("bridge/telegram_bridge.py").read_text()
        # Find the two except blocks in the steering check and verify the guard
        steering_block = bridge_src[
            bridge_src.find("_steering_session_enqueued = False") : bridge_src.find(
                "IN-MEMORY COALESCING GUARD"
            )
        ]
        guard_count = steering_block.count("if _steering_session_enqueued:")
        assert guard_count >= 2, (
            f"Expected at least 2 '_steering_session_enqueued' guards in exception handlers "
            f"(ConnectionError and Exception), found {guard_count} (#997)."
        )
