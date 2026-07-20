"""Track and clear the worker-down (⚠) reaction across the worker→bridge boundary.

When the bridge reacts with the worker-down warning (⚠) on a message that arrived
while no worker was alive (issue #1312), that reaction must not linger forever.
Once the recovering worker drains the enqueued session it should reach back to the
bridge and replace the stale ⚠ with the normal "processing" reaction, so the user
sees the request move from *paused* to *being worked on*.

Two facts shape the design (issue #2178):

- The bridge process owns the Telethon client; the worker process does not. Only
  the bridge can mutate a Telegram reaction. The worker therefore reaches back via
  the same ``telegram:outbox:{session_id}`` relay it already uses for output
  delivery and RTR-suppress reactions (``agent/output_handler.py``, drained by
  ``bridge/telegram_relay.py``). No new IPC channel or listener is introduced.

- The relay's ``_send_queued_reaction`` drops payloads whose ``emoji`` field is
  falsy, so an empty-reaction "clear" cannot traverse it. Issue #2178 sanctions
  *replacing* the warning with a normal processing reaction; a single reaction
  from the bot account overwrites the prior one on the same message, so writing
  the processing emoji makes the ⚠ disappear.

The tracking store lives in ephemeral, non-Popoto Redis keys
(``bridge:worker_down_reactions:{session_id}``), the same precedent as
``worker:registered_pid:*`` and ``telegram:outbox:*`` — so the shared Redis client
is used directly rather than an ORM record.

All operations are fail-silent: neither recording a warning nor clearing one may
crash the bridge ingestion path or the worker session-pickup path.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

# Redis key prefix for the per-session list of warned messages. Ephemeral bridge
# infra key (not Popoto-managed) — direct shared-client access, same as
# ``worker:registered_pid:*``.
WORKER_DOWN_REACTIONS_KEY_PREFIX = "bridge:worker_down_reactions:"

# TTL (seconds) on the tracking list. Provisional/tunable: a warned message that
# is never drained (worker stays dead) should not accumulate a Redis key
# indefinitely. One hour matches ``TelegramRelayOutputHandler.OUTBOX_TTL`` so the
# tracking record and the outbox it feeds age out on the same clock.
WORKER_DOWN_REACTIONS_TTL_S = 3600

# Outbox key the bridge relay drains — same format the worker uses for output.
_OUTBOX_KEY_PREFIX = "telegram:outbox:"


def _redis():
    """Return the shared popoto Redis connection (same client as the rest of agent/)."""
    from popoto.redis_db import POPOTO_REDIS_DB  # noqa: PLC0415

    return POPOTO_REDIS_DB


def _recovered_emoji() -> str:
    """The reaction that replaces the stale ⚠ on worker recovery.

    ``REACTION_PROCESSING`` (✍, "actively composing a reply") is accurate to the
    state the message enters the instant the worker picks it up. Imported lazily
    to avoid pulling ``bridge.response`` into every ``agent`` import.
    """
    from bridge.response import REACTION_PROCESSING  # noqa: PLC0415

    return REACTION_PROCESSING


def _tracking_key(session_id: str) -> str:
    return f"{WORKER_DOWN_REACTIONS_KEY_PREFIX}{session_id}"


def record_worker_down_reaction(session_id: str, chat_id: int | str, message_id: int) -> bool:
    """Record that ``message_id`` in ``chat_id`` got a worker-down (⚠) reaction.

    Called by the bridge at ⚠-set time (issue #1312 ingestion path), keyed by the
    session id the message was enqueued under, so the worker can later clear it.

    Returns ``True`` on a successful write, ``False`` if anything went wrong
    (Redis down, bad inputs) — recording is best-effort and never raises.
    """
    if not session_id or chat_id in (None, "") or message_id is None:
        return False
    try:
        r = _redis()
        key = _tracking_key(session_id)
        r.rpush(key, json.dumps({"chat_id": str(chat_id), "message_id": int(message_id)}))
        r.expire(key, WORKER_DOWN_REACTIONS_TTL_S)
        return True
    except Exception as e:  # noqa: BLE001 — recording must never crash ingestion
        logger.debug("record_worker_down_reaction failed (non-fatal): %s", e)
        return False


def clear_worker_down_reactions(session_id: str) -> int:
    """Replace any tracked worker-down (⚠) reactions for ``session_id``.

    Called by the worker the moment it drains the session (pending→running). For
    each warned message it queues a replacement reaction (the processing emoji) on
    ``telegram:outbox:{session_id}`` — which the bridge relay drains and applies,
    overwriting the ⚠ — then deletes the tracking key.

    Returns the number of replacement reactions queued. Fail-silent: returns 0 on
    any error and never raises, so session pickup is never blocked.
    """
    if not session_id:
        return 0
    try:
        r = _redis()
        key = _tracking_key(session_id)
        raw_entries = r.lrange(key, 0, -1)
    except Exception as e:  # noqa: BLE001
        logger.debug("clear_worker_down_reactions read failed (non-fatal): %s", e)
        return 0

    if not raw_entries:
        return 0

    try:
        from agent.output_handler import TelegramRelayOutputHandler  # noqa: PLC0415

        emoji = _recovered_emoji()
        outbox_key = f"{_OUTBOX_KEY_PREFIX}{session_id}"
        queued = 0
        for raw in raw_entries:
            try:
                entry = json.loads(raw)
                chat_id = entry["chat_id"]
                message_id = int(entry["message_id"])
            except (ValueError, TypeError, KeyError) as parse_err:
                logger.debug("skipping malformed worker-down entry %r: %s", raw, parse_err)
                continue
            payload = TelegramRelayOutputHandler._build_reaction_payload(
                str(chat_id), message_id, emoji, session_id
            )
            r.rpush(outbox_key, json.dumps(payload))
            queued += 1

        if queued:
            r.expire(outbox_key, WORKER_DOWN_REACTIONS_TTL_S)
        r.delete(key)
        if queued:
            logger.info(
                "Queued %d worker-recovered reaction(s) for session %s (replacing ⚠)",
                queued,
                session_id,
            )
        return queued
    except Exception as e:  # noqa: BLE001 — must never crash session pickup
        logger.debug("clear_worker_down_reactions write failed (non-fatal): %s", e)
        return 0
