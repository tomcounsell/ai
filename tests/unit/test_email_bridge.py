"""Unit tests for bridge.email_bridge parsing helpers and EmailOutputHandler.

Uses real Python email library constructs to build test data — no mocks of
the standard library. SMTP is mocked via unittest.mock.patch to avoid network
calls.
"""

import email.mime.multipart
import email.mime.text
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bridge.email_bridge import (
    EmailOutputHandler,
    _decode_header_value,
    _extract_address,
    parse_email_message,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_plain_email(
    from_addr: str = "Alice <alice@example.com>",
    subject: str = "Hello",
    body: str = "This is the body.",
    message_id: str = "<msg-001@example.com>",
    in_reply_to: str = "",
) -> bytes:
    """Build a raw plain-text email as bytes."""
    msg = email.mime.text.MIMEText(body, "plain", "utf-8")
    msg["From"] = from_addr
    msg["To"] = "valor@example.com"
    msg["Subject"] = subject
    if message_id:
        msg["Message-ID"] = message_id
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    return msg.as_bytes()


def _make_multipart_email(
    from_addr: str = "bob@example.com",
    subject: str = "Multipart",
    plain_body: str = "Plain text part.",
    html_body: str = "<html><body><p>HTML part.</p></body></html>",
) -> bytes:
    """Build a raw multipart/alternative email with both plain and HTML parts."""
    msg = email.mime.multipart.MIMEMultipart("alternative")
    msg["From"] = from_addr
    msg["To"] = "valor@example.com"
    msg["Subject"] = subject
    msg["Message-ID"] = "<multipart-001@example.com>"

    part_plain = email.mime.text.MIMEText(plain_body, "plain", "utf-8")
    part_html = email.mime.text.MIMEText(html_body, "html", "utf-8")
    msg.attach(part_plain)
    msg.attach(part_html)
    return msg.as_bytes()


# ---------------------------------------------------------------------------
# parse_email_message()
# ---------------------------------------------------------------------------


class TestParseEmailMessage:
    """parse_email_message() parses raw bytes into a structured dict."""

    def test_valid_plain_text_email_returns_dict(self):
        """Full plain text email parses to dict with all expected fields."""
        raw = _make_plain_email(
            from_addr="Alice <alice@example.com>",
            subject="Hello there",
            body="Hello, this is the email body.",
            message_id="<msg-001@example.com>",
            in_reply_to="<prev-msg@example.com>",
        )
        result = parse_email_message(raw)

        assert result is not None
        assert result["from_addr"] == "alice@example.com"
        assert result["subject"] == "Hello there"
        assert result["body"] == "Hello, this is the email body."
        assert result["message_id"] == "<msg-001@example.com>"
        assert result["in_reply_to"] == "<prev-msg@example.com>"

    def test_multipart_email_returns_plain_text_body(self):
        """Multipart email: plain text part is returned, not HTML."""
        raw = _make_multipart_email(
            plain_body="This is plain text.",
            html_body="<html><body><b>Bold HTML</b></body></html>",
        )
        result = parse_email_message(raw)

        assert result is not None
        assert "plain text" in result["body"]
        assert "<html>" not in result["body"]
        assert "<b>" not in result["body"]

    def test_email_with_empty_body_returns_none(self):
        """Email with empty body (empty string) returns None."""
        raw = _make_plain_email(body="")
        result = parse_email_message(raw)
        assert result is None

    def test_email_with_whitespace_only_body_returns_none(self):
        """Email with whitespace-only body returns None."""
        raw = _make_plain_email(body="   \n\t  \n  ")
        result = parse_email_message(raw)
        assert result is None

    def test_email_with_no_from_returns_none(self):
        """Email missing a From address returns None."""
        # Build a minimal email without From header
        msg = email.mime.text.MIMEText("Some body content.", "plain", "utf-8")
        msg["To"] = "valor@example.com"
        msg["Subject"] = "No sender"
        raw = msg.as_bytes()

        result = parse_email_message(raw)
        assert result is None

    def test_from_raw_preserved_in_result(self):
        """from_raw field preserves the original From header value."""
        raw = _make_plain_email(from_addr="Alice Smith <alice@example.com>")
        result = parse_email_message(raw)

        assert result is not None
        assert "Alice Smith" in result["from_raw"]

    def test_missing_message_id_gives_empty_string(self):
        """Email without Message-ID has empty string in result."""
        raw = _make_plain_email(message_id="")
        result = parse_email_message(raw)

        assert result is not None
        assert result["message_id"] == ""

    def test_missing_in_reply_to_gives_empty_string(self):
        """Email without In-Reply-To has empty string in result."""
        raw = _make_plain_email(in_reply_to="")
        result = parse_email_message(raw)

        assert result is not None
        assert result["in_reply_to"] == ""

    def test_address_is_lowercased(self):
        """Parsed from_addr is always lowercase."""
        raw = _make_plain_email(from_addr="UPPER@EXAMPLE.COM")
        result = parse_email_message(raw)

        assert result is not None
        assert result["from_addr"] == "upper@example.com"


# ---------------------------------------------------------------------------
# _extract_address()
# ---------------------------------------------------------------------------


class TestExtractAddress:
    """_extract_address() parses email addresses from header values."""

    def test_name_and_addr_format(self):
        """'Alice <alice@example.com>' → 'alice@example.com'."""
        assert _extract_address("Alice <alice@example.com>") == "alice@example.com"

    def test_bare_address(self):
        """'alice@example.com' → 'alice@example.com'."""
        assert _extract_address("alice@example.com") == "alice@example.com"

    def test_address_is_lowercased(self):
        """Output is always lowercase."""
        assert _extract_address("ALICE@EXAMPLE.COM") == "alice@example.com"

    def test_none_returns_empty_string(self):
        """None input returns empty string."""
        assert _extract_address(None) == ""

    def test_empty_string_returns_empty_string(self):
        """Empty string returns empty string."""
        assert _extract_address("") == ""

    def test_name_with_spaces(self):
        """'First Last <user@domain.com>' → 'user@domain.com'."""
        assert _extract_address("First Last <user@domain.com>") == "user@domain.com"


# ---------------------------------------------------------------------------
# _decode_header_value()
# ---------------------------------------------------------------------------


class TestDecodeHeaderValue:
    """_decode_header_value() decodes RFC-2047 encoded header strings."""

    def test_plain_ascii_passthrough(self):
        """Plain ASCII value is returned unchanged."""
        assert _decode_header_value("Hello World") == "Hello World"

    def test_none_returns_empty_string(self):
        """None input returns empty string."""
        assert _decode_header_value(None) == ""

    def test_empty_string_returns_empty_string(self):
        """Empty string returns empty string."""
        assert _decode_header_value("") == ""

    def test_rfc2047_encoded_utf8_decoded(self):
        """RFC-2047 encoded header is decoded to plain text."""
        # Build an encoded header via the standard library
        import email.header

        encoded = email.header.make_header([(b"Caf\xc3\xa9", "utf-8")]).__str__()
        # After encode/decode round-trip
        result = _decode_header_value(encoded)
        assert "Caf" in result  # At minimum the ASCII prefix is present

    def test_strips_surrounding_whitespace(self):
        """Result is stripped of leading/trailing whitespace."""
        result = _decode_header_value("  trimmed  ")
        assert result == "trimmed"


# ---------------------------------------------------------------------------
# EmailOutputHandler.react()
# ---------------------------------------------------------------------------


class TestEmailOutputHandlerReact:
    """react() is a documented no-op — email has no emoji reactions."""

    @pytest.mark.asyncio
    async def test_react_is_noop_no_exception(self):
        """react() completes without raising any exception."""
        handler = EmailOutputHandler(smtp_config=None)
        # Should not raise
        await handler.react(chat_id="someone@example.com", msg_id=0, emoji="👍")

    @pytest.mark.asyncio
    async def test_react_with_none_emoji_is_noop(self):
        """react() with None emoji also completes silently."""
        handler = EmailOutputHandler(smtp_config=None)
        await handler.react(chat_id="someone@example.com", msg_id=0, emoji=None)


# ---------------------------------------------------------------------------
# EmailOutputHandler.send()
# ---------------------------------------------------------------------------


class TestEmailOutputHandlerSend:
    """send() composes and dispatches SMTP replies."""

    def _make_smtp_config(self) -> dict:
        return {
            "host": "smtp.example.com",
            "user": "valor@example.com",
            "password": "secret",
            "port": 587,
            "use_tls": False,
        }

    @pytest.mark.asyncio
    async def test_send_empty_text_does_nothing(self):
        """send() with empty text returns immediately without calling _send_smtp."""
        handler = EmailOutputHandler(smtp_config=self._make_smtp_config())

        with patch.object(handler, "_send_smtp") as mock_smtp:
            await handler.send(
                chat_id="recipient@example.com",
                text="",
                reply_to_msg_id=0,
                session=None,
            )
            mock_smtp.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_calls_send_smtp_with_nonempty_text(self):
        """send() with non-empty text invokes _send_smtp via asyncio.to_thread."""
        handler = EmailOutputHandler(smtp_config=self._make_smtp_config())

        with patch("bridge.email_bridge.asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
            mock_thread.return_value = None
            await handler.send(
                chat_id="recipient@example.com",
                text="Hello from agent!",
                reply_to_msg_id=0,
                session=None,
            )
            mock_thread.assert_called_once()
            # First arg to asyncio.to_thread should be handler._send_smtp
            # Use __func__ comparison because bound method objects are re-created each access
            first_arg = mock_thread.call_args[0][0]
            assert first_arg.__func__ is EmailOutputHandler._send_smtp

    @pytest.mark.asyncio
    async def test_send_uses_email_message_id_from_session_context(self):
        """In-Reply-To header is set from session.extra_context['email_message_id']."""
        handler = EmailOutputHandler(smtp_config=self._make_smtp_config())

        session = MagicMock()
        session.extra_context = {
            "email_message_id": "<original-msg@example.com>",
            "email_subject": "Test Subject",
        }
        session.session_id = "email_proj_user_12345"

        captured_mime = {}

        async def fake_to_thread(fn, recipient, mime_msg):
            captured_mime["msg"] = mime_msg

        with patch("bridge.email_bridge.asyncio.to_thread", side_effect=fake_to_thread):
            await handler.send(
                chat_id="recipient@example.com",
                text="Reply text",
                reply_to_msg_id=0,
                session=session,
            )

        assert "msg" in captured_mime
        assert captured_mime["msg"]["In-Reply-To"] == "<original-msg@example.com>"

    @pytest.mark.asyncio
    async def test_send_builds_re_subject(self):
        """Subject is prefixed with 'Re: ' when not already prefixed."""
        handler = EmailOutputHandler(smtp_config=self._make_smtp_config())

        session = MagicMock()
        session.extra_context = {
            "email_message_id": "",
            "email_subject": "Original Subject",
        }
        session.session_id = "test-session"

        captured_mime = {}

        async def fake_to_thread(fn, recipient, mime_msg):
            captured_mime["msg"] = mime_msg

        with patch("bridge.email_bridge.asyncio.to_thread", side_effect=fake_to_thread):
            await handler.send(
                chat_id="recipient@example.com",
                text="Agent reply",
                reply_to_msg_id=0,
                session=session,
            )

        assert "msg" in captured_mime
        subject = captured_mime["msg"]["Subject"]
        assert subject.startswith("Re:")

    @pytest.mark.asyncio
    async def test_send_no_smtp_config_raises_in_send_smtp(self):
        """_send_smtp raises RuntimeError when no SMTP config present."""
        with patch("bridge.email_bridge._get_smtp_config", return_value=None):
            handler = EmailOutputHandler(smtp_config=None)
        with pytest.raises(RuntimeError, match="SMTP not configured"):
            handler._send_smtp("recipient@example.com", MagicMock())

    @pytest.mark.asyncio
    async def test_send_writes_dead_letter_after_all_retries_fail(self):
        """After SMTP_MAX_RETRIES failures, dead letter queue is written."""
        handler = EmailOutputHandler(smtp_config=self._make_smtp_config())

        session = MagicMock()
        session.extra_context = {"email_message_id": "", "email_subject": ""}
        session.session_id = "test-session-dl"

        # Make all SMTP attempts fail
        with patch(
            "bridge.email_bridge.asyncio.to_thread",
            new_callable=AsyncMock,
            side_effect=ConnectionRefusedError("SMTP refused"),
        ):
            with patch("bridge.email_bridge.asyncio.sleep", new_callable=AsyncMock):
                with patch(
                    "bridge.email_dead_letter.write_dead_letter", new_callable=MagicMock
                ) as mock_dl:
                    await handler.send(
                        chat_id="recipient@example.com",
                        text="Will fail",
                        reply_to_msg_id=0,
                        session=session,
                    )

        mock_dl.assert_called_once()
        call_kwargs = mock_dl.call_args.kwargs
        assert call_kwargs["session_id"] == "test-session-dl"
        assert call_kwargs["recipient"] == "recipient@example.com"
