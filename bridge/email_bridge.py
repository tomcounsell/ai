"""
Email bridge: IMAP inbox poller and SMTP output handler.

Implements the inbox/outbox pattern for email, mirroring the Telegram bridge.
Inbound emails arrive via IMAP polling; outbound replies go via SMTP.

Architecture:
  IMAP poll → parse → find_project_for_email() → enqueue_agent_session()
  Worker executes → EmailOutputHandler.send() → SMTP reply

Config (from .env):
  IMAP_HOST        IMAP server hostname (default: imap.gmail.com)
  IMAP_PORT        IMAP port (default: 993, SSL)
  IMAP_USER        IMAP username / email address
  IMAP_PASSWORD    IMAP password or app password
  SMTP_HOST        SMTP server hostname (default: smtp.gmail.com)
  SMTP_PORT        SMTP port (default: 587, STARTTLS)
  SMTP_USER        SMTP username
  SMTP_PASSWORD    SMTP password or app password
  EMAIL_ADDRESS    Sender address for outbound replies (default: SMTP_USER)
  EMAIL_POLL_INTERVAL  Poll interval in seconds (default: 30)

Usage:
  python -m bridge.email_bridge          # Run the inbox poller
  python -m bridge.email_bridge --dry-run  # Connect and print config, then exit
"""

from __future__ import annotations

import asyncio
import email as email_stdlib
import email.header
import email.mime.text
import email.utils
import imaplib
import logging
import os
import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

logger = logging.getLogger(__name__)

# Redis key for health monitoring
LAST_POLL_KEY = "email:last_poll_ts"
MSGID_REVERSE_KEY_PREFIX = "email:msgid:"
MSGID_REVERSE_TTL = 30 * 24 * 3600  # 30 days

# Reconnection backoff constants
_BACKOFF_INITIAL = 5
_BACKOFF_MAX = 300
_BACKOFF_FACTOR = 2


def _get_email_config() -> dict:
    """Load email bridge configuration from environment variables.

    Returns a config dict with all IMAP/SMTP settings.
    Defaults to Gmail standard ports when HOST vars are not set.
    """
    return {
        "imap_host": os.environ.get("IMAP_HOST", "imap.gmail.com"),
        "imap_port": int(os.environ.get("IMAP_PORT", "993")),
        "imap_user": os.environ.get("IMAP_USER", ""),
        "imap_password": os.environ.get("IMAP_PASSWORD", ""),
        "smtp_host": os.environ.get("SMTP_HOST", "smtp.gmail.com"),
        "smtp_port": int(os.environ.get("SMTP_PORT", "587")),
        "smtp_user": os.environ.get("SMTP_USER", ""),
        "smtp_password": os.environ.get("SMTP_PASSWORD", ""),
        "email_address": os.environ.get(
            "EMAIL_ADDRESS",
            os.environ.get("SMTP_USER", ""),
        ),
        "poll_interval": int(os.environ.get("EMAIL_POLL_INTERVAL", "30")),
    }


def is_email_configured() -> bool:
    """Return True if enough email config is present to start the bridge."""
    cfg = _get_email_config()
    return bool(cfg["imap_user"] and cfg["imap_password"] and cfg["smtp_user"])


# ===========================================================================
# SMTP send helper (used by EmailOutputHandler and dead letter replay)
# ===========================================================================


def _smtp_send(
    recipient: str,
    subject: str,
    body: str,
    headers: dict | None = None,
) -> None:
    """Send an email via SMTP (STARTTLS).

    Args:
        recipient: Recipient email address.
        subject: Email subject line.
        body: Plain text email body.
        headers: Optional dict of additional headers (e.g. In-Reply-To, References).

    Raises:
        Exception: On SMTP connection or send failure (caller handles retries).
    """
    cfg = _get_email_config()

    msg = MIMEMultipart("alternative")
    msg["From"] = cfg["email_address"]
    msg["To"] = recipient
    msg["Subject"] = subject
    msg["Date"] = email.utils.formatdate(localtime=True)

    # Add optional headers (thread continuation)
    for header_name, header_value in (headers or {}).items():
        if header_value:
            msg[header_name] = header_value

    msg.attach(MIMEText(body, "plain", "utf-8"))

    with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"]) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        smtp.login(cfg["smtp_user"], cfg["smtp_password"])
        smtp.sendmail(cfg["email_address"], [recipient], msg.as_string())

    logger.info(f"[email] Sent SMTP message to {recipient} subject='{subject[:60]}'")


def _smtp_send_with_retry(
    recipient: str,
    subject: str,
    body: str,
    headers: dict | None = None,
    session_id: str = "",
    max_retries: int = 3,
) -> bool:
    """Send email with retry logic. On exhausted retries, push to dead letter queue.

    Args:
        recipient: Recipient email address.
        subject: Subject line.
        body: Plain text body.
        headers: Optional extra headers.
        session_id: Session identifier (for dead letter key).
        max_retries: Number of send attempts before giving up.

    Returns:
        True if delivered, False if all retries failed (dead-lettered).
    """
    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            _smtp_send(recipient, subject, body, headers)
            return True
        except Exception as exc:
            last_exc = exc
            wait = _BACKOFF_INITIAL * (_BACKOFF_FACTOR ** (attempt - 1))
            logger.warning(
                f"[email] SMTP send attempt {attempt}/{max_retries} failed "
                f"for {recipient}: {exc}. Retrying in {wait}s..."
            )
            if attempt < max_retries:
                time.sleep(wait)

    # All retries exhausted — dead letter
    logger.error(
        f"[email] SMTP send failed after {max_retries} attempts for {recipient}. "
        f"Dead-lettering for session_id={session_id}. Last error: {last_exc}"
    )
    try:
        from bridge.email_dead_letter import push_dead_letter

        push_dead_letter(
            session_id=session_id,
            recipient=recipient,
            subject=subject,
            body=body,
            headers=headers or {},
            retry_count=max_retries,
        )
    except Exception as dl_exc:
        logger.error(f"[email] Failed to write dead letter for {session_id}: {dl_exc}")

    return False


# ===========================================================================
# OutputHandler implementation
# ===========================================================================


class EmailOutputHandler:
    """Route agent session output via SMTP reply.

    Implements the OutputHandler protocol. Used by the worker to deliver
    agent responses to email senders.

    The handler uses the session's extra_context to extract:
    - ``email_recipient``: Where to send the reply
    - ``email_subject``: Subject line (prefixed with "Re: " if not already)
    - ``email_message_id``: The original Message-ID for In-Reply-To threading
    - ``email_references``: Accumulated References header chain

    react() is a no-op — email has no emoji reactions.
    """

    async def send(
        self,
        chat_id: str,
        text: str,
        reply_to_msg_id: int,
        session: Any = None,
    ) -> None:
        """Send agent output as an SMTP reply.

        Args:
            chat_id: Recipient email address (used as chat_id for email sessions).
            text: Message text to send as email body.
            reply_to_msg_id: Original message ID (int). For email sessions this
                is 0 (sentinel) — threading uses In-Reply-To header from
                extra_context["email_message_id"] instead.
            session: AgentSession providing extra_context for thread headers.
        """
        if not text:
            return

        # Resolve recipient: prefer chat_id (set to email address at enqueue time)
        recipient = chat_id
        subject = "Re: (no subject)"
        in_reply_to: str | None = None
        references: str | None = None
        session_id = ""

        if session is not None:
            session_id = getattr(session, "session_id", "") or ""
            ec = getattr(session, "extra_context", None) or {}
            recipient = ec.get("email_recipient") or recipient
            raw_subject = ec.get("email_subject", "")
            if raw_subject:
                subject = raw_subject if raw_subject.startswith("Re:") else f"Re: {raw_subject}"
            in_reply_to = ec.get("email_message_id")
            references = ec.get("email_references")

        headers: dict[str, str] = {}
        if in_reply_to:
            headers["In-Reply-To"] = in_reply_to
            headers["References"] = references or in_reply_to

        ok = _smtp_send_with_retry(
            recipient=recipient,
            subject=subject,
            body=text,
            headers=headers,
            session_id=session_id,
        )

        if ok:
            # Store outbound Message-ID reverse mapping for thread continuation
            # Generate a Message-ID for our outbound message
            try:
                cfg = _get_email_config()
                addr = cfg["email_address"]
                domain = addr.split("@")[-1] if "@" in addr else "localhost"
                outbound_msg_id = f"<valor-{session_id}-{int(time.time())}@{domain}>"
                _store_msgid_reverse(outbound_msg_id, session_id)
            except Exception as e:
                logger.debug(f"[email] Failed to store outbound Message-ID mapping: {e}")

    async def react(
        self,
        chat_id: str,
        msg_id: int,
        emoji: str | None = None,
    ) -> None:
        """No-op — email has no emoji reactions."""
        pass


# ===========================================================================
# Redis helpers for thread continuation
# ===========================================================================


def _get_redis():
    """Return a Redis connection (lazy import)."""
    import redis

    return redis.Redis.from_url(
        os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
        decode_responses=True,
    )


def _store_msgid_reverse(message_id: str, session_id: str) -> None:
    """Store email:msgid:{message_id} -> session_id in Redis."""
    if not message_id or not session_id:
        return
    try:
        r = _get_redis()
        key = f"{MSGID_REVERSE_KEY_PREFIX}{message_id}"
        r.set(key, session_id, ex=MSGID_REVERSE_TTL)
    except Exception as e:
        logger.debug(f"[email] Failed to store Message-ID reverse mapping: {e}")


def _lookup_session_for_msgid(message_id: str) -> str | None:
    """Look up session_id for a given email Message-ID. Returns None if not found."""
    if not message_id:
        return None
    try:
        r = _get_redis()
        key = f"{MSGID_REVERSE_KEY_PREFIX}{message_id}"
        return r.get(key)
    except Exception as e:
        logger.debug(f"[email] Failed to look up Message-ID reverse mapping: {e}")
        return None


def _update_last_poll_ts() -> None:
    """Store current timestamp in Redis for health monitoring."""
    try:
        r = _get_redis()
        r.set(LAST_POLL_KEY, str(time.time()))
    except Exception as e:
        logger.debug(f"[email] Failed to update last_poll_ts: {e}")


def get_last_poll_age() -> float | None:
    """Return seconds since last successful IMAP poll, or None if never polled."""
    try:
        r = _get_redis()
        ts_str = r.get(LAST_POLL_KEY)
        if ts_str is None:
            return None
        return time.time() - float(ts_str)
    except Exception:
        return None


# ===========================================================================
# IMAP parsing helpers
# ===========================================================================


def _decode_header_value(raw: str | bytes | None) -> str:
    """Decode an email header value, handling RFC 2047 encoding."""
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    decoded_parts = email.header.decode_header(raw)
    parts = []
    for part, charset in decoded_parts:
        if isinstance(part, bytes):
            parts.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(part)
    return "".join(parts)


def _extract_email_address(header_value: str) -> str:
    """Extract the raw email address from a From/To header."""
    if not header_value:
        return ""
    realname, addr = email.utils.parseaddr(header_value)
    return addr.lower().strip()


def _extract_plain_body(msg: email_stdlib.message.Message) -> str:
    """Extract plain text body from an email message.

    Prefers text/plain parts. Returns empty string for attachment-only emails.
    """
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            cd = part.get("Content-Disposition", "")
            if ct == "text/plain" and "attachment" not in cd:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace").strip()
        return ""
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace").strip()
        return ""


def _parse_imap_message(raw_bytes: bytes) -> dict | None:
    """Parse a raw IMAP message into a structured dict.

    Returns a dict with keys:
        sender: email address string
        subject: decoded subject
        body: plain text body
        message_id: Message-ID header value
        in_reply_to: In-Reply-To header value (for thread continuation)
        references: References header value

    Returns None if the message is malformed or has no sender/body.
    """
    try:
        msg = email_stdlib.message_from_bytes(raw_bytes)
    except Exception as e:
        logger.warning(f"[email] Failed to parse raw IMAP message: {e}")
        return None

    sender = _extract_email_address(_decode_header_value(msg.get("From", "")))
    if not sender:
        logger.warning("[email] Skipping message with no sender")
        return None

    subject = _decode_header_value(msg.get("Subject", ""))
    body = _extract_plain_body(msg)

    if not body or not body.strip():
        logger.warning(f"[email] Skipping empty-body message from {sender}")
        return None

    return {
        "sender": sender,
        "subject": subject,
        "body": body.strip(),
        "message_id": msg.get("Message-ID", "").strip(),
        "in_reply_to": msg.get("In-Reply-To", "").strip(),
        "references": msg.get("References", "").strip(),
    }


# ===========================================================================
# IMAP polling
# ===========================================================================


def _imap_fetch_unseen(imap: imaplib.IMAP4_SSL) -> list[dict]:
    """Fetch all UNSEEN messages from the inbox and mark them SEEN.

    Returns a list of parsed message dicts (see _parse_imap_message).
    Malformed messages are skipped with a warning.
    """
    results = []

    imap.select("INBOX")
    status, data = imap.search(None, "UNSEEN")
    if status != "OK" or not data or not data[0]:
        return results

    msg_nums = data[0].split()
    if not msg_nums:
        return results

    logger.info(f"[email] Found {len(msg_nums)} unseen message(s)")

    for num in msg_nums:
        try:
            # Mark SEEN immediately to prevent duplicate processing on next poll
            imap.store(num, "+FLAGS", r"(\Seen)")

            status, msg_data = imap.fetch(num, "(RFC822)")
            if status != "OK" or not msg_data:
                logger.warning(f"[email] Failed to fetch message {num}")
                continue

            raw_bytes = msg_data[0][1]
            if not isinstance(raw_bytes, bytes):
                continue

            parsed = _parse_imap_message(raw_bytes)
            if parsed is not None:
                results.append(parsed)
        except Exception as e:
            logger.warning(f"[email] Error processing message {num}: {e}")
            continue

    return results


def _process_inbound_email(parsed: dict) -> None:
    """Resolve project, check for thread continuation, and enqueue a session.

    Args:
        parsed: Dict from _parse_imap_message with sender/subject/body/headers.
    """
    from bridge.routing import find_project_for_email

    sender = parsed["sender"]
    project = find_project_for_email(sender)

    if project is None:
        logger.info(f"[email] Unknown sender {sender} — discarding (no project match)")
        return

    project_key = project.get("_key", "default")
    working_dir = project.get("working_directory", "~/src")
    subject = parsed.get("subject", "")
    body = parsed["body"]
    message_id = parsed.get("message_id", "")
    in_reply_to = parsed.get("in_reply_to", "")
    references = parsed.get("references", "")

    # Thread continuation: check if this is a reply to an existing session
    existing_session_id = None
    if in_reply_to:
        existing_session_id = _lookup_session_for_msgid(in_reply_to)

    if existing_session_id:
        logger.info(f"[email] Inbound reply from {sender} — resuming session {existing_session_id}")
        session_id = existing_session_id
    else:
        # New thread: generate a fresh email session ID
        timestamp = int(time.time() * 1000)
        session_id = f"email_{project_key}_{sender}_{timestamp}"

    # Store inbound Message-ID for future reply tracking
    if message_id:
        _store_msgid_reverse(message_id, session_id)

    # Build extra_context for transport identification and email headers
    extra_context_items = {
        "transport": "email",
        "email_recipient": sender,
        "email_subject": subject,
        "email_message_id": message_id,
        "email_references": (f"{references} {message_id}".strip() if references else message_id),
    }

    logger.info(f"[email] Enqueueing session {session_id} for {sender} -> project '{project_key}'")

    # Enqueue the session. extra_context is passed via revival_context since
    # enqueue_agent_session does not have a direct extra_context parameter.
    # We encode it as a JSON string in revival_context and the session model
    # will merge it into extra_context on load.

    import asyncio as _asyncio

    async def _enqueue() -> None:
        from agent.agent_session_queue import enqueue_agent_session

        await enqueue_agent_session(
            project_key=project_key,
            session_id=session_id,
            working_dir=str(working_dir),
            message_text=f"[Email from {sender}]\nSubject: {subject}\n\n{body}",
            sender_name=sender,
            chat_id=sender,  # Use email address as chat_id for email sessions
            telegram_message_id=0,  # Sentinel: email has no Telegram message ID
        )

    # Store transport metadata directly after creation
    async def _enqueue_and_tag() -> None:
        await _enqueue()
        # Patch extra_context on the freshly created AgentSession
        try:
            import asyncio

            await asyncio.sleep(0.1)  # Brief wait for Redis write to complete
            from models.agent_session import AgentSession

            sessions = list(AgentSession.query.filter(session_id=session_id, status="pending"))
            if sessions:
                s = sessions[0]
                ec = s.extra_context or {}
                ec.update(extra_context_items)
                s.extra_context = ec
                s.save()
                logger.debug(f"[email] Tagged session {session_id} with transport=email")
        except Exception as e:
            logger.warning(f"[email] Failed to tag session transport metadata: {e}")

    try:
        loop = _asyncio.get_event_loop()
        if loop.is_running():
            _asyncio.ensure_future(_enqueue_and_tag())
        else:
            loop.run_until_complete(_enqueue_and_tag())
    except RuntimeError:
        # No event loop in this thread — run in a new one
        _asyncio.run(_enqueue_and_tag())


# ===========================================================================
# Main poll loop
# ===========================================================================


async def _email_inbox_loop(config: dict) -> None:
    """Main IMAP poll loop with reconnection and backoff.

    Runs until the process is killed. On each successful poll, updates
    Redis ``email:last_poll_ts`` for health monitoring.

    Args:
        config: Email config dict from _get_email_config().
    """
    backoff = _BACKOFF_INITIAL
    imap: imaplib.IMAP4_SSL | None = None

    logger.info(
        f"[email] Starting IMAP poll loop "
        f"(host={config['imap_host']}, user={config['imap_user']}, "
        f"interval={config['poll_interval']}s)"
    )

    while True:
        try:
            # (Re)connect if needed
            if imap is None:
                logger.info(
                    f"[email] Connecting to IMAP {config['imap_host']}:{config['imap_port']}"
                )
                imap = imaplib.IMAP4_SSL(config["imap_host"], config["imap_port"])
                imap.login(config["imap_user"], config["imap_password"])
                logger.info(f"[email] IMAP connected as {config['imap_user']}")
                backoff = _BACKOFF_INITIAL  # Reset backoff on successful connect

            # Fetch and process unseen messages
            messages = await asyncio.to_thread(_imap_fetch_unseen, imap)

            for parsed in messages:
                try:
                    _process_inbound_email(parsed)
                except Exception as e:
                    logger.error(f"[email] Failed to process inbound email: {e}", exc_info=True)

            # Health monitoring: record successful poll
            _update_last_poll_ts()

            # Wait before next poll
            await asyncio.sleep(config["poll_interval"])

        except (imaplib.IMAP4.error, imaplib.IMAP4.abort, OSError, ConnectionError) as e:
            logger.warning(f"[email] IMAP connection error: {e}. Reconnecting in {backoff}s...")
            try:
                if imap is not None:
                    imap.logout()
            except Exception:
                pass
            imap = None
            await asyncio.sleep(backoff)
            backoff = min(backoff * _BACKOFF_FACTOR, _BACKOFF_MAX)

        except asyncio.CancelledError:
            logger.info("[email] IMAP poll loop cancelled, shutting down")
            try:
                if imap is not None:
                    imap.logout()
            except Exception:
                pass
            raise

        except Exception as e:
            logger.error(f"[email] Unexpected error in poll loop: {e}", exc_info=True)
            try:
                if imap is not None:
                    imap.logout()
            except Exception:
                pass
            imap = None
            await asyncio.sleep(backoff)
            backoff = min(backoff * _BACKOFF_FACTOR, _BACKOFF_MAX)


# ===========================================================================
# Entry point
# ===========================================================================


async def main(dry_run: bool = False) -> None:
    """Start the email bridge.

    Args:
        dry_run: If True, load config and validate credentials, then exit.
    """
    from pathlib import Path

    # Load .env if present
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        try:
            from dotenv import load_dotenv

            load_dotenv(env_path)
        except ImportError:
            pass  # dotenv not installed, rely on environment

    config = _get_email_config()

    if not config["imap_user"] or not config["imap_password"]:
        logger.error(
            "[email] IMAP credentials not configured. Set IMAP_USER and IMAP_PASSWORD in .env"
        )
        return

    if not config["smtp_user"] or not config["smtp_password"]:
        logger.error(
            "[email] SMTP credentials not configured. Set SMTP_USER and SMTP_PASSWORD in .env"
        )
        return

    # Load project email contacts so find_project_for_email() works
    try:
        from bridge.routing import EMAIL_TO_PROJECT, load_config, load_email_contacts

        full_config = load_config()
        contacts = load_email_contacts(full_config)
        EMAIL_TO_PROJECT.update(contacts)
        logger.info(f"[email] Loaded {len(contacts)} email contact(s)")
    except Exception as e:
        logger.warning(f"[email] Failed to load email contacts: {e}")

    if dry_run:
        logger.info(
            f"[email] Dry run: config OK. "
            f"IMAP={config['imap_host']}:{config['imap_port']}, "
            f"SMTP={config['smtp_host']}:{config['smtp_port']}, "
            f"user={config['imap_user']}"
        )
        return

    await _email_inbox_loop(config)


if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    dry_run = "--dry-run" in sys.argv
    asyncio.run(main(dry_run=dry_run))
