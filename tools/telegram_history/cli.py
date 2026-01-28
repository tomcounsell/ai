#!/usr/bin/env python3
"""
Telegram History CLI

Search and browse Telegram conversation history from the command line.

Usage:
    valor-history search "query" [--group "Dev: Valor"] [--days 30]
    valor-history recent [--group "Dev: Valor"] [--limit 20]
    valor-history groups
    valor-history links [--domain github.com] [--status unread]
    valor-history stats [--group "Dev: Valor"]

Install:
    pip install -e /Users/valorengels/src/ai
    # or add to PATH:
    ln -s /Users/valorengels/src/ai/tools/telegram_history/cli.py ~/.local/bin/valor-history
"""

import argparse
import json
import sys
from datetime import datetime

from tools.telegram_history import (
    get_chat_stats,
    get_link_stats,
    get_recent_messages,
    list_chats,
    list_links,
    resolve_chat_id,
    search_all_chats,
    search_history,
    search_links,
)


def format_timestamp(ts: str | None) -> str:
    """Format a timestamp for display."""
    if not ts:
        return "unknown"
    try:
        dt = datetime.fromisoformat(ts)
        return dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return ts[:16] if len(ts) > 16 else ts


def truncate(text: str, length: int = 100) -> str:
    """Truncate text with ellipsis."""
    if not text:
        return ""
    if len(text) <= length:
        return text
    return text[:length - 3] + "..."


def cmd_search(args: argparse.Namespace) -> int:
    """Search message history."""
    if args.group:
        # Resolve group name to chat_id
        chat_id = resolve_chat_id(args.group)
        if not chat_id:
            # Maybe it's already a chat_id
            if args.group.startswith("-") or args.group.isdigit():
                chat_id = args.group
            else:
                print(f"Error: Could not find group '{args.group}'", file=sys.stderr)
                print("Use 'valor-history groups' to list known groups", file=sys.stderr)
                return 1

        result = search_history(
            query=args.query,
            chat_id=chat_id,
            max_results=args.limit,
            max_age_days=args.days,
        )
    else:
        # Search all chats
        result = search_all_chats(
            query=args.query,
            max_results=args.limit,
            max_age_days=args.days,
        )

    if "error" in result:
        print(f"Error: {result['error']}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 0

    # Human-readable output
    matches = result.get("results", [])
    if not matches:
        print(f"No messages found matching '{args.query}'")
        return 0

    print(f"Found {len(matches)} messages matching '{args.query}':")
    print()

    for msg in matches:
        chat_name = msg.get("chat_name", msg.get("chat_id", "unknown"))
        sender = msg.get("sender", "unknown")
        ts = format_timestamp(msg.get("timestamp"))
        content = truncate(msg.get("content", ""), 200)
        score = msg.get("relevance_score", 0)

        print(f"[{ts}] {chat_name} | {sender}")
        print(f"  {content}")
        print(f"  (relevance: {score:.2f})")
        print()

    return 0


def cmd_recent(args: argparse.Namespace) -> int:
    """Show recent messages."""
    if not args.group:
        print("Error: --group is required for 'recent' command", file=sys.stderr)
        print("Use 'valor-history groups' to list known groups", file=sys.stderr)
        return 1

    chat_id = resolve_chat_id(args.group)
    if not chat_id:
        if args.group.startswith("-") or args.group.isdigit():
            chat_id = args.group
        else:
            print(f"Error: Could not find group '{args.group}'", file=sys.stderr)
            return 1

    result = get_recent_messages(chat_id=chat_id, limit=args.limit)

    if "error" in result:
        print(f"Error: {result['error']}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 0

    messages = result.get("messages", [])
    if not messages:
        print(f"No messages found in '{args.group}'")
        return 0

    print(f"Recent {len(messages)} messages from '{args.group}':")
    print()

    # Reverse to show oldest first (chronological order)
    for msg in reversed(messages):
        sender = msg.get("sender", "unknown")
        ts = format_timestamp(msg.get("timestamp"))
        content = truncate(msg.get("content", ""), 200)

        print(f"[{ts}] {sender}: {content}")

    return 0


def cmd_groups(args: argparse.Namespace) -> int:
    """List known groups/chats."""
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
        name = truncate(chat.get("chat_name", "unknown"), 33)
        count = chat.get("message_count", 0)
        last = format_timestamp(chat.get("last_message"))

        print(f"{name:<35} {count:>10} {last:<20}")

    return 0


def cmd_links(args: argparse.Namespace) -> int:
    """Search or list links."""
    if args.query or args.domain or args.sender:
        result = search_links(
            query=args.query,
            domain=args.domain,
            sender=args.sender,
            status=args.status,
            limit=args.limit,
        )
    else:
        result = list_links(
            limit=args.limit,
            status=args.status,
        )

    if "error" in result:
        print(f"Error: {result['error']}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 0

    links = result.get("links", [])
    if not links:
        print("No links found.")
        return 0

    print(f"Found {len(links)} links:")
    print()

    for link in links:
        url = link.get("url", "")
        title = link.get("title") or link.get("domain", "")
        sender = link.get("sender", "unknown")
        ts = format_timestamp(link.get("timestamp"))
        status = link.get("status", "")
        summary = truncate(link.get("ai_summary", ""), 100)

        status_icon = {"unread": "*", "read": " ", "archived": "x"}.get(status, "?")

        print(f"[{status_icon}] {truncate(title, 50)}")
        print(f"    {truncate(url, 80)}")
        print(f"    from {sender} on {ts}")
        if summary:
            print(f"    {summary}")
        print()

    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    """Show statistics."""
    if args.group:
        chat_id = resolve_chat_id(args.group)
        if not chat_id:
            if args.group.startswith("-") or args.group.isdigit():
                chat_id = args.group
            else:
                print(f"Error: Could not find group '{args.group}'", file=sys.stderr)
                return 1

        result = get_chat_stats(chat_id)
        if "error" in result:
            print(f"Error: {result['error']}", file=sys.stderr)
            return 1

        if args.json:
            print(json.dumps(result, indent=2, default=str))
            return 0

        print(f"Statistics for '{args.group}':")
        print(f"  Total messages: {result.get('total_messages', 0)}")
        print(f"  Unique senders: {result.get('unique_senders', 0)}")
        print(f"  First message: {format_timestamp(result.get('first_message'))}")
        print(f"  Last message: {format_timestamp(result.get('last_message'))}")

    else:
        # Show link stats
        result = get_link_stats()
        if "error" in result:
            print(f"Error: {result['error']}", file=sys.stderr)
            return 1

        if args.json:
            print(json.dumps(result, indent=2, default=str))
            return 0

        by_status = result.get("by_status", {})
        print("Link Statistics:")
        print(f"  Total links: {result.get('total_links', 0)}")
        print(f"  Unique domains: {result.get('unique_domains', 0)}")
        print(f"  Unique senders: {result.get('unique_senders', 0)}")
        print(f"  Unread: {by_status.get('unread', 0)}")
        print(f"  Read: {by_status.get('read', 0)}")
        print(f"  Archived: {by_status.get('archived', 0)}")

        top_domains = result.get("top_domains", [])
        if top_domains:
            print("\nTop domains:")
            for d in top_domains[:5]:
                print(f"  {d['domain']}: {d['count']} links")

    return 0


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        prog="valor-history",
        description="Search and browse Telegram conversation history",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON (machine-readable)",
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # search command
    search_parser = subparsers.add_parser(
        "search",
        help="Search message history",
    )
    search_parser.add_argument(
        "query",
        help="Search query",
    )
    search_parser.add_argument(
        "--group", "-g",
        help="Group name or chat ID to search in (searches all if omitted)",
    )
    search_parser.add_argument(
        "--days", "-d",
        type=int,
        default=30,
        help="Search within last N days (default: 30)",
    )
    search_parser.add_argument(
        "--limit", "-n",
        type=int,
        default=20,
        help="Maximum results (default: 20)",
    )

    # recent command
    recent_parser = subparsers.add_parser(
        "recent",
        help="Show recent messages from a group",
    )
    recent_parser.add_argument(
        "--group", "-g",
        required=True,
        help="Group name or chat ID",
    )
    recent_parser.add_argument(
        "--limit", "-n",
        type=int,
        default=20,
        help="Number of messages (default: 20)",
    )

    # groups command
    subparsers.add_parser(
        "groups",
        help="List known groups/chats",
    )

    # links command
    links_parser = subparsers.add_parser(
        "links",
        help="Search or list stored links",
    )
    links_parser.add_argument(
        "query",
        nargs="?",
        help="Search query (optional)",
    )
    links_parser.add_argument(
        "--domain", "-d",
        help="Filter by domain",
    )
    links_parser.add_argument(
        "--sender", "-s",
        help="Filter by sender",
    )
    links_parser.add_argument(
        "--status",
        choices=["unread", "read", "archived"],
        help="Filter by status",
    )
    links_parser.add_argument(
        "--limit", "-n",
        type=int,
        default=20,
        help="Maximum results (default: 20)",
    )

    # stats command
    stats_parser = subparsers.add_parser(
        "stats",
        help="Show statistics",
    )
    stats_parser.add_argument(
        "--group", "-g",
        help="Group name or chat ID (shows link stats if omitted)",
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    # Dispatch to command handler
    handlers = {
        "search": cmd_search,
        "recent": cmd_recent,
        "groups": cmd_groups,
        "links": cmd_links,
        "stats": cmd_stats,
    }

    handler = handlers.get(args.command)
    if handler:
        return handler(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
