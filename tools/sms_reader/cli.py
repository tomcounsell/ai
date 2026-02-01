#!/usr/bin/env python3
"""CLI for SMS reader tools."""

import argparse
import json
import sys

from tools.sms_reader import (
    get_2fa,
    get_latest_2fa_code,
    get_recent_messages,
    search_messages,
    list_senders,
)


def main():
    parser = argparse.ArgumentParser(prog="sms", description="Read macOS Messages")
    sub = parser.add_subparsers(dest="command", required=True)

    # sms 2fa
    p = sub.add_parser("2fa", help="Get most recent 2FA code")
    p.add_argument("--minutes", type=int, default=5)
    p.add_argument("--sender", type=str)
    p.add_argument("--detailed", action="store_true", help="Show full message info")

    # sms recent
    p = sub.add_parser("recent", help="Get recent messages")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--sender", type=str)
    p.add_argument("--since-minutes", type=int)

    # sms search
    p = sub.add_parser("search", help="Search messages by content")
    p.add_argument("query", type=str)
    p.add_argument("--limit", type=int, default=20)

    # sms senders
    p = sub.add_parser("senders", help="List message senders")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--since-days", type=int, default=30)

    args = parser.parse_args()

    if args.command == "2fa":
        if args.detailed:
            result = get_latest_2fa_code(minutes=args.minutes, sender=args.sender)
            if result:
                print(json.dumps(result, indent=2))
            else:
                print("No 2FA code found", file=sys.stderr)
                sys.exit(1)
        else:
            code = get_2fa(minutes=args.minutes, sender=args.sender)
            if code:
                print(code)
            else:
                print("No 2FA code found", file=sys.stderr)
                sys.exit(1)

    elif args.command == "recent":
        messages = get_recent_messages(
            limit=args.limit,
            sender=args.sender,
            since_minutes=args.since_minutes,
        )
        print(json.dumps(messages, indent=2))

    elif args.command == "search":
        messages = search_messages(query=args.query, limit=args.limit)
        print(json.dumps(messages, indent=2))

    elif args.command == "senders":
        senders = list_senders(limit=args.limit, since_days=args.since_days)
        print(json.dumps(senders, indent=2))


if __name__ == "__main__":
    main()
