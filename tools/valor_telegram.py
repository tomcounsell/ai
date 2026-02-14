#!/usr/bin/env python3
"""
Unified Telegram messaging CLI.

Usage:
    valor-telegram read --chat "Dev: Valor" --limit 10
    valor-telegram read --chat "Tom" --search "deployment"
    valor-telegram read --chat "Dev: Valor" --since "1 hour ago"
    valor-telegram send --chat "Dev: Valor" "Hello world"
    valor-telegram send --chat "Tom" --file ./screenshot.png "Caption"
    valor-telegram chats
"""

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path


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
            return datetime.now() - delta_fn(match)

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


def _fetch_live_messages(chat_id: str, limit: int) -> list[dict]:
    """Fetch messages from Telegram via Telethon and upsert into SQLite cache.

    Returns a list of message dicts with keys: message_id, sender, content,
    timestamp, message_type.
    """
    import sqlite3

    from dotenv import load_dotenv

    load_dotenv()

    async def _fetch():
        from telethon import TelegramClient

        session_path = str(Path(__file__).parent.parent / "data" / "valor_bridge")
        api_id = int(os.environ.get("TELEGRAM_API_ID", "0"))
        api_hash = os.environ.get("TELEGRAM_API_HASH", "")

        if not api_id or not api_hash:
            raise RuntimeError(
                "TELEGRAM_API_ID and TELEGRAM_API_HASH must be set in .env"
            )

        client = TelegramClient(session_path, api_id, api_hash)
        await client.start()
        try:
            msgs = await client.get_messages(int(chat_id), limit=limit)
            results = []
            for msg in msgs:
                if msg is None:
                    continue
                # Determine sender name
                sender_name = "unknown"
                try:
                    sender = await msg.get_sender()
                    if sender is not None:
                        if hasattr(sender, "first_name"):
                            sender_name = sender.first_name or ""
                            if getattr(sender, "last_name", None):
                                sender_name += f" {sender.last_name}"
                            sender_name = sender_name.strip() or "unknown"
                        elif hasattr(sender, "title"):
                            sender_name = sender.title or "unknown"
                except Exception:
                    pass

                content = msg.text or ""
                timestamp = msg.date.isoformat() if msg.date else None
                msg_type = "text"
                if msg.photo:
                    msg_type = "photo"
                elif msg.document:
                    msg_type = "document"
                elif msg.sticker:
                    msg_type = "sticker"

                results.append(
                    {
                        "message_id": msg.id,
                        "sender": sender_name,
                        "content": content,
                        "timestamp": timestamp,
                        "message_type": msg_type,
                    }
                )

            # Upsert fetched messages into SQLite cache
            from tools.telegram_history import store_message

            for m in results:
                try:
                    store_message(
                        chat_id=chat_id,
                        content=m["content"],
                        sender=m["sender"],
                        message_id=m["message_id"],
                        timestamp=(
                            datetime.fromisoformat(m["timestamp"])
                            if m["timestamp"]
                            else None
                        ),
                        message_type=m["message_type"],
                    )
                except sqlite3.IntegrityError:
                    pass  # Duplicate message, skip
                except Exception:
                    pass  # Best-effort cache update

            return results
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
            else:
                print(f"Error: Unknown chat '{args.chat}'", file=sys.stderr)
                print(
                    "Use 'valor-telegram chats' to list known chats.", file=sys.stderr
                )
                return 1

    # Search mode (always uses SQLite cache)
    if args.search:
        if chat_id:
            days = 365  # search broadly when explicit search
            if args.since:
                since_dt = parse_since(args.since)
                if since_dt:
                    days = max(1, (datetime.now() - since_dt).days + 1)
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
        if not chat_id:
            print("Error: --chat is required when not using --search", file=sys.stderr)
            return 1

        use_cache = getattr(args, "cached", False)

        if not use_cache:
            # Live fetch via Telethon (default)
            try:
                messages = _fetch_live_messages(chat_id, args.limit)
            except Exception as e:
                print(
                    f"Warning: Live fetch failed ({e}), falling back to cache.",
                    file=sys.stderr,
                )
                use_cache = True

        if use_cache:
            # Cache-only path (fallback or --cached flag)
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


def cmd_send(args: argparse.Namespace) -> int:
    """Send a message to a chat."""
    from dotenv import load_dotenv

    load_dotenv()

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

    if not text and not file_path:
        print("Error: Must provide a message or file to send.", file=sys.stderr)
        return 1

    if file_path and not Path(file_path).exists():
        print(f"Error: File not found: {file_path}", file=sys.stderr)
        return 1

    async def _send():
        from telethon import TelegramClient

        session_path = str(Path(__file__).parent.parent / "data" / "valor_bridge")
        api_id = int(os.environ.get("TELEGRAM_API_ID", "0"))
        api_hash = os.environ.get("TELEGRAM_API_HASH", "")

        if not api_id or not api_hash:
            print(
                "Error: TELEGRAM_API_ID and TELEGRAM_API_HASH must be set in .env",
                file=sys.stderr,
            )
            return 1

        client = TelegramClient(session_path, api_id, api_hash)
        await client.start()
        try:
            entity = await client.get_entity(int(chat_id))
            if file_path:
                await client.send_file(entity, file_path, caption=text)
            else:
                await client.send_message(entity, text)
            print(f"Sent to {args.chat}")
            return 0
        except Exception as e:
            print(f"Error sending: {e}", file=sys.stderr)
            return 1
        finally:
            await client.disconnect()

    return asyncio.run(_send())


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
    read_parser.add_argument(
        "--since", help="Time filter, e.g. '1 hour ago', '2 days ago'"
    )
    read_parser.add_argument("--json", action="store_true", help="Output as JSON")
    read_parser.add_argument(
        "--cached",
        action="store_true",
        help="Use cached messages only (skip live fetch)",
    )

    # send subcommand
    send_parser = subparsers.add_parser("send", help="Send a message")
    send_parser.add_argument("--chat", "-c", required=True, help="Chat name or ID")
    send_parser.add_argument("message", nargs="?", default="", help="Message text")
    send_parser.add_argument("--file", "-f", help="File to attach")
    send_parser.add_argument("--image", "-i", help="Image to send")
    send_parser.add_argument("--audio", "-a", help="Audio file to send")

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
