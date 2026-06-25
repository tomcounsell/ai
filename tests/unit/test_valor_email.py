"""Unit tests for ``tools.valor_email``.

Covers CLI argparse, the unified send payload contract, session_id format,
--reply-to normalization, and read subcommand output paths.

Uses live local Redis via the xdist-aware ``redis_test_url`` fixture so
``pytest -n auto`` is safe (each worker gets its own db number).
"""

from __future__ import annotations

import argparse
import json
import re
import time

import pytest
import redis

from tools.valor_email import (
    EMAIL_DRAFT_INLINE_MAX_TOTAL_BYTES,
    _build_session_id,
    _imap_fallback_fetch,
    _normalize_msgid,
    _validate_attachment_files,
    cmd_draft,
    cmd_read,
    cmd_send,
    cmd_threads,
)


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


class TestNormalizeMsgid:
    def test_adds_angle_brackets(self):
        assert _normalize_msgid("abc@host") == "<abc@host>"

    def test_preserves_angle_brackets(self):
        assert _normalize_msgid("<abc@host>") == "<abc@host>"

    def test_strips_whitespace(self):
        assert _normalize_msgid("  abc@host  ") == "<abc@host>"

    def test_empty_rejected(self):
        with pytest.raises(argparse.ArgumentTypeError):
            _normalize_msgid("")

    def test_whitespace_only_rejected(self):
        with pytest.raises(argparse.ArgumentTypeError):
            _normalize_msgid("   ")


class TestImapFallbackFetchAttachments:
    """Critique C2: the cache-miss IMAP fallback read path must project attachments."""

    def test_fallback_projects_attachment_metadata(self, monkeypatch):
        import email.message

        # Build a raw multipart email with an attachment.
        msg = email.message.EmailMessage()
        msg["From"] = "tom@yuda.me"
        msg["To"] = "valor@example.com"
        msg["Subject"] = "report"
        msg["Message-ID"] = "<fb-1@x>"
        msg["Date"] = "Mon, 01 Jan 2026 00:00:00 +0000"
        msg.set_content("see attached")
        msg.add_attachment(b"%PDF data", maintype="application", subtype="pdf", filename="r.pdf")
        raw_bytes = msg.as_bytes()

        class FakeConn:
            def login(self, *a, **k):
                return ("OK", [b""])

            def select(self, *a, **k):
                return ("OK", [b"1"])

            def uid(self, cmd, *args):
                if cmd == "search":
                    return ("OK", [b"1"])
                if cmd == "fetch":
                    return ("OK", [(b"1 (RFC822 {%d}" % len(raw_bytes), raw_bytes)])
                return ("NO", [b""])

            def logout(self):
                return ("OK", [b""])

        monkeypatch.setattr("tools.valor_email.imaplib.IMAP4_SSL", lambda *a, **k: FakeConn())
        monkeypatch.setattr(
            "bridge.email_bridge._get_imap_config",
            lambda: {"host": "imap.test", "port": 993, "user": "u", "password": "p", "ssl": True},
        )
        monkeypatch.setattr("bridge.routing.ensure_email_routing_loaded", lambda: True)
        monkeypatch.setattr("bridge.routing.get_known_email_search_terms", lambda: ["tom@yuda.me"])

        results = _imap_fallback_fetch(limit=5, search=None, since_ts=None)
        assert len(results) == 1
        atts = results[0]["attachments"]
        assert len(atts) == 1
        assert atts[0]["filename"] == "r.pdf"
        assert atts[0]["content_type"] == "application/pdf"
        # Read-only path never persists bytes, so path stays None (critique C1).
        assert atts[0]["path"] is None


class TestBuildSessionId:
    def test_format(self):
        sid = _build_session_id()
        # cli-<seconds>-<pid>-<8hex>
        assert re.match(r"^cli-\d+-\d+-[0-9a-f]{8}$", sid)

    def test_uniqueness_across_same_second(self):
        seen = {_build_session_id() for _ in range(100)}
        assert len(seen) == 100  # token_hex(4) gives 32 bits — collisions effectively never


class TestCmdSend:
    def _args(self, **overrides):
        defaults = {
            # ``--to`` uses argparse ``action="append"``, so the runtime
            # shape is ``list[str]`` (each entry may itself be a comma-
            # separated string flattened by cmd_send).
            "to": ["alice@example.com"],
            "subject": None,
            "message": "Hello",
            "file": None,
            "reply_to": None,
            "json": False,
        }
        defaults.update(overrides)
        return argparse.Namespace(**defaults)

    def test_rejects_empty_message_and_no_file(self, r, capsys):
        rc = cmd_send(self._args(message=""))
        assert rc == 1
        captured = capsys.readouterr()
        assert "message or --file" in captured.err

    def test_queues_unified_payload(self, r, capsys):
        rc = cmd_send(self._args(subject="Re: foo", message="Body!"))
        assert rc == 0

        # Find the queued key — it uses our generated session_id
        keys = list(r.scan_iter(match="email:outbox:cli-*"))
        assert len(keys) == 1
        raw = r.lpop(keys[0])
        payload = json.loads(raw)
        assert payload["to"] == ["alice@example.com"]
        assert payload["subject"] == "Re: foo"
        assert payload["body"] == "Body!"
        assert payload["attachments"] == []
        assert payload["in_reply_to"] is None
        assert payload["references"] is None
        assert payload["from_addr"] == "valor@test.local"
        assert payload["session_id"].startswith("cli-")

    def test_reply_to_propagates_to_both_headers(self, r):
        rc = cmd_send(self._args(message="ack", reply_to="<abc@host>"))
        assert rc == 0
        keys = list(r.scan_iter(match="email:outbox:cli-*"))
        assert len(keys) == 1
        payload = json.loads(r.lpop(keys[0]))
        assert payload["in_reply_to"] == "<abc@host>"
        assert payload["references"] == "<abc@host>"

    def test_rejects_missing_file(self, r, tmp_path, capsys):
        # ``--file`` is action="append" → runtime shape is list[str].
        rc = cmd_send(self._args(message="hi", file=[str(tmp_path / "not-here.pdf")]))
        assert rc == 1
        captured = capsys.readouterr()
        assert "File not found" in captured.err

    def test_attachment_path_absolute(self, r, tmp_path):
        f = tmp_path / "payload.txt"
        f.write_text("contents")
        rc = cmd_send(self._args(message="see attached", file=[str(f)]))
        assert rc == 0
        keys = list(r.scan_iter(match="email:outbox:cli-*"))
        payload = json.loads(r.lpop(keys[0]))
        assert payload["attachments"] == [str(f.resolve())]

    def test_multiple_files_all_land_in_payload_in_order(self, r, tmp_path):
        # Two ``--file`` flags must collect BOTH absolute paths into the
        # outbox ``attachments`` list, preserving CLI order (the multi-file
        # bug: a single string arg kept only the last file).
        a = tmp_path / "a.pdf"
        b = tmp_path / "b.pdf"
        a.write_text("aaa")
        b.write_text("bbb")
        rc = cmd_send(self._args(message="see both", file=[str(a), str(b)]))
        assert rc == 0
        keys = list(r.scan_iter(match="email:outbox:cli-*"))
        assert len(keys) == 1
        payload = json.loads(r.lpop(keys[0]))
        assert payload["attachments"] == [str(a.resolve()), str(b.resolve())]

    def test_rejects_second_invalid_file_without_skipping(self, r, tmp_path, capsys):
        good = tmp_path / "good.txt"
        good.write_text("ok")
        rc = cmd_send(self._args(message="hi", file=[str(good), str(tmp_path / "missing.pdf")]))
        assert rc == 1
        captured = capsys.readouterr()
        assert "File not found" in captured.err

    def test_ttl_set_on_queue_key(self, r):
        cmd_send(self._args(message="hi"))
        keys = list(r.scan_iter(match="email:outbox:cli-*"))
        ttl = r.ttl(keys[0])
        assert 0 < ttl <= 3600


class TestCmdRead:
    def _args(self, **overrides):
        defaults = {
            "mailbox": "INBOX",
            "limit": 10,
            "search": None,
            "since": None,
            "json": False,
        }
        defaults.update(overrides)
        return argparse.Namespace(**defaults)

    def test_empty_cache_no_messages(self, r, capsys, monkeypatch):
        # Disable IMAP fallback by clearing env vars
        monkeypatch.delenv("IMAP_HOST", raising=False)
        monkeypatch.delenv("IMAP_USER", raising=False)
        monkeypatch.delenv("IMAP_PASSWORD", raising=False)

        rc = cmd_read(self._args())
        assert rc == 0
        captured = capsys.readouterr()
        assert "No messages found" in captured.out

    def test_non_inbox_rejected(self, r, capsys):
        rc = cmd_read(self._args(mailbox="SENT"))
        assert rc == 1

    def test_cache_hit_prints_rows(self, r, capsys):
        from bridge.email_bridge import HISTORY_MSG_KEY, HISTORY_SET_KEY

        now = time.time()
        set_key = HISTORY_SET_KEY.format(mailbox="INBOX")
        r.set(
            HISTORY_MSG_KEY.format(message_id="<m@x>"),
            json.dumps(
                {
                    "from_addr": "alice@x.com",
                    "subject": "Ping",
                    "body": "Hello!",
                    "timestamp": now,
                    "message_id": "<m@x>",
                    "in_reply_to": "",
                }
            ),
        )
        r.zadd(set_key, {"<m@x>": now})

        rc = cmd_read(self._args(limit=5))
        assert rc == 0
        out = capsys.readouterr().out
        assert "alice@x.com" in out
        assert "Ping" in out
        assert "Hello!" in out

    def test_json_output(self, r, capsys):
        from bridge.email_bridge import HISTORY_MSG_KEY, HISTORY_SET_KEY

        now = time.time()
        set_key = HISTORY_SET_KEY.format(mailbox="INBOX")
        r.set(
            HISTORY_MSG_KEY.format(message_id="<m@x>"),
            json.dumps(
                {
                    "from_addr": "alice@x.com",
                    "subject": "Ping",
                    "body": "Hello!",
                    "timestamp": now,
                    "message_id": "<m@x>",
                    "in_reply_to": "",
                }
            ),
        )
        r.zadd(set_key, {"<m@x>": now})

        rc = cmd_read(self._args(json=True, limit=5))
        assert rc == 0
        out = capsys.readouterr().out
        parsed = json.loads(out)
        # --json output is a dict envelope, unified across read/send/threads.
        assert isinstance(parsed, dict)
        assert parsed["count"] == 1
        assert parsed["mailbox"] == "INBOX"
        assert isinstance(parsed["messages"], list)
        assert parsed["messages"][0]["from_addr"] == "alice@x.com"


class TestCmdThreads:
    def _args(self, **overrides):
        defaults = {"json": False}
        defaults.update(overrides)
        return argparse.Namespace(**defaults)

    def test_empty(self, r, capsys):
        rc = cmd_threads(self._args())
        assert rc == 0
        assert "No threads found" in capsys.readouterr().out

    def test_lists_threads(self, r, capsys):
        from bridge.email_bridge import HISTORY_THREADS_KEY

        now = time.time()
        r.hset(
            HISTORY_THREADS_KEY,
            "<root@x>",
            json.dumps(
                {
                    "root": "<root@x>",
                    "subject": "Subject",
                    "message_count": 3,
                    "last_ts": now,
                    "participants": ["a@x", "b@x"],
                }
            ),
        )
        rc = cmd_threads(self._args())
        assert rc == 0
        out = capsys.readouterr().out
        assert "Subject" in out
        assert " 3 " in out


class TestValidateAttachmentFiles:
    """Tests for the shared _validate_attachment_files helper."""

    def test_valid_file_returns_resolved_path(self, tmp_path):
        f = tmp_path / "a.pdf"
        f.write_text("data")
        result = _validate_attachment_files([str(f)])
        assert result == [str(f.resolve())]

    def test_missing_file_returns_none(self, tmp_path, capsys):
        result = _validate_attachment_files([str(tmp_path / "missing.pdf")])
        assert result is None
        assert "File not found" in capsys.readouterr().err

    def test_multiple_valid_files(self, tmp_path):
        a = tmp_path / "a.pdf"
        b = tmp_path / "b.pdf"
        a.write_text("a")
        b.write_text("b")
        result = _validate_attachment_files([str(a), str(b)])
        assert result == [str(a.resolve()), str(b.resolve())]

    def test_send_and_draft_produce_identical_missing_file_error(self, tmp_path, capsys):
        """Shared helper guarantees identical error text from send and draft."""
        missing = str(tmp_path / "nope.pdf")
        import argparse

        # cmd_send path
        args_send = argparse.Namespace(
            to=["x@y.com"], subject=None, message="hi", file=[missing], reply_to=None, json=False
        )
        cmd_send(args_send)
        err_send = capsys.readouterr().err

        # cmd_draft path
        args_draft = argparse.Namespace(
            to=["x@y.com"], subject=None, message="hi", file=[missing], reply_to=None, json=False
        )
        cmd_draft(args_draft)
        err_draft = capsys.readouterr().err

        assert "File not found" in err_send
        assert "File not found" in err_draft
        # Both must contain the same missing path
        assert missing in err_send
        assert missing in err_draft


class TestCmdDraft:
    """Tests for the valor-email draft subcommand."""

    def _args(self, **overrides):
        defaults = {
            "to": ["recipient@example.com"],
            "subject": "Test Draft",
            "message": "Please see attached.",
            "file": None,
            "reply_to": None,
            "json": False,
        }
        defaults.update(overrides)
        return argparse.Namespace(**defaults)

    def _patch_subprocess(self, monkeypatch, fake_run):
        """Patch subprocess.run at the module level where cmd_draft uses it."""
        from unittest.mock import MagicMock

        mock = MagicMock(side_effect=fake_run)
        monkeypatch.setattr("subprocess.run", mock)
        return mock

    def test_rejects_empty_message_and_no_file(self, capsys, monkeypatch):
        rc = cmd_draft(self._args(message="", file=None))
        assert rc == 1
        assert "message or --file" in capsys.readouterr().err

    def test_successful_draft_creation(self, monkeypatch, capsys):
        from unittest.mock import MagicMock

        draft_response = json.dumps({"id": "draft123", "message": {"id": "msg456"}})

        def fake_run(cmd, *args, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stdout = draft_response
            result.stderr = ""
            return result

        self._patch_subprocess(monkeypatch, fake_run)
        rc = cmd_draft(self._args())
        assert rc == 0
        out = capsys.readouterr().out
        assert "Draft created" in out

    def test_json_output_on_success(self, monkeypatch, capsys):
        from unittest.mock import MagicMock

        draft_response = json.dumps({"id": "draft123"})

        def fake_run(cmd, *args, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stdout = draft_response
            result.stderr = ""
            return result

        self._patch_subprocess(monkeypatch, fake_run)
        rc = cmd_draft(self._args(json=True))
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["created"] is True
        assert "draft" in data

    def test_invalid_grant_exits_nonzero_with_actionable_message(self, monkeypatch, capsys):
        """Auth failure surfaces an actionable 'gws auth login' message."""
        from unittest.mock import MagicMock

        def fake_run(cmd, *args, **kwargs):
            result = MagicMock()
            result.returncode = 1
            result.stderr = "error: invalid_grant: Token has been expired or revoked."
            result.stdout = ""
            return result

        self._patch_subprocess(monkeypatch, fake_run)
        rc = cmd_draft(self._args())
        assert rc == 1
        err = capsys.readouterr().err
        assert "gws auth login" in err
        assert "send immediately" in err

    def test_fresh_draft_no_reply_prefix(self, monkeypatch, tmp_path, capsys):
        """Fresh draft (no --reply-to) must NOT get a spurious 'Re:' subject prefix."""
        import base64
        import email as email_lib
        from unittest.mock import MagicMock

        captured_cmd = []

        def fake_run(cmd, *args, **kwargs):
            captured_cmd.extend(cmd)
            result = MagicMock()
            result.returncode = 0
            result.stdout = json.dumps({"id": "d1"})
            result.stderr = ""
            return result

        self._patch_subprocess(monkeypatch, fake_run)
        rc = cmd_draft(self._args(subject="My Subject", reply_to=None))
        assert rc == 0

        # Find the --json payload passed to gws and decode the raw MIME
        # The last --json arg is the gws payload
        payload_str = None
        for i in range(len(captured_cmd) - 1, -1, -1):
            if captured_cmd[i] == "--json" and i + 1 < len(captured_cmd):
                payload_str = captured_cmd[i + 1]
                break

        if payload_str:
            payload = json.loads(payload_str)
            raw_b64 = payload["message"]["raw"]
            # Add padding just in case
            raw_bytes = base64.urlsafe_b64decode(raw_b64 + "==")
            msg = email_lib.message_from_bytes(raw_bytes)
            subject = msg.get("Subject", "")
            assert not subject.startswith("Re:"), (
                f"Fresh draft got spurious Re: prefix: {subject!r}"
            )

    def test_attachment_included_in_mime(self, monkeypatch, tmp_path, capsys):
        """Files <= inline threshold are included as MIME attachment parts."""
        import base64
        import email as email_lib
        from unittest.mock import MagicMock

        test_file = tmp_path / "report.pdf"
        test_file.write_bytes(b"%PDF-1.4 content here")

        captured_cmd = []

        def fake_run(cmd, *args, **kwargs):
            captured_cmd.extend(cmd)
            result = MagicMock()
            result.returncode = 0
            result.stdout = json.dumps({"id": "d1"})
            result.stderr = ""
            return result

        self._patch_subprocess(monkeypatch, fake_run)
        rc = cmd_draft(self._args(file=[str(test_file)]))
        assert rc == 0

        # Decode the raw MIME to verify the attachment part exists
        payload_str = None
        for i in range(len(captured_cmd) - 1, -1, -1):
            if captured_cmd[i] == "--json" and i + 1 < len(captured_cmd):
                payload_str = captured_cmd[i + 1]
                break

        assert payload_str is not None
        payload = json.loads(payload_str)
        raw_b64 = payload["message"]["raw"]
        raw_bytes = base64.urlsafe_b64decode(raw_b64 + "==")
        msg = email_lib.message_from_bytes(raw_bytes)
        assert msg.is_multipart()
        filenames = [p.get_filename() for p in msg.walk() if p.get_filename()]
        assert "report.pdf" in filenames

    def test_draft_create_failure_cleans_up_drive_upload(self, monkeypatch, tmp_path, capsys):
        """On draft-create failure, best-effort cleanup of uploaded Drive files."""
        from unittest.mock import MagicMock

        large_file = tmp_path / "big.pdf"
        large_file.write_bytes(b"x" * (EMAIL_DRAFT_INLINE_MAX_TOTAL_BYTES + 1))

        cleanup_called = [False]

        def fake_run(cmd, *args, **kwargs):
            result = MagicMock()
            # Drive upload (success)
            if "files" in cmd and "create" in cmd and "--upload" in cmd:
                result.returncode = 0
                result.stdout = json.dumps(
                    {
                        "id": "drive-file-id-123",
                        "webViewLink": "https://drive.google.com/file/d/drive-file-id-123/view",
                    }
                )
                result.stderr = ""
            # Draft create (failure)
            elif "drafts" in cmd and "create" in cmd:
                result.returncode = 1
                result.stderr = "Error: API error"
                result.stdout = ""
            # Cleanup delete call
            elif "files" in cmd and "delete" in cmd:
                cleanup_called[0] = True
                result.returncode = 0
                result.stdout = ""
                result.stderr = ""
            else:
                result.returncode = 0
                result.stdout = ""
                result.stderr = ""
            return result

        self._patch_subprocess(monkeypatch, fake_run)
        rc = cmd_draft(self._args(file=[str(large_file)]))
        assert rc == 1
        err = capsys.readouterr().err
        # Either cleanup succeeded (confirmed) or orphaned fileId named
        assert cleanup_called[0] or "drive-file-id-123" in err

    def test_drive_upload_failure_returns_error_not_silent_fallback(
        self, monkeypatch, tmp_path, capsys
    ):
        """A Drive upload failure must return an error — no silent fallback to inline."""
        from unittest.mock import MagicMock

        large_file = tmp_path / "big.pdf"
        large_file.write_bytes(b"x" * (EMAIL_DRAFT_INLINE_MAX_TOTAL_BYTES + 1))

        def fake_run(cmd, *args, **kwargs):
            result = MagicMock()
            result.returncode = 1
            result.stderr = "Error: Drive upload quota exceeded"
            result.stdout = ""
            return result

        self._patch_subprocess(monkeypatch, fake_run)
        rc = cmd_draft(self._args(file=[str(large_file)]))
        assert rc == 1
        # Must not silently produce a draft

    def test_constant_is_25mib(self):
        """EMAIL_DRAFT_INLINE_MAX_TOTAL_BYTES defaults to 25 MiB."""
        assert EMAIL_DRAFT_INLINE_MAX_TOTAL_BYTES == 25 * 1024 * 1024


class TestValorEmailPromiseGate:
    """Promise gate integration (cycle-2 B-NEW-2: no --no-promise-gate flag)."""

    def test_send_help_does_not_mention_no_promise_gate(self):
        """Cycle-2 B-NEW-2: ``valor-email send --help`` must not advertise the bypass."""
        import sys as _sys
        from io import StringIO
        from unittest.mock import patch

        with patch.object(_sys, "argv", ["valor-email", "send", "--help"]):
            from tools.valor_email import main

            buf = StringIO()
            with patch.object(_sys, "stdout", buf), pytest.raises(SystemExit):
                main()
            help_output = buf.getvalue()

        assert "--no-promise-gate" not in help_output
        assert "VALOR_OPERATOR_MODE" not in help_output
        assert "PROMISE_GATE_ENABLED" not in help_output
