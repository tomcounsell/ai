"""
Dead letter queue for failed SMTP sends.

Failed outbound emails (after retries) are persisted to Redis under
``email:dead_letter:{session_id}`` as JSON blobs. This module provides
utilities to list and replay dead-lettered messages.

Usage::

    # List all dead-lettered messages
    from bridge.email_dead_letter import list_dead_letters
    for entry in list_dead_letters():
        print(entry["session_id"], entry["recipient"], entry["failed_at"])

    # Replay a specific message
    from bridge.email_dead_letter import replay_dead_letter
    replay_dead_letter("email_valor_alice@example.com_1234567890")

CLI usage (via valor-service.sh)::

    python -m bridge.email_dead_letter list
    python -m bridge.email_dead_letter replay <session_id>
    python -m bridge.email_dead_letter replay --all
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time

logger = logging.getLogger(__name__)

# Redis key pattern for dead-lettered messages
DEAD_LETTER_KEY_PREFIX = "email:dead_letter:"
DEAD_LETTER_TTL = 7 * 24 * 3600  # 7 days


def _get_redis():
    """Return a Redis connection (lazy import)."""
    import redis

    return redis.Redis.from_url(
        os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
        decode_responses=True,
    )


def push_dead_letter(
    session_id: str,
    recipient: str,
    subject: str,
    body: str,
    headers: dict,
    retry_count: int = 3,
) -> None:
    """Persist a failed SMTP send to the dead letter queue.

    Args:
        session_id: AgentSession session_id for the failed send.
        recipient: Recipient email address.
        subject: Email subject line.
        body: Plain text email body.
        headers: Dict of additional SMTP headers (In-Reply-To, References, etc.).
        retry_count: Number of send attempts that failed.
    """
    payload = {
        "session_id": session_id,
        "recipient": recipient,
        "subject": subject,
        "body": body,
        "headers": headers,
        "failed_at": time.time(),
        "retry_count": retry_count,
    }

    key = f"{DEAD_LETTER_KEY_PREFIX}{session_id}"
    try:
        r = _get_redis()
        r.set(key, json.dumps(payload), ex=DEAD_LETTER_TTL)
        logger.warning(
            f"[email] Dead-lettered failed send for session {session_id} "
            f"-> {recipient} after {retry_count} retries"
        )
    except Exception as e:
        logger.error(f"[email] Failed to write dead letter for {session_id}: {e}")


def list_dead_letters() -> list[dict]:
    """Return all dead-lettered messages as a list of dicts.

    Returns:
        List of payload dicts, sorted by failed_at ascending (oldest first).
    """
    try:
        r = _get_redis()
        keys = r.keys(f"{DEAD_LETTER_KEY_PREFIX}*")
        results = []
        for key in keys:
            raw = r.get(key)
            if raw:
                try:
                    results.append(json.loads(raw))
                except json.JSONDecodeError:
                    logger.warning(f"[email] Malformed dead letter at {key}, skipping")
        results.sort(key=lambda e: e.get("failed_at", 0))
        return results
    except Exception as e:
        logger.error(f"[email] Failed to list dead letters: {e}")
        return []


def replay_dead_letter(session_id: str) -> bool:
    """Attempt to resend a dead-lettered email.

    Loads the payload from Redis, attempts SMTP delivery using the current
    email bridge configuration, and removes the dead letter key on success.

    Args:
        session_id: The session_id key to replay.

    Returns:
        True if the replay succeeded and the dead letter was removed.
        False if the replay failed (dead letter is preserved for retry).
    """
    key = f"{DEAD_LETTER_KEY_PREFIX}{session_id}"

    try:
        r = _get_redis()
        raw = r.get(key)
        if not raw:
            logger.warning(f"[email] No dead letter found for session_id={session_id}")
            return False

        payload = json.loads(raw)
    except Exception as e:
        logger.error(f"[email] Failed to load dead letter for {session_id}: {e}")
        return False

    # Attempt SMTP resend
    try:
        from bridge.email_bridge import _smtp_send

        _smtp_send(
            recipient=payload["recipient"],
            subject=payload["subject"],
            body=payload["body"],
            headers=payload.get("headers", {}),
        )
    except Exception as e:
        logger.error(f"[email] Replay failed for {session_id}: {e}")
        return False

    # Success — remove dead letter
    try:
        r = _get_redis()
        r.delete(key)
        logger.info(f"[email] Replayed and removed dead letter for session_id={session_id}")
    except Exception as e:
        logger.warning(f"[email] Replay succeeded but failed to remove dead letter key: {e}")

    return True


def replay_all_dead_letters() -> tuple[int, int]:
    """Replay all dead-lettered emails.

    Returns:
        Tuple of (succeeded_count, failed_count).
    """
    entries = list_dead_letters()
    succeeded = 0
    failed = 0

    for entry in entries:
        session_id = entry.get("session_id", "")
        if not session_id:
            continue
        if replay_dead_letter(session_id):
            succeeded += 1
        else:
            failed += 1

    return succeeded, failed


# ===========================================================================
# CLI entry point
# ===========================================================================


def _cli_main(args: list[str]) -> None:
    """CLI interface for dead letter management."""
    if not args or args[0] == "list":
        entries = list_dead_letters()
        if not entries:
            print("No dead-lettered emails.")
            return
        print(f"Dead-lettered emails ({len(entries)} total):")
        for entry in entries:
            import datetime

            failed_dt = datetime.datetime.fromtimestamp(
                entry.get("failed_at", 0), tz=datetime.UTC
            ).strftime("%Y-%m-%d %H:%M UTC")
            print(
                f"  [{failed_dt}] session={entry.get('session_id', '?')} "
                f"to={entry.get('recipient', '?')} retries={entry.get('retry_count', '?')}"
            )

    elif args[0] == "replay":
        if len(args) < 2:
            print("Usage: replay <session_id> | --all")
            sys.exit(1)
        if args[1] == "--all":
            ok, fail = replay_all_dead_letters()
            print(f"Replay complete: {ok} succeeded, {fail} failed")
        else:
            ok = replay_dead_letter(args[1])
            print("Replay succeeded" if ok else "Replay FAILED — dead letter preserved")
            sys.exit(0 if ok else 1)

    else:
        print(f"Unknown command: {args[0]}")
        print(
            "Usage: python -m bridge.email_dead_letter [list | replay <session_id> | replay --all]"
        )
        sys.exit(1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    _cli_main(sys.argv[1:])
