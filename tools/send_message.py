#!/usr/bin/env python3
"""Polymorphic send_message CLI — medium-agnostic message delivery.

Routes the agent's tool-call deliveries through the SAME canonical handler
(``agent.output_handler.TelegramRelayOutputHandler.send``) that the silent
worker path uses. The handler hoists the drafter to a single call site above
its transport branch, so both telegram and email writes inherit the full
``drafter → redundancy filter → read-the-room → narration fallback → outbox``
pipeline. The tool is a thin wrapper: it does the linkify pass, runs the
promise gate, looks up the AgentSession, and delegates everything else.

This closes the long-standing gap where ``tools/send_message.py`` wrote raw
payloads straight to the Redis outbox and bypassed every safety net the
worker path enjoys (see ``docs/features/agent-message-delivery.md`` and
issue #1369).

Usage:
    python tools/send_message.py "your draft text"
    python tools/send_message.py "caption" --file path/to/attachment.png
    python tools/send_message.py --stdin    # read text from stdin

Environment variables (injected by sdk_client.py):
    VALOR_SESSION_ID                Required; session ID for routing
    TELEGRAM_CHAT_ID                Set for Telegram-triggered sessions
    TELEGRAM_REPLY_TO               Set for Telegram replies
    EMAIL_REPLY_TO                  Set for email-triggered sessions (sender address)
    VALOR_TRANSPORT                 Explicit transport override (``telegram`` or
                                    ``email``, case-insensitive). Otherwise inferred
                                    from chat_id/email vars.
    ALLOW_LEGACY_RPUSH_FALLBACK     Diagnostic only — when set to ``1``, a missing
                                    AgentSession row falls back to the legacy raw
                                    rpush path. Default unset → fail closed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

# Script-mode bootstrap: `python tools/send_message.py` puts tools/ at
# sys.path[0], where tools/analytics.py shadows the repo's analytics package
# and breaks the agent/bridge import chain (ModuleNotFoundError:
# analytics.collector). Replace it with the repo root so imports resolve the
# same as `python -m tools.send_message`.
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if sys.path and Path(sys.path[0]).resolve() == Path(__file__).resolve().parent:
    sys.path[0] = _REPO_ROOT
elif _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

TELEGRAM_MAX_LENGTH = 4096
TELEGRAM_MAX_ALBUM_SIZE = 10

logger = logging.getLogger(__name__)


def _resolve_transport() -> str:
    """Pick the transport based on env vars.

    Priority:
    1. VALOR_TRANSPORT explicit override (``telegram`` or ``email``,
       case-insensitive)
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


def _lookup_session(session_id: str):
    """Reconstitute the AgentSession from Popoto.

    Returns the session row, or ``None`` when the row is missing (caller
    decides between fail-closed exit and the env-gated legacy rpush path).
    Re-raises non-row-not-found Popoto/Redis errors so the harness sees them.
    """
    from models.agent_session import AgentSession

    return AgentSession.query.filter(session_id=session_id).first()


def _legacy_telegram_rpush(
    text: str,
    file_paths: list[str] | None,
    *,
    chat_id: str,
    reply_to: str | None,
    session_id: str,
) -> None:
    """Last-resort raw rpush to ``telegram:outbox:{session_id}``.

    Only reachable when ``ALLOW_LEGACY_RPUSH_FALLBACK=1`` is set AND the
    AgentSession lookup returned ``None``. Logs a warning so the bypass is
    not silent.
    """
    logger.warning(
        "ALLOW_LEGACY_RPUSH_FALLBACK active; writing raw payload to "
        "telegram:outbox:%s without drafter/RTR/redundancy filtering",
        session_id,
    )
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
    _files_suffix = f", {len(file_paths)} files" if file_paths else ""
    print(f"Queued ({len(text)} chars{_files_suffix}) [legacy]")


def _legacy_email_rpush(text: str, *, recipient: str, session_id: str) -> None:
    """Last-resort raw rpush to ``email:outbox:{session_id}``.

    Counterpart to ``_legacy_telegram_rpush`` for the email transport. Same
    semantics: only reachable under ``ALLOW_LEGACY_RPUSH_FALLBACK=1`` with a
    missing AgentSession row.
    """
    logger.warning(
        "ALLOW_LEGACY_RPUSH_FALLBACK active; writing raw email payload to "
        "email:outbox:%s without drafter filtering",
        session_id,
    )
    in_reply_to = os.environ.get("EMAIL_IN_REPLY_TO") or None
    subject = os.environ.get("EMAIL_SUBJECT") or "(no subject)"
    payload = {
        "session_id": session_id,
        "to": recipient,
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
    print(f"Queued email ({len(text)} chars) [legacy]")


def _send_via_telegram(text: str, file_paths: list[str] | None) -> None:
    """Route through ``TelegramRelayOutputHandler.send`` for Telegram delivery.

    The handler runs the canonical pipeline (drafter → redundancy filter →
    RTR → narration fallback → outbox rpush). This tool retains responsibility
    for the upstream concerns the handler does not own: env var validation,
    file existence checks, linkify, and the promise gate.
    """
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
        except Exception:  # noqa: S110 -- best-effort linkification
            pass

    # Promise gate — see docs/features/promise-gate.md (polymorphic transport).
    # Runs BEFORE the handler so we short-circuit before the drafter Haiku call
    # and before the Popoto session lookup when the agent owes a promise.
    from bridge.promise_gate import cli_check_or_exit

    cli_check_or_exit(text, transport="polymorphic", session_id=session_id)

    # Reconstitute the AgentSession so the handler can read transport,
    # extra_context, and recent_sent_drafts. Fail-closed default; the legacy
    # raw-rpush path is opt-in via env flag for short-lived diagnostic use.
    try:
        session = _lookup_session(session_id)
    except Exception as e:
        print(
            f"Error: AgentSession lookup failed (Popoto/Redis error): {e}",
            file=sys.stderr,
        )
        sys.exit(1)

    if session is None:
        if os.environ.get("ALLOW_LEGACY_RPUSH_FALLBACK") == "1":
            _legacy_telegram_rpush(
                text,
                file_paths,
                chat_id=chat_id,
                reply_to=reply_to,
                session_id=session_id,
            )
            return
        print(
            f"Error: AgentSession {session_id!r} not found; refusing to bypass "
            "the canonical handler. Set ALLOW_LEGACY_RPUSH_FALLBACK=1 for "
            "diagnostic raw rpush.",
            file=sys.stderr,
        )
        sys.exit(1)

    from agent.output_handler import DeliveryOutcome, TelegramRelayOutputHandler

    handler = TelegramRelayOutputHandler()
    reply_to_int = int(reply_to) if reply_to else 0
    try:
        outcome = asyncio.run(
            handler.send(
                chat_id,
                text,
                reply_to_int,
                session=session,
                file_paths=file_paths,
            )
        )
    except Exception as e:
        print(f"Error: handler.send failed: {e}", file=sys.stderr)
        sys.exit(1)

    # Surface the handler's pipeline verdict instead of an unconditional
    # "Queued". Suppression/defer verdicts are NOT errors — the delivery review
    # gate deliberately withheld the send — so they print the outcome name and
    # exit 0, telling the agent exactly what happened (e.g. suppressed_redundant,
    # deferred_self_draft) so it can rephrase and resend if it chooses.
    _label = outcome.value if outcome is not None else DeliveryOutcome.sent.value
    _files_suffix = f", {len(file_paths)} files" if file_paths else ""
    print(f"{_label} ({len(text)} chars{_files_suffix})")


def _send_via_email(text: str, file_paths: list[str] | None = None) -> None:
    """Route through the SAME ``TelegramRelayOutputHandler.send`` for email.

    Despite the type name, this method is the single canonical queue-side
    entrypoint for both transports. The handler's internal email branch
    builds the email-shaped outbox payload (reply-all ``to`` list, subject,
    threading headers). The CLI deliberately does NOT import the synchronous
    SMTP handler — that is the wrong layer for a queue-only writer.
    """
    session_id = os.environ.get("VALOR_SESSION_ID")
    reply_to_addr = os.environ.get("EMAIL_REPLY_TO")
    if not session_id:
        print("Error: VALOR_SESSION_ID not set.", file=sys.stderr)
        sys.exit(1)
    if not reply_to_addr:
        print("Error: EMAIL_REPLY_TO not set.", file=sys.stderr)
        sys.exit(1)

    # Promise gate runs first — short-circuits before any handler/Popoto cost.
    from bridge.promise_gate import cli_check_or_exit

    cli_check_or_exit(text, transport="email", session_id=session_id)

    try:
        session = _lookup_session(session_id)
    except Exception as e:
        print(
            f"Error: AgentSession lookup failed (Popoto/Redis error): {e}",
            file=sys.stderr,
        )
        sys.exit(1)

    if session is None:
        if os.environ.get("ALLOW_LEGACY_RPUSH_FALLBACK") == "1":
            _legacy_email_rpush(text, recipient=reply_to_addr, session_id=session_id)
            return
        print(
            f"Error: AgentSession {session_id!r} not found; refusing to bypass "
            "the canonical handler. Set ALLOW_LEGACY_RPUSH_FALLBACK=1 for "
            "diagnostic raw rpush.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Defensive: if the bridge spawned this session before EMAIL_SUBJECT /
    # EMAIL_IN_REPLY_TO were canonicalised onto extra_context, stamp them now
    # so the handler's email branch can read them uniformly.
    try:
        extra = getattr(session, "extra_context", None)
        if isinstance(extra, dict):
            dirty = False
            if "email_subject" not in extra and os.environ.get("EMAIL_SUBJECT"):
                extra["email_subject"] = os.environ["EMAIL_SUBJECT"]
                dirty = True
            if "email_message_id" not in extra and os.environ.get("EMAIL_IN_REPLY_TO"):
                extra["email_message_id"] = os.environ["EMAIL_IN_REPLY_TO"]
                dirty = True
            if dirty:
                session.extra_context = extra
                try:
                    session.save(update_fields=["extra_context"])
                except Exception:  # noqa: S110 -- defensive backfill (documented)
                    # Non-fatal: stamping is a defensive backfill, not a
                    # prerequisite for delivery.
                    pass
    except Exception:  # noqa: S110 -- defensive backfill; delivery proceeds
        pass

    from agent.output_handler import DeliveryOutcome, TelegramRelayOutputHandler

    handler = TelegramRelayOutputHandler()
    try:
        outcome = asyncio.run(
            handler.send(
                reply_to_addr,
                text,
                0,
                session=session,
                file_paths=file_paths,
            )
        )
    except Exception as e:
        print(f"Error: handler.send failed: {e}", file=sys.stderr)
        sys.exit(1)

    # Surface the pipeline verdict (see the telegram branch for rationale).
    # Suppression/defer verdicts print the outcome name and exit 0.
    _label = outcome.value if outcome is not None else DeliveryOutcome.sent.value
    print(f"{_label} email ({len(text)} chars)")


def send_message(text: str, file_paths: list[str] | None = None) -> None:
    """Entry point. Dispatches by transport."""
    transport = _resolve_transport()
    if transport == "telegram":
        _send_via_telegram(text, file_paths)
    elif transport == "email":
        _send_via_email(text, file_paths=file_paths)
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
