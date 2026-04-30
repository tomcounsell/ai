"""Unit tests for ``tools.send_message``.

Covers the unified outbox payload emitted by ``_send_via_email`` (was updated
from legacy ``{session_id, to, text, timestamp}`` to the full shape consumed
by ``bridge/email_relay.py``) and the transport resolver.
"""

from __future__ import annotations

import json

import pytest
import redis


@pytest.fixture(autouse=True)
def _bypass_promise_gate(monkeypatch):
    """Default-mock the promise gate so existing tests do not call the LLM."""
    monkeypatch.setattr(
        "bridge.promise_gate.cli_check_or_exit",
        lambda text, transport, session_id: None,
    )


@pytest.fixture
def r(monkeypatch, redis_test_url):
    monkeypatch.setenv("REDIS_URL", redis_test_url)
    monkeypatch.setenv("SMTP_USER", "valor@test.local")
    client = redis.Redis.from_url(redis_test_url, decode_responses=True)
    yield client
    client.close()


class TestResolveTransport:
    def test_explicit_override(self, monkeypatch):
        from tools.send_message import _resolve_transport

        monkeypatch.setenv("VALOR_TRANSPORT", "EMAIL")
        assert _resolve_transport() == "email"

    def test_email_reply_to_implies_email(self, monkeypatch):
        from tools.send_message import _resolve_transport

        monkeypatch.delenv("VALOR_TRANSPORT", raising=False)
        monkeypatch.setenv("EMAIL_REPLY_TO", "alice@x")
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        assert _resolve_transport() == "email"

    def test_telegram_chat_id_implies_telegram(self, monkeypatch):
        from tools.send_message import _resolve_transport

        monkeypatch.delenv("VALOR_TRANSPORT", raising=False)
        monkeypatch.delenv("EMAIL_REPLY_TO", raising=False)
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
        assert _resolve_transport() == "telegram"

    def test_default_is_telegram(self, monkeypatch):
        from tools.send_message import _resolve_transport

        monkeypatch.delenv("VALOR_TRANSPORT", raising=False)
        monkeypatch.delenv("EMAIL_REPLY_TO", raising=False)
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        assert _resolve_transport() == "telegram"


class TestSendViaEmail:
    def test_unified_payload_shape(self, r, monkeypatch, capsys):
        from tools.send_message import _send_via_email

        monkeypatch.setenv("VALOR_SESSION_ID", "sess-1")
        monkeypatch.setenv("EMAIL_REPLY_TO", "alice@example.com")
        monkeypatch.setenv("EMAIL_SUBJECT", "Re: hi")
        monkeypatch.setenv("EMAIL_IN_REPLY_TO", "<orig@x>")

        _send_via_email("Body text here")

        key = "email:outbox:sess-1"
        raw = r.lpop(key)
        assert raw is not None
        payload = json.loads(raw)

        assert payload["session_id"] == "sess-1"
        assert payload["to"] == "alice@example.com"
        assert payload["subject"] == "Re: hi"
        # Body field is the unified shape; legacy `text` is not emitted anymore
        assert payload["body"] == "Body text here"
        assert "text" not in payload
        assert payload["attachments"] == []
        assert payload["in_reply_to"] == "<orig@x>"
        assert payload["references"] == "<orig@x>"
        assert payload["from_addr"] == "valor@test.local"
        assert isinstance(payload["timestamp"], float)

    def test_ttl_set_on_queue(self, r, monkeypatch):
        from tools.send_message import _send_via_email

        monkeypatch.setenv("VALOR_SESSION_ID", "sess-2")
        monkeypatch.setenv("EMAIL_REPLY_TO", "alice@example.com")

        _send_via_email("hi")

        ttl = r.ttl("email:outbox:sess-2")
        assert 0 < ttl <= 3600

    def test_missing_session_id_exits(self, monkeypatch):
        from tools.send_message import _send_via_email

        monkeypatch.delenv("VALOR_SESSION_ID", raising=False)
        monkeypatch.setenv("EMAIL_REPLY_TO", "alice@example.com")

        with pytest.raises(SystemExit) as ei:
            _send_via_email("hi")
        assert ei.value.code == 1

    def test_missing_reply_to_exits(self, monkeypatch):
        from tools.send_message import _send_via_email

        monkeypatch.setenv("VALOR_SESSION_ID", "sess-x")
        monkeypatch.delenv("EMAIL_REPLY_TO", raising=False)

        with pytest.raises(SystemExit) as ei:
            _send_via_email("hi")
        assert ei.value.code == 1

    def test_missing_subject_defaults(self, r, monkeypatch):
        from tools.send_message import _send_via_email

        monkeypatch.setenv("VALOR_SESSION_ID", "sess-3")
        monkeypatch.setenv("EMAIL_REPLY_TO", "alice@example.com")
        monkeypatch.delenv("EMAIL_SUBJECT", raising=False)
        monkeypatch.delenv("EMAIL_IN_REPLY_TO", raising=False)

        _send_via_email("hi")
        payload = json.loads(r.lpop("email:outbox:sess-3"))
        assert payload["subject"] == "(no subject)"
        assert payload["in_reply_to"] is None
        assert payload["references"] is None


class TestSendMessagePromiseGate:
    """Promise gate integration (cycle-2 B-NEW-2: no --no-promise-gate flag)."""

    def test_help_does_not_mention_no_promise_gate(self):
        """Cycle-2 B-NEW-2: --help output must NOT advertise --no-promise-gate."""
        from io import StringIO
        from unittest.mock import patch

        with patch("sys.argv", ["send_message.py", "--help"]):
            from tools.send_message import main

            buf = StringIO()
            with patch("sys.stdout", buf), pytest.raises(SystemExit):
                main()
            help_output = buf.getvalue()

        assert "--no-promise-gate" not in help_output
        assert "VALOR_OPERATOR_MODE" not in help_output
        assert "PROMISE_GATE_ENABLED" not in help_output
