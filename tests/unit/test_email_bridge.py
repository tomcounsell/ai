"""Unit tests for bridge.email_bridge parsing helpers and EmailOutputHandler.

Uses real Python email library constructs to build test data — no mocks of
the standard library. SMTP is mocked via unittest.mock.patch to avoid network
calls.
"""

import email.message
import email.mime.multipart
import email.mime.text
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import bridge.email_bridge as eb
from bridge.email_bridge import (
    HISTORY_MSG_KEY,
    HISTORY_SET_KEY,
    HISTORY_THREADS_KEY,
    IMAP_MAX_BATCH,
    EmailOutputHandler,
    _build_reply_mime,
    _decode_header_value,
    _extract_address,
    _persist_attachments,
    _poll_imap,
    _public_attachment,
    _record_history,
    _record_thread,
    _sanitize_attachment_filename,
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
# Inbound attachment extraction + persistence
# ---------------------------------------------------------------------------


def _make_email_with_attachments(
    from_addr: str = "tom@yuda.me",
    subject: str = "Files attached",
    body: str | None = "Take a look at these.",
    message_id: str = "<att-001@example.com>",
    attachments: list[tuple[str, str, str, bytes]] | None = None,
) -> bytes:
    """Build a raw multipart email carrying attachments.

    ``attachments`` is a list of ``(filename, maintype, subtype, data)`` tuples.
    A ``None`` filename produces an attachment part with no filename header.
    """
    msg = email.message.EmailMessage()
    msg["From"] = from_addr
    msg["To"] = "valor@example.com"
    msg["Subject"] = subject
    if message_id:
        msg["Message-ID"] = message_id
    if body is not None:
        msg.set_content(body)
    for filename, maintype, subtype, data in attachments or []:
        kwargs = {"maintype": maintype, "subtype": subtype}
        if filename is not None:
            kwargs["filename"] = filename
        msg.add_attachment(data, **kwargs)
    return msg.as_bytes()


@pytest.fixture
def attachment_dirs(tmp_path, monkeypatch):
    """Redirect attachment storage + vault mirror to tmp dirs (no repo pollution)."""
    store = tmp_path / "data" / "media" / "email-attachments"
    vault = tmp_path / "work-vault" / "email-attachments"
    monkeypatch.setattr(eb, "EMAIL_ATTACHMENT_DIR", store)
    monkeypatch.setattr(eb, "EMAIL_ATTACHMENT_VAULT_SUBDIR", vault)
    return store, vault


class TestAttachmentExtraction:
    """_extract_attachment_metadata() / parse_email_message() attachment fields."""

    def test_single_attachment_parsed(self):
        raw = _make_email_with_attachments(
            attachments=[("report.pdf", "application", "pdf", b"%PDF-1.4 data")]
        )
        result = parse_email_message(raw)
        assert result is not None
        assert len(result["attachments"]) == 1
        att = result["attachments"][0]
        assert att["filename"] == "report.pdf"
        assert att["content_type"] == "application/pdf"
        assert att["size"] == len(b"%PDF-1.4 data")
        assert att["path"] is None  # not persisted by parse (critique C1)
        assert result["attachments_truncated"] is False

    def test_multiple_attachments_parsed(self):
        raw = _make_email_with_attachments(
            attachments=[
                ("a.pdf", "application", "pdf", b"aaa"),
                ("b.csv", "text", "csv", b"col1,col2"),
            ]
        )
        result = parse_email_message(raw)
        assert result is not None
        names = [a["filename"] for a in result["attachments"]]
        assert names == ["a.pdf", "b.csv"]

    def test_empty_body_with_attachment_is_processed(self):
        """An attachment-only email (no text body) must NOT be dropped."""
        raw = _make_email_with_attachments(
            body=None,
            attachments=[("only.pdf", "application", "pdf", b"data")],
        )
        result = parse_email_message(raw)
        assert result is not None
        assert len(result["attachments"]) == 1

    def test_empty_body_no_attachment_still_dropped(self):
        """Empty body AND no attachments still returns None (guard intact)."""
        msg = email.message.EmailMessage()
        msg["From"] = "tom@yuda.me"
        msg["Subject"] = "nothing"
        msg["Message-ID"] = "<empty@x>"
        msg.set_content("")
        assert parse_email_message(msg.as_bytes()) is None

    def test_filename_with_path_traversal_sanitized(self):
        raw = _make_email_with_attachments(
            attachments=[("../../etc/passwd", "text", "plain", b"x")]
        )
        result = parse_email_message(raw)
        att = result["attachments"][0]
        assert "/" not in att["filename"]
        assert ".." not in att["filename"]
        assert att["filename"] == "passwd"

    def test_attachment_without_filename_falls_back(self):
        raw = _make_email_with_attachments(
            attachments=[(None, "application", "octet-stream", b"blob")]
        )
        result = parse_email_message(raw)
        atts = result["attachments"]
        assert len(atts) == 1
        assert atts[0]["filename"].startswith("attachment_")

    def test_size_cap_truncates(self, monkeypatch):
        """Cumulative size over the cap skips later parts and marks truncated."""
        monkeypatch.setattr(eb, "EMAIL_ATTACHMENT_MAX_TOTAL_BYTES", 10)
        raw = _make_email_with_attachments(
            attachments=[
                ("small.bin", "application", "octet-stream", b"12345"),
                ("big.bin", "application", "octet-stream", b"X" * 1000),
            ]
        )
        result = parse_email_message(raw)
        # First fits, second is over the cap → skipped, truncated flag set.
        assert result["attachments_truncated"] is True
        assert all(a["size"] <= 10 for a in result["attachments"])

    def test_parts_cap_truncates(self, monkeypatch):
        monkeypatch.setattr(eb, "EMAIL_ATTACHMENT_MAX_PARTS", 2)
        raw = _make_email_with_attachments(
            attachments=[
                ("a.bin", "application", "octet-stream", b"a"),
                ("b.bin", "application", "octet-stream", b"b"),
                ("c.bin", "application", "octet-stream", b"c"),
            ]
        )
        result = parse_email_message(raw)
        assert len(result["attachments"]) == 2
        assert result["attachments_truncated"] is True

    def test_non_multipart_email_has_empty_attachments(self):
        raw = _make_plain_email(body="just text")
        result = parse_email_message(raw)
        assert result["attachments"] == []
        assert result["attachments_truncated"] is False

    def test_public_attachment_strips_payload(self):
        att = {
            "filename": "x.pdf",
            "content_type": "application/pdf",
            "size": 3,
            "path": None,
            "_payload": b"abc",
        }
        pub = _public_attachment(att)
        assert "_payload" not in pub
        assert set(pub.keys()) == {"filename", "content_type", "size", "path"}


class TestSanitizeFilename:
    def test_strips_directories(self):
        assert _sanitize_attachment_filename("/abs/path/x.pdf", 0, "application/pdf") == "x.pdf"

    def test_collapses_unsafe_chars(self):
        out = _sanitize_attachment_filename("my file (1).pdf", 0, "application/pdf")
        assert out == "my_file_1_.pdf" or out.endswith(".pdf")
        assert "/" not in out and " " not in out

    def test_dotdot_falls_back(self):
        out = _sanitize_attachment_filename("..", 3, "application/pdf")
        assert out.startswith("attachment_3")


class TestPersistAttachments:
    """_persist_attachments() writes bytes to disk and mirrors to the vault."""

    def test_persists_bytes_and_sets_path(self, attachment_dirs):
        store, vault = attachment_dirs
        raw = _make_email_with_attachments(
            attachments=[("report.pdf", "application", "pdf", b"%PDF data")]
        )
        parsed = parse_email_message(raw)
        _persist_attachments(parsed)
        att = parsed["attachments"][0]
        assert att["path"] is not None
        from pathlib import Path

        p = Path(att["path"])
        assert p.exists()
        assert p.read_bytes() == b"%PDF data"
        assert "_payload" not in att  # transient bytes stripped
        # Vault mirror copied the file too
        assert any(vault.rglob("*report.pdf"))

    def test_same_name_collision_disambiguated(self, attachment_dirs):
        raw = _make_email_with_attachments(
            attachments=[
                ("image.png", "image", "png", b"first"),
                ("image.png", "image", "png", b"second"),
            ]
        )
        parsed = parse_email_message(raw)
        _persist_attachments(parsed)
        paths = [a["path"] for a in parsed["attachments"]]
        assert len(set(paths)) == 2  # distinct files, no overwrite
        from pathlib import Path

        assert {Path(p).read_bytes() for p in paths} == {b"first", b"second"}

    def test_empty_message_id_does_not_collide_cross_message(self, attachment_dirs):
        """Two Message-ID-less emails persist into distinct subdirs (critique C3)."""
        raw1 = _make_email_with_attachments(
            from_addr="a@x.com",
            subject="one",
            message_id="",
            attachments=[("f.pdf", "application", "pdf", b"one")],
        )
        raw2 = _make_email_with_attachments(
            from_addr="b@x.com",
            subject="two",
            message_id="",
            attachments=[("f.pdf", "application", "pdf", b"two")],
        )
        p1 = parse_email_message(raw1)
        p2 = parse_email_message(raw2)
        _persist_attachments(p1)
        _persist_attachments(p2)
        from pathlib import Path

        path1, path2 = p1["attachments"][0]["path"], p2["attachments"][0]["path"]
        assert Path(path1).parent != Path(path2).parent
        assert Path(path1).read_bytes() == b"one"
        assert Path(path2).read_bytes() == b"two"

    def test_vault_mirror_failure_is_nonfatal(self, attachment_dirs, monkeypatch):
        """A broken vault dir must not prevent disk persistence (failure-path)."""
        store, vault = attachment_dirs

        def boom(*a, **k):
            raise OSError("vault unwritable")

        monkeypatch.setattr(eb.shutil, "copy2", boom)
        raw = _make_email_with_attachments(attachments=[("ok.pdf", "application", "pdf", b"data")])
        parsed = parse_email_message(raw)
        # Should not raise despite the copy2 failure.
        _persist_attachments(parsed)
        from pathlib import Path

        assert Path(parsed["attachments"][0]["path"]).exists()


class TestRecordHistoryAttachments:
    """_record_history blob carries attachment metadata only — never bytes."""

    def test_blob_includes_attachments_metadata(self, attachment_dirs):
        import json

        raw = _make_email_with_attachments(
            attachments=[("doc.pdf", "application", "pdf", b"pdfbytes")]
        )
        parsed = parse_email_message(raw)
        _persist_attachments(parsed)

        captured = {}

        class FakePipe:
            def set(self, k, v, ex=None):
                captured["blob"] = v

            def zadd(self, *a, **k):
                pass

            def execute(self):
                pass

        class FakeRedis:
            def pipeline(self):
                return FakePipe()

            def zcard(self, *a, **k):
                return 1

        with patch("bridge.email_bridge._get_redis", return_value=FakeRedis()):
            _record_history(parsed)

        blob = json.loads(captured["blob"])
        assert blob["attachments"][0]["filename"] == "doc.pdf"
        assert blob["attachments"][0]["size"] == len(b"pdfbytes")
        assert "_payload" not in blob["attachments"][0]
        assert blob["attachments_truncated"] is False


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
        """Subject is prefixed with 'Re: ' for worker-reply sends.

        Regression for PR #1094: even when the inbound email carried **no**
        ``Message-ID`` header (so ``email_message_id`` is empty and
        ``in_reply_to`` becomes ``None``), the worker-reply path must still
        prepend ``"Re: "`` — this is the legacy contract that pre-dates
        PR #1094 and the parsed-header regression test guarantees.
        The ``force_reply_prefix=True`` default on ``_build_reply_mime``
        preserves this behavior; the relay/CLI path passes ``False`` so
        new sends are not silently mangled.
        """
        handler = EmailOutputHandler(smtp_config=self._make_smtp_config())

        session = MagicMock()
        session.extra_context = {
            # Intentionally empty: simulates an inbound email without a
            # Message-ID header. The Re: prefix must still be applied.
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
        assert subject == "Re: Original Subject"

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

    def test_main_calls_load_dotenv_with_correct_paths(self, monkeypatch):
        """main() calls load_dotenv twice: repo .env and vault .env.

        Defensive guard (Cycle-3 critique S-1): explicitly clear VALOR_LAUNCHD
        so this test continues to assert the dotenv-call expectation even when
        CI or a developer's shell has VALOR_LAUNCHD=1 set. Without this guard,
        the new gate added for #1325 makes the existing test order-dependent
        on environment state.
        """
        monkeypatch.delenv("VALOR_LAUNCHD", raising=False)

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

    def test_main_skips_dotenv_under_launchd(self, monkeypatch):
        """Under VALOR_LAUNCHD=1, main() must NOT call load_dotenv.

        Regression guard for the macOS TCC failure mode: launchd agents are
        blocked from reading ~/Desktop files, so unconditional dotenv reads
        would hang the bridge process at startup. Mirrors the same gate in
        bridge/telegram_bridge.py:42-48. See #1325 audit and #1338 installer.
        """
        monkeypatch.setenv("VALOR_LAUNCHD", "1")

        with patch("dotenv.load_dotenv") as mock_load:
            with patch("bridge.email_bridge.asyncio.run"):
                from bridge.email_bridge import main

                main()

        assert mock_load.call_count == 0, (
            "load_dotenv must not run when VALOR_LAUNCHD is set; launchd "
            "injects env vars directly into the plist, so dotenv reads of the "
            "iCloud-synced ~/Desktop/Valor/.env would block on macOS TCC."
        )


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

        to_addrs = "alice@example.com"
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
            to_addrs=to_addrs,
            subject=subject,
            body=body,
            in_reply_to=in_reply_to,
            references=references,
            from_addr=from_addr,
        )

        # New module-level helper with attachments=None
        new_mime = _build_reply_mime(
            to_addrs=to_addrs,
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
            to_addrs="alice@example.com",
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
            to_addrs="alice@example.com",
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
            to_addrs="alice@example.com",
            subject="Hi",
            body="Body",
            in_reply_to=None,
            references=None,
            from_addr="valor@example.com",
            attachments=[],
        )
        assert isinstance(mime, email.mime.text.MIMEText)


class TestBuildReplyMimeSubjectPrefix:
    """Subject ``Re:`` prefix semantics for ``_build_reply_mime``.

    Two callers have different contracts:

    - Worker reply (``EmailOutputHandler._build_reply``) passes
      ``force_reply_prefix=True`` so ``"Re:"`` is always prepended — the
      pre-#1094 behavior, preserved even when the inbound email lacked a
      ``Message-ID`` header.
    - Relay / CLI new-send path passes ``force_reply_prefix=False`` so
      caller-provided subjects are preserved verbatim; ``"Re:"`` is only
      added when ``in_reply_to`` is truthy.
    """

    def test_new_send_subject_unchanged(self):
        # CLI/relay contract: force_reply_prefix=False
        mime = _build_reply_mime(
            to_addrs="alice@example.com",
            subject="New meeting",
            body="Body",
            in_reply_to=None,
            references=None,
            from_addr="valor@example.com",
            force_reply_prefix=False,
        )
        assert mime["Subject"] == "New meeting"

    def test_new_send_with_existing_re_prefix_unchanged(self):
        # User deliberately typed "Re: ..." on a new send — don't touch it.
        mime = _build_reply_mime(
            to_addrs="alice@example.com",
            subject="Re: existing thread",
            body="Body",
            in_reply_to=None,
            references=None,
            from_addr="valor@example.com",
            force_reply_prefix=False,
        )
        assert mime["Subject"] == "Re: existing thread"

    def test_reply_prepends_re(self):
        mime = _build_reply_mime(
            to_addrs="alice@example.com",
            subject="Meeting tomorrow",
            body="ack",
            in_reply_to="<orig@host>",
            references="<orig@host>",
            from_addr="valor@example.com",
            force_reply_prefix=False,
        )
        assert mime["Subject"] == "Re: Meeting tomorrow"

    def test_reply_with_existing_re_prefix_not_doubled(self):
        mime = _build_reply_mime(
            to_addrs="alice@example.com",
            subject="Re: Meeting tomorrow",
            body="ack",
            in_reply_to="<orig@host>",
            references="<orig@host>",
            from_addr="valor@example.com",
            force_reply_prefix=False,
        )
        assert mime["Subject"] == "Re: Meeting tomorrow"

    def test_empty_subject_new_send_uses_no_subject_placeholder(self):
        # CLI/relay contract: force_reply_prefix=False
        mime = _build_reply_mime(
            to_addrs="alice@example.com",
            subject="",
            body="Body",
            in_reply_to=None,
            references=None,
            from_addr="valor@example.com",
            force_reply_prefix=False,
        )
        assert mime["Subject"] == "(no subject)"

    def test_empty_subject_reply_uses_re_no_subject(self):
        mime = _build_reply_mime(
            to_addrs="alice@example.com",
            subject="",
            body="Body",
            in_reply_to="<orig@host>",
            references="<orig@host>",
            from_addr="valor@example.com",
            force_reply_prefix=False,
        )
        assert mime["Subject"] == "Re: (no subject)"

    def test_worker_reply_path_always_prepends_re(self):
        # Worker reply path (default force_reply_prefix=True) always prepends
        # "Re:" even when in_reply_to is empty — preserves pre-#1094 behavior
        # for inbound emails that lacked a Message-ID header.
        mime = _build_reply_mime(
            to_addrs="alice@example.com",
            subject="Meeting tomorrow",
            body="ack",
            in_reply_to=None,
            references=None,
            from_addr="valor@example.com",
            # default force_reply_prefix=True
        )
        assert mime["Subject"] == "Re: Meeting tomorrow"

    def test_worker_empty_subject_no_in_reply_to_still_re_no_subject(self):
        # Worker reply path with empty subject and no in_reply_to still uses
        # the "Re: (no subject)" placeholder.
        mime = _build_reply_mime(
            to_addrs="alice@example.com",
            subject="",
            body="Body",
            in_reply_to=None,
            references=None,
            from_addr="valor@example.com",
        )
        assert mime["Subject"] == "Re: (no subject)"

    def test_worker_existing_re_prefix_not_doubled(self):
        # Even with force_reply_prefix=True, an existing "Re:" prefix is
        # not doubled (case-insensitive check).
        mime = _build_reply_mime(
            to_addrs="alice@example.com",
            subject="Re: Original",
            body="Body",
            in_reply_to=None,
            references=None,
            from_addr="valor@example.com",
        )
        assert mime["Subject"] == "Re: Original"


# ---------------------------------------------------------------------------
# _record_history() and _record_thread() — history cache writers
# ---------------------------------------------------------------------------


@pytest.fixture
def history_redis(monkeypatch, redis_test_url):
    """Pin REDIS_URL to the xdist-aware test db and yield a decoded Redis client."""
    import redis as _redis

    monkeypatch.setenv("REDIS_URL", redis_test_url)
    client = _redis.Redis.from_url(redis_test_url, decode_responses=True)
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


# ---------------------------------------------------------------------------
# Wedge guard: _body_references_attachments + extra_context tagging (#1775)
# ---------------------------------------------------------------------------


class TestBodyReferencesAttachments:
    """Unit tests for the pure attachment-reference detector."""

    from bridge.email_bridge import _body_references_attachments as _bra

    def test_returns_false_for_none(self):
        from bridge.email_bridge import _body_references_attachments

        assert _body_references_attachments(None) is False

    def test_returns_false_for_empty(self):
        from bridge.email_bridge import _body_references_attachments

        assert _body_references_attachments("") is False

    def test_detects_attached(self):
        from bridge.email_bridge import _body_references_attachments

        assert _body_references_attachments("Please see the attached report.") is True

    def test_detects_attachment(self):
        from bridge.email_bridge import _body_references_attachments

        assert _body_references_attachments("I'm sending the attachment now.") is True

    def test_detects_enclosed(self):
        from bridge.email_bridge import _body_references_attachments

        assert _body_references_attachments("Please find enclosed the contract.") is True

    def test_detects_find_attached(self):
        from bridge.email_bridge import _body_references_attachments

        assert _body_references_attachments("Find attached the invoice.") is True

    def test_case_insensitive(self):
        from bridge.email_bridge import _body_references_attachments

        assert _body_references_attachments("PLEASE SEE ATTACHED.") is True

    def test_no_false_positive_plain_body(self):
        from bridge.email_bridge import _body_references_attachments

        assert _body_references_attachments("Let's schedule a meeting for Monday.") is False

    def test_no_crash_on_odd_characters(self):
        from bridge.email_bridge import _body_references_attachments

        # Must not raise on unusual input
        result = _body_references_attachments("\x00\xff\n\t" * 100)
        assert isinstance(result, bool)


class TestWedgeGuardExtraContext:
    """Truth-table tests for the attachments_unrecoverable tagging in _process_inbound_email.

    Each test drives the real _process_inbound_email() function (not a copy of its logic)
    and inspects the extra_context_overrides passed to the mocked enqueue_agent_session.
    Deleting the production wedge guard would fail these tests.
    """

    _PROJECT_KEY = "wedge-test-project"
    _FROM_ADDR = "sender@example.com"

    def _make_parsed(
        self,
        body: str = "Please see attached reports.",
        attachments: list | None = None,
        attachments_truncated: bool = False,
    ) -> dict:
        return {
            "from_addr": self._FROM_ADDR,
            "from_raw": f"Sender <{self._FROM_ADDR}>",
            "to_addrs": ["valor@example.com"],
            "cc_addrs": [],
            "subject": "Reports",
            "body": body,
            "message_id": "<test@example.com>",
            "in_reply_to": "",
            "attachments": attachments if attachments is not None else [],
            "attachments_truncated": attachments_truncated,
            "timestamp": 1700000000.0,
        }

    def _project_config(self) -> dict:
        return {
            "_key": self._PROJECT_KEY,
            "name": self._PROJECT_KEY,
            "working_directory": "/tmp/wedge-test",
            "email": {
                "contacts": {
                    self._FROM_ADDR: {"name": "Sender"},
                }
            },
        }

    def _projects_json(self) -> dict:
        return {"projects": {self._PROJECT_KEY: self._project_config()}}

    async def _run(self, parsed: dict, monkeypatch) -> dict:
        """Route parsed dict through real _process_inbound_email; return extra_context_overrides."""
        import bridge.routing as routing
        from bridge.email_bridge import _process_inbound_email

        original_email_map = routing.EMAIL_TO_PROJECT.copy()
        original_active = routing.ACTIVE_PROJECTS[:]
        try:
            routing.EMAIL_TO_PROJECT[self._FROM_ADDR] = self._project_config()
            if self._PROJECT_KEY not in routing.ACTIVE_PROJECTS:
                routing.ACTIVE_PROJECTS.append(self._PROJECT_KEY)

            mock_enqueue = AsyncMock()
            import redis
            from popoto.redis_db import POPOTO_REDIS_DB

            # Stay on this process's per-process claimed test db (issue #2060),
            # not a hardcoded db=1 — otherwise a concurrent pytest process that
            # claimed db1 could flush this client's data mid-test.
            _test_db = POPOTO_REDIS_DB.connection_pool.connection_kwargs.get("db", 1)
            test_r = redis.Redis(db=_test_db, decode_responses=True)
            with patch("agent.agent_session_queue.enqueue_agent_session", mock_enqueue):
                with patch("bridge.email_bridge._get_redis", return_value=test_r):
                    await _process_inbound_email(parsed, self._projects_json())
            test_r.close()
        finally:
            routing.EMAIL_TO_PROJECT.clear()
            routing.EMAIL_TO_PROJECT.update(original_email_map)
            routing.ACTIVE_PROJECTS[:] = original_active

        assert mock_enqueue.called, "enqueue_agent_session should have been called"
        return mock_enqueue.call_args.kwargs.get("extra_context_overrides", {})

    @pytest.mark.asyncio
    async def test_case_a_references_empty_list_tagged(self, monkeypatch):
        """(a) body references attachments + empty list → tagged, recovered_count=0."""
        parsed = self._make_parsed(attachments=[], attachments_truncated=False)
        ctx = await self._run(parsed, monkeypatch)
        assert ctx.get("attachments_unrecoverable") is True
        assert ctx.get("attachments_recovered_count") == 0
        assert ctx.get("attachments_truncated") is False
        assert ctx.get("attachments_referenced") is True

    @pytest.mark.asyncio
    async def test_case_b_references_truncated_non_empty_tagged(self, monkeypatch):
        """(b) body references attachments + non-empty + truncated=True → tagged."""
        att = {
            "filename": "r1.pdf",
            "content_type": "application/pdf",
            "size": 100,
            "path": "/tmp/r1.pdf",
        }
        parsed = self._make_parsed(attachments=[att], attachments_truncated=True)
        ctx = await self._run(parsed, monkeypatch)
        assert ctx.get("attachments_unrecoverable") is True
        assert ctx.get("attachments_truncated") is True
        assert ctx.get("attachments_recovered_count") == 1
        assert ctx.get("attachments_referenced") is True

    @pytest.mark.asyncio
    async def test_case_c_references_non_empty_not_truncated_not_tagged(self, monkeypatch):
        """(c) body references attachments + non-empty + not truncated → no guard tag."""
        att = {
            "filename": "r1.pdf",
            "content_type": "application/pdf",
            "size": 100,
            "path": "/tmp/r1.pdf",
        }
        parsed = self._make_parsed(attachments=[att], attachments_truncated=False)
        ctx = await self._run(parsed, monkeypatch)
        assert "attachments_unrecoverable" not in ctx

    @pytest.mark.asyncio
    async def test_case_d_no_reference_not_tagged(self, monkeypatch):
        """(d) body has no attachment reference → not tagged, no false positive."""
        parsed = self._make_parsed(
            body="Let's schedule a call.", attachments=[], attachments_truncated=False
        )
        ctx = await self._run(parsed, monkeypatch)
        assert "attachments_unrecoverable" not in ctx
