"""Integration coverage for the #1312 worker-liveness ingestion signal.

Composes the two real units the bridge call sites wire together —
``bridge.response.react_if_worker_down`` and ``bridge.dispatch.dispatch_telegram_session``
— in the exact order the live handler runs them (react FIRST, then enqueue),
and asserts the load-bearing contract:

- The message ENQUEUES on BOTH the worker-alive and worker-down paths (the ⚠
  signal never drops work).
- ⚠ is applied ONLY on the worker-down path (happy path adds no reaction).

Only the leaf side-effects (Redis beacon read, Telegram reaction send, the
dispatch claim/enqueue/dedup writes) are mocked; the react→dispatch wiring and
the freshness decision run for real.
"""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bridge.dispatch import dispatch_telegram_session
from bridge.response import REACTION_WORKER_DOWN, react_if_worker_down


def _beacon_get(fresh: bool):
    """Return a Redis-.get() replacement yielding a fresh or stale loop beacon."""
    wall_ts = time.time() if fresh else time.time() - 100_000
    payload = json.dumps({"wall_ts": wall_ts, "loop_beacon_age_s": 1.0, "armed": True})

    def _get(key, *a, **k):
        if isinstance(key, str) and key.startswith("worker:loop_beacon:"):
            return payload
        return None

    return _get


async def _ingest(worker_fresh: bool):
    """Run the call-site sequence: react_if_worker_down THEN dispatch.

    Returns ``(enqueue_called, worker_down_reaction_applied)``.
    """
    client = MagicMock()
    redis = MagicMock()
    redis.get.side_effect = _beacon_get(worker_fresh)

    applied: list[str] = []

    async def _fake_set_reaction(_client, _chat, _msg, emoji):
        applied.append(str(emoji))
        return True

    with (
        patch("popoto.redis_db.POPOTO_REDIS_DB", redis),
        patch("bridge.response.set_reaction", new=_fake_set_reaction),
        patch("agent.worker_down_reactions.record_worker_down_reaction"),
        # Dispatch leaf side-effects (mirrors tests/unit/bridge/test_dispatch.py).
        patch("bridge.dispatch.claim_message", new=AsyncMock(return_value=True)),
        patch(
            "bridge.dispatch.enqueue_agent_session", new=AsyncMock(return_value=1)
        ) as mock_enqueue,
        patch("bridge.dispatch.record_message_processed", new=AsyncMock()),
        patch("bridge.dispatch.record_last_processed", new=AsyncMock()),
        patch("bridge.dispatch._append_inbound_chat_log"),
    ):
        # Exactly the call-site order: signal first, enqueue unconditionally after.
        await react_if_worker_down(client, "chat-1", 101, "sess-1")
        await dispatch_telegram_session(
            project_key="test-project",
            session_id="sess-1",
            working_dir="/tmp/test",
            message_text="hello",
            sender_name="Tom",
            chat_id="chat-1",
            telegram_message_id=101,
        )

    enqueue_called = mock_enqueue.await_count > 0
    worker_down_applied = REACTION_WORKER_DOWN in applied
    return enqueue_called, worker_down_applied


@pytest.mark.asyncio
async def test_worker_alive_enqueues_without_warning():
    enqueue_called, warned = await _ingest(worker_fresh=True)
    assert enqueue_called is True  # no dropped work on the happy path
    assert warned is False  # happy path adds no reaction


@pytest.mark.asyncio
async def test_worker_down_enqueues_and_warns():
    enqueue_called, warned = await _ingest(worker_fresh=False)
    assert enqueue_called is True  # worker-down must NEVER drop work
    assert warned is True  # ⚠ applied on the down path
