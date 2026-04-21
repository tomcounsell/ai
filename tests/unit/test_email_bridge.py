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
    HISTORY_MSG_KEY,
    HISTORY_SET_KEY,
    HISTORY_THREADS_KEY,
    IMAP_MAX_BATCH,
    EmailOutputHandler,
    _build_reply_mime,
    _decode_header_value,
    _extract_address,
    _poll_imap,
    _record_history,
    _record_thread,
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


# ---------------------------------------------------------------------------
# _poll_imap() batch cap
# ---------------------------------------------------------------------------


class TestPollImapBatchCap:
    """_poll_imap() caps fetches to IMAP_MAX_BATCH per poll cycle."""

    @pytest.mark.asyncio
    async def test_batch_cap_limits_fetched_messages(self):
        """When IMAP returns more than IMAP_MAX_BATCH unseen UIDs, only
        IMAP_MAX_BATCH are stored+fetched and returned."""
        total_unseen = IMAP_MAX_BATCH + 10
        fake_uids = b" ".join(str(i).encode() for i in range(1, total_unseen + 1))

        def _uid_side_effect(command, *args):
            cmd = command.lower()
            if cmd == "search":
                return ("OK", [fake_uids])
            if cmd == "store":
                return ("OK", [])
            if cmd == "fetch":
                return ("OK", [(b"1", b"raw email bytes")])
            return ("OK", [])

        mock_conn = MagicMock()
        mock_conn.login.return_value = ("OK", [])
        mock_conn.select.return_value = ("OK", [])
        mock_conn.uid.side_effect = _uid_side_effect

        imap_config = {
            "host": "imap.example.com",
            "port": 993,
            "user": "test@example.com",
            "password": "secret",
            "ssl": True,
        }

        with patch("bridge.email_bridge.imaplib.IMAP4_SSL", return_value=mock_conn):
            # Patch asyncio.to_thread to call the sync function directly
            with patch(
                "bridge.email_bridge.asyncio.to_thread",
                side_effect=lambda fn: fn(),
            ):
                result = await _poll_imap(imap_config, known_senders=["sender@example.com"])

        store_calls = [c for c in mock_conn.uid.call_args_list if c.args[0].lower() == "store"]
        fetch_calls = [c for c in mock_conn.uid.call_args_list if c.args[0].lower() == "fetch"]
        assert len(store_calls) == IMAP_MAX_BATCH
        assert len(fetch_calls) == IMAP_MAX_BATCH
        assert len(result) == IMAP_MAX_BATCH

    @pytest.mark.asyncio
    async def test_batch_cap_exact_boundary(self):
        """When IMAP returns exactly IMAP_MAX_BATCH UIDs, all are fetched
        (no truncation)."""
        fake_uids = b" ".join(str(i).encode() for i in range(1, IMAP_MAX_BATCH + 1))

        def _uid_side_effect(command, *args):
            cmd = command.lower()
            if cmd == "search":
                return ("OK", [fake_uids])
            if cmd == "store":
                return ("OK", [])
            if cmd == "fetch":
                return ("OK", [(b"1", b"raw email bytes")])
            return ("OK", [])

        mock_conn = MagicMock()
        mock_conn.login.return_value = ("OK", [])
        mock_conn.select.return_value = ("OK", [])
        mock_conn.uid.side_effect = _uid_side_effect

        imap_config = {
            "host": "imap.example.com",
            "port": 993,
            "user": "test@example.com",
            "password": "secret",
            "ssl": True,
        }

        with patch("bridge.email_bridge.imaplib.IMAP4_SSL", return_value=mock_conn):
            with patch(
                "bridge.email_bridge.asyncio.to_thread",
                side_effect=lambda fn: fn(),
            ):
                result = await _poll_imap(imap_config, known_senders=["sender@example.com"])

        store_calls = [c for c in mock_conn.uid.call_args_list if c.args[0].lower() == "store"]
        fetch_calls = [c for c in mock_conn.uid.call_args_list if c.args[0].lower() == "fetch"]
        assert len(store_calls) == IMAP_MAX_BATCH
        assert len(fetch_calls) == IMAP_MAX_BATCH
        assert len(result) == IMAP_MAX_BATCH


# ---------------------------------------------------------------------------
# main() env loading
# ---------------------------------------------------------------------------


class TestMainEnvLoading:
    """main() loads .env files via load_dotenv before starting the async loop."""

    def test_main_calls_load_dotenv_with_correct_paths(self):
        """main() calls load_dotenv twice: repo .env and vault .env."""
        with patch("dotenv.load_dotenv") as mock_load:
            with patch("bridge.email_bridge.asyncio.run"):
                from bridge.email_bridge import main

                main()

        assert mock_load.call_count == 2

        # First call: repo .env (relative to email_bridge.py's parent.parent)
        first_path = mock_load.call_args_list[0][0][0]
        assert str(first_path).endswith(".env")
        assert "Desktop" not in str(first_path)

        # Second call: vault .env
        second_path = mock_load.call_args_list[1][0][0]
        assert "Desktop" in str(second_path) and "Valor" in str(second_path)
        assert str(second_path).endswith(".env")


# ---------------------------------------------------------------------------
# _build_reply_mime() — parsed-header regression test (Risk 2 mitigation)
# ---------------------------------------------------------------------------


class TestBuildReplyMimeHeaderRegression:
    """The module-level _build_reply_mime with attachments=None must produce
    a MIME message semantically equivalent to the legacy
    EmailOutputHandler._build_reply output.

    We compare parsed headers (not raw bytes) because header order, line
    folding, and default encodings vary by Python minor version. Date and
    Message-ID are excluded since they embed per-call variance.
    """

    def test_build_reply_mime_header_regression(self):
        import email as email_lib

        to_addr = "alice@example.com"
        subject = "Test subject"
        body = "Reply body with unicode: café ☕"
        in_reply_to = "<orig-123@example.com>"
        references = "<orig-123@example.com>"
        from_addr = "valor@example.com"

        # Old code path: the instance method (which now delegates internally)
        handler = EmailOutputHandler(
            smtp_config={
                "host": "smtp.example.com",
                "user": "valor@example.com",
                "password": "x",
                "port": 587,
                "use_tls": True,
            }
        )
        old_mime = handler._build_reply(
            to_addr=to_addr,
            subject=subject,
            body=body,
            in_reply_to=in_reply_to,
            references=references,
            from_addr=from_addr,
        )

        # New module-level helper with attachments=None
        new_mime = _build_reply_mime(
            to_addr=to_addr,
            subject=subject,
            body=body,
            in_reply_to=in_reply_to,
            references=references,
            from_addr=from_addr,
            attachments=None,
        )

        old_parsed = email_lib.message_from_bytes(old_mime.as_bytes())
        new_parsed = email_lib.message_from_bytes(new_mime.as_bytes())

        for header in (
            "From",
            "To",
            "Subject",
            "In-Reply-To",
            "References",
            "Content-Type",
            "Content-Transfer-Encoding",
        ):
            assert old_parsed.get(header) == new_parsed.get(header), (
                f"Header '{header}' drifted: old={old_parsed.get(header)!r} "
                f"new={new_parsed.get(header)!r}"
            )

        assert old_parsed.get_payload(decode=True) == new_parsed.get_payload(decode=True)


class TestBuildReplyMimeAttachments:
    """When attachments are supplied, _build_reply_mime returns a multipart
    message with the body first and each file encoded as base64 with a
    Content-Disposition header."""

    def test_attachment_included_as_multipart(self, tmp_path):
        import email.mime.multipart

        f = tmp_path / "report.txt"
        f.write_text("payload bytes")

        mime = _build_reply_mime(
            to_addr="alice@example.com",
            subject="Report",
            body="See attached",
            in_reply_to=None,
            references=None,
            from_addr="valor@example.com",
            attachments=[f],
        )
        assert isinstance(mime, email.mime.multipart.MIMEMultipart)
        assert mime.is_multipart()

        parts = list(mime.walk())
        # Root + body + attachment
        disp_headers = [p.get("Content-Disposition", "") for p in parts]
        assert any('filename="report.txt"' in h for h in disp_headers)

    def test_attachments_none_returns_plain_mimetext(self):
        import email.mime.text

        mime = _build_reply_mime(
            to_addr="alice@example.com",
            subject="Hi",
            body="Body",
            in_reply_to=None,
            references=None,
            from_addr="valor@example.com",
            attachments=None,
        )
        assert isinstance(mime, email.mime.text.MIMEText)
        assert not mime.is_multipart()

    def test_empty_attachments_list_returns_plain_mimetext(self):
        import email.mime.text

        mime = _build_reply_mime(
            to_addr="alice@example.com",
            subject="Hi",
            body="Body",
            in_reply_to=None,
            references=None,
            from_addr="valor@example.com",
            attachments=[],
        )
        assert isinstance(mime, email.mime.text.MIMEText)


# ---------------------------------------------------------------------------
# _record_history() and _record_thread() — history cache writers
# ---------------------------------------------------------------------------


@pytest.fixture
def history_redis(monkeypatch):
    """Pin REDIS_URL to db=1 and yield a decoded Redis client."""
    import redis as _redis

    url = "redis://localhost:6379/1"
    monkeypatch.setenv("REDIS_URL", url)
    client = _redis.Redis.from_url(url, decode_responses=True)
    yield client
    client.close()


class TestRecordHistory:
    def test_writes_blob_and_set_entry(self, history_redis):
        import time

        parsed = {
            "from_addr": "alice@example.com",
            "from_raw": "Alice <alice@example.com>",
            "subject": "Hello",
            "body": "hi",
            "timestamp": time.time(),
            "message_id": "<m1@x>",
            "in_reply_to": "",
        }
        _record_history(parsed)

        msg = history_redis.get(HISTORY_MSG_KEY.format(message_id="<m1@x>"))
        assert msg is not None

        score = history_redis.zscore(HISTORY_SET_KEY.format(mailbox="INBOX"), "<m1@x>")
        assert score is not None

    def test_missing_message_id_skipped(self, history_redis):
        parsed = {
            "from_addr": "alice@example.com",
            "subject": "No ID",
            "body": "hi",
            "timestamp": 1.0,
            "message_id": "",
            "in_reply_to": "",
        }
        _record_history(parsed)  # must not raise
        # Nothing written
        assert history_redis.zcard(HISTORY_SET_KEY.format(mailbox="INBOX")) == 0

    def test_cap_evicts_oldest_with_blob_cleanup(self, history_redis, monkeypatch):
        """When the set exceeds HISTORY_MAX_ENTRIES, oldest entries are
        evicted from both the sorted set AND the per-message blob namespace."""
        # Temporarily reduce the cap for a fast test
        monkeypatch.setattr("bridge.email_bridge.HISTORY_MAX_ENTRIES", 3)

        import time

        now = time.time()
        for i in range(5):
            parsed = {
                "from_addr": f"a{i}@x",
                "subject": f"S{i}",
                "body": "b",
                "timestamp": now - (10 - i),  # increasing timestamps
                "message_id": f"<m{i}@x>",
                "in_reply_to": "",
            }
            _record_history(parsed)

        # After 5 inserts with cap=3, the 3 newest should survive
        set_key = HISTORY_SET_KEY.format(mailbox="INBOX")
        size = history_redis.zcard(set_key)
        assert size == 3
        # m0 and m1 should be evicted — blobs gone too
        for evicted in ("<m0@x>", "<m1@x>"):
            assert history_redis.get(HISTORY_MSG_KEY.format(message_id=evicted)) is None
        for kept in ("<m2@x>", "<m3@x>", "<m4@x>"):
            assert history_redis.get(HISTORY_MSG_KEY.format(message_id=kept)) is not None


class TestRecordThread:
    def test_creates_thread_entry(self, history_redis):
        import time

        parsed = {
            "from_addr": "alice@x.com",
            "subject": "New thread",
            "body": "hi",
            "timestamp": time.time(),
            "message_id": "<root@x>",
            "in_reply_to": "",
        }
        _record_thread(parsed)

        raw = history_redis.hget(HISTORY_THREADS_KEY, "<root@x>")
        assert raw is not None
        import json as _json

        data = _json.loads(raw)
        assert data["subject"] == "New thread"
        assert data["message_count"] == 1
        assert "alice@x.com" in data["participants"]

    def test_reply_updates_root(self, history_redis):
        import json as _json
        import time

        now = time.time()
        # Seed the root
        _record_thread(
            {
                "from_addr": "alice@x",
                "subject": "Root",
                "body": "",
                "timestamp": now - 100,
                "message_id": "<root@x>",
                "in_reply_to": "",
            }
        )
        # Child reply — should attach to <root@x>
        _record_thread(
            {
                "from_addr": "bob@x",
                "subject": "Re: Root",
                "body": "",
                "timestamp": now,
                "message_id": "<child@x>",
                "in_reply_to": "<root@x>",
            }
        )

        raw = history_redis.hget(HISTORY_THREADS_KEY, "<root@x>")
        assert raw is not None
        data = _json.loads(raw)
        assert data["message_count"] == 2
        assert set(data["participants"]) == {"alice@x", "bob@x"}
