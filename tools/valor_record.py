#!/usr/bin/env python3
"""valor-record chats
valor-record messages [CHAT] [--search TEXT] [--limit N] [--before RECORD] [--json]
valor-record tasks [--search TEXT] [--limit N] [--json]
valor-record task ID [--json]

Reads the ledger of this turn's own project: its chats, the messages each
received and what they were bound to, its tasks and what became of them.
The kernel answers (`core/record.py`) at `$VALOR_RECORD_URL`, which carries
the turn's own gateway token, so the project is the kernel's to say. CHAT
is `<channel>:<id>` as `chats` prints it. Messages print oldest first.
Standard library only, and Python 3.9 (macOS's own `python3`), so it runs
in any workspace.
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


def _when(at: object) -> str:
    try:
        return datetime.fromisoformat(str(at)).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")  # noqa: UP017
    except ValueError:
        return "an unknown time"


def message_line(r: dict) -> str:
    m = r["message"]
    who = m.get("sender_name") or m.get("sender_id") or "someone"
    ids = f"message {m.get('message_id')}" + (f", replying to {m['reply_to']}" if m.get("reply_to") else "")
    bound = r.get("bound") or {}
    to = f"{bound.get('as')} {bound.get('task_id') or ''}".strip() if bound else "not bound yet"
    said = (m.get("text") or "").strip() or "(no text)"
    files = "".join(f" [file: {a.get('name') or 'file'}]" for a in m.get("attachments") or [])
    reaction = f"; {r['reaction']}" if r.get("reaction") else ""
    return (
        f"#{r['record']} {r['chat']} [{_when(m.get('sent_at'))}] {who} ({ids}{reaction}; {to}): {said}{files}"
    )


def task_line(t: dict) -> str:
    where = f"session of {t['chat']}" if t["chat"] else f"under {t['parent_id']}" if t["parent_id"] else "root"
    out = f"{t['task_id']} {t['state']} ({where}, last {_when(t.get('last_at'))}): {(t['instruction'] or '')[:200]}"
    if t.get("open_question"):
        out += f"\n    asked: {t['open_question'][:300]}"
    if t.get("delivered"):
        out += f"\n    delivered: {t['delivered'][:300]}"
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="valor-record", description="Read this project's record.")
    sub = parser.add_subparsers(dest="verb", required=True)
    sub.add_parser("chats", help="the project's chats, rooms and sessions")
    msgs = sub.add_parser("messages", help="messages received, latest first or matching --search")
    msgs.add_argument("chat", nargs="?", help="one chat, as `chats` prints it; default every chat")
    msgs.add_argument("--before", help="only messages older than this record number (#N)")
    tasks = sub.add_parser("tasks", help="the project's tasks, newest first")
    for p in (msgs, tasks):
        p.add_argument("--search", "-s", help="text to search for")
        p.add_argument("--limit", "-n", type=int, default=50, help="how many (at most 200)")
    one = sub.add_parser("task", help="one task's ledger rows")
    one.add_argument("id")
    for p in sub.choices.values():
        p.add_argument("--json", action="store_true", help="print the kernel's JSON")
    args = parser.parse_args(argv)

    base = os.environ.get("VALOR_RECORD_URL")
    if not base:
        print("valor-record: VALOR_RECORD_URL is not set; it is set in a kernel turn", file=sys.stderr)
        return 2
    query = {
        "chat": getattr(args, "chat", None),
        "q": getattr(args, "search", None),
        "limit": getattr(args, "limit", None),
        "before": (getattr(args, "before", None) or "").lstrip("#") or None,
        "id": getattr(args, "id", None),
    }
    url = f"{base}/{args.verb}?" + urllib.parse.urlencode({k: v for k, v in query.items() if v is not None})
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            out = json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            said = json.loads(e.read()).get("error")
        except ValueError:
            said = None
        print(f"valor-record: {said or e}", file=sys.stderr)
        return 1
    except OSError:
        print("valor-record: the kernel is not answering", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(out, indent=2, ensure_ascii=False))
    elif not out:
        print("nothing found")
    elif args.verb == "chats":
        for c in out:
            print(f"{c['chat']}: {c['room'] or '(no room)'}; sessions: {', '.join(c['sessions']) or 'none'}")
    elif args.verb == "messages":
        for r in reversed(out):
            print(message_line(r))
    elif args.verb == "tasks":
        for t in out:
            print(task_line(t))
    else:
        for r in out:
            print(
                f"{r['id']:>7} {_when(r['at'])} {r['type']:<18} {json.dumps(r['payload'], ensure_ascii=False)}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
