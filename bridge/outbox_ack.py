"""Producer-readable ack for a sent Telegram message id.

Closes the gap identified in the `upvote-autonomous-sdlc-pickup` plan
(issue #2717, spike-1): `tools/valor_telegram.py`'s `cmd_send` enqueues a
message onto `telegram:outbox:{session_id}` and returns as soon as the
enqueue succeeds — it never learns the Telegram message id the relay
eventually gets back from Telethon. There was previously no channel back to
a programmatic producer that needs that id (e.g. to anchor a session's
outbound thread via `telegram_message_id` / `TELEGRAM_REPLY_TO`).

This module owns exactly that channel: a single-consumer, delete-on-read
Redis list per producer id.

- **Key**: `telegram:sent:{session_id}` — an `RPUSH`-based list, not a `SET`,
  so a slow reader can never miss a fast writer (list writes queue; a `SET`
  would not).
- **TTL**: `_ACK_KEY_TTL_S` seconds from the write, so an un-consumed key from
  a failed or abandoned attempt does not linger.
- **Contract**: single consumer, delete-on-read. `await_sent_message_id`
  blocking-polls for the id and deletes the key as soon as it reads one; it is
  not a pub/sub or broadcast primitive, and a second reader would race the
  first for the one entry.

This is a **leaf module**: it imports only the shared Redis client and
nothing else. `bridge/telegram_relay.py` (the writer) imports
`telethon.errors`, and the reader runs inside the reflection process, which
per spike-2 of the `upvote-autonomous-sdlc-pickup` plan has no Telegram
client and must never gain one transitively. Siting the ack logic in
`telegram_relay.py` instead would drag Telethon into every reader's import
graph; siting it here keeps both sides importable independently.

The write is **opt-in**, gated on the outbox payload's `ack_sent_id` flag
(`bridge/telegram_relay.py::process_outbox`). An unconditional write would add
two Redis ops to every outbound Telegram message system-wide, for a primitive
only one low-traffic consumer (`reflections/sdlc_upvote_lanes.py`) uses today.
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

_SENT_KEY_TEMPLATE = "telegram:sent:{session_id}"
_ACK_KEY_TTL_S = 120
_POLL_INTERVAL_S = 0.25


def _get_redis():
    """Return the shared Popoto Redis connection (lazy import, error-tolerant)."""
    from popoto.redis_db import POPOTO_REDIS_DB

    return POPOTO_REDIS_DB


def _sent_key(session_id: str) -> str:
    return _SENT_KEY_TEMPLATE.format(session_id=session_id)


def publish_sent_message_id(session_id: str, msg_id: int) -> None:
    """Record ``msg_id`` for ``session_id`` so a producer can read it back.

    Called by the relay (writer side) once Telethon confirms the send. Best
    effort by design of the caller (`process_outbox` wraps this in a
    non-fatal `try/except` — the relay must never crash on ack bookkeeping),
    but this function itself raises on a genuine Redis failure so that
    wrapper can log it.
    """
    r = _get_redis()
    key = _sent_key(session_id)
    r.rpush(key, str(msg_id))
    r.expire(key, _ACK_KEY_TTL_S)


def await_sent_message_id(session_id: str, timeout_s: float) -> int | None:
    """Blocking-poll for the sent message id published for ``session_id``.

    Deletes the key on read (single-consumer, delete-on-read). Returns
    ``None`` on timeout — the caller (a producer with a bounded wait budget,
    since nothing can cancel a wedged sync reflection thread) must treat a
    ``None`` result as "delivery unconfirmed", not as a delivery failure: the
    send may still land, just without a captured anchor id.
    """
    r = _get_redis()
    key = _sent_key(session_id)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        raw = r.lpop(key)
        if raw is not None:
            r.delete(key)
            try:
                return int(raw)
            except (TypeError, ValueError):
                logger.warning(
                    "outbox_ack: non-integer payload %r for session_id=%s", raw, session_id
                )
                return None
        time.sleep(_POLL_INTERVAL_S)
    return None
