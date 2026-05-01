#!/usr/bin/env python3
"""Polymorphic send_message CLI — medium-agnostic message delivery.

Introduced by the message-drafter refactor (plan #1035 Part E). Replaces the
string-menu review gate with a prepopulated tool_call that the agent invokes
to deliver its response.

The tool routes by ``session.extra_context.transport``:
- telegram (default): writes a payload to the Redis outbox (same shape as
  tools/send_telegram.py). The bridge relay picks it up and delivers.
- email: writes an ``email:outbox:{session_id}`` payload handled by the
  EmailOutputHandler (future extension point; today the handler sends
  directly from the worker, so email delivery simply calls the handler).

Usage:
    python tools/send_message.py "your draft text"
    python tools/send_message.py "caption" --file path/to/attachment.png
    python tools/send_message.py --react excited
    python tools/send_message.py --stdin    # read text from stdin

Environment variables (injected by sdk_client.py):
    VALOR_SESSION_ID      - Required; session ID for routing
    TELEGRAM_CHAT_ID      - Set for Telegram-triggered sessions
    TELEGRAM_REPLY_TO     - Set for Telegram replies
    EMAIL_REPLY_TO        - Set for email-triggered sessions (sender address)
    VALOR_TRANSPORT       - Explicit override; otherwise inferred from chat_id/email

This tool is polymorphic — one verb, one implementation, transport chosen at
call time. Future Slack/SMS support adds a branch here without any new tool
for the agent to learn.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

TELEGRAM_MAX_LENGTH = 4096
TELEGRAM_MAX_ALBUM_SIZE = 10


def _resolve_transport() -> str:
    """Pick the transport based on env vars.

    Priority:
    1. VALOR_TRANSPORT explicit override
    2. EMAIL_REPLY_TO set -> email
    3. TELEGRAM_CHAT_ID set -> telegram
    4. Default: telegram
    """
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


def _send_via_telegram(text: str, file_paths: list[str] | None) -> None:
    """Route to the Redis outbox for Telegram delivery (same shape as send_telegram.py)."""
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    reply_to = os.environ.get("TELEGRAM_REPLY_TO")
    session_id = os.environ.get("VALOR_SESSION_ID")

    if not chat_id:
        print(
            "Error: TELEGRAM_CHAT_ID not set; session is not Telegram-triggered.",
            file=sys.stderr,
        )
        sys.exit(1)
    if not session_id:
        print("Error: VALOR_SESSION_ID not set.", file=sys.stderr)
        sys.exit(1)

    # Validate files
    if file_paths:
        if len(file_paths) > TELEGRAM_MAX_ALBUM_SIZE:
            print(
                f"Error: Too many files ({len(file_paths)}); max {TELEGRAM_MAX_ALBUM_SIZE}.",
                file=sys.stderr,
            )
            sys.exit(1)
        validated: list[str] = []
        missing: list[str] = []
        for fp in file_paths:
            abs_path = os.path.abspath(fp)
            if os.path.isfile(abs_path):
                validated.append(abs_path)
            else:
                missing.append(abs_path)
        if missing:
            print(
                "Error: File(s) not found:\n" + "\n".join(f"  {p}" for p in missing),
                file=sys.stderr,
            )
            sys.exit(1)
        file_paths = validated

    if text:
        try:
            from bridge.message_drafter import linkify_references

            text = linkify_references(text, os.environ.get("PROJECT_KEY", "ai"))
        except Exception:
            pass

    # Promise gate — see docs/features/promise-gate.md (polymorphic transport).
    from bridge.promise_gate import cli_check_or_exit

    cli_check_or_exit(text, transport="polymorphic", session_id=session_id)

    payload = {
        "chat_id": chat_id,
        "reply_to": int(reply_to) if reply_to else None,
        "text": text,
        "session_id": session_id,
        "timestamp": time.time(),
    }
    if file_paths:
        payload["file_paths"] = file_paths

    queue_key = f"telegram:outbox:{session_id}"
    try:
        r = _get_redis()
        r.rpush(queue_key, json.dumps(payload))
        r.expire(queue_key, 3600)
    except Exception as e:
        print(f"Error: Redis write failed: {e}", file=sys.stderr)
        sys.exit(1)

    print(
        f"Queued ({len(text)} chars{', ' + str(len(file_paths)) + ' files' if file_paths else ''})"
    )


def _send_via_email(text: str) -> None:
    """Route to the email outbox for SMTP delivery via ``bridge/email_relay.py``.

    Writes the unified outbox payload shape (see ``bridge/email_relay.py``
    docstring) consumed by the relay. The relay accepts legacy ``text`` as a
    synonym for ``body`` for one transitional release, but this writer always
    emits ``body`` so the transitional path is unused.

    Required env:
        VALOR_SESSION_ID: session ID that originated this send.
        EMAIL_REPLY_TO:   recipient address (sender of the original inbound).
        EMAIL_SUBJECT:    optional; defaults to "(no subject)".
        EMAIL_IN_REPLY_TO: optional RFC-2822 Message-ID for threading.
        SMTP_USER:        optional from-address override (relay defaults to SMTP_USER).
    """
    session_id = os.environ.get("VALOR_SESSION_ID")
    reply_to_addr = os.environ.get("EMAIL_REPLY_TO")
    if not session_id:
        print("Error: VALOR_SESSION_ID not set.", file=sys.stderr)
        sys.exit(1)
    if not reply_to_addr:
        print("Error: EMAIL_REPLY_TO not set.", file=sys.stderr)
        sys.exit(1)

    in_reply_to = os.environ.get("EMAIL_IN_REPLY_TO") or None
    subject = os.environ.get("EMAIL_SUBJECT") or "(no subject)"

    # Promise gate — see docs/features/promise-gate.md
    from bridge.promise_gate import cli_check_or_exit

    cli_check_or_exit(text, transport="email", session_id=session_id)

    payload = {
        "session_id": session_id,
        "to": reply_to_addr,
        "subject": subject,
        "body": text,
        "attachments": [],
        "in_reply_to": in_reply_to,
        "references": in_reply_to,
        "from_addr": os.environ.get("SMTP_USER", ""),
        "timestamp": time.time(),
    }
    queue_key = f"email:outbox:{session_id}"
    try:
        r = _get_redis()
        r.rpush(queue_key, json.dumps(payload))
        r.expire(queue_key, 3600)
    except Exception as e:
        print(f"Error: Redis write failed: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"Queued email ({len(text)} chars)")


def send_message(text: str, file_paths: list[str] | None = None) -> None:
    """Entry point. Dispatches by transport."""
    transport = _resolve_transport()
    if transport == "telegram":
        _send_via_telegram(text, file_paths)
    elif transport == "email":
        if file_paths:
            print(
                "Warning: --file ignored for email transport (not supported)",
                file=sys.stderr,
            )
        _send_via_email(text)
    else:
        print(f"Error: unsupported transport '{transport}'", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Send a user-visible reply (polymorphic across transports).",
        usage="python tools/send_message.py 'text' [--file PATH ...]",
    )
    parser.add_argument("message", nargs="*", default=[], help="Message text")
    parser.add_argument(
        "--file",
        dest="file_paths",
        action="append",
        default=None,
        help="Path to a file to attach (Telegram only; repeatable)",
    )
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="Read the message body from stdin (useful for multi-line text)",
    )
    args = parser.parse_args()

    if args.stdin:
        text = sys.stdin.read().rstrip("\n")
    else:
        text = " ".join(args.message)

    if not text and not args.file_paths:
        parser.error("message text or --file required")

    send_message(text or "", file_paths=args.file_paths)


if __name__ == "__main__":
    main()
