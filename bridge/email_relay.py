"""Email outbox relay: drain Redis ``email:outbox:*`` queues via SMTP.

Mirrors ``bridge/telegram_relay.py``: atomic ``LPOP`` first, send via SMTP in a
thread executor, increment ``_relay_attempts`` and ``RPUSH`` back on failure,
route to the dead-letter queue via ``bridge.email_dead_letter.write_dead_letter``
after ``MAX_EMAIL_RELAY_RETRIES`` failed attempts.

Redis queue contract:
    Key pattern: email:outbox:{session_id}
    Payload (unified shape, emitted by tools/valor_email.py and
    tools/send_message.py::_send_via_email):
        {
            "session_id": str,
            "to": str,                         # recipient address
            "subject": str | None,             # optional; "(no subject)" default
            "body": str,                       # plain text body
            "attachments": [absolute_path, ...],
            "in_reply_to": str | None,         # RFC-2822 Message-ID
            "references": str | None,          # typically == in_reply_to
            "from_addr": str | None,           # override; defaults to SMTP_USER
            "timestamp": float,
            "_relay_attempts": int (optional)  # managed by the relay
        }

    Legacy compat: payloads with ``text`` instead of ``body`` are normalized on
    read for one transitional release. The queue had no consumer before this
    change so in-flight entries are unlikely but tolerated.

Liveness:
    Writes ``email:relay:last_poll_ts = time.time()`` with a 5-minute TTL once
    per poll cycle. Operators probe via ``GET email:relay:last_poll_ts``.

Invariant:
    ``EmailOutputHandler.send()`` sends directly and NEVER writes to
    ``email:outbox:*``. The relay and the handler do not race on the same
    session's output (see Risk 3 in the plan).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import smtplib
import time
from pathlib import Path

from bridge.email_bridge import _build_reply_mime, _get_smtp_config

logger = logging.getLogger(__name__)

# Poll interval (100 ms — matches telegram_relay)
EMAIL_RELAY_POLL_INTERVAL = 0.1

# Max entries to drain per key per poll cycle (prevents starvation)
EMAIL_RELAY_BATCH_SIZE = 10

# Redis scan pattern
EMAIL_OUTBOX_KEY_PATTERN = "email:outbox:*"

# Retry ceiling — after this many failures, route to the DLQ
MAX_EMAIL_RELAY_RETRIES = 3

# Heartbeat
EMAIL_RELAY_HEARTBEAT_KEY = "email:relay:last_poll_ts"
EMAIL_RELAY_HEARTBEAT_TTL = 300  # 5 minutes


def _get_redis_connection():
    """Return a sync Redis connection for LPOP/RPUSH (called via to_thread)."""
    import redis

    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    return redis.Redis.from_url(redis_url, decode_responses=True)


def _normalize_payload(message: dict) -> dict | None:
    """Normalize incoming payload to the unified shape.

    Returns None if the payload is unrecoverable (missing ``to`` or both
    ``body``/``text``). Callers should DLQ such payloads rather than retry.
    """
    # Legacy compat: text -> body
    if "body" not in message and "text" in message:
        message["body"] = message.pop("text")

    to_addr = message.get("to") or ""
    body = message.get("body")
    if not to_addr or body is None:
        return None

    message.setdefault("subject", "(no subject)")
    smtp_user = os.environ.get("SMTP_USER", "")
    if not message.get("from_addr"):
        message["from_addr"] = smtp_user
    message.setdefault("attachments", [])
    message.setdefault("in_reply_to", None)
    message.setdefault("references", None)
    return message


def _send_smtp_sync(
    to_addr: str,
    mime_msg,
    from_addr: str,
) -> None:
    """Synchronous SMTP send — run in a thread executor from the async loop."""
    cfg = _get_smtp_config()
    if not cfg:
        raise RuntimeError("SMTP not configured (missing SMTP_HOST/USER/PASSWORD)")

    with smtplib.SMTP(cfg["host"], cfg["port"], timeout=30) as smtp:
        if cfg.get("use_tls", True):
            smtp.starttls()
        smtp.login(cfg["user"], cfg["password"])
        smtp.sendmail(from_addr or cfg["user"], [to_addr], mime_msg.as_string())


async def _dead_letter_message(message: dict, reason: str) -> None:
    """Route an exhausted payload to the email dead-letter queue."""
    try:
        from bridge.email_dead_letter import write_dead_letter

        await asyncio.to_thread(
            write_dead_letter,
            session_id=message.get("session_id", "unknown"),
            recipient=message.get("to", ""),
            subject=message.get("subject", ""),
            body=message.get("body", ""),
            headers={
                "In-Reply-To": message.get("in_reply_to") or "",
                "References": message.get("references") or "",
            },
            error=reason,
        )
        logger.warning(
            "Email relay: dead-lettered payload for %s (%s)",
            message.get("to"),
            reason,
        )
    except Exception as e:
        logger.error(f"Email relay: failed to write dead letter: {e}")


async def _process_one(r, key: str, raw: str) -> tuple[bool, bool]:
    """Process a single LPOPped payload.

    Returns a ``(ok, requeued)`` tuple where ``ok`` is True on successful SMTP
    delivery and ``requeued`` is True when the payload was re-pushed onto
    ``key`` for a later retry. Signalling the requeue explicitly avoids the
    previous brittle ``llen`` before/after race — a concurrent writer RPUSHing
    to the same key between the two ``llen`` calls would otherwise be
    misinterpreted as a requeue.
    """
    try:
        message = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning(f"Email relay: malformed JSON in {key}: {e}")
        return False, False

    normalized = _normalize_payload(message)
    if normalized is None:
        logger.warning(f"Email relay: malformed payload in {key} (missing to/body)")
        await _dead_letter_message(message, "malformed payload (missing to or body)")
        return False, False

    message = normalized

    # Validate attachments at drain time (Race-tolerant: DLQ if deleted since enqueue)
    attachment_paths: list[Path] = []
    missing: list[str] = []
    for p in message.get("attachments") or []:
        path = Path(str(p))
        if path.is_file():
            attachment_paths.append(path)
        else:
            missing.append(str(p))
    if missing:
        await _dead_letter_message(
            message,
            f"attachment(s) not found at drain time: {missing}",
        )
        return False, False

    # Build MIME message
    try:
        mime_msg = _build_reply_mime(
            to_addr=message["to"],
            subject=message.get("subject") or "(no subject)",
            body=message.get("body") or "",
            in_reply_to=message.get("in_reply_to"),
            references=message.get("references"),
            from_addr=message.get("from_addr") or "",
            attachments=attachment_paths or None,
        )
    except Exception as e:
        logger.error(f"Email relay: MIME build failed: {e}")
        await _dead_letter_message(message, f"MIME build failed: {e}")
        return False, False

    # Attempt SMTP send
    try:
        await asyncio.to_thread(
            _send_smtp_sync,
            message["to"],
            mime_msg,
            message.get("from_addr") or "",
        )
        logger.info(
            "Email relay: sent to %s (session=%s, body=%d chars, attach=%d)",
            message["to"],
            message.get("session_id"),
            len(message.get("body") or ""),
            len(attachment_paths),
        )
        return True, False
    except Exception as e:
        attempts = int(message.get("_relay_attempts") or 0) + 1
        message["_relay_attempts"] = attempts
        if attempts >= MAX_EMAIL_RELAY_RETRIES:
            await _dead_letter_message(
                message,
                f"max retries ({MAX_EMAIL_RELAY_RETRIES}) exceeded: {e}",
            )
            return False, False
        try:
            await asyncio.to_thread(r.rpush, key, json.dumps(message))
            logger.info(
                "Email relay: re-queued payload in %s (attempt %d/%d, last error: %s)",
                key,
                attempts,
                MAX_EMAIL_RELAY_RETRIES,
                e,
            )
            return False, True
        except Exception as re_err:
            logger.error(f"Email relay: requeue failed for {key}: {re_err}")
            return False, False


async def process_outbox() -> int:
    """Scan all ``email:outbox:*`` keys and drain them.

    Returns the number of payloads successfully sent in this cycle.
    """
    sent = 0
    try:
        r = await asyncio.to_thread(_get_redis_connection)
        keys = await asyncio.to_thread(r.keys, EMAIL_OUTBOX_KEY_PATTERN)

        # Heartbeat every cycle so ``email-status`` can detect a stale relay.
        # ``ex=`` kwarg is explicit to avoid relying on redis-py's positional
        # arg ordering staying stable across versions.
        try:
            await asyncio.to_thread(
                r.set,
                EMAIL_RELAY_HEARTBEAT_KEY,
                str(time.time()),
                ex=EMAIL_RELAY_HEARTBEAT_TTL,
            )
        except Exception as hb_err:
            logger.warning(f"Email relay: heartbeat write failed: {hb_err}")

        for key in keys:
            processed = 0
            requeued_this_cycle = False
            while processed < EMAIL_RELAY_BATCH_SIZE:
                # Atomic LPOP — safe across concurrent relays / restarts.
                raw = await asyncio.to_thread(r.lpop, key)
                if not raw:
                    break
                processed += 1
                # _process_one signals explicitly whether it re-pushed the
                # payload for a later retry. If so, stop processing this key
                # for the cycle so the retry budget is spread across poll
                # cycles instead of burning through in one pass (mirrors the
                # expected semantics of telegram_relay's batch loop). Using an
                # explicit return value is race-free — llen deltas can lie if
                # a concurrent writer RPUSHes to the same key.
                ok, requeued = await _process_one(r, key, raw)
                if ok:
                    sent += 1
                elif requeued:
                    requeued_this_cycle = True
                    break
            if requeued_this_cycle:
                continue
    except Exception as e:
        logger.error(f"Email relay: outbox processing error: {e}", exc_info=True)
    return sent


async def run_email_relay() -> None:
    """Main relay loop. Runs alongside ``_email_inbox_loop`` via ``asyncio.gather``."""
    logger.info("Email relay started -- draining email:outbox:*")
    while True:
        try:
            sent = await process_outbox()
            if sent > 0:
                logger.info(f"Email relay: processed {sent} message(s)")
        except Exception as e:
            logger.error(f"Email relay: loop error: {e}", exc_info=True)
        await asyncio.sleep(EMAIL_RELAY_POLL_INTERVAL)
