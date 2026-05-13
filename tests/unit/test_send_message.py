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
    """Issue #1369: ``_send_via_email`` no longer writes to the email outbox
    directly — it reconstitutes the AgentSession and delegates to
    ``TelegramRelayOutputHandler.send``, which owns the email outbox write.

    The unified email payload SHAPE is now asserted at the handler layer in
    ``tests/unit/test_output_handler.py`` (``TestTransportAwareRouting`` /
    ``TestDrafterHoistedAboveTransport``). The tests below assert the
    CLI-side contract: env validation, session lookup, fail-closed default
    on missing session, and the env-gated legacy fallback.
    """

    def test_invokes_canonical_handler_with_email_session(self, monkeypatch):
        """Happy path: when the session exists, the tool delegates to
        ``TelegramRelayOutputHandler.send`` with the recipient address as
        chat_id and ``reply_to_msg_id=0``."""
        from unittest.mock import AsyncMock, MagicMock, patch

        import tools.send_message as sm

        monkeypatch.setenv("VALOR_SESSION_ID", "sess-1")
        monkeypatch.setenv("EMAIL_REPLY_TO", "alice@example.com")
        monkeypatch.setenv("EMAIL_SUBJECT", "Re: hi")
        monkeypatch.setenv("EMAIL_IN_REPLY_TO", "<orig@x>")
        monkeypatch.delenv("ALLOW_LEGACY_RPUSH_FALLBACK", raising=False)

        fake_session = MagicMock()
        fake_session.session_id = "sess-1"
        fake_session.extra_context = {
            "transport": "email",
            "email_subject": "Re: hi",
            "email_message_id": "<orig@x>",
            "email_to_addrs": [],
            "email_cc_addrs": [],
        }
        monkeypatch.setattr(sm, "_lookup_session", lambda sid: fake_session)

        mock_send = AsyncMock(return_value=None)
        with patch(
            "agent.output_handler.TelegramRelayOutputHandler.send", new=mock_send
        ):
            sm._send_via_email("Body text here")

        mock_send.assert_awaited_once()
        positional = list(mock_send.call_args.args)
        assert "alice@example.com" in positional
        assert "Body text here" in positional
        assert 0 in positional  # reply_to_msg_id sentinel for email
        assert mock_send.call_args.kwargs["session"] is fake_session

    def test_missing_session_fails_closed(self, monkeypatch):
        """Default behavior when the AgentSession lookup returns ``None``:
        non-zero exit (the tool refuses to silently bypass the handler)."""
        import tools.send_message as sm

        monkeypatch.setenv("VALOR_SESSION_ID", "sess-missing")
        monkeypatch.setenv("EMAIL_REPLY_TO", "alice@example.com")
        monkeypatch.delenv("ALLOW_LEGACY_RPUSH_FALLBACK", raising=False)
        monkeypatch.setattr(sm, "_lookup_session", lambda sid: None)

        with pytest.raises(SystemExit) as ei:
            sm._send_via_email("hi")
        assert ei.value.code == 1

    def test_legacy_fallback_writes_unified_payload(self, r, monkeypatch):
        """When ``ALLOW_LEGACY_RPUSH_FALLBACK=1`` AND the session is missing,
        the tool writes the unified payload shape directly to
        ``email:outbox:{session_id}``. This is the diagnostic opt-in path
        — the tests below assert the legacy payload contract still holds."""
        from tools.send_message import _send_via_email

        monkeypatch.setenv("VALOR_SESSION_ID", "sess-legacy")
        monkeypatch.setenv("EMAIL_REPLY_TO", "alice@example.com")
        monkeypatch.setenv("EMAIL_SUBJECT", "Re: hi")
        monkeypatch.setenv("EMAIL_IN_REPLY_TO", "<orig@x>")
        monkeypatch.setenv("ALLOW_LEGACY_RPUSH_FALLBACK", "1")
        # Force the lookup to fail so the legacy path engages.
        monkeypatch.setattr("tools.send_message._lookup_session", lambda sid: None)

        _send_via_email("Body text here")

        raw = r.lpop("email:outbox:sess-legacy")
        assert raw is not None
        payload = json.loads(raw)
        assert payload["session_id"] == "sess-legacy"
        assert payload["to"] == "alice@example.com"
        assert payload["subject"] == "Re: hi"
        assert payload["body"] == "Body text here"
        assert payload["attachments"] == []
        assert payload["in_reply_to"] == "<orig@x>"
        assert payload["references"] == "<orig@x>"
        assert payload["from_addr"] == "valor@test.local"

    def test_legacy_fallback_ttl_set_on_queue(self, r, monkeypatch):
        """The legacy fallback still applies the 3600s TTL to the outbox key."""
        from tools.send_message import _send_via_email

        monkeypatch.setenv("VALOR_SESSION_ID", "sess-legacy-ttl")
        monkeypatch.setenv("EMAIL_REPLY_TO", "alice@example.com")
        monkeypatch.setenv("ALLOW_LEGACY_RPUSH_FALLBACK", "1")
        monkeypatch.setattr("tools.send_message._lookup_session", lambda sid: None)

        _send_via_email("hi")

        ttl = r.ttl("email:outbox:sess-legacy-ttl")
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

    def test_legacy_fallback_missing_subject_defaults(self, r, monkeypatch):
        """Legacy fallback applies ``(no subject)`` when EMAIL_SUBJECT is
        unset. The canonical path lets the handler's ``_send_via_email_outbox``
        derive the subject from ``extra_context.email_subject`` instead."""
        from tools.send_message import _send_via_email

        monkeypatch.setenv("VALOR_SESSION_ID", "sess-legacy-nosubject")
        monkeypatch.setenv("EMAIL_REPLY_TO", "alice@example.com")
        monkeypatch.setenv("ALLOW_LEGACY_RPUSH_FALLBACK", "1")
        monkeypatch.delenv("EMAIL_SUBJECT", raising=False)
        monkeypatch.delenv("EMAIL_IN_REPLY_TO", raising=False)
        monkeypatch.setattr("tools.send_message._lookup_session", lambda sid: None)

        _send_via_email("hi")
        payload = json.loads(r.lpop("email:outbox:sess-legacy-nosubject"))
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
