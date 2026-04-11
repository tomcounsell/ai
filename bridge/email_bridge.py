"""Email bridge: IMAP inbox polling and SMTP output handler.

Implements the secondary transport for inbound/outbound email alongside the
Telegram bridge. Architecture mirrors bridge/telegram_bridge.py + telegram_relay.py:

    IMAP poll loop → _process_inbound_email() → enqueue_agent_session()
    EmailOutputHandler.send() → SMTP reply with In-Reply-To header

Session IDs use the ``email_`` prefix to distinguish them from Telegram sessions.
Transport is stored in AgentSession.extra_context["transport"] = "email".
The sentinel ``telegram_message_id=0`` is used for all email sessions (email has
no Telegram message ID).
"""

from __future__ import annotations

import asyncio
import email as email_lib
import email.header
import email.mime.text
import email.utils
import imaplib
import logging
import os
import smtplib
import time
from typing import Any

logger = logging.getLogger(__name__)

# =============================================================================
# Config helpers
# =============================================================================

# Timeout between IMAP polls (seconds)
IMAP_POLL_INTERVAL = int(os.environ.get("IMAP_POLL_INTERVAL", "30"))

# Max retries for SMTP sends before dead-lettering
SMTP_MAX_RETRIES = 3

# Redis key for health monitoring
REDIS_LAST_POLL_KEY = "email:last_poll_ts"

# TTL for email:msgid reverse-mapping keys (48 hours)
EMAIL_MSGID_TTL = 48 * 3600


def _get_imap_config() -> dict | None:
    """Return IMAP connection config from environment, or None if not configured."""
    host = os.environ.get("IMAP_HOST")
    user = os.environ.get("IMAP_USER")
    password = os.environ.get("IMAP_PASSWORD")
    if not (host and user and password):
        return None
    return {
        "host": host,
        "user": user,
        "password": password,
        "port": int(os.environ.get("IMAP_PORT", "993")),
        "ssl": os.environ.get("IMAP_SSL", "true").lower() != "false",
    }


def _get_smtp_config() -> dict | None:
    """Return SMTP connection config from environment, or None if not configured."""
    host = os.environ.get("SMTP_HOST")
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    if not (host and user and password):
        return None
    return {
        "host": host,
        "user": user,
        "password": password,
        "port": int(os.environ.get("SMTP_PORT", "587")),
        "use_tls": os.environ.get("SMTP_USE_TLS", "true").lower() != "false",
    }


def _get_redis():
    """Return a Redis connection (lazy, cached module-level)."""
    import redis

    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    return redis.Redis.from_url(redis_url, decode_responses=True)


# =============================================================================
# Email parsing helpers
# =============================================================================


def _decode_header_value(value: str | None) -> str:
    """Decode an RFC-2047 encoded email header value to plain text."""
    if not value:
        return ""
    parts = email.header.decode_header(value)
    decoded_parts = []
    for part, charset in parts:
        if isinstance(part, bytes):
            try:
                decoded_parts.append(part.decode(charset or "utf-8", errors="replace"))
            except Exception:
                decoded_parts.append(part.decode("utf-8", errors="replace"))
        else:
            decoded_parts.append(part)
    return "".join(decoded_parts).strip()


def _extract_address(raw: str | None) -> str:
    """Extract the plain email address from a From/Reply-To header value."""
    if not raw:
        return ""
    _, addr = email.utils.parseaddr(raw)
    return addr.lower().strip()


def _extract_body(msg: email_lib.message.Message) -> str:
    """Extract plain text body from an email message.

    Prefers text/plain parts. Falls back to stripping HTML if no plain text.
    Returns empty string if no usable body is found.
    """
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disposition = str(part.get("Content-Disposition", ""))
            if ctype == "text/plain" and "attachment" not in disposition:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace").strip()
        # No plain text — try HTML fallback (strip tags)
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    html = payload.decode(charset, errors="replace")
                    import re

                    text = re.sub(r"<[^>]+>", " ", html)
                    text = re.sub(r"\s+", " ", text).strip()
                    return text
        return ""
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace").strip()
        return ""


def parse_email_message(raw_bytes: bytes) -> dict | None:
    """Parse raw email bytes into a structured dict.

    Returns a dict with keys: from_addr, subject, body, message_id, in_reply_to.
    Returns None if the email cannot be parsed or has no usable body.
    """
    try:
        msg = email_lib.message_from_bytes(raw_bytes)
    except Exception as e:
        logger.warning(f"Failed to parse email bytes: {e}")
        return None

    from_raw = msg.get("From", "")
    from_addr = _extract_address(from_raw)
    if not from_addr:
        logger.warning("Email has no From address, skipping")
        return None

    subject = _decode_header_value(msg.get("Subject", ""))
    body = _extract_body(msg)

    if not body or not body.strip():
        logger.debug(f"Email from {from_addr} has empty body, skipping")
        return None

    message_id = msg.get("Message-ID", "").strip()
    in_reply_to = msg.get("In-Reply-To", "").strip()

    return {
        "from_addr": from_addr,
        "from_raw": from_raw,
        "subject": subject,
        "body": body.strip(),
        "message_id": message_id,
        "in_reply_to": in_reply_to,
    }


# =============================================================================
# EmailOutputHandler
# =============================================================================


class EmailOutputHandler:
    """Route agent session output to the email sender via SMTP.

    Implements the OutputHandler protocol. The send() method composes an SMTP
    reply with In-Reply-To and References headers so the reply threads correctly
    in the recipient's email client.

    react() is a no-op — email has no concept of emoji reactions.

    Failed sends are retried up to SMTP_MAX_RETRIES times with exponential backoff.
    Persistent failures are written to the dead letter queue in Redis under
    email:dead_letter:{session_id}.
    """

    def __init__(
        self,
        smtp_config: dict | None = None,
        redis_url: str | None = None,
    ):
        self._smtp_config = smtp_config or _get_smtp_config()
        self._redis_url = redis_url or os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        self._redis = None

    def _get_redis(self):
        if self._redis is None:
            import redis

            self._redis = redis.Redis.from_url(self._redis_url, decode_responses=True)
        return self._redis

    def _build_reply(
        self,
        to_addr: str,
        subject: str,
        body: str,
        in_reply_to: str | None,
        references: str | None,
        from_addr: str,
    ) -> email.mime.text.MIMEText:
        """Compose an SMTP reply message."""
        msg = email.mime.text.MIMEText(body, "plain", "utf-8")
        msg["From"] = from_addr
        msg["To"] = to_addr
        if subject and not subject.lower().startswith("re:"):
            msg["Subject"] = f"Re: {subject}"
        else:
            msg["Subject"] = subject or "Re: (no subject)"
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
        if references:
            msg["References"] = references
        msg["Date"] = email.utils.formatdate(localtime=True)
        return msg

    def _send_smtp(self, to_addr: str, mime_msg: email.mime.text.MIMEText) -> None:
        """Send via SMTP (synchronous, run in thread executor)."""
        cfg = self._smtp_config
        if not cfg:
            raise RuntimeError("SMTP not configured (missing SMTP_HOST/USER/PASSWORD)")

        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=30) as smtp:
            if cfg.get("use_tls", True):
                smtp.starttls()
            smtp.login(cfg["user"], cfg["password"])
            smtp.sendmail(cfg["user"], [to_addr], mime_msg.as_string())

    async def send(
        self,
        chat_id: str,
        text: str,
        reply_to_msg_id: int,
        session: Any = None,
    ) -> None:
        """Send agent output as an SMTP reply to the originating email.

        Args:
            chat_id: The sender's email address (used as the reply-to address).
            text: Agent output text to send.
            reply_to_msg_id: Ignored for email sessions (sentinel value 0).
                             Threading is handled via In-Reply-To header from extra_context.
            session: AgentSession providing extra_context with email_message_id and subject.
        """
        if not text:
            return

        extra = {}
        session_id = None
        if session is not None:
            extra = getattr(session, "extra_context", None) or {}
            session_id = getattr(session, "session_id", None)

        original_message_id = extra.get("email_message_id", "")
        original_subject = extra.get("email_subject", "")
        from_addr = (
            self._smtp_config["user"] if self._smtp_config else os.environ.get("SMTP_USER", "")
        )

        mime_msg = self._build_reply(
            to_addr=chat_id,
            subject=original_subject,
            body=text,
            in_reply_to=original_message_id or None,
            references=original_message_id or None,
            from_addr=from_addr,
        )

        # Retry with exponential backoff
        last_error = None
        for attempt in range(SMTP_MAX_RETRIES):
            try:
                await asyncio.to_thread(self._send_smtp, chat_id, mime_msg)
                logger.info(
                    f"[email] Sent reply to {chat_id} (session={session_id}, {len(text)} chars)"
                )

                # Store outbound Message-ID for future thread continuation
                outbound_msg_id = mime_msg.get("Message-ID", "")
                if outbound_msg_id and session_id:
                    try:
                        r = self._get_redis()
                        key = f"email:msgid:{outbound_msg_id}"
                        r.set(key, session_id, ex=EMAIL_MSGID_TTL)
                    except Exception as redis_err:
                        logger.warning(f"Failed to store outbound msgid mapping: {redis_err}")

                return
            except Exception as e:
                last_error = e
                backoff = 2**attempt
                logger.warning(
                    f"[email] SMTP send attempt {attempt + 1}/{SMTP_MAX_RETRIES} "
                    f"failed for {chat_id}: {e}. Retrying in {backoff}s..."
                )
                await asyncio.sleep(backoff)

        # All retries exhausted — write to dead letter queue
        logger.error(
            f"[email] SMTP send failed after {SMTP_MAX_RETRIES} attempts for {chat_id}: "
            f"{last_error}. Writing to dead letter queue."
        )
        if session_id:
            try:
                from bridge.email_dead_letter import write_dead_letter

                write_dead_letter(
                    session_id=session_id,
                    recipient=chat_id,
                    subject=str(mime_msg.get("Subject", "")),
                    body=text,
                    headers={
                        "In-Reply-To": str(mime_msg.get("In-Reply-To", "")),
                        "References": str(mime_msg.get("References", "")),
                    },
                    error=str(last_error),
                )
            except Exception as dl_err:
                logger.error(f"[email] Dead letter write also failed: {dl_err}")

    async def react(
        self,
        chat_id: str,
        msg_id: int,
        emoji: str | None = None,
    ) -> None:
        """No-op — email has no emoji reaction concept."""
        pass


# =============================================================================
# IMAP polling loop
# =============================================================================


async def _process_inbound_email(parsed: dict, config: dict) -> None:
    """Process a single parsed inbound email.

    Resolves the sender to a project, checks for thread continuation via
    In-Reply-To header, and enqueues an AgentSession.

    Args:
        parsed: Dict from parse_email_message() with keys:
                from_addr, subject, body, message_id, in_reply_to
        config: The loaded projects.json config dict.
    """
    from agent.agent_session_queue import enqueue_agent_session
    from bridge.routing import ACTIVE_PROJECTS, find_project_for_email

    from_addr = parsed["from_addr"]
    body = parsed["body"]
    message_id = parsed["message_id"]
    in_reply_to = parsed["in_reply_to"]
    subject = parsed["subject"]

    # Resolve sender to project
    project = find_project_for_email(from_addr)
    if project is None:
        logger.info(f"[email] No project found for sender {from_addr}, discarding")
        return

    project_key = project.get("_key") or project.get("name", "unknown")
    if project_key not in ACTIVE_PROJECTS:
        logger.info(f"[email] Project '{project_key}' not in ACTIVE_PROJECTS, discarding")
        return

    working_dir = project.get("working_directory") or config.get("defaults", {}).get(
        "working_directory", "~/src"
    )

    # Check for thread continuation via In-Reply-To
    existing_session_id = None
    if in_reply_to:
        try:
            r = _get_redis()
            existing_session_id = r.get(f"email:msgid:{in_reply_to}")
        except Exception as e:
            logger.warning(f"[email] Redis lookup for In-Reply-To failed: {e}")

    # Construct session_id
    timestamp = int(time.time())
    if existing_session_id:
        session_id = existing_session_id
        logger.info(
            f"[email] Continuing session {session_id} "
            f"from {from_addr} via In-Reply-To={in_reply_to}"
        )
    else:
        safe_addr = from_addr.replace("@", "_at_").replace(".", "_")
        session_id = f"email_{project_key}_{safe_addr}_{timestamp}"
        logger.info(f"[email] New session {session_id} from {from_addr}")

    # Store inbound Message-ID → session_id for future thread continuation
    if message_id and session_id:
        try:
            r = _get_redis()
            r.set(f"email:msgid:{message_id}", session_id, ex=EMAIL_MSGID_TTL)
        except Exception as e:
            logger.warning(f"[email] Failed to store inbound msgid mapping: {e}")

    # Enqueue the session with email transport metadata
    try:
        await enqueue_agent_session(
            project_key=project_key,
            session_id=session_id,
            working_dir=working_dir,
            message_text=body,
            sender_name=from_addr,
            chat_id=from_addr,  # email address as chat_id
            telegram_message_id=0,  # sentinel for email sessions
            chat_title=subject or f"Email from {from_addr}",
            project_config=project,
            extra_context_overrides={
                "transport": "email",
                "email_message_id": message_id,
                "email_from": from_addr,
                "email_subject": subject,
            },
        )
        logger.info(f"[email] Enqueued session {session_id} for {from_addr}")
    except Exception as e:
        logger.error(f"[email] Failed to enqueue session for {from_addr}: {e}")


async def _poll_imap(imap_config: dict) -> list[bytes]:
    """Connect to IMAP and fetch all unseen message bodies.

    Marks fetched messages as SEEN atomically to prevent duplicate processing.
    Returns a list of raw message bytes.
    """
    host = imap_config["host"]
    port = imap_config["port"]
    user = imap_config["user"]
    password = imap_config["password"]
    use_ssl = imap_config.get("ssl", True)

    def _fetch_unseen() -> list[bytes]:
        if use_ssl:
            conn = imaplib.IMAP4_SSL(host, port)
        else:
            conn = imaplib.IMAP4(host, port)
        try:
            conn.login(user, password)
            conn.select("INBOX")

            # Search for unseen messages
            status, data = conn.search(None, "UNSEEN")
            if status != "OK" or not data or not data[0]:
                return []

            msg_ids = data[0].split()
            if not msg_ids:
                return []

            messages = []
            for msg_id in msg_ids:
                # Mark as SEEN before fetching to prevent re-processing on concurrent polls
                conn.store(msg_id, "+FLAGS", "\\Seen")
                status, msg_data = conn.fetch(msg_id, "(RFC822)")
                if status == "OK" and msg_data:
                    for response_part in msg_data:
                        if isinstance(response_part, tuple):
                            messages.append(response_part[1])
            return messages
        finally:
            try:
                conn.logout()
            except Exception:
                pass

    return await asyncio.to_thread(_fetch_unseen)


async def _email_inbox_loop(imap_config: dict, config: dict) -> None:
    """Main IMAP polling loop.

    Polls the IMAP inbox every IMAP_POLL_INTERVAL seconds. On each successful
    poll, updates email:last_poll_ts in Redis for health monitoring.

    Implements exponential backoff on connection failures (up to 5 minutes max).
    """
    backoff = IMAP_POLL_INTERVAL
    max_backoff = 300  # 5 minutes

    while True:
        try:
            messages = await _poll_imap(imap_config)

            # Update health timestamp
            try:
                r = _get_redis()
                r.set(REDIS_LAST_POLL_KEY, str(time.time()))
            except Exception as e:
                logger.warning(f"[email] Failed to update health timestamp: {e}")

            if messages:
                logger.info(f"[email] Fetched {len(messages)} unseen message(s)")
                for raw_bytes in messages:
                    parsed = parse_email_message(raw_bytes)
                    if parsed is None:
                        continue
                    try:
                        await _process_inbound_email(parsed, config)
                    except Exception as e:
                        logger.error(
                            f"[email] Error processing email from "
                            f"{parsed.get('from_addr', 'unknown')}: {e}"
                        )

            # Reset backoff on success
            backoff = IMAP_POLL_INTERVAL

        except imaplib.IMAP4.error as e:
            logger.error(f"[email] IMAP error: {e}. Retrying in {backoff}s...")
            backoff = min(backoff * 2, max_backoff)

        except OSError as e:
            logger.error(f"[email] Network error during IMAP poll: {e}. Retrying in {backoff}s...")
            backoff = min(backoff * 2, max_backoff)

        except Exception as e:
            logger.error(
                f"[email] Unexpected error in IMAP poll loop: {e}. Retrying in {backoff}s..."
            )
            backoff = min(backoff * 2, max_backoff)

        await asyncio.sleep(backoff)


async def run_email_bridge() -> None:
    """Start the email bridge IMAP polling loop.

    Loads IMAP/SMTP config from environment. Exits with error if IMAP config
    is not available. Safe to call even if email config is absent — returns
    immediately with a warning in that case.
    """
    from bridge.routing import build_email_to_project_map, load_config

    imap_config = _get_imap_config()
    if not imap_config:
        logger.warning(
            "[email] IMAP not configured (missing IMAP_HOST/IMAP_USER/IMAP_PASSWORD). "
            "Email bridge will not start."
        )
        return

    smtp_config = _get_smtp_config()
    if not smtp_config:
        logger.warning(
            "[email] SMTP not configured (missing SMTP_HOST/SMTP_USER/SMTP_PASSWORD). "
            "Email bridge will start but cannot send replies."
        )

    # Load projects config and build email contact map
    config = load_config()

    # Initialize EMAIL_TO_PROJECT global (mirrors how telegram_bridge initializes GROUP_TO_PROJECT)
    import bridge.routing as _routing_module

    _routing_module.EMAIL_TO_PROJECT.update(build_email_to_project_map(config))

    logger.info(
        f"[email] Email bridge starting. "
        f"IMAP host={imap_config['host']}, poll interval={IMAP_POLL_INTERVAL}s, "
        f"contacts={len(_routing_module.EMAIL_TO_PROJECT)}"
    )

    await _email_inbox_loop(imap_config, config)


def main() -> None:
    """Entry point for ``python -m bridge.email_bridge``."""
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )

    asyncio.run(run_email_bridge())


if __name__ == "__main__":
    main()
