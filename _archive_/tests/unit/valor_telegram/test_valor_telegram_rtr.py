"""Tests for the Read-the-Room (RTR) pre-send pass on `valor-telegram send`
and the promise-gate integration.

Split out of the former ``tests/unit/test_valor_telegram.py`` monolith (#2879).
"""

import argparse
import json
import sys
from unittest.mock import MagicMock, patch

import pytest

# =============================================================================
# Issue #1203 — Read-the-Room (RTR) pre-send pass on Path B (`valor-telegram send`)
# =============================================================================


class TestShouldRunRTRSecondaryConsumer:
    """Pins `_should_run_rtr` as a secondary consumer of `VALOR_SESSION_ID`
    (issue #2190 PR review, Finding 1): the resolver injection in
    `agent/session_executor.py::_harness_env` broadens `VALOR_SESSION_ID`
    visibility to every harness subprocess, which silently activates this
    gate. This test pins that now-activated behavior directly against
    `_should_run_rtr`, independent of the full `cmd_send` RTR flow already
    covered in `TestCmdSendRTR`.
    """

    def _args(self, read_the_room=False, no_read_the_room=False):
        return argparse.Namespace(
            read_the_room=read_the_room,
            no_read_the_room=no_read_the_room,
        )

    def test_returns_true_when_valor_session_id_set(self, monkeypatch):
        """Non-empty VALOR_SESSION_ID (as injected by _harness_env) → True."""
        from tools.valor_telegram import _should_run_rtr

        monkeypatch.setenv("VALOR_SESSION_ID", "tg_test_-100123_456")

        assert _should_run_rtr(self._args()) is True

    def test_returns_false_when_valor_session_id_unset(self, monkeypatch):
        """No VALOR_SESSION_ID (human caller) → False."""
        from tools.valor_telegram import _should_run_rtr

        monkeypatch.delenv("VALOR_SESSION_ID", raising=False)

        assert _should_run_rtr(self._args()) is False

    def test_returns_false_when_valor_session_id_empty(self, monkeypatch):
        """Empty-string VALOR_SESSION_ID is treated as not set."""
        from tools.valor_telegram import _should_run_rtr

        monkeypatch.setenv("VALOR_SESSION_ID", "")

        assert _should_run_rtr(self._args()) is False


class TestCmdSendRTR:
    """RTR pre-send pass on the agent-invoked `valor-telegram send` path.

    Reuses the public `read_the_room` API. Each test mocks the async entry
    point with `unittest.mock.AsyncMock` returning a `RoomVerdict` directly,
    so no Anthropic API calls are made and the verdict logic is the only
    thing under test.
    """

    def _make_args(
        self,
        chat="-123456",
        message="hello there, this is a long enough message to clear the linkify guard",
        file=None,
        image=None,
        audio=None,
        reply_to=None,
        read_the_room=False,
        no_read_the_room=False,
    ):
        ns = argparse.Namespace(
            chat=chat,
            message=message,
            file=file,
            image=image,
            audio=audio,
            reply_to=reply_to,
            read_the_room=read_the_room,
            no_read_the_room=no_read_the_room,
        )
        return ns

    @patch("tools.valor_telegram.resolve_chat", return_value="-100123456")
    @patch("tools.valor_telegram._get_redis_connection")
    def test_rtr_short_circuits_for_human_caller(self, mock_redis_fn, mock_resolve, monkeypatch):
        """RTR not invoked when VALOR_SESSION_ID is unset (human caller).

        RTR runs unconditionally now -- the *only* gate on Path B is the
        caller-type check (`_should_run_rtr`). With no VALOR_SESSION_ID and
        no --read-the-room flag, the gate returns False and read_the_room is
        never called, regardless of any env var.

        (This test previously had a sibling, `test_rtr_short_circuits_when_disabled`,
        that additionally set the now-removed kill-switch env var to a
        disabling value; once that var was gone, the two bodies were
        identical, so they were merged into this one.)
        """
        from unittest.mock import AsyncMock

        from tools.valor_telegram import cmd_send

        monkeypatch.delenv("VALOR_SESSION_ID", raising=False)
        monkeypatch.delenv("TELEGRAM_REPLY_TO", raising=False)

        rtr_mock = AsyncMock()
        monkeypatch.setattr("bridge.read_the_room.read_the_room", rtr_mock)

        mock_redis = MagicMock()
        mock_redis_fn.return_value = mock_redis

        args = self._make_args(message="hello world")
        result = cmd_send(args)

        assert result == 0
        rtr_mock.assert_not_called()
        mock_redis.rpush.assert_called_once()

    @patch("tools.valor_telegram.resolve_chat", return_value="-100123456")
    @patch("tools.valor_telegram._get_redis_connection")
    def test_rtr_runs_for_agent_caller(self, mock_redis_fn, mock_resolve, monkeypatch):
        """VALOR_SESSION_ID set → RTR called once with the linkified text."""
        from unittest.mock import AsyncMock

        from bridge.read_the_room import RoomVerdict
        from tools.valor_telegram import cmd_send

        monkeypatch.setenv("VALOR_SESSION_ID", "tg_test_-100123_456")
        monkeypatch.delenv("TELEGRAM_REPLY_TO", raising=False)

        rtr_mock = AsyncMock(return_value=RoomVerdict(action="send"))
        monkeypatch.setattr("bridge.read_the_room.read_the_room", rtr_mock)

        # Block AgentSession lookup so test doesn't hit Redis-backed Popoto.
        monkeypatch.setattr(
            "models.agent_session.AgentSession.query.filter",
            MagicMock(return_value=[]),
        )

        mock_redis = MagicMock()
        mock_redis_fn.return_value = mock_redis

        args = self._make_args(message="this is a fine message to send to the chat")
        result = cmd_send(args)

        assert result == 0
        assert rtr_mock.call_count == 1
        # First positional: the (possibly linkified) text.
        called_text = rtr_mock.call_args[0][0]
        assert "fine message" in called_text
        mock_redis.rpush.assert_called_once()

    @patch("tools.valor_telegram.resolve_chat", return_value="-100123456")
    @patch("tools.valor_telegram._get_redis_connection")
    def test_rtr_send_verdict_proceeds(self, mock_redis_fn, mock_resolve, monkeypatch):
        """verdict=send → rpush proceeds with original text unchanged."""
        from unittest.mock import AsyncMock

        from bridge.read_the_room import RoomVerdict
        from tools.valor_telegram import cmd_send

        monkeypatch.setenv("VALOR_SESSION_ID", "tg_test_-100123_456")
        monkeypatch.delenv("TELEGRAM_REPLY_TO", raising=False)

        rtr_mock = AsyncMock(return_value=RoomVerdict(action="send"))
        monkeypatch.setattr("bridge.read_the_room.read_the_room", rtr_mock)
        monkeypatch.setattr(
            "models.agent_session.AgentSession.query.filter",
            MagicMock(return_value=[]),
        )

        mock_redis = MagicMock()
        mock_redis_fn.return_value = mock_redis

        original = "send this message exactly as it stands"
        args = self._make_args(message=original)
        cmd_send(args)

        payload = json.loads(mock_redis.rpush.call_args[0][1])
        assert payload["text"] == original

    @patch("tools.valor_telegram.resolve_chat", return_value="-100123456")
    @patch("tools.valor_telegram._get_redis_connection")
    def test_rtr_trim_substitutes_text(self, mock_redis_fn, mock_resolve, monkeypatch):
        """verdict=trim with revised >= 20 chars → rpush proceeds with revised."""
        from unittest.mock import AsyncMock

        from bridge.read_the_room import RoomVerdict
        from tools.valor_telegram import cmd_send

        monkeypatch.setenv("VALOR_SESSION_ID", "tg_test_-100123_456")
        monkeypatch.delenv("TELEGRAM_REPLY_TO", raising=False)

        revised = "Got it — see thread for details."
        rtr_mock = AsyncMock(
            return_value=RoomVerdict(action="trim", revised_text=revised, reason="overlap")
        )
        monkeypatch.setattr("bridge.read_the_room.read_the_room", rtr_mock)
        monkeypatch.setattr(
            "models.agent_session.AgentSession.query.filter",
            MagicMock(return_value=[]),
        )

        mock_redis = MagicMock()
        mock_redis_fn.return_value = mock_redis

        args = self._make_args(message="long-form draft repeating what was already said")
        cmd_send(args)

        payload = json.loads(mock_redis.rpush.call_args[0][1])
        assert payload["text"] == revised

    @patch("tools.valor_telegram.resolve_chat", return_value="-100123456")
    @patch("tools.valor_telegram._get_redis_connection")
    def test_rtr_trim_too_short_with_anchor_queues_reaction(
        self, mock_redis_fn, mock_resolve, monkeypatch
    ):
        """verdict=trim with revised < 20 chars + reply anchor → reaction, no msg rpush."""
        from unittest.mock import AsyncMock

        from bridge.read_the_room import RoomVerdict
        from tools.valor_telegram import cmd_send

        monkeypatch.setenv("VALOR_SESSION_ID", "tg_test_-100123_456")
        monkeypatch.delenv("TELEGRAM_REPLY_TO", raising=False)

        rtr_mock = AsyncMock(
            return_value=RoomVerdict(action="trim", revised_text="ok", reason="short")
        )
        monkeypatch.setattr("bridge.read_the_room.read_the_room", rtr_mock)
        monkeypatch.setattr(
            "models.agent_session.AgentSession.query.filter",
            MagicMock(return_value=[]),
        )

        mock_redis = MagicMock()
        mock_redis_fn.return_value = mock_redis

        args = self._make_args(message="long-form draft", reply_to=42)
        result = cmd_send(args)

        assert result == 0
        # Exactly one rpush — the reaction payload, not the message.
        assert mock_redis.rpush.call_count == 1
        payload = json.loads(mock_redis.rpush.call_args[0][1])
        assert payload["type"] == "reaction"
        assert payload["emoji"] == "👀"
        assert payload["reply_to"] == 42

    @patch("tools.valor_telegram.resolve_chat", return_value="-100123456")
    @patch("tools.valor_telegram._get_redis_connection")
    def test_rtr_suppress_with_reply_anchor_queues_reaction(
        self, mock_redis_fn, mock_resolve, monkeypatch
    ):
        """verdict=suppress + reply_to → reaction queued, original message NOT rpushed."""
        from unittest.mock import AsyncMock

        from bridge.read_the_room import RoomVerdict
        from tools.valor_telegram import cmd_send

        monkeypatch.setenv("VALOR_SESSION_ID", "tg_test_-100123_456")
        monkeypatch.delenv("TELEGRAM_REPLY_TO", raising=False)

        rtr_mock = AsyncMock(return_value=RoomVerdict(action="suppress", reason="self_duplicate"))
        monkeypatch.setattr("bridge.read_the_room.read_the_room", rtr_mock)
        monkeypatch.setattr(
            "models.agent_session.AgentSession.query.filter",
            MagicMock(return_value=[]),
        )

        mock_redis = MagicMock()
        mock_redis_fn.return_value = mock_redis

        args = self._make_args(message="redundant draft we should not send", reply_to=99)
        result = cmd_send(args)

        assert result == 0
        assert mock_redis.rpush.call_count == 1
        payload = json.loads(mock_redis.rpush.call_args[0][1])
        assert payload["type"] == "reaction"
        assert payload["reply_to"] == 99

    @patch("tools.valor_telegram.resolve_chat", return_value="-100123456")
    @patch("tools.valor_telegram._get_redis_connection")
    def test_rtr_suppress_without_anchor_falls_through(
        self, mock_redis_fn, mock_resolve, monkeypatch
    ):
        """verdict=suppress + reply_to=None → original text rpushed (F4 fall-through)."""
        from unittest.mock import AsyncMock

        from bridge.read_the_room import RoomVerdict
        from tools.valor_telegram import cmd_send

        monkeypatch.setenv("VALOR_SESSION_ID", "tg_test_-100123_456")
        monkeypatch.delenv("TELEGRAM_REPLY_TO", raising=False)

        rtr_mock = AsyncMock(return_value=RoomVerdict(action="suppress", reason="self_duplicate"))
        monkeypatch.setattr("bridge.read_the_room.read_the_room", rtr_mock)
        monkeypatch.setattr(
            "models.agent_session.AgentSession.query.filter",
            MagicMock(return_value=[]),
        )

        mock_redis = MagicMock()
        mock_redis_fn.return_value = mock_redis

        original = "this draft has no reply anchor at all today"
        args = self._make_args(message=original, reply_to=None)
        result = cmd_send(args)

        assert result == 0
        # F4 fall-through: original text is rpush'd as a regular message.
        payload = json.loads(mock_redis.rpush.call_args[0][1])
        assert payload.get("type") != "reaction"
        assert payload["text"] == original

    @patch("tools.valor_telegram.resolve_chat", return_value="-100123456")
    @patch("tools.valor_telegram._get_redis_connection")
    def test_rtr_failure_falls_open(self, mock_redis_fn, mock_resolve, monkeypatch, caplog):
        """RTR raises → original text rpush'd; warning logged (fail-open contract)."""
        from unittest.mock import AsyncMock

        from tools.valor_telegram import cmd_send

        monkeypatch.setenv("VALOR_SESSION_ID", "tg_test_-100123_456")
        monkeypatch.delenv("TELEGRAM_REPLY_TO", raising=False)

        rtr_mock = AsyncMock(side_effect=RuntimeError("haiku down"))
        monkeypatch.setattr("bridge.read_the_room.read_the_room", rtr_mock)
        monkeypatch.setattr(
            "models.agent_session.AgentSession.query.filter",
            MagicMock(return_value=[]),
        )

        mock_redis = MagicMock()
        mock_redis_fn.return_value = mock_redis

        original = "send despite RTR failure please"
        args = self._make_args(message=original)
        with caplog.at_level("WARNING"):
            result = cmd_send(args)

        assert result == 0
        payload = json.loads(mock_redis.rpush.call_args[0][1])
        assert payload["text"] == original
        # Warning was logged via tools.valor_telegram.logger
        assert any("RTR" in rec.message for rec in caplog.records)

    @patch("tools.valor_telegram.resolve_chat", return_value="-100123456")
    @patch("tools.valor_telegram._get_redis_connection")
    def test_rtr_flag_force_on_for_human(self, mock_redis_fn, mock_resolve, monkeypatch):
        """--read-the-room flag activates RTR even when VALOR_SESSION_ID is unset."""
        from unittest.mock import AsyncMock

        from bridge.read_the_room import RoomVerdict
        from tools.valor_telegram import cmd_send

        monkeypatch.delenv("VALOR_SESSION_ID", raising=False)
        monkeypatch.delenv("TELEGRAM_REPLY_TO", raising=False)

        rtr_mock = AsyncMock(return_value=RoomVerdict(action="send"))
        monkeypatch.setattr("bridge.read_the_room.read_the_room", rtr_mock)

        mock_redis = MagicMock()
        mock_redis_fn.return_value = mock_redis

        args = self._make_args(message="forced human RTR run", read_the_room=True)
        cmd_send(args)

        assert rtr_mock.call_count == 1

    @patch("tools.valor_telegram.resolve_chat", return_value="-100123456")
    @patch("tools.valor_telegram._get_redis_connection")
    def test_rtr_flag_force_off_for_agent(self, mock_redis_fn, mock_resolve, monkeypatch):
        """--no-read-the-room flag skips RTR even when VALOR_SESSION_ID is set."""
        from unittest.mock import AsyncMock

        from tools.valor_telegram import cmd_send

        monkeypatch.setenv("VALOR_SESSION_ID", "tg_test_-100123_456")
        monkeypatch.delenv("TELEGRAM_REPLY_TO", raising=False)

        rtr_mock = AsyncMock()
        monkeypatch.setattr("bridge.read_the_room.read_the_room", rtr_mock)

        mock_redis = MagicMock()
        mock_redis_fn.return_value = mock_redis

        args = self._make_args(message="agent send opting out", no_read_the_room=True)
        cmd_send(args)

        rtr_mock.assert_not_called()

    def test_rtr_mutex_flags_error(self):
        """--read-the-room and --no-read-the-room are mutually exclusive at argparse."""
        from tools.valor_telegram import main

        sys.argv = [
            "valor-telegram",
            "send",
            "--chat",
            "-123",
            "--read-the-room",
            "--no-read-the-room",
            "msg",
        ]
        with pytest.raises(SystemExit) as exc_info:
            main()
        # argparse exits 2 on a usage error.
        assert exc_info.value.code != 0

    @patch("tools.valor_telegram.resolve_chat", return_value="-100123456")
    @patch("tools.valor_telegram._get_redis_connection")
    def test_rtr_session_events_recorded_for_agent_caller(
        self, mock_redis_fn, mock_resolve, monkeypatch
    ):
        """Regression guard for B1: trim verdict for agent-invoked send writes
        an `rtr.trimmed` entry to session.session_events.

        A silent fallback to session=None (e.g. someone reverts to
        query.get(session_id), which raises) would leave session_events
        empty and fail this test.
        """
        from unittest.mock import AsyncMock

        from bridge.read_the_room import RoomVerdict
        from tools.valor_telegram import cmd_send

        monkeypatch.setenv("VALOR_SESSION_ID", "tg_test_-100123_456")
        monkeypatch.delenv("TELEGRAM_REPLY_TO", raising=False)

        # Stand-in session object: matches what query.filter(session_id=...)
        # would yield in production but uses a MagicMock so we can inspect
        # session_events without touching Popoto/Redis.
        fake_session = MagicMock()
        fake_session.session_events = []

        # Critical: query.filter(session_id=...) is the canonical pattern
        # (see agent/health_check.py:42 and agent/agent_session_queue.py:275).
        # The pre-revision code used query.get(session_id), which raises
        # AttributeError because Popoto rejects positional strings — this
        # test would fail the assertion below if the regression returns.
        monkeypatch.setattr(
            "models.agent_session.AgentSession.query.filter",
            MagicMock(return_value=[fake_session]),
        )

        revised = "Got it — see thread for details."
        rtr_mock = AsyncMock(
            return_value=RoomVerdict(action="trim", revised_text=revised, reason="overlap")
        )
        monkeypatch.setattr("bridge.read_the_room.read_the_room", rtr_mock)

        mock_redis = MagicMock()
        mock_redis_fn.return_value = mock_redis

        args = self._make_args(message="long-form redundant draft about the same topic")
        result = cmd_send(args)

        assert result == 0
        # Exactly one rtr.trimmed event was appended to the session.
        events = fake_session.session_events
        assert len(events) == 1
        assert events[0]["type"] == "rtr.trimmed"
        assert events[0]["revised_preview"] == revised

    @patch("tools.valor_telegram.resolve_chat", return_value="-100123456")
    @patch("tools.valor_telegram._get_redis_connection")
    def test_rtr_stderr_diagnostic_on_activation(
        self, mock_redis_fn, mock_resolve, monkeypatch, capsys
    ):
        """When RTR runs, stderr emits the activation diagnostic; when it
        skips (gate False), stderr does NOT contain it.

        This is the formal escape-hatch signal for the env-inheritance
        false-positive vector documented in Risk 1 (sub-shells, tmux,
        claude --resume from inside an existing session).
        """
        from unittest.mock import AsyncMock

        from bridge.read_the_room import RoomVerdict
        from tools.valor_telegram import cmd_send

        monkeypatch.setenv("VALOR_SESSION_ID", "tg_test_-100123_456")
        monkeypatch.delenv("TELEGRAM_REPLY_TO", raising=False)

        rtr_mock = AsyncMock(return_value=RoomVerdict(action="send"))
        monkeypatch.setattr("bridge.read_the_room.read_the_room", rtr_mock)
        monkeypatch.setattr(
            "models.agent_session.AgentSession.query.filter",
            MagicMock(return_value=[]),
        )

        mock_redis = MagicMock()
        mock_redis_fn.return_value = mock_redis

        # Activation case: agent caller, RTR runs.
        args = self._make_args(message="this triggers the diagnostic line")
        cmd_send(args)
        captured = capsys.readouterr()
        assert "RTR active" in captured.err

        # Skip case: human caller (no VALOR_SESSION_ID), no diagnostic.
        monkeypatch.delenv("VALOR_SESSION_ID", raising=False)
        args2 = self._make_args(message="no diagnostic this time")
        cmd_send(args2)
        captured2 = capsys.readouterr()
        assert "RTR active" not in captured2.err


class TestValorTelegramPromiseGate:
    """Promise gate integration (cycle-2 B-NEW-2: no --no-promise-gate flag)."""

    def test_send_help_does_not_mention_no_promise_gate(self):
        """Cycle-2 B-NEW-2: ``valor-telegram send --help`` must not advertise the bypass."""
        from io import StringIO

        with patch("sys.argv", ["valor-telegram", "send", "--help"]):
            from tools.valor_telegram import main

            buf = StringIO()
            with patch("sys.stdout", buf), pytest.raises(SystemExit):
                main()
            help_output = buf.getvalue()

        assert "--no-promise-gate" not in help_output
        assert "VALOR_OPERATOR_MODE" not in help_output
        assert "PROMISE_GATE_ENABLED" not in help_output
