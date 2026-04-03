#!/usr/bin/env python3
"""Send a Telegram message from the PM (ChatSession) via Redis outbox queue.

This tool is called by ChatSession via Bash to compose and send its own
Telegram messages, bypassing the summarizer. The bridge relay task
(bridge/telegram_relay.py) processes the queue and sends via Telethon.

Usage:
    python tools/send_telegram.py "Your message text here"
    python tools/send_telegram.py "Caption for file" --file /path/to/screenshot.png
    python tools/send_telegram.py --file /path/to/document.pdf

Environment variables (injected by sdk_client.py for chat sessions):
    TELEGRAM_CHAT_ID   - Target Telegram chat ID
    TELEGRAM_REPLY_TO  - Message ID to reply to
    VALOR_SESSION_ID   - Session ID for queue routing

Redis queue contract:
    Key pattern: telegram:outbox:{session_id}
    Message format: JSON with {chat_id, reply_to, text, file_path, session_id, timestamp}
    TTL: 1 hour (safety net for crashed sessions)
"""

import argparse
import json
import os
import sys
import time

# Telegram message length limit
TELEGRAM_MAX_LENGTH = 4096


def _get_redis_connection():
    """Get a Redis connection using the project's standard pattern."""
    import redis

    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    return redis.Redis.from_url(redis_url, decode_responses=True)


def _linkify_text(text: str) -> str:
    """Apply PR/Issue linkification to the message text.

    Uses bridge.formatting.linkify_references with a default project key
    derived from the environment, falling back to 'ai' (this project).
    """
    try:
        from bridge.formatting import linkify_references

        # Try to get project key from session context
        project_key = os.environ.get("PROJECT_KEY", "ai")
        return linkify_references(text, project_key)
    except Exception:
        # If formatting module unavailable, return text unchanged
        return text


def send_message(text: str, file_path: str | None = None) -> None:
    """Queue a Telegram message for delivery by the bridge relay.

    Args:
        text: The message text to send. Will be linkified and truncated
            to Telegram's character limit before queueing. Can be empty
            if file_path is provided.
        file_path: Optional path to a file to attach. Must exist on disk.

    Raises:
        SystemExit: On missing env vars, missing file, or Redis errors.
    """
    # Validate environment
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    reply_to = os.environ.get("TELEGRAM_REPLY_TO")
    session_id = os.environ.get("VALOR_SESSION_ID")

    if not chat_id:
        print(
            "Error: TELEGRAM_CHAT_ID not set. This tool is only available in ChatSession context.",
            file=sys.stderr,
        )
        sys.exit(1)

    if not session_id:
        print(
            "Error: VALOR_SESSION_ID not set. This tool is only available in ChatSession context.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Validate file if provided
    if file_path is not None:
        if not file_path or not file_path.strip():
            print("Error: --file path is empty.", file=sys.stderr)
            sys.exit(1)
        file_path = os.path.abspath(file_path)
        if not os.path.isfile(file_path):
            print(f"Error: File not found: {file_path}", file=sys.stderr)
            sys.exit(1)

    # Validate message (text required unless file is provided)
    if not text or not text.strip():
        if file_path is None:
            print("Error: Message text is empty.", file=sys.stderr)
            sys.exit(1)
        # File-only send: text stays empty
        text = ""

    if text:
        # Apply linkification
        text = _linkify_text(text)

        # Enforce Telegram length limit
        if len(text) > TELEGRAM_MAX_LENGTH:
            text = text[: TELEGRAM_MAX_LENGTH - 3] + "..."

    # Build queue entry
    payload = {
        "chat_id": chat_id,
        "reply_to": int(reply_to) if reply_to else None,
        "text": text,
        "session_id": session_id,
        "timestamp": time.time(),
    }
    if file_path:
        payload["file_path"] = file_path

    message_payload = json.dumps(payload)

    # Push to Redis outbox queue
    queue_key = f"telegram:outbox:{session_id}"
    try:
        r = _get_redis_connection()
        r.rpush(queue_key, message_payload)
        # Set TTL of 1 hour as safety net for crashed sessions
        r.expire(queue_key, 3600)
    except Exception as e:
        print(f"Error: Failed to queue message in Redis: {e}", file=sys.stderr)
        sys.exit(1)

    parts = []
    if text:
        parts.append(f"{len(text)} chars")
    if file_path:
        parts.append(f"file: {os.path.basename(file_path)}")
    print(f"Message queued ({', '.join(parts)})")


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Send a Telegram message via Redis outbox queue.",
        usage='python tools/send_telegram.py "message text" [--file PATH]',
    )
    parser.add_argument(
        "message",
        nargs="*",
        default=[],
        help="Message text to send (can be omitted if --file is provided)",
    )
    parser.add_argument(
        "--file",
        dest="file_path",
        default=None,
        help="Path to a file to attach (image, document, etc.)",
    )

    args = parser.parse_args()
    text = " ".join(args.message)

    if not text and not args.file_path:
        parser.error("Either message text or --file must be provided.")

    send_message(text, file_path=args.file_path)


if __name__ == "__main__":
    main()
