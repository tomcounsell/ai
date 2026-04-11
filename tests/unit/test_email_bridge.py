"""Unit tests for bridge/email_bridge.py.

Tests EmailOutputHandler.send(), _parse_imap_message(), react() no-op,
and SMTP header construction for thread continuation.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from bridge.email_bridge import (
    EmailOutputHandler,
    _decode_header_value,
    _extract_email_address,
    _extract_plain_body,
    _parse_imap_message,
)

# ---------------------------------------------------------------------------
# Helper: build a mock AgentSession
# ---------------------------------------------------------------------------


def _make_session(
    session_id: str = "email_proj_alice@test.com_123",
    extra_context: dict | None = None,
) -> MagicMock:
    session = MagicMock()
    session.session_id = session_id
    session.extra_context = extra_context or {}
    return session


# ---------------------------------------------------------------------------
# EmailOutputHandler.react()
# ---------------------------------------------------------------------------


class TestEmailOutputHandlerReact:
    @pytest.mark.asyncio
    async def test_react_is_noop(self):
        """react() must not raise and must have no observable side effects."""
        handler = EmailOutputHandler()
        # Should not raise
        await handler.react("alice@example.com", 0, "👍")
        await handler.react("alice@example.com", 0, None)
        await handler.react("alice@example.com", 0)


# ---------------------------------------------------------------------------
# EmailOutputHandler.send()
# ---------------------------------------------------------------------------


class TestEmailOutputHandlerSend:
    @pytest.mark.asyncio
    async def test_send_empty_text_is_noop(self):
        """Empty text must not attempt SMTP delivery."""
        handler = EmailOutputHandler()
        with patch("bridge.email_bridge._smtp_send_with_retry") as mock_smtp:
            await handler.send("alice@example.com", "", 0)
            mock_smtp.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_calls_smtp_with_correct_recipient(self):
        handler = EmailOutputHandler()
        session = _make_session(
            extra_context={
                "transport": "email",
                "email_recipient": "alice@example.com",
                "email_subject": "Hello",
            }
        )
        with patch("bridge.email_bridge._smtp_send_with_retry", return_value=True) as mock_smtp:
            await handler.send("alice@example.com", "Hello world", 0, session)
        mock_smtp.assert_called_once()
        call_kwargs = mock_smtp.call_args.kwargs
        assert call_kwargs["recipient"] == "alice@example.com"
        assert call_kwargs["body"] == "Hello world"

    @pytest.mark.asyncio
    async def test_send_prefixes_re_to_subject(self):
        """Subject without 'Re:' prefix gets one added."""
        handler = EmailOutputHandler()
        session = _make_session(
            extra_context={
                "email_recipient": "alice@example.com",
                "email_subject": "Hello",
            }
        )
        with patch("bridge.email_bridge._smtp_send_with_retry", return_value=True) as mock_smtp:
            await handler.send("alice@example.com", "Response text", 0, session)
        call_kwargs = mock_smtp.call_args.kwargs
        assert call_kwargs["subject"].startswith("Re:")

    @pytest.mark.asyncio
    async def test_send_does_not_double_prefix_re(self):
        """Subject already starting with 'Re:' must not get another prefix."""
        handler = EmailOutputHandler()
        session = _make_session(
            extra_context={
                "email_recipient": "alice@example.com",
                "email_subject": "Re: Original subject",
            }
        )
        with patch("bridge.email_bridge._smtp_send_with_retry", return_value=True) as mock_smtp:
            await handler.send("alice@example.com", "Response", 0, session)
        subject = mock_smtp.call_args.kwargs["subject"]
        assert not subject.startswith("Re: Re:")
        assert subject == "Re: Original subject"

    @pytest.mark.asyncio
    async def test_send_adds_in_reply_to_header(self):
        """When email_message_id is in extra_context, In-Reply-To header is set."""
        handler = EmailOutputHandler()
        session = _make_session(
            extra_context={
                "email_recipient": "alice@example.com",
                "email_subject": "Hello",
                "email_message_id": "<original-msg-id@example.com>",
            }
        )
        with patch("bridge.email_bridge._smtp_send_with_retry", return_value=True) as mock_smtp:
            await handler.send("alice@example.com", "Response", 0, session)
        headers = mock_smtp.call_args.kwargs.get("headers", {})
        assert headers.get("In-Reply-To") == "<original-msg-id@example.com>"

    @pytest.mark.asyncio
    async def test_send_no_session_uses_chat_id_as_recipient(self):
        """When no session is provided, chat_id is used as recipient."""
        handler = EmailOutputHandler()
        with patch("bridge.email_bridge._smtp_send_with_retry", return_value=True) as mock_smtp:
            await handler.send("fallback@example.com", "Hello", 0, None)
        recipient = mock_smtp.call_args.kwargs["recipient"]
        assert recipient == "fallback@example.com"

    @pytest.mark.asyncio
    async def test_send_ignores_nonzero_reply_to_msg_id(self):
        """reply_to_msg_id is always ignored for email (email uses In-Reply-To headers)."""
        handler = EmailOutputHandler()
        session = _make_session(
            extra_context={"email_recipient": "alice@example.com", "email_subject": "Hi"}
        )
        with patch("bridge.email_bridge._smtp_send_with_retry", return_value=True):
            # Should not raise even with a bogus msg id
            await handler.send("alice@example.com", "Hi", 42, session)


# ---------------------------------------------------------------------------
# _parse_imap_message()
# ---------------------------------------------------------------------------


def _make_raw_email(
    from_addr: str = "Alice <alice@example.com>",
    subject: str = "Test subject",
    body: str = "Hello there",
    message_id: str = "<abc123@example.com>",
    in_reply_to: str = "",
) -> bytes:
    """Build a minimal raw RFC 2822 email."""
    lines = [
        f"From: {from_addr}",
        "To: valor@yuda.me",
        f"Subject: {subject}",
        f"Message-ID: {message_id}",
        "MIME-Version: 1.0",
        "Content-Type: text/plain; charset=utf-8",
        "",
        body,
    ]
    if in_reply_to:
        lines.insert(4, f"In-Reply-To: {in_reply_to}")
    return "\r\n".join(lines).encode("utf-8")


class TestParseImapMessage:
    def test_parses_basic_email(self):
        raw = _make_raw_email()
        result = _parse_imap_message(raw)
        assert result is not None
        assert result["sender"] == "alice@example.com"
        assert result["subject"] == "Test subject"
        assert result["body"] == "Hello there"
        assert result["message_id"] == "<abc123@example.com>"

    def test_returns_none_for_empty_body(self):
        raw = _make_raw_email(body="")
        result = _parse_imap_message(raw)
        assert result is None

    def test_returns_none_for_whitespace_only_body(self):
        raw = _make_raw_email(body="   \n  \t  ")
        result = _parse_imap_message(raw)
        assert result is None

    def test_returns_none_for_missing_sender(self):
        raw = b"Subject: Test\r\nContent-Type: text/plain\r\n\r\nHello"
        result = _parse_imap_message(raw)
        assert result is None

    def test_extracts_in_reply_to(self):
        raw = _make_raw_email(in_reply_to="<original@example.com>")
        result = _parse_imap_message(raw)
        assert result is not None
        assert result["in_reply_to"] == "<original@example.com>"

    def test_in_reply_to_empty_when_not_present(self):
        raw = _make_raw_email()
        result = _parse_imap_message(raw)
        assert result is not None
        assert result["in_reply_to"] == ""

    def test_extracts_sender_from_display_name_format(self):
        raw = _make_raw_email(from_addr="Bob Smith <bob@corp.com>")
        result = _parse_imap_message(raw)
        assert result is not None
        assert result["sender"] == "bob@corp.com"

    def test_handles_malformed_bytes_gracefully(self):
        # Should not raise; may return None or partial result
        # Key invariant: no exception
        _parse_imap_message(b"not a valid email\xff\xfe")


class TestDecodeHeaderValue:
    def test_ascii_header(self):
        assert _decode_header_value("Hello World") == "Hello World"

    def test_none_returns_empty(self):
        assert _decode_header_value(None) == ""

    def test_bytes_decoded(self):
        result = _decode_header_value(b"Hello")
        assert result == "Hello"


class TestExtractEmailAddress:
    def test_plain_address(self):
        assert _extract_email_address("alice@example.com") == "alice@example.com"

    def test_display_name_format(self):
        assert _extract_email_address("Alice <alice@example.com>") == "alice@example.com"

    def test_empty_returns_empty(self):
        assert _extract_email_address("") == ""

    def test_lowercased(self):
        assert _extract_email_address("ALICE@EXAMPLE.COM") == "alice@example.com"


class TestExtractPlainBody:
    def test_simple_text_plain(self):
        import email as email_stdlib

        raw = b"Content-Type: text/plain\r\n\r\nHello world"
        msg = email_stdlib.message_from_bytes(raw)
        assert _extract_plain_body(msg) == "Hello world"

    def test_multipart_prefers_text_plain(self):
        import email as email_stdlib

        raw = (
            b"Content-Type: multipart/alternative; boundary=BOUNDARY\r\n\r\n"
            b"--BOUNDARY\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
            b"Plain text\r\n"
            b"--BOUNDARY\r\n"
            b"Content-Type: text/html\r\n\r\n"
            b"<p>HTML</p>\r\n"
            b"--BOUNDARY--\r\n"
        )
        msg = email_stdlib.message_from_bytes(raw)
        body = _extract_plain_body(msg)
        assert "Plain text" in body
        assert "<p>" not in body

    def test_attachment_only_returns_empty(self):
        import email as email_stdlib

        raw = (
            b"Content-Type: multipart/mixed; boundary=BOUNDARY\r\n\r\n"
            b"--BOUNDARY\r\n"
            b"Content-Type: application/octet-stream\r\n"
            b"Content-Disposition: attachment; filename=file.bin\r\n\r\n"
            b"binarydata\r\n"
            b"--BOUNDARY--\r\n"
        )
        msg = email_stdlib.message_from_bytes(raw)
        body = _extract_plain_body(msg)
        assert body == ""
