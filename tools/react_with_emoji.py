#!/usr/bin/env python3
"""Polymorphic react_with_emoji CLI — transport-aware reactions.

Replaces the REACT: menu choice in the old review gate (plan #1035 Part E).
Today reactions are a no-op for email; the implementation short-circuits
when the session transport is email.

Usage:
    python tools/react_with_emoji.py 'excited'
    python tools/react_with_emoji.py '👍'

Environment: same as tools/send_message.py. Requires VALOR_SESSION_ID +
TELEGRAM_CHAT_ID + TELEGRAM_REPLY_TO for Telegram reactions.
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


def main() -> None:
    parser = argparse.ArgumentParser(description="React with an emoji (polymorphic).")
    parser.add_argument("feeling", help="Emoji character or feeling word (e.g. 'excited')")
    args = parser.parse_args()
    react(args.feeling)


if __name__ == "__main__":
    main()
