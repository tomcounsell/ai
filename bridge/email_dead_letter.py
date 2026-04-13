"""Email dead letter queue management.

Failed SMTP sends (after SMTP_MAX_RETRIES attempts) are persisted here as JSON
blobs in Redis. Each entry is keyed by session_id.

Redis key pattern: email:dead_letter:{session_id}

Usage:
    from bridge.email_dead_letter import list_dead_letters, replay_dead_letter

Or via CLI:
    python -m bridge.email_dead_letter list
    python -m bridge.email_dead_letter replay --session-id <id>
    python -m bridge.email_dead_letter replay --all
"""

from __future__ import annotations

import json
import logging
import os
import time

logger = logging.getLogger(__name__)

DEAD_LETTER_KEY_PREFIX = "email:dead_letter:"


def _get_redis():
    """Return a Redis connection."""
    import redis

    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    return redis.Redis.from_url(redis_url, decode_responses=True)


def write_dead_letter(
    session_id: str,
    recipient: str,
    subject: str,
    body: str,
    headers: dict[str, str],
    error: str,
) -> None:
    """Persist a failed SMTP send to the dead letter queue.

    Args:
        session_id: The AgentSession ID that generated the output.
        recipient: Target email address.
        subject: Email subject.
        body: Email body text.
        headers: SMTP headers dict (In-Reply-To, References, etc.).
        error: Last error message from SMTP send attempt.
    """
    payload = {
        "session_id": session_id,
        "recipient": recipient,
        "subject": subject,
        "body": body,
        "headers": headers,
        "error": error,
        "failed_at": time.time(),
        "retry_count": 0,
    }
    key = f"{DEAD_LETTER_KEY_PREFIX}{session_id}"
    try:
        r = _get_redis()
        r.set(key, json.dumps(payload))
        logger.info(f"[email] Dead letter written: {key}")
    except Exception as e:
        logger.error(f"[email] Failed to write dead letter for {session_id}: {e}")


def list_dead_letters() -> list[dict]:
    """Return all dead letter entries as a list of dicts.

    Returns:
        List of dead letter payload dicts, sorted by failed_at (oldest first).
    """
    try:
        r = _get_redis()
        keys = list(r.scan_iter(f"{DEAD_LETTER_KEY_PREFIX}*"))
        entries = []
        for key in keys:
            raw = r.get(key)
            if raw:
                try:
                    entries.append(json.loads(raw))
                except json.JSONDecodeError:
                    logger.warning(f"[email] Corrupt dead letter entry at {key}, skipping")
        entries.sort(key=lambda e: e.get("failed_at", 0))
        return entries
    except Exception as e:
        logger.error(f"[email] Failed to list dead letters: {e}")
        return []


def replay_dead_letter(session_id: str) -> bool:
    """Replay a single dead letter entry by sending via SMTP.

    Removes the dead letter entry on success.

    Args:
        session_id: The session_id of the dead letter to replay.

    Returns:
        True if replay succeeded, False otherwise.
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
        logger.error(f"[email] Failed to read dead letter {session_id}: {e}")
        return False

    # Attempt SMTP send
    try:
        import email.mime.text
        import email.utils
        import smtplib

        from bridge.email_bridge import _get_smtp_config

        smtp_config = _get_smtp_config()
        if not smtp_config:
            logger.error("[email] SMTP not configured, cannot replay dead letter")
            return False

        from_addr = smtp_config["user"]
        recipient = payload["recipient"]
        subject = payload["subject"]
        body = payload["body"]
        headers = payload.get("headers", {})

        msg = email.mime.text.MIMEText(body, "plain", "utf-8")
        msg["From"] = from_addr
        msg["To"] = recipient
        msg["Subject"] = subject
        msg["Date"] = email.utils.formatdate(localtime=True)
        for header_name, header_value in headers.items():
            if header_value:
                msg[header_name] = header_value

        with smtplib.SMTP(smtp_config["host"], smtp_config["port"], timeout=30) as smtp:
            if smtp_config.get("use_tls", True):
                smtp.starttls()
            smtp.login(smtp_config["user"], smtp_config["password"])
            smtp.sendmail(from_addr, [recipient], msg.as_string())

        # Success — remove from dead letter queue
        r.delete(key)
        logger.info(f"[email] Replayed dead letter for session_id={session_id}")
        return True

    except Exception as e:
        # Update retry count
        try:
            payload["retry_count"] = payload.get("retry_count", 0) + 1
            payload["last_retry_error"] = str(e)
            r.set(key, json.dumps(payload))
        except Exception:
            pass
        logger.error(f"[email] Dead letter replay failed for {session_id}: {e}")
        return False


def replay_all_dead_letters() -> tuple[int, int]:
    """Replay all dead letter entries.

    Returns:
        Tuple of (succeeded, failed) counts.
    """
    entries = list_dead_letters()
    succeeded = 0
    failed = 0
    for entry in entries:
        session_id = entry.get("session_id", "")
        if replay_dead_letter(session_id):
            succeeded += 1
        else:
            failed += 1
    logger.info(f"[email] Dead letter replay complete: {succeeded} succeeded, {failed} failed")
    return succeeded, failed


# =============================================================================
# CLI entry point
# =============================================================================


def _cli_list() -> None:
    """Print all dead letter entries."""
    entries = list_dead_letters()
    if not entries:
        print("No dead letter entries.")
        return

    print(f"Dead letter queue: {len(entries)} entry(ies)\n")
    for entry in entries:
        failed_at = entry.get("failed_at", 0)
        failed_str = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(failed_at))
        print(f"  session_id: {entry.get('session_id', 'unknown')}")
        print(f"  recipient:  {entry.get('recipient', 'unknown')}")
        print(f"  subject:    {entry.get('subject', '')}")
        print(f"  failed_at:  {failed_str}")
        print(f"  error:      {entry.get('error', 'unknown')}")
        print(f"  retries:    {entry.get('retry_count', 0)}")
        print()


def _cli_replay(session_id: str | None, replay_all: bool) -> None:
    """Replay dead letter(s)."""
    if replay_all:
        succeeded, failed = replay_all_dead_letters()
        print(f"Replayed: {succeeded} succeeded, {failed} failed")
    elif session_id:
        ok = replay_dead_letter(session_id)
        if ok:
            print(f"Replayed dead letter for session_id={session_id}")
        else:
            print(f"Failed to replay dead letter for session_id={session_id}")
    else:
        print("Error: provide --session-id or --all")
        raise SystemExit(1)


def main() -> None:
    """CLI entry point for dead letter management.

    Usage:
        python -m bridge.email_dead_letter list
        python -m bridge.email_dead_letter replay --session-id <id>
        python -m bridge.email_dead_letter replay --all
    """
    import argparse
    import sys

    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)

    parser = argparse.ArgumentParser(
        description="Email dead letter queue management",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="List all dead letter entries")

    replay_parser = subparsers.add_parser("replay", help="Replay dead letter(s)")
    replay_parser.add_argument("--session-id", help="Session ID to replay")
    replay_parser.add_argument("--all", action="store_true", help="Replay all entries")

    args = parser.parse_args()

    if args.command == "list":
        _cli_list()
    elif args.command == "replay":
        _cli_replay(args.session_id, args.all)


if __name__ == "__main__":
    main()
