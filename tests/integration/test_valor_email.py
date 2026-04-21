"""Integration tests for the valor-email CLI + email relay flow.

End-to-end: CLI pushes a payload to the Redis outbox, a single call to
``process_outbox`` drains it, and a mocked SMTP sees the send.

All tests run against live local Redis on db=1 (the popoto autouse fixture
flushes it before each test).
"""

from __future__ import annotations

import argparse
import json
import time
from unittest.mock import patch

import pytest
import redis

from bridge.email_relay import process_outbox
from tools.valor_email import cmd_send


@pytest.fixture
def r(monkeypatch):
    url = "redis://localhost:6379/1"
    monkeypatch.setenv("REDIS_URL", url)
    monkeypatch.setenv("SMTP_HOST", "smtp.test.local")
    monkeypatch.setenv("SMTP_USER", "valor@test.local")
    monkeypatch.setenv("SMTP_PASSWORD", "x")
    client = redis.Redis.from_url(url, decode_responses=True)
    yield client
    client.close()


def _send_args(**overrides):
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


class TestCliSendDrainsViaRelayToSmtp:
    @pytest.mark.asyncio
    async def test_end_to_end_plain_send(self, r):
        rc = cmd_send(_send_args(subject="Hi", message="Body"))
        assert rc == 0

        captured = {}

        def fake_send(to_addr, mime_msg, from_addr):
            captured["to"] = to_addr
            captured["subject"] = mime_msg["Subject"]
            captured["body"] = mime_msg.get_payload(decode=True).decode("utf-8")

        with patch("bridge.email_relay._send_smtp_sync", side_effect=fake_send):
            sent = await process_outbox()

        assert sent == 1
        assert captured["to"] == "alice@example.com"
        assert "Hi" in captured["subject"]
        assert captured["body"] == "Body"

    @pytest.mark.asyncio
    async def test_end_to_end_with_attachment(self, r, tmp_path):
        f = tmp_path / "doc.txt"
        f.write_text("payload")

        rc = cmd_send(_send_args(subject="Report", message="see attached", file=str(f)))
        assert rc == 0

        captured = {}

        def fake_send(to_addr, mime_msg, from_addr):
            captured["msg"] = mime_msg

        with patch("bridge.email_relay._send_smtp_sync", side_effect=fake_send):
            sent = await process_outbox()

        assert sent == 1
        msg = captured["msg"]
        assert msg.is_multipart()
        disp = [p.get("Content-Disposition", "") for p in msg.walk()]
        assert any('filename="doc.txt"' in h for h in disp)

    @pytest.mark.asyncio
    async def test_end_to_end_with_reply_to_threading(self, r):
        rc = cmd_send(_send_args(subject="Re: thing", message="ack", reply_to="<orig@host>"))
        assert rc == 0

        captured = {}

        def fake_send(to_addr, mime_msg, from_addr):
            captured["msg"] = mime_msg

        with patch("bridge.email_relay._send_smtp_sync", side_effect=fake_send):
            await process_outbox()

        msg = captured["msg"]
        assert msg["In-Reply-To"] == "<orig@host>"
        assert msg["References"] == "<orig@host>"

    @pytest.mark.asyncio
    async def test_end_to_end_dlq_after_retries(self, r):
        from bridge.email_relay import MAX_EMAIL_RELAY_RETRIES

        rc = cmd_send(_send_args(message="Will fail"))
        assert rc == 0

        dl_calls: list[dict] = []

        def fake_dl(**kwargs):
            dl_calls.append(kwargs)

        def boom(*args, **kwargs):
            raise ConnectionRefusedError("smtp")

        # Need MAX_EMAIL_RELAY_RETRIES calls to process_outbox() to exhaust the
        # in-memory counter — each call LPOPs the latest requeue.
        with patch("bridge.email_relay._send_smtp_sync", side_effect=boom):
            with patch("bridge.email_dead_letter.write_dead_letter", fake_dl):
                for _ in range(MAX_EMAIL_RELAY_RETRIES):
                    await process_outbox()

        # No DLQ calls until the final attempt
        assert len(dl_calls) == 1
        # Queue drained
        keys = list(r.scan_iter(match="email:outbox:cli-*"))
        assert keys == []


class TestPollLoopCacheIntegration:
    """The IMAP poll loop writes to the history cache; the CLI reads from it."""

    @pytest.mark.asyncio
    async def test_cache_writes_surface_via_cli(self, r):
        from bridge.email_bridge import _record_history
        from tools.email_history import get_recent_emails

        now = time.time()
        _record_history(
            {
                "from_addr": "alice@x.com",
                "from_raw": "Alice <alice@x.com>",
                "subject": "Testing",
                "body": "cached body",
                "timestamp": now,
                "message_id": "<cache-1@x>",
                "in_reply_to": "",
            }
        )

        result = get_recent_emails(limit=5)
        assert result["count"] == 1
        msg = result["messages"][0]
        assert msg["message_id"] == "<cache-1@x>"
        assert msg["subject"] == "Testing"
