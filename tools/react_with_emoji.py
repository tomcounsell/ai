#!/usr/bin/env python3
"""Polymorphic react_with_emoji CLI — transport-aware reactions.

Replaces the REACT: menu choice in the old review gate (plan #1035 Part E).
Today reactions are a no-op for email; the implementation short-circuits
when the session transport is email.

Usage:
    python tools/react_with_emoji.py 'excited'
    python tools/react_with_emoji.py '👍'
    python tools/react_with_emoji.py --standalone 'celebration'

Environment: same as tools/send_message.py. Requires VALOR_SESSION_ID +
TELEGRAM_CHAT_ID + TELEGRAM_REPLY_TO for Telegram reactions.

``--standalone`` sends a custom-emoji *message* (its own bubble) rather than
a reaction on an existing message; it requires VALOR_SESSION_ID +
TELEGRAM_CHAT_ID (TELEGRAM_REPLY_TO optional). This is the migrated home of
the retired ``tools/send_telegram.py --emoji`` capability; the outbox payload
shape (``type: custom_emoji_message``) is unchanged so the relay needs no
changes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time


def _resolve_transport() -> str:
    override = os.environ.get("VALOR_TRANSPORT")
    if override:
        return override.strip().lower()
    if os.environ.get("EMAIL_REPLY_TO"):
        return "email"
    if os.environ.get("TELEGRAM_CHAT_ID"):
        return "telegram"
    return "telegram"


def _get_redis():
    import redis

    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    return redis.Redis.from_url(redis_url, decode_responses=True)


def react(feeling: str) -> None:
    transport = _resolve_transport()
    if transport == "email":
        print("react: email transport has no reactions; no-op")
        return
    if transport != "telegram":
        print(f"Error: unsupported transport '{transport}'", file=sys.stderr)
        sys.exit(1)

    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    reply_to = os.environ.get("TELEGRAM_REPLY_TO")
    session_id = os.environ.get("VALOR_SESSION_ID")
    if not (chat_id and reply_to and session_id):
        print(
            "Error: TELEGRAM_CHAT_ID / TELEGRAM_REPLY_TO / VALOR_SESSION_ID must be set.",
            file=sys.stderr,
        )
        sys.exit(1)
    if not feeling.strip():
        print("Error: feeling is empty.", file=sys.stderr)
        sys.exit(1)

    from tools.emoji_embedding import find_best_emoji

    result = find_best_emoji(feeling.strip())

    payload = {
        "type": "reaction",
        "chat_id": chat_id,
        "reply_to": int(reply_to),
        "emoji": str(result),
        "session_id": session_id,
        "timestamp": time.time(),
    }
    if result.is_custom and result.document_id is not None:
        payload["custom_emoji_document_id"] = result.document_id

    queue_key = f"telegram:outbox:{session_id}"
    try:
        r = _get_redis()
        r.rpush(queue_key, json.dumps(payload))
        r.expire(queue_key, 3600)
    except Exception as e:
        print(f"Error: Redis write failed: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Reaction queued: {result} ({feeling})")


def standalone(feeling: str) -> None:
    """Queue a custom-emoji standalone message (its own bubble, not a reaction).

    Resolves the feeling word to the best custom emoji (or standard emoji
    fallback), then queues a ``custom_emoji_message`` payload for the relay.
    Migrated verbatim (payload shape unchanged) from the retired
    ``tools/send_telegram.py --emoji``.

    Requires VALOR_SESSION_ID + TELEGRAM_CHAT_ID; TELEGRAM_REPLY_TO optional.

    Raises:
        SystemExit: On missing env vars, empty feeling, or Redis errors.
    """
    transport = _resolve_transport()
    if transport == "email":
        print("react: email transport has no emoji messages; no-op")
        return
    if transport != "telegram":
        print(f"Error: unsupported transport '{transport}'", file=sys.stderr)
        sys.exit(1)

    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    reply_to = os.environ.get("TELEGRAM_REPLY_TO")
    session_id = os.environ.get("VALOR_SESSION_ID")
    if not (chat_id and session_id):
        print(
            "Error: TELEGRAM_CHAT_ID / VALOR_SESSION_ID must be set.",
            file=sys.stderr,
        )
        sys.exit(1)
    if not feeling.strip():
        print("Error: feeling is empty.", file=sys.stderr)
        sys.exit(1)

    from tools.emoji_embedding import find_best_emoji

    result = find_best_emoji(feeling.strip())

    payload = {
        "type": "custom_emoji_message",
        "chat_id": chat_id,
        "reply_to": int(reply_to) if reply_to else None,
        "emoji": str(result),
        "session_id": session_id,
        "timestamp": time.time(),
    }
    if result.is_custom and result.document_id is not None:
        payload["custom_emoji_document_id"] = result.document_id

    queue_key = f"telegram:outbox:{session_id}"
    try:
        r = _get_redis()
        r.rpush(queue_key, json.dumps(payload))
        r.expire(queue_key, 3600)
    except Exception as e:
        print(f"Error: Redis write failed: {e}", file=sys.stderr)
        sys.exit(1)

    if result.is_custom:
        print(f"Custom emoji message queued: doc_id={result.document_id} (feeling: {feeling})")
    else:
        print(f"Emoji message queued: {result} (feeling: {feeling})")


def main() -> None:
    parser = argparse.ArgumentParser(description="React with an emoji (polymorphic).")
    parser.add_argument("feeling", help="Emoji character or feeling word (e.g. 'excited')")
    parser.add_argument(
        "--standalone",
        action="store_true",
        default=False,
        help="Send a custom-emoji standalone message (its own bubble) instead of "
        "reacting to an existing message",
    )
    args = parser.parse_args()
    if args.standalone:
        standalone(args.feeling)
    else:
        react(args.feeling)


if __name__ == "__main__":
    main()
