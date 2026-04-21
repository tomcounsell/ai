"""Unit tests for ``tools.valor_email``.

Covers CLI argparse, the unified send payload contract, session_id format,
--reply-to normalization, and read subcommand output paths.

Uses live local Redis on db=1 (shared with the popoto autouse flush).
"""

from __future__ import annotations

import argparse
import json
import re
import time

import pytest
import redis

from tools.valor_email import _build_session_id, _normalize_msgid, cmd_read, cmd_send, cmd_threads


@pytest.fixture
def r(monkeypatch):
    url = "redis://localhost:6379/1"
    monkeypatch.setenv("REDIS_URL", url)
    monkeypatch.setenv("SMTP_USER", "valor@test.local")
    client = redis.Redis.from_url(url, decode_responses=True)
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
            "to": "alice@example.com",
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
        assert payload["to"] == "alice@example.com"
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
        rc = cmd_send(self._args(message="hi", file=str(tmp_path / "not-here.pdf")))
        assert rc == 1
        captured = capsys.readouterr()
        assert "File not found" in captured.err

    def test_attachment_path_absolute(self, r, tmp_path):
        f = tmp_path / "payload.txt"
        f.write_text("contents")
        rc = cmd_send(self._args(message="see attached", file=str(f)))
        assert rc == 0
        keys = list(r.scan_iter(match="email:outbox:cli-*"))
        payload = json.loads(r.lpop(keys[0]))
        assert payload["attachments"] == [str(f.resolve())]

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
        assert isinstance(parsed, list)
        assert parsed[0]["from_addr"] == "alice@x.com"


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
