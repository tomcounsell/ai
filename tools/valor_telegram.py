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


def resolve_chat(name: str, *, strict: bool = False) -> str | None:
    """Resolve a chat name to a chat_id.

    Tries the history database first (groups), then the DM whitelist (users).

    Args:
        name: The chat name to resolve.
        strict: Passed through to `resolve_chat_id`. When True, >1 ambiguous
            candidates raises `AmbiguousChatError`. When False (default),
            the resolver picks the most-recent candidate and emits a
            `logger.warning` — callers never see the exception on the
            ambiguity path.

    Raises:
        AmbiguousChatError: only when `strict=True` and the history resolver
            finds >1 candidate, OR when the resolver's defensive invariant
            fails (regardless of `strict`). Callers using `strict=True`
            (e.g., `cmd_read` / `cmd_send` under `--strict`) MUST catch
            this and render a disambiguation message.
    """
    from tools.telegram_history import AmbiguousChatError  # noqa: F401 — re-export

    try:
        from tools.telegram_history import resolve_chat_id

        chat_id = resolve_chat_id(name, strict=strict)
        if chat_id:
            return chat_id
    except AmbiguousChatError:
        # Intentionally propagate — strict-mode callers must disambiguate.
        raise
    except Exception:
        # Other failures (Redis down, etc.) fall through to the DM path.
        pass

    try:
        from tools.telegram_users import resolve_username

        user_id = resolve_username(name)
        if user_id:
            return str(user_id)
    except Exception:
        pass

    return None


def _format_relative_age(ts: float | None) -> str:
    """Format a unix timestamp as a human-readable "X ago" string.

    Returns "never" for None; "<1m ago" for very fresh activity.
    """
    if ts is None:
        return "never"
    try:
        delta_s = max(0.0, time.time() - float(ts))
    except (TypeError, ValueError):
        return "never"
    if delta_s < 60:
        return "<1m ago"
    if delta_s < 3600:
        return f"{int(delta_s // 60)}m ago"
    if delta_s < 86400:
        return f"{int(delta_s // 3600)}h ago"
    days = int(delta_s // 86400)
    return f"{days}d ago"


def _format_ambiguity_error(candidates) -> str:
    """Render an `AmbiguousChatError` payload as a user-facing message."""
    lines = [f"Ambiguous chat name. {len(candidates)} candidates (most recent first):"]
    # Column width sized to the longest chat_id / chat_name for readable alignment.
    max_id = max((len(str(c.chat_id)) for c in candidates), default=8)
    max_name = max((len(c.chat_name) for c in candidates), default=16)
    for c in candidates:
        age = _format_relative_age(c.last_activity_ts)
        lines.append(f"  {str(c.chat_id):<{max_id}}  {c.chat_name:<{max_name}}  last: {age}")
    lines.append("Re-run with --chat-id <id> or a more specific --chat string.")
    return "\n".join(lines)


def _did_you_mean_candidates(query: str, limit: int = 3) -> list:
    """Return top-N chats (by last activity) whose normalized name contains `query`.

    Used to render a "did you mean" list on zero-match. Uses a lower bar
    than the resolver: any normalized substring match, sorted by updated_at
    desc. Returns a list of ChatCandidate-like (chat_id, chat_name,
    last_activity_ts) dicts. Empty list if lookup fails.
    """
    try:
        # If resolve_chat_candidates found nothing, the user query didn't hit
        # any of the three stages. We run a broader scan across all chats.
        from models.chat import Chat
        from tools.telegram_history import _normalize_chat_name

        normalized = _normalize_chat_name(query)
        if not normalized:
            return []
        all_chats = list(Chat.query.all())
        matches = []
        for chat in all_chats:
            if not chat.chat_name:
                continue
            if normalized in _normalize_chat_name(chat.chat_name):
                matches.append(
                    {
                        "chat_id": str(chat.chat_id),
                        "chat_name": chat.chat_name,
                        "last_activity_ts": (float(chat.updated_at) if chat.updated_at else None),
                    }
                )

        # Sort by last_activity_ts desc, None-last.
        def _sort_key(c):
            has_ts = 0 if c["last_activity_ts"] is not None else 1
            neg_ts = -(c["last_activity_ts"] or 0.0)
            return (has_ts, neg_ts, c["chat_name"])

        matches.sort(key=_sort_key)
        return matches[:limit]
    except Exception:
        return []


def _lookup_chat_metadata(chat_id: str) -> dict | None:
    """Look up chat_name and last_activity_ts for a given chat_id.

    Used to render the freshness header on successful reads. Returns None if
    the chat is unknown (e.g., a raw `--chat-id` with no stored metadata).
    """
    try:
        from models.chat import Chat

        hits = list(Chat.query.filter(chat_id=str(chat_id)))
        if not hits:
            return None
        chat = hits[0]
        return {
            "chat_name": chat.chat_name or "",
            "last_activity_ts": float(chat.updated_at) if chat.updated_at else None,
        }
    except Exception:
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
    load_dotenv(Path.home() / "Desktop" / "Valor" / ".env")  # symlink target — no-op

    try:
        api_id = int(os.environ.get("TELEGRAM_API_ID", "0"))
    except ValueError:
        api_id = 0
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


_PROJECT_PER_LINE_NAME_MAX = 25
_PROJECT_HEADER_NAME_CAP = 5


def _truncate_chat_name(name: str, limit: int = _PROJECT_PER_LINE_NAME_MAX) -> str:
    """Truncate a chat_name for the per-line `[chat_name]` tag.

    Names longer than `limit` are cut to (limit - 3) chars + "..." so the
    final visible width is exactly `limit` characters. The full name is
    always available in the project header and JSON output.
    """
    if len(name) <= limit:
        return name
    return name[: max(0, limit - 3)] + "..."


def _cmd_read_project(
    args: argparse.Namespace,
    resolve_chats_by_project,
    get_recent_messages,
) -> int:
    """Cross-chat project-level read path (issue #1169).

    Resolves chats by `project_key`, fetches up to `args.limit` recent
    messages per chat, merges by `timestamp` desc, trims to `args.limit`
    total, and renders with a project freshness header plus per-line
    `[chat_name]` tag. JSON mode enriches each message dict with `chat_id`
    and `chat_name` fields.
    """
    project_key = args.project.strip()
    candidates = resolve_chats_by_project(project_key)

    if not candidates:
        print(
            f"No chats found for project {project_key!r}. "
            f"Run `valor-telegram chats --project {project_key}` to verify.",
            file=sys.stderr,
        )
        return 1

    # Per-chat fetch budget = args.limit so the merge candidate pool is at
    # least N (avoids missing recent messages from one chat behind a flood
    # from another). Worst case K chats × N each before merge-and-trim.
    merged: list[dict] = []
    for c in candidates:
        result = get_recent_messages(chat_id=c.chat_id, limit=args.limit)
        if "error" in result:
            # Surface per-chat errors but keep going — partial results are
            # still useful for cross-chat situational awareness.
            print(
                f"Warning: failed to fetch chat {c.chat_id}: {result['error']}",
                file=sys.stderr,
            )
            continue
        for msg in result.get("messages", []):
            enriched = dict(msg)
            enriched["chat_id"] = c.chat_id
            enriched["chat_name"] = c.chat_name
            merged.append(enriched)

    # Sort by timestamp desc with chat_id then message_id tiebreakers for
    # deterministic ordering. ISO-8601 strings sort lexicographically the
    # same as chronologically, so string compare is correct.
    merged.sort(
        key=lambda m: (
            m.get("timestamp") or "",
            str(m.get("chat_id") or ""),
            m.get("message_id") or 0,
        ),
        reverse=True,
    )
    merged = merged[: max(0, args.limit)]

    # Display oldest-first to match the single-chat path's chronological order.
    display_msgs = list(reversed(merged))

    if args.json:
        print(json.dumps(display_msgs, indent=2, default=str))
        return 0

    # --- Project freshness header --------------------------------------------
    chat_names = [c.chat_name or "(unnamed)" for c in candidates]
    if len(chat_names) > _PROJECT_HEADER_NAME_CAP:
        visible = chat_names[:_PROJECT_HEADER_NAME_CAP]
        more = len(chat_names) - _PROJECT_HEADER_NAME_CAP
        names_str = ", ".join(visible) + f", ... +{more} more"
    else:
        names_str = ", ".join(chat_names)
    last_ts = max(
        (c.last_activity_ts for c in candidates if c.last_activity_ts is not None),
        default=None,
    )
    age = _format_relative_age(last_ts)
    print(f"[project={project_key} · {len(candidates)} chats: {names_str} · last activity: {age}]")

    if not display_msgs:
        print(f"No messages found for project {project_key!r}.")
        return 0

    for msg in display_msgs:
        ts = format_timestamp(msg.get("timestamp"))
        sender = msg.get("sender", "unknown")
        content = msg.get("content", "") or ""
        if len(content) > 500:
            content = content[:497] + "..."
        chat_tag = _truncate_chat_name(msg.get("chat_name", "") or "")
        print(f"[{ts}] [{chat_tag}] {sender}: {content}")

    return 0


def resolve_chats_by_project(project_key: str):
    """Module-level wrapper around `tools.telegram_history.resolve_chats_by_project`.

    Exposed at module level so tests can patch
    `tools.valor_telegram.resolve_chats_by_project` directly (mirrors the
    existing `resolve_chat` wrapper pattern).
    """
    from tools.telegram_history import resolve_chats_by_project as _impl

    return _impl(project_key)


def cmd_read(args: argparse.Namespace) -> int:
    """Read messages from a chat."""
    from tools.telegram_history import (
        AmbiguousChatError,
        get_recent_messages,
        search_all_chats,
        search_history,
    )

    # --- Flag mutex validation (argparse mutex group already enforces, but
    # defend here for direct cmd_read() calls from tests). ---
    flag_count = sum(
        1
        for v in (
            getattr(args, "chat", None),
            getattr(args, "chat_id", None),
            getattr(args, "user", None),
            getattr(args, "project", None),
        )
        if v
    )
    if flag_count > 1:
        print(
            "Error: --chat, --chat-id, --user, and --project are mutually exclusive.",
            file=sys.stderr,
        )
        return 1

    # --strict only makes sense for name-resolution paths (--chat). It is a
    # footgun under --project (which never resolves a name) — reject explicitly.
    if getattr(args, "project", None) and getattr(args, "strict", False):
        print(
            "Error: --strict has no effect with --project; remove one of them.",
            file=sys.stderr,
        )
        return 1

    # C3 concern from critique: empty / whitespace-only --chat must be rejected
    # BEFORE reaching the resolver, so it never hits the normalizer's
    # "returns []" path (which would be reported as a plain zero-match) or
    # sneaks into substring match-all logic elsewhere. We also reject empty
    # --user / --project for the same reason.
    raw_chat = getattr(args, "chat", None)
    if raw_chat is not None and not raw_chat.strip():
        print(
            "Error: --chat cannot be empty or whitespace-only.",
            file=sys.stderr,
        )
        return 1
    raw_user = getattr(args, "user", None)
    if raw_user is not None and not raw_user.strip():
        print(
            "Error: --user cannot be empty or whitespace-only.",
            file=sys.stderr,
        )
        return 1
    raw_project = getattr(args, "project", None)
    if raw_project is not None and not raw_project.strip():
        print(
            "Error: --project cannot be empty or whitespace-only.",
            file=sys.stderr,
        )
        return 1

    # --- Cross-chat project-level read (issue #1169). Branches off before the
    # single-chat path so it does not interact with name resolution / fallback. ---
    if raw_project:
        return _cmd_read_project(args, resolve_chats_by_project, get_recent_messages)

    chat_id = None
    strict_mode = bool(getattr(args, "strict", False))

    # Explicit numeric --chat-id bypasses the resolver.
    if getattr(args, "chat_id", None):
        chat_id = str(args.chat_id)

    # Explicit --user routes through the DM whitelist.
    elif getattr(args, "user", None):
        try:
            from tools.telegram_users import resolve_username

            user_id = resolve_username(args.user)
            if not user_id:
                print(f"Error: Unknown username '{args.user}'", file=sys.stderr)
                print(
                    "Use 'valor-telegram chats' to list chats, or check the DM whitelist.",
                    file=sys.stderr,
                )
                return 1
            chat_id = str(user_id)
        except Exception as e:
            print(f"Error resolving --user: {e}", file=sys.stderr)
            return 1

    # Default path — resolve by name.
    elif args.chat:
        try:
            # strict=False (default): ambiguity yields a logger.warning + picks
            # the most-recent candidate. The CLI prints no error and exits 0.
            # strict=True: ambiguity raises AmbiguousChatError (caught below).
            chat_id = resolve_chat(args.chat, strict=strict_mode)
        except AmbiguousChatError as e:
            # Only reachable under --strict (or the defensive invariant guard
            # inside resolve_chat_id). Print candidates to stdout so scripted
            # callers can parse without needing stderr capture; exit 1.
            print(_format_ambiguity_error(e.candidates))
            return 1
        if not chat_id:
            # Try raw value (might be a numeric chat ID typed via --chat).
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
        if not chat_id and not args.chat and not getattr(args, "user", None):
            print(
                "Error: --chat, --chat-id, or --user is required when not using --search",
                file=sys.stderr,
            )
            return 1

        # Zero-match on --chat with no numeric fallback — render "did you mean".
        if not chat_id and args.chat:
            suggestions = _did_you_mean_candidates(args.chat, limit=3)
            if suggestions:
                print(
                    f"No chat matched {args.chat!r}. Did you mean:",
                    file=sys.stderr,
                )
                for s in suggestions:
                    age = _format_relative_age(s["last_activity_ts"])
                    print(
                        f"  {s['chat_id']}  {s['chat_name']}  last: {age}",
                        file=sys.stderr,
                    )
            else:
                print(f"No chat matched {args.chat!r}.", file=sys.stderr)
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

    # Freshness header — single line with chat name, chat_id, and last-activity
    # age. Written BEFORE any "No messages found" so the reader always knows
    # which chat they queried and whether it's active. Skipped in --json mode.
    if chat_id:
        meta = _lookup_chat_metadata(chat_id)
        if meta is not None:
            age = _format_relative_age(meta["last_activity_ts"])
            name = meta["chat_name"] or "(unnamed)"
            print(f"[{name} · chat_id={chat_id} · last activity: {age}]")
        else:
            # --chat-id with no stored metadata (e.g., never seen by bridge).
            print(f"[chat_id={chat_id} · last activity: never]")

    if not messages:
        if chat_id:
            print(f"No messages found for chat {chat_id}.")
        else:
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
        from bridge.message_drafter import linkify_references

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
    # C3 concern: empty --chat is rejected before resolution on both
    # read and send paths. Send has no --strict flag (Q2 resolved: pick-
    # most-recent-with-warning is the default for both read and send);
    # a scripted sender that needs hard-error semantics should pass the
    # numeric chat_id directly.
    if args.chat is not None and not args.chat.strip():
        print(
            "Error: --chat cannot be empty or whitespace-only.",
            file=sys.stderr,
        )
        return 1

    # Resolve chat name to numeric ID
    from tools.telegram_history import AmbiguousChatError

    try:
        # strict=False: send matches read's default — pick most-recent +
        # logger.warning on ambiguity. The AmbiguousChatError branch below
        # is still reachable via the defensive invariant guard inside
        # resolve_chat_id, which raises unconditionally on a broken sort.
        chat_id = resolve_chat(args.chat, strict=False)
    except AmbiguousChatError as e:
        print(_format_ambiguity_error(e.candidates), file=sys.stderr)
        return 1
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
        resolved = str(Path(file_path).resolve())
        payload["file_paths"] = [resolved]

        # Voice-note delivery: tell the relay to send via Telethon's
        # voice_note=True path so the file arrives as a Telegram voice
        # bubble (waveform UI), not a generic audio document.
        if getattr(args, "voice_note", False):
            payload["voice_note"] = True
            # Compute duration via tools.tts._compute_duration_opus so the
            # relay can populate DocumentAttributeAudio(duration=...).
            try:
                from tools.tts import _compute_duration_opus

                payload["duration"] = _compute_duration_opus(resolved)
            except Exception:
                payload["duration"] = 0.0

        # Hand temp-file cleanup to the relay so it survives async retries.
        if getattr(args, "cleanup_after_send", False):
            payload["cleanup_file"] = True

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
    from tools.telegram_history import _normalize_chat_name, list_chats

    # C3 concern: empty --search is rejected, same as empty --chat on read
    # and send. An empty-string substring matches every chat, which is
    # indistinguishable from "no filter" and silently produces the full
    # list — confusing. Require the caller to drop the flag entirely for
    # unfiltered listing.
    raw_search = getattr(args, "search", None)
    if raw_search is not None and not raw_search.strip():
        print(
            "Error: --search cannot be empty or whitespace-only.",
            file=sys.stderr,
        )
        return 1

    raw_project = getattr(args, "project", None)
    if raw_project is not None and not raw_project.strip():
        print(
            "Error: --project cannot be empty or whitespace-only.",
            file=sys.stderr,
        )
        return 1

    result = list_chats()

    if "error" in result:
        print(f"Error: {result['error']}", file=sys.stderr)
        return 1

    chats = result.get("chats", [])

    # Optional --project filter: exact-match on Chat.project_key. Combinable
    # with --search; both filters apply when both are set.
    project_filter = raw_project
    if project_filter:
        chats = [c for c in chats if c.get("project_key") == project_filter]

    # Optional --search filter: normalized substring match, keeps existing sort.
    search_pattern = getattr(args, "search", None)
    if search_pattern:
        normalized_pattern = _normalize_chat_name(search_pattern)
        if not normalized_pattern:
            chats = []
        else:
            chats = [
                c
                for c in chats
                if c.get("chat_name") and normalized_pattern in _normalize_chat_name(c["chat_name"])
            ]

    # Preserve `count` invariant by rebuilding the returned dict whenever a
    # filter ran.
    if project_filter or search_pattern:
        result = {"chats": chats, "count": len(chats)}
        if search_pattern:
            result["search"] = search_pattern
        if project_filter:
            result["project"] = project_filter

    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 0

    if not chats:
        if search_pattern and project_filter:
            print(f"No chats matched project {project_filter!r} and search {search_pattern!r}.")
        elif project_filter:
            print(f"No chats matched project {project_filter!r}.")
        elif search_pattern:
            print(f"No chats matched {search_pattern!r}.")
        else:
            print("No chats found in history database.")
            print("Chats are registered as messages are received by the bridge.")
        return 0

    if project_filter and search_pattern:
        header = (
            f"Known chats matching project {project_filter!r} "
            f"and search {search_pattern!r} ({len(chats)}):"
        )
    elif project_filter:
        header = f"Known chats matching project {project_filter!r} ({len(chats)}):"
    elif search_pattern:
        header = f"Known chats matching {search_pattern!r} ({len(chats)}):"
    else:
        header = f"Known chats ({len(chats)}):"
    print(header)
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
    # --chat / --chat-id / --user / --project are mutually exclusive — the user
    # picks ONE. We still allow not specifying any when --search is set
    # (cross-chat search).
    read_target_group = read_parser.add_mutually_exclusive_group()
    read_target_group.add_argument("--chat", "-c", help="Chat name (resolved against history)")
    read_target_group.add_argument(
        "--chat-id",
        help="Explicit numeric chat ID — bypasses the name matcher",
    )
    read_target_group.add_argument(
        "--user",
        help="Username from the DM whitelist — forces the DM path",
    )
    read_target_group.add_argument(
        "--project",
        help=(
            "Project key — unions messages across all chats with this project_key, "
            "interleaved chronologically. --limit applies to the merged total, NOT "
            "per-chat. Mutually exclusive with --chat/--chat-id/--user/--strict."
        ),
    )
    read_parser.add_argument(
        "--limit", "-n", type=int, default=10, help="Max messages (default: 10)"
    )
    read_parser.add_argument("--search", "-s", help="Search keyword")
    read_parser.add_argument("--since", help="Time filter, e.g. '1 hour ago', '2 days ago'")
    read_parser.add_argument("--json", action="store_true", help="Output as JSON")
    read_parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Opt into hard error on ambiguous --chat. Default behavior picks "
            "the most-recently-active candidate and logs a warning. Use "
            "--strict for scripted callers that need a non-zero exit when "
            "the name matches >1 chat; the candidate list is printed on stdout."
        ),
    )

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
    send_parser.add_argument(
        "--voice-note",
        action="store_true",
        help=(
            "Send the attached file as a native Telegram voice message "
            "(waveform bubble). Requires an OGG/Opus file via --audio."
        ),
    )
    send_parser.add_argument(
        "--cleanup-after-send",
        action="store_true",
        help=(
            "Ask the relay to delete the attached file after a successful "
            "send (or after dead-letter placement on retry exhaustion). "
            "Use this for ephemeral temp files; the relay owns lifecycle."
        ),
    )

    # chats subcommand
    chats_parser = subparsers.add_parser("chats", help="List known chats")
    chats_parser.add_argument(
        "--search",
        "-s",
        help="Filter by substring of chat name (normalized)",
    )
    chats_parser.add_argument(
        "--project",
        help="Filter by project_key (combinable with --search)",
    )
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
