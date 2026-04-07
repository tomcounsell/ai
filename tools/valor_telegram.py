#!/usr/bin/env python3
"""
Unified Telegram messaging CLI.

Usage:
    valor-telegram read --chat "Dev: Valor" --limit 10
    valor-telegram read --chat "Tom" --search "deployment"
    valor-telegram read --chat "Dev: Valor" --since "1 hour ago"
    valor-telegram send --chat "Dev: Valor" "Hello world"
    valor-telegram send --chat "Forum Group" --reply-to 123 "Message to topic"
    valor-telegram send --chat "Tom" --file ./screenshot.png "Caption"
    valor-telegram chats
"""

import argparse
import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from bridge.utc import utc_now

# Telegram message length limit (same constant as send_telegram.py)
TELEGRAM_MAX_LENGTH = 4096


def parse_since(text: str) -> datetime | None:
    """Parse relative time strings like '1 hour ago', '30 minutes ago', '2 days ago'.

    Returns a datetime or None if unparseable.
    """
    text = text.strip().lower()

    patterns = [
        (r"(\d+)\s*hours?\s*ago", lambda m: timedelta(hours=int(m.group(1)))),
        (r"(\d+)\s*minutes?\s*ago", lambda m: timedelta(minutes=int(m.group(1)))),
        (r"(\d+)\s*days?\s*ago", lambda m: timedelta(days=int(m.group(1)))),
        (r"(\d+)\s*weeks?\s*ago", lambda m: timedelta(weeks=int(m.group(1)))),
    ]

    for pattern, delta_fn in patterns:
        match = re.match(pattern, text)
        if match:
            return utc_now() - delta_fn(match)

    return None


def resolve_chat(name: str) -> str | None:
    """Resolve a chat name to a chat_id.

    Tries the history database first (groups), then the DM whitelist (users).
    """
    try:
        from tools.telegram_history import resolve_chat_id

        chat_id = resolve_chat_id(name)
        if chat_id:
            return chat_id
    except Exception:
        pass

    try:
        from tools.telegram_users import resolve_username

        user_id = resolve_username(name)
        if user_id:
            return str(user_id)
    except Exception:
        pass

    return None


def format_timestamp(ts: str | None) -> str:
    """Format a timestamp for display."""
    if not ts:
        return "unknown"
    try:
        dt = datetime.fromisoformat(ts)
        return dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return ts[:16] if len(ts) > 16 else ts


def _telethon_client():
    """Create a Telethon client from env vars. Returns (client, api_id, api_hash) or raises."""
    from dotenv import load_dotenv

    load_dotenv()
    load_dotenv(Path.home() / "Desktop" / "Valor" / ".env")

    api_id = int(os.environ.get("TELEGRAM_API_ID", "0"))
    api_hash = os.environ.get("TELEGRAM_API_HASH", "")

    if not api_id or not api_hash:
        raise RuntimeError("TELEGRAM_API_ID and TELEGRAM_API_HASH must be set in .env")

    from telethon import TelegramClient

    session_path = str(Path(__file__).parent.parent / "data" / "valor_bridge")
    return TelegramClient(session_path, api_id, api_hash)


def _fetch_from_telegram_api(
    chat_name: str,
    limit: int = 10,
    search: str | None = None,
    since_dt: datetime | None = None,
) -> list[dict]:
    """Fetch messages directly from Telegram API via Telethon.

    Used as fallback when Redis has no results.
    """

    async def _fetch():
        client = _telethon_client()
        await client.start()
        try:
            # Find the chat by name
            entity = None
            async for dialog in client.iter_dialogs():
                name = dialog.name or ""
                if chat_name.lower() in name.lower():
                    entity = dialog.entity
                    break

            if not entity:
                # Try as numeric ID
                if chat_name.lstrip("-").isdigit():
                    entity = await client.get_entity(int(chat_name))

            if not entity:
                return []

            # Fetch messages (Telethon returns newest first)
            fetch_limit = limit * 3 if search else limit
            raw_messages = await client.get_messages(entity, limit=fetch_limit)

            results = []
            for m in raw_messages:
                text = m.text or ""
                ts = m.date

                # Apply since filter
                if since_dt and ts and ts.replace(tzinfo=None) < since_dt.replace(tzinfo=None):
                    continue

                # Apply search filter
                if search and search.lower() not in text.lower():
                    continue

                sender = "Valor" if m.out else (getattr(m.sender, "first_name", None) or "Unknown")
                results.append(
                    {
                        "sender": sender,
                        "content": text,
                        "timestamp": ts.isoformat() if ts else None,
                        "message_type": "text",
                    }
                )

                if len(results) >= limit:
                    break

            # Return in chronological order (oldest first)
            return list(reversed(results))
        finally:
            await client.disconnect()

    return asyncio.run(_fetch())


def cmd_read(args: argparse.Namespace) -> int:
    """Read messages from a chat."""
    from tools.telegram_history import (
        get_recent_messages,
        search_all_chats,
        search_history,
    )

    chat_id = None
    if args.chat:
        chat_id = resolve_chat(args.chat)
        if not chat_id:
            # Try raw value (might be a numeric chat ID)
            if args.chat.lstrip("-").isdigit():
                chat_id = args.chat

    # Search mode
    if args.search:
        if chat_id:
            days = 365  # search broadly when explicit search
            if args.since:
                since_dt = parse_since(args.since)
                if since_dt:
                    days = max(1, (utc_now() - since_dt).days + 1)
            result = search_history(
                query=args.search,
                chat_id=chat_id,
                max_results=args.limit,
                max_age_days=days,
            )
        else:
            result = search_all_chats(
                query=args.search,
                max_results=args.limit,
                max_age_days=365,
            )

        if "error" in result:
            print(f"Error: {result['error']}", file=sys.stderr)
            return 1

        messages = result.get("results", [])
    else:
        # Recent messages mode
        if not chat_id and not args.chat:
            print("Error: --chat is required when not using --search", file=sys.stderr)
            return 1

        if chat_id:
            result = get_recent_messages(chat_id=chat_id, limit=args.limit)

            if "error" in result:
                print(f"Error: {result['error']}", file=sys.stderr)
                return 1

            messages = result.get("messages", [])

            # Filter by --since if provided
            if args.since:
                since_dt = parse_since(args.since)
                if since_dt:
                    filtered = []
                    for msg in messages:
                        try:
                            msg_dt = datetime.fromisoformat(msg.get("timestamp", ""))
                            if msg_dt >= since_dt:
                                filtered.append(msg)
                        except (ValueError, TypeError):
                            filtered.append(msg)  # include if we can't parse
                    messages = filtered

            # Reverse to show chronological order (oldest first)
            messages = list(reversed(messages))
        else:
            messages = []

    # Fallback to Telegram API if Redis returned nothing
    if not messages and args.chat:
        try:
            since_dt = parse_since(args.since) if args.since else None
            messages = _fetch_from_telegram_api(
                chat_name=args.chat,
                limit=args.limit,
                search=args.search,
                since_dt=since_dt,
            )
            if messages:
                print(f"(fetched from Telegram API — {len(messages)} messages)", file=sys.stderr)
        except Exception as e:
            print(f"Telegram API fallback failed: {e}", file=sys.stderr)

    # Output
    if args.json:
        print(json.dumps(messages, indent=2, default=str))
        return 0

    if not messages:
        print("No messages found.")
        return 0

    for msg in messages:
        ts = format_timestamp(msg.get("timestamp"))
        sender = msg.get("sender", "unknown")
        content = msg.get("content", "")
        # Truncate long messages for display
        if len(content) > 500:
            content = content[:497] + "..."
        print(f"[{ts}] {sender}: {content}")

    return 0


def _linkify_text(text: str) -> str:
    """Apply PR/Issue linkification to the message text.

    Mirrors the same helper in send_telegram.py. Falls back to unmodified
    text if the formatting module is unavailable.
    """
    try:
        from bridge.formatting import linkify_references

        project_key = os.environ.get("PROJECT_KEY", "ai")
        return linkify_references(text, project_key)
    except Exception:
        return text


def _get_redis_connection():
    """Get a Redis connection using the project's standard pattern."""
    import redis

    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    return redis.Redis.from_url(redis_url, decode_responses=True)


def cmd_send(args: argparse.Namespace) -> int:
    """Send a message to a chat via the Redis relay.

    Routes through the bridge relay (bridge/telegram_relay.py) rather than
    creating a direct Telethon client. This avoids the SQLite session lock
    conflict when the bridge is running, and gives us markdown formatting,
    forum/reply_to support, and retry logic for free.

    Queue contract (matches send_telegram.py and telegram_relay.py):
        Key: telegram:outbox:{session_id}
        Payload: {chat_id, reply_to, text, file_paths, session_id, timestamp}
        TTL: 1 hour
    """
    # Resolve chat name to numeric ID
    chat_id = resolve_chat(args.chat)
    if not chat_id:
        if args.chat.lstrip("-").isdigit():
            chat_id = args.chat
        else:
            print(f"Error: Unknown chat '{args.chat}'", file=sys.stderr)
            print("Use 'valor-telegram chats' to list known chats.", file=sys.stderr)
            return 1

    text = args.message or ""
    file_path = args.file or args.image or args.audio

    # Validate: must have either text or a file
    if not text and not file_path:
        print("Error: Must provide a message or file to send.", file=sys.stderr)
        return 1

    # Validate file exists before queueing
    if file_path and not Path(file_path).exists():
        print(f"Error: File not found: {file_path}", file=sys.stderr)
        return 1

    # Apply linkification and length truncation to text (skip for empty string,
    # matching send_telegram.py's guard at line 138)
    if text:
        text = _linkify_text(text)
        if len(text) > TELEGRAM_MAX_LENGTH:
            # Truncate at sentence boundary if possible
            truncated = text[: TELEGRAM_MAX_LENGTH - 3]
            last_period = truncated.rfind(". ")
            if last_period > TELEGRAM_MAX_LENGTH // 2:
                truncated = truncated[: last_period + 1]
            text = truncated + "..."

    # Build relay-compatible payload
    # Use synthetic session_id with cli- prefix to avoid collision with bridge session IDs
    session_id = f"cli-{int(time.time())}"
    reply_to = getattr(args, "reply_to", None)

    payload: dict = {
        "chat_id": chat_id,
        "reply_to": int(reply_to) if reply_to else None,
        "text": text,
        "session_id": session_id,
        "timestamp": time.time(),
    }
    if file_path:
        # Relay expects file_paths as a list of absolute paths
        payload["file_paths"] = [str(Path(file_path).resolve())]

    # Push to Redis outbox queue
    queue_key = f"telegram:outbox:{session_id}"
    try:
        r = _get_redis_connection()
        r.rpush(queue_key, json.dumps(payload))
        r.expire(queue_key, 3600)
    except Exception as e:
        print(f"Error: Failed to queue message in Redis: {e}", file=sys.stderr)
        print(
            "Ensure Redis is running and REDIS_URL is configured (default: redis://localhost:6379/0).",
            file=sys.stderr,
        )
        return 1

    # Confirmation
    parts = []
    if text:
        parts.append(f"{len(text)} chars")
    if file_path:
        parts.append(f"file: {Path(file_path).name}")
    print(f"Message queued ({', '.join(parts)})")
    print(
        "Note: delivery requires the bridge relay to be running"
        " (./scripts/valor-service.sh status)."
    )
    return 0


def cmd_chats(args: argparse.Namespace) -> int:
    """List known chats."""
    from tools.telegram_history import list_chats

    result = list_chats()

    if "error" in result:
        print(f"Error: {result['error']}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 0

    chats = result.get("chats", [])
    if not chats:
        print("No chats found in history database.")
        print("Chats are registered as messages are received by the bridge.")
        return 0

    print(f"Known chats ({len(chats)}):")
    print()
    print(f"{'Chat Name':<35} {'Messages':>10} {'Last Activity':<20}")
    print("-" * 70)

    for chat in chats:
        name = chat.get("chat_name", "unknown")
        if len(name) > 33:
            name = name[:30] + "..."
        count = chat.get("message_count", 0)
        last = format_timestamp(chat.get("last_message"))
        print(f"{name:<35} {count:>10} {last:<20}")

    return 0


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        prog="valor-telegram",
        description="Read and send Telegram messages",
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # read subcommand
    read_parser = subparsers.add_parser("read", help="Read messages from a chat")
    read_parser.add_argument("--chat", "-c", help="Chat name or ID")
    read_parser.add_argument(
        "--limit", "-n", type=int, default=10, help="Max messages (default: 10)"
    )
    read_parser.add_argument("--search", "-s", help="Search keyword")
    read_parser.add_argument("--since", help="Time filter, e.g. '1 hour ago', '2 days ago'")
    read_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # send subcommand
    send_parser = subparsers.add_parser("send", help="Send a message")
    send_parser.add_argument("--chat", "-c", required=True, help="Chat name or ID")
    send_parser.add_argument("message", nargs="?", default="", help="Message text")
    send_parser.add_argument("--file", "-f", help="File to attach")
    send_parser.add_argument("--image", "-i", help="Image to send")
    send_parser.add_argument("--audio", "-a", help="Audio file to send")
    send_parser.add_argument(
        "--reply-to",
        type=int,
        default=None,
        help="Message ID to reply to (required for forum groups/topics)",
    )

    # chats subcommand
    chats_parser = subparsers.add_parser("chats", help="List known chats")
    chats_parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    handlers = {
        "read": cmd_read,
        "send": cmd_send,
        "chats": cmd_chats,
    }

    handler = handlers.get(args.command)
    if handler:
        return handler(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
