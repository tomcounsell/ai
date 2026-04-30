#!/usr/bin/env python3
"""Unified email CLI (``valor-email``).

Structural mirror of ``tools/valor_telegram.py`` — three subcommands that
read/send/list email via the Redis history cache and the ``email:outbox:*``
relay.

Usage:
    valor-email read --limit 5
    valor-email read --search "deployment"
    valor-email read --since "2 hours ago"
    valor-email send --to alice@example.com --subject "Re: Deploy" "Looks good"
    valor-email send --to alice@example.com --file ./report.pdf "See attached"
    valor-email send --to alice@example.com --reply-to "<abc@host>" "Body"
    valor-email threads

Delivery path:
    The CLI always queues via Redis (``email:outbox:{session_id}``) — never sends
    directly. If the relay is not running, queued messages sit until the bridge
    is restarted. Check with ``./scripts/valor-service.sh email-status``.

Read path:
    Tries the Redis history cache first (populated by the bridge's IMAP poll
    loop). On empty cache, opens a read-only IMAP connection and fetches from
    INBOX filtered by known senders (spike-4 — prevents cross-machine interference).
"""

from __future__ import annotations

import argparse
import email as email_lib
import imaplib
import json
import os
import secrets
import sys
import time
from datetime import datetime
from pathlib import Path

# Shared helper from the Telegram CLI (only piece of shared code, per plan).
from tools.valor_telegram import parse_since

_SESSION_ID_PREFIX = "cli"


def _get_redis_connection():
    """Return a Redis connection using the project's standard env var."""
    import redis

    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    return redis.Redis.from_url(redis_url, decode_responses=True)


def format_timestamp(ts: str | None) -> str:
    """Format a timestamp for display, mirroring valor-telegram."""
    if not ts:
        return "unknown"
    try:
        dt = datetime.fromisoformat(ts)
        return dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return ts[:16] if len(ts) > 16 else ts


def _msg_ts(msg: dict) -> float | None:
    """Extract a unix timestamp from a history-cache message dict.

    Cache blobs render ``timestamp`` as an ISO 8601 string; convert it back to
    a float for comparison with ``since_ts`` in the CLI filter layer. Returns
    None when the timestamp is missing or unparseable — such messages are kept
    rather than dropped, since we cannot prove they violate ``--since``.
    """
    ts = msg.get("timestamp")
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        return float(ts)
    try:
        return datetime.fromisoformat(str(ts)).timestamp()
    except (ValueError, TypeError):
        return None


def _normalize_msgid(raw: str) -> str:
    """Normalize a Message-ID to angle-bracketed form.

    Accepts ``abc@host``, ``<abc@host>``, or whitespace-padded variants.
    Raises argparse.ArgumentTypeError on empty input.
    """
    s = (raw or "").strip()
    if not s:
        raise argparse.ArgumentTypeError("Message-ID cannot be empty")
    if not s.startswith("<"):
        s = "<" + s
    if not s.endswith(">"):
        s = s + ">"
    return s


# =============================================================================
# read
# =============================================================================


def _imap_fallback_fetch(limit: int, search: str | None, since_ts: float | None) -> list[dict]:
    """Read-only IMAP fallback when the history cache returns nothing.

    Opens IMAP with ``readonly=True`` and filters FROM by known senders
    (spike-4 in the plan) — must not mark messages as SEEN and must not
    leak mail intended for other machines.

    Returns a list of message dicts shaped like the cache entries. On any
    error, returns an empty list and logs to stderr.
    """
    from bridge.email_bridge import _build_imap_sender_query, _get_imap_config, parse_email_message
    from bridge.routing import ensure_email_routing_loaded, get_known_email_search_terms

    # Populate routing maps once (idempotent — single entry point, no direct
    # mutation of routing module globals from the CLI).
    if not ensure_email_routing_loaded():
        print("IMAP fallback: could not load routing config.", file=sys.stderr)

    cfg = _get_imap_config()
    if not cfg:
        print(
            "IMAP fallback unavailable: IMAP_HOST/IMAP_USER/IMAP_PASSWORD not set.",
            file=sys.stderr,
        )
        return []

    known_senders = get_known_email_search_terms()
    if not known_senders:
        print(
            "IMAP fallback: no known senders configured — refusing to read INBOX.",
            file=sys.stderr,
        )
        return []

    sender_query = _build_imap_sender_query(known_senders)

    try:
        if cfg.get("ssl", True):
            conn = imaplib.IMAP4_SSL(cfg["host"], cfg["port"])
        else:
            conn = imaplib.IMAP4(cfg["host"], cfg["port"])
    except Exception as e:
        print(f"IMAP fallback: connection failed: {e}", file=sys.stderr)
        return []

    results: list[dict] = []
    try:
        conn.login(cfg["user"], cfg["password"])
        # readonly=True prevents SEEN flag side effects (spike-4 in the plan)
        conn.select("INBOX", readonly=True)

        # Search: most recent N messages matching our sender set. IMAP UID
        # search returns ascending so we take the tail for most-recent-first.
        status, data = conn.uid("search", None, sender_query)
        if status != "OK" or not data or not data[0]:
            return []
        uids = data[0].split()
        if not uids:
            return []
        uids = uids[-(limit * 3 if search else limit) :]

        for uid in reversed(uids):  # newest first
            status, msg_data = conn.uid("fetch", uid, "(RFC822)")
            if status != "OK" or not msg_data:
                continue
            raw_bytes = None
            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    raw_bytes = response_part[1]
                    break
            if not raw_bytes:
                continue

            parsed = parse_email_message(raw_bytes)
            if parsed is None:
                continue

            # Extract date for filtering
            try:
                msg = email_lib.message_from_bytes(raw_bytes)
                date_hdr = msg.get("Date", "")
                dt = email_lib.utils.parsedate_to_datetime(date_hdr) if date_hdr else None
                ts = dt.timestamp() if dt else time.time()
            except Exception:
                ts = time.time()

            if since_ts is not None and ts < since_ts:
                continue

            subject = parsed.get("subject") or ""
            body = parsed.get("body") or ""
            if search and (
                search.lower() not in subject.lower() and search.lower() not in body.lower()
            ):
                continue

            results.append(
                {
                    "message_id": parsed.get("message_id") or "",
                    "from_addr": parsed.get("from_addr", ""),
                    "subject": subject,
                    "body": body,
                    "timestamp": datetime.fromtimestamp(ts).isoformat(),
                    "in_reply_to": parsed.get("in_reply_to", ""),
                }
            )
            if len(results) >= limit:
                break
    except Exception as e:
        print(f"IMAP fallback: fetch error: {e}", file=sys.stderr)
    finally:
        try:
            conn.logout()
        except Exception:
            pass

    return results


def cmd_read(args: argparse.Namespace) -> int:
    """Read recent emails from the history cache; fall back to IMAP on miss."""
    from tools.email_history import get_recent_emails, search_history

    if args.mailbox and args.mailbox != "INBOX":
        print(
            f"Error: only INBOX is supported in v1 (got '{args.mailbox}').",
            file=sys.stderr,
        )
        return 1

    since_dt = parse_since(args.since) if args.since else None
    since_ts = since_dt.timestamp() if since_dt else None

    if args.search:
        # search_history's max_age_days is a coarse zrange floor in whole days;
        # we intentionally widen it to at least 1 day here so any sub-day
        # --since value (e.g. "2 hours ago") still yields a non-empty candidate
        # set. The caller-provided --since is then enforced strictly below by
        # post-filtering against since_ts, so the widened query never leaks
        # messages older than the user asked for.
        days = 7
        if since_dt:
            age = datetime.now(since_dt.tzinfo) - since_dt
            days = max(1, age.days + 1)
        result = search_history(
            query=args.search,
            mailbox="INBOX",
            max_results=args.limit,
            max_age_days=days,
        )
    else:
        result = get_recent_emails(
            mailbox="INBOX",
            limit=args.limit,
            since_ts=since_ts,
        )

    if "error" in result:
        print(f"Error: {result['error']}", file=sys.stderr)
        return 1

    messages = result.get("messages") or result.get("results") or []

    # Enforce --since strictly at the CLI layer — search_history's max_age_days
    # is intentionally coarser than the user's since_ts (see comment above),
    # so we drop any message older than since_ts before rendering.
    if since_ts is not None and messages:
        messages = [m for m in messages if _msg_ts(m) is None or _msg_ts(m) >= since_ts]

    # Fallback to IMAP if cache is empty
    if not messages:
        messages = _imap_fallback_fetch(args.limit, args.search, since_ts)
        if messages:
            print(
                f"(fetched from IMAP — {len(messages)} messages)",
                file=sys.stderr,
            )

    if args.json:
        # Unified --json envelope across subcommands: always a dict with a
        # named collection field plus a count, never a bare list.
        envelope = {
            "messages": messages,
            "count": len(messages),
            "mailbox": "INBOX",
        }
        if args.search:
            envelope["query"] = args.search
        print(json.dumps(envelope, indent=2, default=str))
        return 0

    if not messages:
        print("No messages found.")
        return 0

    for msg in messages:
        ts = format_timestamp(msg.get("timestamp"))
        sender = msg.get("from_addr", "unknown")
        subject = msg.get("subject", "") or "(no subject)"
        body = msg.get("body", "") or ""
        if len(body) > 300:
            body = body[:297] + "..."
        print(f"[{ts}] {sender}")
        print(f"  Subject: {subject}")
        print(f"  {body}")
        print()

    return 0


# =============================================================================
# send
# =============================================================================


def _build_session_id() -> str:
    """Build a collision-resistant CLI session_id.

    Format: ``cli-{unix_seconds}-{pid}-{hex8}`` — 32 bits of randomness guards
    against same-second concurrent invocations (Race 2 in the plan).
    """
    return f"{_SESSION_ID_PREFIX}-{int(time.time())}-{os.getpid()}-{secrets.token_hex(4)}"


def cmd_send(args: argparse.Namespace) -> int:
    """Enqueue an outbound email payload to the Redis relay.

    Builds the unified outbox payload shape (see plan ``Technical Approach``
    §Unified outbox payload contract) and pushes it to
    ``email:outbox:{session_id}`` with a 1-hour TTL. The relay picks it up
    within 100 ms when the bridge is running.

    Exit codes:
        0 on success (queued).
        1 on validation failure, missing file, or Redis push error.
    """
    body = args.message or ""
    file_path = args.file

    if not body and not file_path:
        print("Error: Must provide a message or --file", file=sys.stderr)
        return 1

    if file_path:
        p = Path(file_path)
        if not p.is_file():
            print(f"Error: File not found: {file_path}", file=sys.stderr)
            return 1
        try:
            # Readability check — fail fast before enqueue
            with p.open("rb"):
                pass
        except OSError as e:
            print(f"Error: Cannot read file {file_path}: {e}", file=sys.stderr)
            return 1

    attachments = [str(Path(file_path).resolve())] if file_path else []

    in_reply_to = None
    references = None
    if args.reply_to:
        in_reply_to = args.reply_to
        references = args.reply_to

    session_id = _build_session_id()
    smtp_user = os.environ.get("SMTP_USER", "")
    # args.to is a list (action="append") — flatten comma-separated entries too
    to_addrs = []
    for entry in args.to:
        to_addrs.extend(a.strip() for a in entry.split(",") if a.strip())

    # Promise gate — see docs/features/promise-gate.md
    # Synthetic cli-{epoch}-... session_id; gate routes to audit JSONL only.
    from bridge.promise_gate import cli_check_or_exit

    cli_check_or_exit(body, transport="email", session_id=session_id)

    payload: dict = {
        "session_id": session_id,
        "to": to_addrs,
        "subject": args.subject or "(no subject)",
        "body": body,
        "attachments": attachments,
        "in_reply_to": in_reply_to,
        "references": references,
        "from_addr": smtp_user,
        "timestamp": time.time(),
    }

    queue_key = f"email:outbox:{session_id}"
    try:
        r = _get_redis_connection()
        r.rpush(queue_key, json.dumps(payload))
        r.expire(queue_key, 3600)
    except Exception as e:
        print(f"Error: Redis write failed: {e}", file=sys.stderr)
        print(
            "Ensure Redis is running and REDIS_URL is configured (default: redis://localhost:6379/0).",
            file=sys.stderr,
        )
        return 1

    if args.json:
        print(
            json.dumps(
                {
                    "queued": True,
                    "session_id": session_id,
                    "to": to_addrs,
                    "subject": payload["subject"],
                    "attachments": attachments,
                }
            )
        )
    else:
        parts = [f"{len(body)} chars"]
        if attachments:
            parts.append(f"file: {Path(file_path).name}")
        print(f"Queued ({', '.join(parts)}).")
        print("Check delivery via ./scripts/valor-service.sh email-status (relay heartbeat + DLQ).")
    return 0


# =============================================================================
# threads
# =============================================================================


def cmd_threads(args: argparse.Namespace) -> int:
    """List known email threads from the Redis cache."""
    from tools.email_history import list_threads

    result = list_threads()
    if "error" in result:
        print(f"Error: {result['error']}", file=sys.stderr)
        return 1

    threads = result.get("threads", [])

    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 0

    if not threads:
        print("No threads found in history cache.")
        return 0

    print(f"Threads ({len(threads)}):")
    print()
    print(f"{'Subject':<40} {'Msgs':>5} {'Last Activity':<20}")
    print("-" * 72)
    for t in threads:
        subj = t.get("subject") or "(no subject)"
        if len(subj) > 38:
            subj = subj[:35] + "..."
        count = t.get("message_count", 0)
        last = format_timestamp(t.get("last_ts"))
        print(f"{subj:<40} {count:>5} {last:<20}")

    return 0


# =============================================================================
# main
# =============================================================================


def main() -> int:
    """CLI entry point."""
    from dotenv import load_dotenv

    load_dotenv()
    load_dotenv(Path.home() / "Desktop" / "Valor" / ".env")  # symlink target — no-op

    parser = argparse.ArgumentParser(
        prog="valor-email",
        description="Read, send, and browse email threads via the valor email bridge.",
        epilog=(
            "Delivery: sends are queued to Redis (email:outbox:*) and drained by "
            "the email relay. If a send fails after 3 retries the message moves to "
            "the dead-letter queue — inspect via "
            "'./scripts/valor-service.sh email-dead-letter list'."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", help="Subcommand")

    # read
    read_parser = subparsers.add_parser("read", help="Read recent emails")
    read_parser.add_argument(
        "--mailbox",
        default="INBOX",
        help="IMAP mailbox (only INBOX is supported in v1)",
    )
    read_parser.add_argument(
        "--limit", "-n", type=int, default=10, help="Max messages (default: 10)"
    )
    read_parser.add_argument("--search", "-s", help="Substring filter (subject + body)")
    read_parser.add_argument("--since", help="Age filter, e.g. '1 hour ago', '2 days ago'")
    read_parser.add_argument("--json", action="store_true", help="JSON output")

    # send
    send_parser = subparsers.add_parser("send", help="Send an email via the relay")
    send_parser.add_argument(
        "--to",
        required=True,
        action="append",
        dest="to",
        metavar="ADDRESS",
        help="Recipient email address (repeat for multiple)",
    )
    send_parser.add_argument(
        "--subject",
        default=None,
        help="Subject (default: '(no subject)')",
    )
    send_parser.add_argument("message", nargs="?", default="", help="Body text")
    send_parser.add_argument("--file", "-f", help="File to attach (absolute or relative path)")
    send_parser.add_argument(
        "--reply-to",
        type=_normalize_msgid,
        default=None,
        help=(
            "RFC-2822 Message-ID of the message being replied to "
            "(e.g. '<abc@host>'; angle brackets optional). "
            "Copy it from 'valor-email read --json'."
        ),
    )
    send_parser.add_argument("--json", action="store_true", help="JSON output")

    # threads
    threads_parser = subparsers.add_parser("threads", help="List known email threads")
    threads_parser.add_argument("--json", action="store_true", help="JSON output")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    handlers = {
        "read": cmd_read,
        "send": cmd_send,
        "threads": cmd_threads,
    }
    handler = handlers.get(args.command)
    if not handler:
        parser.print_help()
        return 1

    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
