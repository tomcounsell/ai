"""Unit tests for ``bridge.email_relay``.

Covers the unified payload drain path: atomic LPOP, requeue-with-counter on
failure, DLQ after ``MAX_EMAIL_RELAY_RETRIES`` attempts, and heartbeat writes.

Uses live local Redis via the xdist-aware ``redis_test_url`` fixture so
``pytest -n auto`` is safe (each worker gets its own db number).
"""

from __future__ import annotations

import json
import time
from unittest.mock import patch

import pytest
import redis

from bridge.email_relay import (
    EMAIL_RELAY_HEARTBEAT_KEY,
    MAX_EMAIL_RELAY_RETRIES,
    _normalize_payload,
    process_outbox,
)


@pytest.fixture
def r(monkeypatch, redis_test_url):
    """Redis client pinned to the xdist-aware test db so the relay uses the same db."""
    monkeypatch.setenv("REDIS_URL", redis_test_url)
    monkeypatch.setenv("SMTP_HOST", "smtp.test.local")
    monkeypatch.setenv("SMTP_USER", "valor@test.local")
    monkeypatch.setenv("SMTP_PASSWORD", "x")
    client = redis.Redis.from_url(redis_test_url, decode_responses=True)
    yield client
    client.close()


class TestNormalizePayload:
    def test_missing_to_rejected(self):
        msg = {"session_id": "s", "body": "hi", "timestamp": 1.0}
        assert _normalize_payload(dict(msg)) is None

    def test_missing_body_rejected(self):
        msg = {"session_id": "s", "to": "a@x", "timestamp": 1.0}
        assert _normalize_payload(dict(msg)) is None

    def test_empty_body_allowed(self):
        # Empty string body is valid (attachments-only path); relay CLI layer
        # rejects empty-body-and-no-attachments separately.
        msg = {"session_id": "s", "to": "a@x", "body": "", "timestamp": 1.0}
        result = _normalize_payload(dict(msg))
        assert result is not None
        assert result["body"] == ""

    def test_defaults_applied(self, monkeypatch):
        monkeypatch.setenv("SMTP_USER", "sender@x")
        msg = {"session_id": "s", "to": "a@x", "body": "hi", "timestamp": 1.0}
        result = _normalize_payload(dict(msg))
        assert result["subject"] == "(no subject)"
        assert result["from_addr"] == "sender@x"
        assert result["attachments"] == []
        assert result["in_reply_to"] is None
        assert result["references"] is None


class TestProcessOutboxHeartbeat:
    @pytest.mark.asyncio
    async def test_heartbeat_written_each_cycle(self, r):
        await process_outbox()
        hb = r.get(EMAIL_RELAY_HEARTBEAT_KEY)
        assert hb is not None
        # Within the last few seconds
        assert abs(time.time() - float(hb)) < 5
        ttl = r.ttl(EMAIL_RELAY_HEARTBEAT_KEY)
        # TTL should be positive and <= 300 (the configured value)
        assert 0 < ttl <= 300


class TestProcessOutboxSend:
    @pytest.mark.asyncio
    async def test_successful_send_drains_payload(self, r):
        key = "email:outbox:cli-ok-1"
        payload = {
            "session_id": "cli-ok-1",
            "to": "alice@example.com",
            "subject": "Hi",
            "body": "Hello",
            "attachments": [],
            "in_reply_to": None,
            "references": None,
            "from_addr": "valor@test.local",
            "timestamp": time.time(),
        }
        r.rpush(key, json.dumps(payload))

        with patch("bridge.email_relay._send_smtp_sync") as mock_send:
            sent = await process_outbox()

        assert sent == 1
        assert mock_send.call_count == 1
        # Queue should be empty now
        assert r.llen(key) == 0

    @pytest.mark.asyncio
    async def test_failure_requeues_with_counter(self, r):
        key = "email:outbox:cli-fail-1"
        payload = {
            "session_id": "cli-fail-1",
            "to": "alice@example.com",
            "subject": "Hi",
            "body": "Hello",
            "attachments": [],
            "in_reply_to": None,
            "references": None,
            "from_addr": "valor@test.local",
            "timestamp": time.time(),
        }
        r.rpush(key, json.dumps(payload))

        def boom(*args, **kwargs):
            raise ConnectionRefusedError("smtp down")

        with patch("bridge.email_relay._send_smtp_sync", side_effect=boom):
            sent = await process_outbox()

        assert sent == 0
        # Payload should be requeued with _relay_attempts=1
        queued = r.lrange(key, 0, -1)
        assert len(queued) == 1
        requeued = json.loads(queued[0])
        assert requeued["_relay_attempts"] == 1

    @pytest.mark.asyncio
    async def test_dlq_after_max_retries(self, r):
        key = "email:outbox:cli-dlq-1"
        payload = {
            "session_id": "cli-dlq-1",
            "to": "alice@example.com",
            "subject": "Hi",
            "body": "Hello",
            "attachments": [],
            "in_reply_to": None,
            "references": None,
            "from_addr": "valor@test.local",
            "timestamp": time.time(),
            "_relay_attempts": MAX_EMAIL_RELAY_RETRIES - 1,
        }
        r.rpush(key, json.dumps(payload))

        def boom(*args, **kwargs):
            raise ConnectionRefusedError("smtp down")

        dl_calls = []

        def fake_write_dead_letter(**kwargs):
            dl_calls.append(kwargs)

        with patch("bridge.email_relay._send_smtp_sync", side_effect=boom):
            with patch("bridge.email_dead_letter.write_dead_letter", fake_write_dead_letter):
                sent = await process_outbox()

        assert sent == 0
        # Queue should be empty — NOT re-pushed after exhaustion
        assert r.llen(key) == 0
        # DLQ should have been invoked once
        assert len(dl_calls) == 1
        assert dl_calls[0]["session_id"] == "cli-dlq-1"

    @pytest.mark.asyncio
    async def test_text_payload_dlqd_as_malformed(self, r):
        """A legacy ``text``-only payload is DLQ'd — the compat shim is gone.

        Pins ``body == ""`` on the DLQ record: ``_dead_letter_message`` reads
        ``message.get("body", "")``, and with the shim removed the ``text``
        content is dropped at the DLQ boundary rather than aliased to
        ``body``. If a future change reintroduces ``text`` aliasing inside
        the DLQ path, this assertion fails.
        """
        key = "email:outbox:legacy-1"
        payload = {
            "session_id": "legacy-1",
            "to": "alice@example.com",
            "text": "Legacy body via text field",
            "timestamp": time.time(),
        }
        r.rpush(key, json.dumps(payload))

        dl_calls = []

        def fake_write_dead_letter(**kwargs):
            dl_calls.append(kwargs)

        with patch("bridge.email_relay._send_smtp_sync") as mock_send:
            with patch("bridge.email_dead_letter.write_dead_letter", fake_write_dead_letter):
                sent = await process_outbox()

        assert sent == 0
        # No SMTP send occurred — payload rejected before the network path.
        assert mock_send.call_count == 0
        # Queue is empty — LPOPped and not re-pushed.
        assert r.llen(key) == 0
        # DLQ invoked exactly once.
        assert len(dl_calls) == 1
        # Content-loss pin: ``text`` is not aliased to ``body`` on DLQ.
        assert dl_calls[0]["body"] == ""

    @pytest.mark.asyncio
    async def test_malformed_payload_dlqd_without_retry(self, r):
        key = "email:outbox:malformed-1"
        # Missing `to` AND missing `body` — not recoverable
        r.rpush(key, json.dumps({"session_id": "malformed-1", "timestamp": 1.0}))

        dl_calls = []

        def fake_write_dead_letter(**kwargs):
            dl_calls.append(kwargs)

        with patch("bridge.email_dead_letter.write_dead_letter", fake_write_dead_letter):
            sent = await process_outbox()

        assert sent == 0
        assert r.llen(key) == 0  # LPOPped and not re-pushed
        assert len(dl_calls) == 1

    @pytest.mark.asyncio
    async def test_missing_attachment_dlqd(self, r, tmp_path):
        key = "email:outbox:attach-missing"
        payload = {
            "session_id": "attach-missing",
            "to": "alice@example.com",
            "body": "body",
            "subject": "hi",
            "attachments": [str(tmp_path / "does-not-exist.pdf")],
            "in_reply_to": None,
            "references": None,
            "from_addr": "valor@test.local",
            "timestamp": time.time(),
        }
        r.rpush(key, json.dumps(payload))

        dl_calls = []

        def fake_write_dead_letter(**kwargs):
            dl_calls.append(kwargs)

        with patch("bridge.email_dead_letter.write_dead_letter", fake_write_dead_letter):
            sent = await process_outbox()

        assert sent == 0
        assert r.llen(key) == 0
        assert len(dl_calls) == 1


class TestProcessOutboxAttachment:
    @pytest.mark.asyncio
    async def test_attachment_included_in_mime(self, r, tmp_path):
        attach_path = tmp_path / "report.txt"
        attach_path.write_text("hello world")

        key = "email:outbox:attach-ok"
        payload = {
            "session_id": "attach-ok",
            "to": "alice@example.com",
            "body": "See attached",
            "subject": "Report",
            "attachments": [str(attach_path)],
            "in_reply_to": None,
            "references": None,
            "from_addr": "valor@test.local",
            "timestamp": time.time(),
        }
        r.rpush(key, json.dumps(payload))

        captured = {}

        def fake_send(to_addr, mime_msg, from_addr):
            captured["msg"] = mime_msg

        with patch("bridge.email_relay._send_smtp_sync", side_effect=fake_send):
            sent = await process_outbox()

        assert sent == 1
        msg = captured["msg"]
        assert msg.is_multipart()
        disp_headers = [part.get("Content-Disposition", "") for part in msg.walk()]
        assert any('filename="report.txt"' in h for h in disp_headers)


class TestInReplyToHeaders:
    @pytest.mark.asyncio
    async def test_in_reply_to_and_references_propagate(self, r):
        key = "email:outbox:thread-1"
        payload = {
            "session_id": "thread-1",
            "to": "alice@example.com",
            "subject": "Re: deploy",
            "body": "ack",
            "attachments": [],
            "in_reply_to": "<abc@host>",
            "references": "<abc@host>",
            "from_addr": "valor@test.local",
            "timestamp": time.time(),
        }
        r.rpush(key, json.dumps(payload))

        captured = {}

        def fake_send(to_addr, mime_msg, from_addr):
            captured["msg"] = mime_msg

        with patch("bridge.email_relay._send_smtp_sync", side_effect=fake_send):
            await process_outbox()

        msg = captured["msg"]
        assert msg["In-Reply-To"] == "<abc@host>"
        assert msg["References"] == "<abc@host>"
