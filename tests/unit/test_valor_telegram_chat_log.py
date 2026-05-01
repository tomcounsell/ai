"""Unit tests for owner_agent_session_id injection in valor-telegram send (issue #1192).

Tests that cmd_send() adds owner_agent_session_id to the relay payload when
AGENT_SESSION_ID or VALOR_SESSION_ID env vars are set, and omits the key
when neither is set (manual CLI invocation outside any agent session).
"""

import argparse
import json
from unittest.mock import MagicMock, patch


def _make_send_args(chat="-100123456", message="test message", reply_to=None):
    return argparse.Namespace(
        chat=chat,
        message=message,
        file=None,
        image=None,
        audio=None,
        reply_to=reply_to,
    )


class TestCmdSendOwnerAgentSessionId:
    """owner_agent_session_id is injected into the relay payload when env vars are set."""

    @patch("tools.valor_telegram.resolve_chat", return_value="-100123456")
    @patch("tools.valor_telegram._get_redis_connection")
    def test_agent_session_id_injected_when_set(self, mock_redis_fn, mock_resolve, monkeypatch):
        """When AGENT_SESSION_ID is set, payload contains owner_agent_session_id."""
        from tools.valor_telegram import cmd_send

        monkeypatch.setenv("AGENT_SESSION_ID", "agent-session-abc123")
        monkeypatch.delenv("VALOR_SESSION_ID", raising=False)
        monkeypatch.delenv("TELEGRAM_REPLY_TO", raising=False)

        mock_redis = MagicMock()
        mock_redis_fn.return_value = mock_redis

        args = _make_send_args()
        result = cmd_send(args)

        assert result == 0
        payload = json.loads(mock_redis.rpush.call_args[0][1])
        assert payload.get("owner_agent_session_id") == "agent-session-abc123"

    @patch("tools.valor_telegram.resolve_chat", return_value="-100123456")
    @patch("tools.valor_telegram._get_redis_connection")
    def test_valor_session_id_fallback_when_agent_session_id_unset(
        self, mock_redis_fn, mock_resolve, monkeypatch
    ):
        """When AGENT_SESSION_ID is absent, VALOR_SESSION_ID is used as fallback."""
        from tools.valor_telegram import cmd_send

        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)
        monkeypatch.setenv("VALOR_SESSION_ID", "valor-session-xyz789")
        monkeypatch.delenv("TELEGRAM_REPLY_TO", raising=False)

        mock_redis = MagicMock()
        mock_redis_fn.return_value = mock_redis

        args = _make_send_args()
        result = cmd_send(args)

        assert result == 0
        payload = json.loads(mock_redis.rpush.call_args[0][1])
        assert payload.get("owner_agent_session_id") == "valor-session-xyz789"

    @patch("tools.valor_telegram.resolve_chat", return_value="-100123456")
    @patch("tools.valor_telegram._get_redis_connection")
    def test_owner_key_absent_when_no_env_vars(self, mock_redis_fn, mock_resolve, monkeypatch):
        """When neither env var is set, owner_agent_session_id is absent from payload."""
        from tools.valor_telegram import cmd_send

        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)
        monkeypatch.delenv("VALOR_SESSION_ID", raising=False)
        monkeypatch.delenv("TELEGRAM_REPLY_TO", raising=False)

        mock_redis = MagicMock()
        mock_redis_fn.return_value = mock_redis

        args = _make_send_args()
        result = cmd_send(args)

        assert result == 0
        payload = json.loads(mock_redis.rpush.call_args[0][1])
        assert "owner_agent_session_id" not in payload

    @patch("tools.valor_telegram.resolve_chat", return_value="-100123456")
    @patch("tools.valor_telegram._get_redis_connection")
    def test_agent_session_id_takes_precedence_over_valor_session_id(
        self, mock_redis_fn, mock_resolve, monkeypatch
    ):
        """When both env vars are set, AGENT_SESSION_ID takes precedence."""
        from tools.valor_telegram import cmd_send

        monkeypatch.setenv("AGENT_SESSION_ID", "agent-wins")
        monkeypatch.setenv("VALOR_SESSION_ID", "valor-loses")
        monkeypatch.delenv("TELEGRAM_REPLY_TO", raising=False)

        mock_redis = MagicMock()
        mock_redis_fn.return_value = mock_redis

        args = _make_send_args()
        result = cmd_send(args)

        assert result == 0
        payload = json.loads(mock_redis.rpush.call_args[0][1])
        assert payload.get("owner_agent_session_id") == "agent-wins"

    @patch("tools.valor_telegram.resolve_chat", return_value="-100123456")
    @patch("tools.valor_telegram._get_redis_connection")
    def test_empty_text_validation_still_runs_before_env_var_read(
        self, mock_redis_fn, mock_resolve, monkeypatch, capsys
    ):
        """Empty text is rejected before the owner_agent_session_id code runs.

        This ensures the chat-log injection doesn't bypass the existing validation.
        """
        from tools.valor_telegram import cmd_send

        monkeypatch.setenv("AGENT_SESSION_ID", "agent-session-abc")
        monkeypatch.delenv("TELEGRAM_REPLY_TO", raising=False)

        mock_redis = MagicMock()
        mock_redis_fn.return_value = mock_redis

        args = _make_send_args(message="")
        result = cmd_send(args)

        # Empty message with no file → error before Redis push
        assert result == 1
        mock_redis.rpush.assert_not_called()
