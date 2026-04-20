"""Primary acceptance test for the worker-bypass fix (plan #1035 §Part C).

A PM session producing >4096 chars of raw output must NEVER reach the Redis
outbox (and from there, the Telegram relay) without going through the message
drafter first. This used to be a silent vulnerability — worker send_cb wrote
raw text straight to Redis, producing MessageTooLongError downstream.

The fix lives in ``agent.output_handler.TelegramRelayOutputHandler.send``:
it now calls ``bridge.message_drafter.draft_message`` before writing to the
outbox, and attaches the full raw output as a ``file_paths`` entry when the
drafter returned a ``full_output_file``.

This test bypasses the real worker process and tests the output-handler
boundary directly — the handler is the single funnel all PM/Dev output must
pass through. We verify:

- 4800 chars → payload.text ≤ 4096 AND payload.file_paths contains the
  .txt file with the raw content.
- If the drafter itself raises, the handler falls back to raw-text delivery
  (the relay's belt-and-suspenders length guard remains as the last line of
  defense — covered separately by test_relay_length_guard.py).

No real worker, no Redis, no LLM — we patch ``_get_redis`` with a dict-like
fake and patch ``draft_message`` to produce a deterministic short draft.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from agent.output_handler import TelegramRelayOutputHandler
from bridge.message_drafter import MessageDraft

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeRedis:
    """Minimal in-memory stand-in for a Redis client.

    Implements only the three calls TelegramRelayOutputHandler.send uses:
    ``rpush``, ``expire``, and (for inspection) a plain dict backing store.
    """

    def __init__(self):
        self.store: dict[str, list[str]] = {}
        self.ttls: dict[str, int] = {}

    def rpush(self, key: str, value: str) -> int:
        self.store.setdefault(key, []).append(value)
        return len(self.store[key])

    def expire(self, key: str, ttl: int) -> bool:
        self.ttls[key] = ttl
        return True


def _make_pm_session(session_id: str = "pm-session-worker-bypass"):
    """Build a minimal AgentSession-shaped stand-in.

    TelegramRelayOutputHandler.send only reads ``session_id`` off the session
    object; using SimpleNamespace avoids pulling in the full popoto model.
    """
    return SimpleNamespace(
        session_id=session_id,
        session_type="pm",
        session_mode="pm",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_long_output_gets_drafted_and_full_text_attached(tmp_path):
    """Oversize raw text never reaches the outbox — drafter always runs.

    Preconditions:
      - draft_message returns a SHORT drafted text + a full-output file path

    Expected outcome (the bypass fix):
      - payload["text"] is the short draft (<=4096 chars)
      - payload["file_paths"] is present and references the .txt with raw output
      - the referenced file exists on disk and contains the original raw text
    """
    handler = TelegramRelayOutputHandler()

    # Install the fake redis — avoids a real Redis dependency.
    fake_redis = _FakeRedis()
    handler._redis = fake_redis  # skip lazy _get_redis() connect

    # Write the full-output file to disk first so assertions can read it back.
    raw_text = "X" * 4800
    full_output_path = tmp_path / "worker_bypass_test_full.txt"
    full_output_path.write_text(raw_text)

    short_draft = "PM session complete — full output attached. (150 chars of summary.)"
    assert len(short_draft) < 4096

    fake_draft = MessageDraft(
        text=short_draft,
        full_output_file=full_output_path,
        was_drafted=True,
        artifacts={},
    )

    session = _make_pm_session()

    with patch(
        "bridge.message_drafter.draft_message",
        new=AsyncMock(return_value=fake_draft),
    ) as mock_draft:
        await handler.send(
            chat_id="12345",
            text=raw_text,
            reply_to_msg_id=42,
            session=session,
        )

    # Drafter invoked exactly once with medium=telegram.
    assert mock_draft.await_count == 1
    call = mock_draft.await_args
    assert call.args[0] == raw_text
    assert call.kwargs.get("medium") == "telegram"

    # Exactly one payload pushed to the session's outbox key.
    queue_key = f"telegram:outbox:{session.session_id}"
    entries = fake_redis.store.get(queue_key, [])
    assert len(entries) == 1, f"expected 1 queued payload, got {len(entries)}"

    payload = json.loads(entries[0])

    # (a) Delivery text is the short drafted message, well under the 4096 limit.
    assert len(payload["text"]) <= 4096, (
        f"payload.text is {len(payload['text'])} chars — must be <=4096"
    )
    assert payload["text"] == short_draft

    # (b) file_paths is attached and points at our .txt file.
    assert "file_paths" in payload, "file_paths must be set when drafter returned a file"
    assert payload["file_paths"] == [str(full_output_path)]

    # (c) The raw content is preserved on disk.
    assert full_output_path.exists()
    assert full_output_path.read_text() == raw_text
    assert len(full_output_path.read_text()) == 4800

    # The outbox TTL was applied (not strictly required for correctness, but
    # a regression in the outbox contract would surface here).
    assert queue_key in fake_redis.ttls


@pytest.mark.asyncio
async def test_drafter_failure_falls_back_to_raw_text_without_blocking(tmp_path):
    """Defense-in-depth: if draft_message itself raises, delivery still happens.

    Per the handler's try/except in output_handler.py, drafter failure
    MUST NOT block delivery. The relay's length guard is the last line of
    defense. This test ensures a raised exception inside draft_message does
    not propagate out of send().
    """
    handler = TelegramRelayOutputHandler()
    fake_redis = _FakeRedis()
    handler._redis = fake_redis

    raw_text = "Z" * 4800
    session = _make_pm_session(session_id="pm-session-drafter-crash")

    boom = AsyncMock(side_effect=RuntimeError("simulated Haiku outage"))

    with patch("bridge.message_drafter.draft_message", new=boom):
        # Must not raise — the handler swallows drafter errors.
        await handler.send(
            chat_id="12345",
            text=raw_text,
            reply_to_msg_id=42,
            session=session,
        )

    boom.assert_awaited_once()

    queue_key = f"telegram:outbox:{session.session_id}"
    entries = fake_redis.store.get(queue_key, [])
    assert len(entries) == 1
    payload = json.loads(entries[0])
    # Fell back to raw text, no file attached.
    assert payload["text"] == raw_text
    assert "file_paths" not in payload
