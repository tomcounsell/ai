"""Unit tests for _append_outbound_chat_log in bridge/telegram_relay.py (issue #1192).

Tests the three-tier session resolution:
  1. owner_agent_session_id in payload (Path B)
  2. real session_id without cli-/local- prefix (Path A)
  3. fallback to get_active_session_for_chat(chat_id)

Also tests:
- Exception during lookup or append does not crash the caller (non-fatal)
- Empty/None text is skipped (no log pollution from voice-note-only sends)
"""

from unittest.mock import MagicMock, patch

import pytest

from bridge.telegram_relay import _append_outbound_chat_log


def _make_session(session_id="sess-abc"):
    session = MagicMock()
    session.session_id = session_id
    return session


class TestAppendOutboundChatLogTierOne:
    """Tier 1: owner_agent_session_id resolves the owning session."""

    def test_owner_agent_session_id_is_used_when_present(self):
        """When payload has owner_agent_session_id, that session gets the log entry."""
        session = _make_session("owner-session-123")
        mock_qs = MagicMock()
        mock_qs.filter.return_value = [session]

        mock_agent_session_cls = MagicMock()
        mock_agent_session_cls.query = mock_qs

        with patch.dict("sys.modules", {"models.agent_session": MagicMock(AgentSession=mock_agent_session_cls)}):
            # Re-import inside the patch so the lazy import in _append_outbound_chat_log
            # gets our mock. Since the function uses a local import, we patch the module.
            import importlib
            import bridge.telegram_relay as relay_mod
            original = relay_mod._append_outbound_chat_log

            # Call with a known mock path: patch at the models level
            with patch("models.agent_session.AgentSession", mock_agent_session_cls):
                _append_outbound_chat_log(
                    {
                        "text": "Hello world",
                        "chat_id": "12345",
                        "session_id": "cli-9999",
                        "owner_agent_session_id": "owner-session-123",
                    },
                    msg_id=42,
                )

        session.append_chat_log.assert_called_once_with(
            direction="out",
            sender="valor",
            content="Hello world",
            message_id=42,
        )


class TestAppendOutboundChatLogTierTwo:
    """Tier 2: real session_id (no cli-/local- prefix) resolves the owning session."""

    def test_real_session_id_without_prefix_is_used(self):
        """A session_id without cli-/local- prefix is used as-is to look up the session."""
        session = _make_session("pm-session-xyz")
        mock_agent_session_cls = MagicMock()

        def mock_filter(**kwargs):
            session_id_val = kwargs.get("session_id", "")
            if session_id_val == "pm-session-xyz":
                return [session]
            return []

        mock_agent_session_cls.query.filter.side_effect = mock_filter
        mock_agent_session_cls.query.all.return_value = []

        with patch("models.agent_session.AgentSession", mock_agent_session_cls):
            _append_outbound_chat_log(
                {
                    "text": "From relay",
                    "chat_id": "99999",
                    "session_id": "pm-session-xyz",
                },
                msg_id=55,
            )

        session.append_chat_log.assert_called_once_with(
            direction="out",
            sender="valor",
            content="From relay",
            message_id=55,
        )

    def test_cli_prefixed_session_id_does_not_trigger_tier_two_lookup(self):
        """A session_id starting with 'cli-' is skipped in tier-2 and falls to tier-3."""
        fallback_session = _make_session("active-session")
        mock_agent_session_cls = MagicMock()

        def mock_filter(**kwargs):
            # Tier 3: chat_id lookup for running sessions returns the fallback
            chat_id_val = kwargs.get("chat_id")
            status_val = kwargs.get("status")
            if chat_id_val == "11111" and status_val == "running":
                return [fallback_session]
            return []

        mock_agent_session_cls.query.filter.side_effect = mock_filter
        mock_agent_session_cls.query.all.return_value = []

        with patch("models.agent_session.AgentSession", mock_agent_session_cls):
            _append_outbound_chat_log(
                {
                    "text": "CLI send",
                    "chat_id": "11111",
                    "session_id": "cli-1234567890",
                },
                msg_id=77,
            )

        # Tier 3 (fallback) session gets the call
        fallback_session.append_chat_log.assert_called_once()
        # Tier-2 filter should NOT have been called with the cli- session_id
        for call_obj in mock_agent_session_cls.query.filter.call_args_list:
            session_id_arg = call_obj[1].get("session_id", "")
            assert not str(session_id_arg).startswith("cli-"), (
                f"Tier-2 filter was called with cli- session_id: {session_id_arg!r}"
            )


class TestAppendOutboundChatLogTierThree:
    """Tier 3: get_active_session_for_chat fallback."""

    def test_fallback_to_running_session_for_chat(self):
        """When tiers 1 and 2 fail, tier-3 looks up a running session by chat_id."""
        fallback_session = _make_session("active-session-fallback")
        mock_agent_session_cls = MagicMock()

        def mock_filter(**kwargs):
            chat_id_val = kwargs.get("chat_id")
            status_val = kwargs.get("status")
            if chat_id_val == "44444" and status_val == "running":
                return [fallback_session]
            return []

        mock_agent_session_cls.query.filter.side_effect = mock_filter
        mock_agent_session_cls.query.all.return_value = []

        with patch("models.agent_session.AgentSession", mock_agent_session_cls):
            _append_outbound_chat_log(
                {
                    "text": "Fallback path",
                    "chat_id": "44444",
                    "session_id": "cli-9999",
                },
                msg_id=88,
            )

        fallback_session.append_chat_log.assert_called_once_with(
            direction="out",
            sender="valor",
            content="Fallback path",
            message_id=88,
        )

    def test_no_session_resolved_returns_without_crashing(self):
        """When no session is found via any tier, the function returns without crashing."""
        mock_agent_session_cls = MagicMock()
        mock_agent_session_cls.query.filter.return_value = []
        mock_agent_session_cls.query.all.return_value = []

        with patch("models.agent_session.AgentSession", mock_agent_session_cls):
            # Should not raise
            _append_outbound_chat_log(
                {
                    "text": "No session",
                    "chat_id": "55555",
                    "session_id": "cli-999",
                },
                msg_id=None,
            )


class TestAppendOutboundChatLogEdgeCases:
    """Edge cases: empty text, exception handling."""

    def test_empty_text_is_skipped(self):
        """Voice-note or file-only sends (no text) do not trigger any lookup."""
        mock_agent_session_cls = MagicMock()
        with patch("models.agent_session.AgentSession", mock_agent_session_cls):
            _append_outbound_chat_log(
                {
                    "text": "",
                    "chat_id": "12345",
                    "session_id": "session-abc",
                },
                msg_id=42,
            )
        # No query should occur for empty text
        mock_agent_session_cls.query.filter.assert_not_called()

    def test_none_text_is_skipped(self):
        """None text (missing key) is treated as empty and skipped."""
        mock_agent_session_cls = MagicMock()
        with patch("models.agent_session.AgentSession", mock_agent_session_cls):
            _append_outbound_chat_log(
                {
                    "chat_id": "12345",
                    "session_id": "session-abc",
                },
                msg_id=42,
            )
        mock_agent_session_cls.query.filter.assert_not_called()

    def test_exception_during_lookup_is_non_fatal(self):
        """An exception during session lookup does not propagate to the caller."""
        mock_agent_session_cls = MagicMock()
        mock_agent_session_cls.query.filter.side_effect = RuntimeError("Redis exploded")

        with patch("models.agent_session.AgentSession", mock_agent_session_cls):
            # Should not raise
            _append_outbound_chat_log(
                {
                    "text": "Hello",
                    "chat_id": "12345",
                    "session_id": "real-session-abc",
                },
                msg_id=99,
            )

    def test_exception_during_append_is_non_fatal(self):
        """An exception during append_chat_log does not propagate."""
        session = _make_session("real-session-abc")
        session.append_chat_log.side_effect = RuntimeError("append failed")

        mock_agent_session_cls = MagicMock()
        mock_agent_session_cls.query.filter.return_value = [session]
        mock_agent_session_cls.query.all.return_value = []

        with patch("models.agent_session.AgentSession", mock_agent_session_cls):
            # Should not raise
            _append_outbound_chat_log(
                {
                    "text": "Hello",
                    "chat_id": "12345",
                    "session_id": "real-session-abc",
                },
                msg_id=99,
            )
