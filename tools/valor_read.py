#!/usr/bin/env python3
"""valor-telegram read [CHAT] [--search TEXT] [--limit N] [--before ID] [--json]
valor-slack read [CHAT] [--search TEXT] [--limit N] [--before TS] [--json]

Reads a chat's history, or searches it, through the channel's running
bridge (`core.bridge.reads` on 127.0.0.1), which reads with Valor's own
account. The name it is run by picks the channel. CHAT is the chat id as
the ledger has it (`-1003890616618`, `T0ABC/C0DEF`, `T0ABC/U0GHI`), with or
without a `<channel>:` prefix; with none, `$VALOR_CHAT`, the turn's own
chat. Prints oldest first, one message per line. Standard library only,
and Python 3.9 (macOS's own `python3`), so it runs in any workspace.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

PORTS = {"telegram": ("VALOR_TELEGRAM_READ_PORT", "8712"), "slack": ("VALOR_SLACK_READ_PORT", "8713")}


def line(m: dict) -> str:
    """One message, in the shape a chat session's prompt shows one."""
    try:
        when = datetime.fromisoformat(str(m.get("sent_at"))).astimezone(timezone.utc)  # noqa: UP017  3.9 has no UTC
        when = when.strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        when = "an unknown time"
    who = m.get("sender_name") or m.get("sender_id") or "someone"
    if m.get("out"):
        who = f"{who} (you)"
    ids = f"message {m['id']}" + (f", replying to {m['reply_to']}" if m.get("reply_to") else "")
    said = (m.get("text") or "").strip() or "(no text)"
    media = m.get("media")
    files = f" [{media['kind']}: {media['name']}]" if media else ""
    return f"[{when}] {who} ({ids}): {said}{files}"


def main(argv: list[str] | None = None, prog: str | None = None) -> int:
    prog = os.path.basename(prog or sys.argv[0])
    channel = prog.removeprefix("valor-")
    if channel not in PORTS:
        print(f"{prog}: run as valor-telegram or valor-slack", file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(prog=prog, description=f"Read or search a {channel} chat's history.")
    sub = parser.add_subparsers(dest="verb", required=True)
    read = sub.add_parser("read", help="the chat's latest messages, or those matching --search")
    read.add_argument("chat", nargs="?", help="the chat id; default $VALOR_CHAT")
    read.add_argument("--search", "-s", help="text to search for")
    read.add_argument("--limit", "-n", type=int, default=50, help="how many (at most 100)")
    read.add_argument("--before", help="only messages older than this message id")
    read.add_argument("--json", action="store_true", help="print the bridge's JSON")
    args = parser.parse_args(argv)

    chat = args.chat
    if chat is None:
        mine = os.environ.get("VALOR_CHAT", "")
        if not mine.startswith(f"{channel}:"):
            print(f"{prog}: name the chat; this turn's own chat is {mine or 'not set'}", file=sys.stderr)
            return 2
        chat = mine
    chat = chat.removeprefix(f"{channel}:")
    query = {"chat": chat, "limit": args.limit, "q": args.search, "before": args.before}
    env, default = PORTS[channel]
    port = os.environ.get(env, default)
    url = f"http://127.0.0.1:{port}/history?" + urllib.parse.urlencode(
        {k: v for k, v in query.items() if v is not None}
    )
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            out = json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            said = json.loads(e.read()).get("error")
        except ValueError:
            said = None
        print(f"{prog}: {said or e}", file=sys.stderr)
        return 1
    except OSError:  # URLError is one
        print(f"{prog}: the {channel} bridge is not answering on 127.0.0.1:{port}", file=sys.stderr)
        return 1
    messages = out["messages"]
    if args.json:
        print(json.dumps(messages, indent=2, ensure_ascii=False))
    elif not messages:
        print("no messages found")
    else:
        for m in reversed(messages):
            print(line(m))
    return 0


if __name__ == "__main__":
    sys.exit(main())
