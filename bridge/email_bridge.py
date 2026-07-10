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
import email.mime.base
import email.mime.multipart
import email.mime.text
import email.utils
import hashlib
import imaplib
import json
import logging
import mimetypes
import os
import re
import shutil
import smtplib
import time
from email import encoders
from pathlib import Path
from typing import Any

from config.settings import settings as _app_settings

logger = logging.getLogger(__name__)

# =============================================================================
# Config helpers
# =============================================================================

# Timeout between IMAP polls (seconds)
IMAP_POLL_INTERVAL = int(os.environ.get("IMAP_POLL_INTERVAL", "30"))

# Socket timeout for IMAP connections (seconds) — prevents hung connections from
# accumulating on the server side when the network drops mid-session.
IMAP_SOCKET_TIMEOUT = int(os.environ.get("IMAP_SOCKET_TIMEOUT", "30"))

# Max messages to fetch per poll cycle (prevents hanging on inboxes with many unseen messages)
IMAP_MAX_BATCH = int(os.environ.get("IMAP_MAX_BATCH", "20"))

# Max retries for SMTP sends before dead-lettering
SMTP_MAX_RETRIES = 3

# Redis key for health monitoring
REDIS_LAST_POLL_KEY = "email:last_poll_ts"

# Redis operator-alert keys (issue #1817, workstreams A2/A3). Surfaced on the
# dashboard's "email" health field (ui/app.py::_get_email_health) — not a new
# alert surface, just two new keys read by the existing one.
REDIS_AUTH_FAILED_KEY = "email:auth_failed"
REDIS_RESOLVER_UNAVAILABLE_KEY = "email:resolver_unavailable"

# Consecutive resolver:failures:{project_key} readings (A2) before the
# email:resolver_unavailable alert arms. Provisional/tunable — take with a
# grain of salt: chosen to absorb a one-off transient resolver blip without
# paging, while still catching a genuinely stuck resolver (e.g. an expired
# OAuth token) within a handful of inbound emails. Env: EMAIL_RESOLVER_ALERT_AFTER
# (config/settings.py).
EMAIL_RESOLVER_ALERT_AFTER = _app_settings.email_resolver_alert_after

# IMAP auth-failure message signatures that indicate a PERMANENT credential
# problem (revoked app password, disabled account) rather than a transient
# network/server blip (A3). Matched case-sensitively against the raw
# imaplib.IMAP4.error message text.
IMAP_PERMANENT_AUTH_SIGNATURES = (
    "AUTHENTICATIONFAILED",
    "Invalid credentials",
    "LOGIN failed",
)

# TTL for email:msgid reverse-mapping keys (48 hours)
EMAIL_MSGID_TTL = 48 * 3600

# --- Inbound attachment storage (mirrors the Telegram inbound-media pattern) ---
# Take these with a grain of salt: provisional, tunable defaults. Both are
# env-overridable so a machine can loosen/tighten without a code change.
# Cumulative cap on decoded attachment bytes persisted per inbound email (25 MiB).
# A SINGLE cumulative knob (no per-file cap) — once exceeded, remaining parts are
# skipped and the message is marked truncated (critique C4).
EMAIL_ATTACHMENT_MAX_TOTAL_BYTES = int(
    os.environ.get("EMAIL_ATTACHMENT_MAX_TOTAL_BYTES", str(25 * 1024 * 1024))
)
# Cheap multipart-bomb guard: cap the number of attachment parts decoded per email.
EMAIL_ATTACHMENT_MAX_PARTS = int(os.environ.get("EMAIL_ATTACHMENT_MAX_PARTS", "50"))

# On-disk location for persisted inbound attachment bytes — parallel to the
# Telegram media path (bridge/media.py MEDIA_DIR). Redis carries metadata/paths
# only, never bytes. Created on first use.
EMAIL_ATTACHMENT_DIR = Path(__file__).parent.parent / "data" / "media" / "email-attachments"

# Fire-and-forget vault mirror so the KnowledgeWatcher indexes inbound files,
# mirroring bridge/telegram_bridge.py:_ingest_attachments.
EMAIL_ATTACHMENT_VAULT_SUBDIR = Path.home() / "work-vault" / "email-attachments"


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


def _extract_addresses(raw: str | None) -> list[str]:
    """Extract all plain email addresses from a To/CC header value."""
    if not raw:
        return []
    return [addr.lower().strip() for _, addr in email.utils.getaddresses([raw]) if addr.strip()]


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


# =============================================================================
# Inbound attachment extraction + persistence
#
# Split into two phases to keep the read path side-effect-free (critique C1):
#   1. _extract_attachment_metadata(msg) — PURE. Walks the MIME tree, decodes
#      payloads into memory, returns metadata dicts. Called inside
#      parse_email_message, which is ALSO invoked by the read-only
#      `valor-email read` IMAP fallback — so it must never touch disk.
#   2. _persist_attachments(parsed) — writes the decoded bytes to disk and
#      fire-and-forget mirrors them to the work-vault. Called ONLY from the
#      IMAP poll loop (_email_inbox_loop), never from the read path.
#
# An attachment metadata dict has the public keys
# {filename, content_type, size, path} plus a transient private "_payload"
# (the decoded bytes) that _persist_attachments consumes and strips. _payload
# is NEVER serialized — use _public_attachment() at every serialization point.
# On the read/fallback path bytes are never persisted, so `path` stays None.
# =============================================================================

# Public (JSON-safe) attachment metadata fields, in canonical order.
_ATTACHMENT_PUBLIC_FIELDS = ("filename", "content_type", "size", "path")


def _public_attachment(att: dict) -> dict:
    """Project an attachment dict down to its JSON-safe public fields.

    Strips the transient ``_payload`` bytes so the result is always
    ``json.dumps``-able. Used by every serialization point (history blob,
    read output, session context).
    """
    return {k: att.get(k) for k in _ATTACHMENT_PUBLIC_FIELDS}


def _sanitize_attachment_filename(raw: str | None, index: int, content_type: str) -> str:
    """Reduce an attachment filename to a safe basename (no path traversal).

    Strategy (matches the plan's sanitization contract):
      - Reduce to ``Path(raw).name`` so any directory components / ``..`` /
        absolute paths collapse to a bare basename.
      - Allow only ``[A-Za-z0-9._-]``; replace every other char with ``_``.
      - Strip leading/trailing dots and underscores so ``.`` / ``..`` / hidden
        dotfiles cannot survive as a traversal or empty name.
      - If nothing usable remains, fall back to
        ``attachment_{index}{guessed_ext}``.
    The ``{msgid_hash}`` subdir created by _persist_attachments contains the
    result to a single directory regardless.
    """
    base = Path(raw or "").name
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", base).strip("._")
    if not cleaned or cleaned in (".", ".."):
        ext = mimetypes.guess_extension(content_type or "") or ""
        cleaned = f"attachment_{index}{ext}"
    return cleaned


def _is_attachment_part(part: email_lib.message.Message) -> bool:
    """True if a MIME part should be treated as a downloadable attachment.

    Captures parts with an ``attachment`` Content-Disposition, plus any
    filename-bearing leaf part (e.g. an ``inline`` CID image) — the plan treats
    inline/CID parts as ordinary attachments at most. Multipart containers and
    bare text body parts (no filename, no attachment disposition) are skipped.
    """
    if part.is_multipart():
        return False
    disposition = str(part.get("Content-Disposition", "")).lower()
    if "attachment" in disposition:
        return True
    try:
        return bool(part.get_filename())
    except Exception:
        return False


def _body_references_attachments(text: str | None) -> bool:
    """Return True if the email body appears to reference attachments.

    Uses a conservative regex over common attachment-reference phrases.
    Total function: returns False for empty string or None, never raises.
    """
    import re

    if not text:
        return False
    try:
        return bool(
            re.search(
                r"\b(attach(ed|ment|ments)?|see attached|enclosed|find attached)\b",
                text,
                re.IGNORECASE,
            )
        )
    except Exception:
        return False


def _extract_attachment_metadata(
    msg: email_lib.message.Message,
) -> tuple[list[dict], bool]:
    """Walk a parsed message and return (attachments, truncated). PURE — no disk.

    Each attachment dict has public keys ``filename`` (sanitized),
    ``content_type``, ``size`` (decoded byte length), ``path`` (always ``None``
    here — set later by _persist_attachments), plus a transient ``_payload``
    holding the decoded bytes.

    Guardrails (critique C4):
      - A running cumulative-byte total short-circuits the walk: a part whose
        ENCODED length already pushes the total over
        ``EMAIL_ATTACHMENT_MAX_TOTAL_BYTES`` is rejected pre-decode (estimated
        from the base64 string length), so a multipart bomb never forces a full
        decode of every part. ``truncated`` is set and the walk stops.
      - ``EMAIL_ATTACHMENT_MAX_PARTS`` caps the number of parts decoded.
    Each part is wrapped in its own try/except so one malformed/undecodable part
    never aborts the rest — it is logged and skipped.
    """
    attachments: list[dict] = []
    truncated = False
    if not msg.is_multipart():
        return attachments, truncated

    running_total = 0
    index = 0
    for part in msg.walk():
        if not _is_attachment_part(part):
            continue
        if len(attachments) >= EMAIL_ATTACHMENT_MAX_PARTS:
            truncated = True
            break
        try:
            ctype = part.get_content_type() or "application/octet-stream"
            # Pre-decode size estimate from the encoded payload string so an
            # oversized part is rejected BEFORE base64-decoding it into RAM.
            raw_payload = part.get_payload(decode=False)
            est = (len(raw_payload) * 3) // 4 if isinstance(raw_payload, str) else 0
            if running_total + est > EMAIL_ATTACHMENT_MAX_TOTAL_BYTES:
                truncated = True
                break
            payload = part.get_payload(decode=True)
            if payload is None:
                logger.warning("[email] Attachment part had no decodable payload, skipping")
                continue
            size = len(payload)
            if running_total + size > EMAIL_ATTACHMENT_MAX_TOTAL_BYTES:
                # True size exceeded the cap (estimate was low) — skip and stop.
                truncated = True
                break
            running_total += size
            filename = _sanitize_attachment_filename(part.get_filename(), index, ctype)
            attachments.append(
                {
                    "filename": filename,
                    "content_type": ctype,
                    "size": size,
                    "path": None,
                    "_payload": payload,
                }
            )
            index += 1
        except Exception as e:
            logger.warning(f"[email] Skipping malformed attachment part: {e}")
            truncated = True
            continue

    return attachments, truncated


def _attachment_storage_key(parsed: dict) -> str:
    """Derive a stable, collision-resistant subdir key for an email's attachments.

    Hashes the Message-ID. When the Message-ID is empty (some providers omit it),
    falls back to ``from_addr:subject:timestamp`` so attachments from different
    Message-ID-less emails do NOT collide into one shared subdir (critique C3).
    """
    mid = (parsed.get("message_id") or "").strip()
    if not mid:
        ts = int(parsed.get("timestamp") or time.time())
        mid = f"{parsed.get('from_addr', '')}:{parsed.get('subject', '')}:{ts}"
    return hashlib.sha256(mid.encode("utf-8", errors="replace")).hexdigest()[:16]


def _persist_attachments(parsed: dict) -> list[dict]:
    """Write decoded attachment bytes to disk + mirror to the vault. NOT pure.

    Called ONLY from the IMAP poll loop (never the read path). Mutates each
    attachment dict in place: pops the transient ``_payload``, sets ``path`` to
    the on-disk location. Files land under
    ``data/media/email-attachments/{msgid_hash}/{sanitized_name}``; same-email
    filename collisions are disambiguated with an index suffix (critique Risk 3).
    Every failure is logged and never propagates — the poll loop must not break.
    """
    attachments = parsed.get("attachments") or []
    if not attachments:
        return attachments

    key = _attachment_storage_key(parsed)
    dest_dir = EMAIL_ATTACHMENT_DIR / key
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.warning(f"[email] Could not create attachment dir {dest_dir}: {e}")
        for att in attachments:
            att.pop("_payload", None)
        return attachments

    dest_root = str(dest_dir.resolve())
    used_names: set[str] = set()
    for att in attachments:
        payload = att.pop("_payload", None)
        if payload is None:
            continue
        try:
            name = att.get("filename") or "attachment"
            stem, suffix = Path(name).stem, Path(name).suffix
            final = name
            n = 1
            while final in used_names or (dest_dir / final).exists():
                final = f"{stem}_{n}{suffix}"
                n += 1
            target = dest_dir / final
            # Defense in depth: confirm the resolved path stays inside dest_dir.
            if not str(target.resolve()).startswith(dest_root):
                logger.warning(f"[email] Blocked attachment path traversal: {name!r}")
                continue
            target.write_bytes(payload)
            used_names.add(final)
            att["filename"] = final
            att["path"] = str(target)
        except Exception as e:
            logger.warning(f"[email] Failed to persist attachment {att.get('filename')!r}: {e}")
            continue

    _mirror_attachments_to_vault(parsed)
    return attachments


def _mirror_attachments_to_vault(parsed: dict) -> None:
    """Fire-and-forget copy of persisted attachments into the work-vault.

    Mirrors bridge/telegram_bridge.py:_ingest_attachments — the KnowledgeWatcher
    indexes anything dropped under ``~/work-vault/`` recursively, so no further
    wiring is needed. Target names reuse the Telegram joint-key formula
    ``{date}_{sender}_{msgid}_{filename}``, substituting the C3 storage key when
    the Message-ID is empty. Never blocks, never raises — every failure logged.
    """
    try:
        attachments = [a for a in (parsed.get("attachments") or []) if a.get("path")]
        if not attachments:
            return
        EMAIL_ATTACHMENT_VAULT_SUBDIR.mkdir(parents=True, exist_ok=True)
        ts = float(parsed.get("timestamp") or time.time())
        date_part = time.strftime("%Y%m%d_%H%M%S", time.localtime(ts))
        safe_sender = (
            "".join(
                c if (c.isalnum() or c in ("-", "_")) else "_"
                for c in (parsed.get("from_addr") or "unknown")
            ).strip("_")
            or "unknown"
        )
        mid = (parsed.get("message_id") or "").strip() or _attachment_storage_key(parsed)
        safe_mid = re.sub(r"[^A-Za-z0-9]", "", mid)[:24] or "nomsgid"
        for att in attachments:
            try:
                src = Path(att["path"])
                if not src.exists():
                    continue
                target = (
                    EMAIL_ATTACHMENT_VAULT_SUBDIR
                    / f"{date_part}_{safe_sender}_{safe_mid}_{src.name}"
                )
                shutil.copy2(src, target)
                logger.info(f"[email] Mirrored {src.name} -> {target} (watcher will index)")
            except Exception as inner_e:
                logger.warning(
                    f"[email] Failed to mirror attachment {att.get('path')!r}: {inner_e}"
                )
    except Exception as e:
        logger.warning(f"[email] Vault mirror task failed: {e}")


def parse_email_message(raw_bytes: bytes) -> dict | None:
    """Parse raw email bytes into a structured dict.

    Returns a dict with keys: from_addr, to_addrs, cc_addrs, subject, body,
    message_id, in_reply_to, attachments, attachments_truncated.  Returns None
    if the email cannot be parsed or has neither a usable body nor attachments.

    The ``attachments`` list holds metadata dicts
    ``{filename, content_type, size, path}`` (plus a transient ``_payload``).
    Extraction is PURE here — bytes are decoded into memory but NOT written to
    disk; ``path`` is ``None`` until the poll loop calls _persist_attachments.
    This keeps the read-only `valor-email read` IMAP fallback side-effect-free
    (critique C1).
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
    attachments, attachments_truncated = _extract_attachment_metadata(msg)

    # Empty-body guard, relaxed for attachment-only emails: a message whose body
    # is empty but which carries attachments is legitimate (sender just sent a
    # file) and must NOT be dropped here.
    if (not body or not body.strip()) and not attachments:
        logger.debug(f"Email from {from_addr} has empty body and no attachments, skipping")
        return None

    message_id = msg.get("Message-ID", "").strip()
    in_reply_to = msg.get("In-Reply-To", "").strip()
    to_addrs = _extract_addresses(msg.get("To", ""))
    cc_addrs = _extract_addresses(msg.get("CC", ""))

    return {
        "from_addr": from_addr,
        "from_raw": from_raw,
        "to_addrs": to_addrs,
        "cc_addrs": cc_addrs,
        "subject": subject,
        "body": body.strip(),
        "message_id": message_id,
        "in_reply_to": in_reply_to,
        "attachments": attachments,
        "attachments_truncated": attachments_truncated,
    }


# =============================================================================
# MIME assembly (module-level helper so the relay and the handler share one path)
# =============================================================================


def _build_reply_mime(
    to_addrs: list[str] | str,
    subject: str,
    body: str,
    in_reply_to: str | None,
    references: str | None,
    from_addr: str,
    attachments: list[Path] | None = None,
    force_reply_prefix: bool = True,
) -> email.mime.text.MIMEText | email.mime.multipart.MIMEMultipart:
    """Compose an SMTP reply message, optionally with attachments.

    When ``attachments`` is None or empty, returns a plain ``MIMEText`` object —
    the exact shape of the pre-CLI ``_build_reply`` method (preserved for
    backward-compatible worker behavior; see the parsed-header regression test).

    When ``attachments`` are provided, returns a ``MIMEMultipart("mixed")`` with
    the body as the first part followed by one ``MIMEBase`` part per file.
    Content-Type is guessed via ``mimetypes.guess_type`` and falls back to
    ``application/octet-stream``.

    Args:
        to_addrs: Recipient address(es). Accepts a single string or a list;
            multiple addresses are joined with ", " in the To header.
        subject: Subject line. Whether ``Re:`` is prepended depends on
            ``force_reply_prefix`` (see below).
        body: Plain text body (utf-8).
        in_reply_to: RFC-2822 Message-ID of the message being replied to.
        references: RFC-2822 References chain (typically equal to in_reply_to).
        from_addr: Sender email address.
        attachments: Optional list of filesystem paths to attach.
        force_reply_prefix: When ``True`` (default), unconditionally prepend
            ``"Re: "`` to the subject if it does not already start with
            ``re:`` — this matches the legacy ``_build_reply`` semantics and
            preserves the worker agent-reply path even when the inbound email
            lacked a ``Message-ID`` header (so ``in_reply_to`` is empty).
            When ``False``, only prepend ``"Re: "`` if ``in_reply_to`` is
            truthy — this is the relay/CLI new-send path, where caller-provided
            subjects must be preserved verbatim.

    Returns:
        MIMEText when no attachments; MIMEMultipart when attachments are present.
    """
    if isinstance(to_addrs, str):
        to_addrs = [to_addrs]
    text_part = email.mime.text.MIMEText(body, "plain", "utf-8")

    if not attachments:
        msg: email.mime.text.MIMEText | email.mime.multipart.MIMEMultipart = text_part
    else:
        msg = email.mime.multipart.MIMEMultipart("mixed")
        msg.attach(text_part)
        for path in attachments:
            p = Path(path)
            ctype, encoding = mimetypes.guess_type(str(p))
            if ctype is None or encoding is not None:
                ctype = "application/octet-stream"
            maintype, _, subtype = ctype.partition("/")
            with p.open("rb") as fh:
                part = email.mime.base.MIMEBase(maintype, subtype or "octet-stream")
                part.set_payload(fh.read())
            encoders.encode_base64(part)
            part.add_header(
                "Content-Disposition",
                f'attachment; filename="{p.name}"',
            )
            msg.attach(part)

    msg["From"] = from_addr
    msg["To"] = ", ".join(to_addrs)
    # Subject prefixing depends on the caller:
    # - Worker reply path (``EmailOutputHandler._build_reply``): passes
    #   ``force_reply_prefix=True`` so the legacy semantics hold — ``"Re: "``
    #   is always prepended (if not already present), even when the inbound
    #   email lacked a ``Message-ID`` header and ``in_reply_to`` is empty.
    # - Relay / CLI new-send path: passes ``force_reply_prefix=False`` so
    #   caller-provided subjects are preserved verbatim; ``"Re: "`` is only
    #   added when ``in_reply_to`` is truthy (a genuine reply).
    _should_prefix = force_reply_prefix or bool(in_reply_to)
    if subject:
        if _should_prefix and not subject.lower().startswith("re:"):
            msg["Subject"] = f"Re: {subject}"
        else:
            msg["Subject"] = subject
    else:
        msg["Subject"] = "Re: (no subject)" if _should_prefix else "(no subject)"
    msg["Message-ID"] = email.utils.make_msgid(domain=from_addr.split("@")[-1])
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
    msg["Date"] = email.utils.formatdate(localtime=True)
    return msg


# =============================================================================
# History cache writers (Race 1: JSON blob + ZADD in one MULTI/EXEC pipeline)
# =============================================================================

# Redis key schema for the CLI history cache
HISTORY_SET_KEY = "email:history:{mailbox}"
HISTORY_MSG_KEY = "email:history:msg:{message_id}"
HISTORY_THREADS_KEY = "email:threads"

# Cap on entries per mailbox sorted set (newest retained)
HISTORY_MAX_ENTRIES = 500

# TTL on individual message JSON blobs (7 days)
HISTORY_MSG_TTL = 7 * 24 * 3600


def _record_history(parsed: dict, mailbox: str = "INBOX") -> None:
    """Write a parsed inbound email to the Redis history cache.

    The per-message JSON blob (``SET``) and the sorted-set membership (``ZADD``)
    are queued into a single ``r.pipeline()``, which defaults to
    ``transaction=True`` — redis-py emits ``MULTI``/``EXEC`` so both commands
    are applied atomically server-side. Readers never observe one without the
    other, so no ordering-on-the-wire framing is needed (see Race 1 in the
    plan for the original write-order mitigation this supersedes).

    After the write, trims the sorted set to ``HISTORY_MAX_ENTRIES`` newest
    entries. Evicted Message-IDs are actively DELed from the per-msg namespace
    in the same pipeline to bound orphan-blob leaks (C6 in the critique table).

    Failures are logged as warnings and do not propagate — the poll loop must
    never break because of a cache write error.
    """
    try:
        message_id = parsed.get("message_id") or ""
        if not message_id:
            return  # no stable key to hang the blob on
        ts = float(parsed.get("timestamp") or time.time())
        blob = json.dumps(
            {
                "from_addr": parsed.get("from_addr", ""),
                "from_raw": parsed.get("from_raw", ""),
                "subject": parsed.get("subject", ""),
                "body": parsed.get("body", ""),
                "timestamp": ts,
                "message_id": message_id,
                "in_reply_to": parsed.get("in_reply_to", ""),
                # Metadata only — never bytes. Projected through _public_attachment
                # so a stray transient _payload can never reach the blob. Old blobs
                # written before this field hydrate as [] on read (back-compat).
                "attachments": [_public_attachment(a) for a in (parsed.get("attachments") or [])],
                "attachments_truncated": bool(parsed.get("attachments_truncated")),
            }
        )

        set_key = HISTORY_SET_KEY.format(mailbox=mailbox)
        msg_key = HISTORY_MSG_KEY.format(message_id=message_id)

        r = _get_redis()
        # Phase 1: blob + ZADD queued inside a MULTI/EXEC pipeline (redis-py
        # defaults to transaction=True), so both commands land atomically.
        pipe = r.pipeline()
        pipe.set(msg_key, blob, ex=HISTORY_MSG_TTL)
        pipe.zadd(set_key, {message_id: ts})
        pipe.execute()

        # Phase 2: if over the cap, capture victims then DEL blobs + trim the set
        # atomically. ZRANGE 0 -(HISTORY_MAX_ENTRIES+1) selects the oldest over the cap.
        size = r.zcard(set_key)
        if size > HISTORY_MAX_ENTRIES:
            overflow = size - HISTORY_MAX_ENTRIES
            victims = r.zrange(set_key, 0, overflow - 1)
            if victims:
                pipe = r.pipeline()
                for vmsgid in victims:
                    pipe.delete(HISTORY_MSG_KEY.format(message_id=vmsgid))
                pipe.zremrangebyrank(set_key, 0, overflow - 1)
                pipe.execute()
    except Exception as e:  # pragma: no cover - defensive
        logger.warning(f"[email] _record_history failed: {e}")


def _record_thread(parsed: dict) -> None:
    """Update the ``email:threads`` hash for the CLI threads listing.

    Thread root is approximated as the ``in_reply_to`` chain head; when a new
    message reveals an earlier root than we'd stored, we re-key the entry.
    Drift is accepted for v1 — the hash is a best-effort navigation aid.

    Failures are logged as warnings.
    """
    try:
        message_id = parsed.get("message_id") or ""
        if not message_id:
            return
        in_reply_to = (parsed.get("in_reply_to") or "").strip()
        subject = parsed.get("subject", "") or ""
        ts = float(parsed.get("timestamp") or time.time())
        from_addr = parsed.get("from_addr", "") or ""

        # Single connection for this function — cheaper than re-resolving
        # the pool twice and easier to reason about under monkeypatch.
        r = _get_redis()

        # The root is the message at the end of the chain. Without a full
        # server-side traversal, approximate: if we have an in_reply_to, that's
        # a better root candidate than the current message. Walk one link via
        # Redis when possible.
        root = in_reply_to or message_id
        if in_reply_to:
            try:
                existing = r.hget(HISTORY_THREADS_KEY, in_reply_to)
                if existing:
                    try:
                        data = json.loads(existing)
                        candidate = data.get("root") or in_reply_to
                        root = candidate
                    except (json.JSONDecodeError, TypeError):
                        root = in_reply_to
            except Exception:
                root = in_reply_to

        existing_raw = r.hget(HISTORY_THREADS_KEY, root)
        if existing_raw:
            try:
                data = json.loads(existing_raw)
            except (json.JSONDecodeError, TypeError):
                data = {}
        else:
            data = {}

        data["root"] = root
        data["subject"] = data.get("subject") or subject
        data["message_count"] = int(data.get("message_count") or 0) + 1
        data["last_ts"] = max(float(data.get("last_ts") or 0.0), ts)
        participants = set(data.get("participants") or [])
        if from_addr:
            participants.add(from_addr)
        data["participants"] = sorted(participants)

        r.hset(HISTORY_THREADS_KEY, root, json.dumps(data))
    except Exception as e:  # pragma: no cover - defensive
        logger.warning(f"[email] _record_thread failed: {e}")


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
        to_addrs: list[str] | str,
        subject: str,
        body: str,
        in_reply_to: str | None,
        references: str | None,
        from_addr: str,
    ) -> email.mime.text.MIMEText:
        """Compose an SMTP reply message.

        Thin wrapper around the module-level ``_build_reply_mime`` helper.
        Kept as an instance method so existing tests that patch
        ``EmailOutputHandler._build_reply`` continue to work. Always returns
        a ``MIMEText`` (no attachments in the worker-reply path).

        Worker-reply semantics: passes ``force_reply_prefix=True`` so the
        ``"Re: "`` prefix is added unconditionally (matching pre-#1094
        behavior) even when the inbound email carried no ``Message-ID``
        header and ``in_reply_to`` is empty.
        """
        return _build_reply_mime(
            to_addrs=to_addrs,
            subject=subject,
            body=body,
            in_reply_to=in_reply_to,
            references=references,
            from_addr=from_addr,
            attachments=None,
            force_reply_prefix=True,
        )

    def _send_smtp(
        self,
        to_addrs: list[str],
        mime_msg: email.mime.text.MIMEText | email.mime.multipart.MIMEMultipart,
    ) -> None:
        """Send via SMTP (synchronous, run in thread executor)."""
        cfg = self._smtp_config
        if not cfg:
            raise RuntimeError("SMTP not configured (missing SMTP_HOST/USER/PASSWORD)")

        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=30) as smtp:
            if cfg.get("use_tls", True):
                smtp.starttls()
            smtp.login(cfg["user"], cfg["password"])
            smtp.sendmail(cfg["user"], to_addrs, mime_msg.as_string())

    async def send(
        self,
        chat_id: str,
        text: str,
        reply_to_msg_id: int,
        session: Any = None,
    ) -> None:
        """Send agent output as an SMTP reply to the originating email.

        Text is routed through ``bridge.message_drafter.draft_message`` with
        ``medium="email"`` before being wrapped as MIME. This is the same
        plumbing the Telegram handler uses — per-medium format rules live in
        the drafter (no markdown on the wire for email). See
        docs/plans/completed/message-drafter.md §Part C.

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

        # Drafter-at-the-handler. Fail open: any exception in the drafter must
        # not block the email send.
        body_text = text
        try:
            from bridge.message_drafter import draft_message

            draft = await draft_message(text, session=session, medium="email")
            if draft.text:
                body_text = draft.text
        except Exception as e:
            logger.warning("[email] Drafter failed, falling back to raw text: %s", e)

        original_message_id = extra.get("email_message_id", "")
        original_subject = extra.get("email_subject", "")
        from_addr = (
            self._smtp_config["user"] if self._smtp_config else os.environ.get("SMTP_USER", "")
        )

        # Build reply-all recipient list: original sender + everyone in To/CC
        # except ourselves.
        own_addr = from_addr.lower()
        original_to = extra.get("email_to_addrs") or []
        original_cc = extra.get("email_cc_addrs") or []
        reply_all_addrs = [chat_id] + [
            a
            for a in (original_to + original_cc)
            if a.lower() != own_addr and a.lower() != chat_id.lower()
        ]

        mime_msg = self._build_reply(
            to_addrs=reply_all_addrs,
            subject=original_subject,
            body=body_text,
            in_reply_to=original_message_id or None,
            references=original_message_id or None,
            from_addr=from_addr,
        )

        # Retry with exponential backoff
        last_error = None
        for attempt in range(SMTP_MAX_RETRIES):
            try:
                await asyncio.to_thread(self._send_smtp, reply_all_addrs, mime_msg)
                logger.info(
                    "[email] Sent reply to %s (session=%s, %d chars)",
                    reply_all_addrs,
                    session_id,
                    len(body_text),
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
                    f"failed for {reply_all_addrs}: {e}. Retrying in {backoff}s..."
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
                    body=body_text,
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
# Subject-line coalescing helpers
# =============================================================================

# Max age for subject-line coalescing: sessions older than this are ignored.
# Prevents accidentally merging unrelated threads from months ago.
COALESCE_MAX_AGE_SECONDS = 48 * 3600

# Regex for stripping leading reply/forward prefixes (case-insensitive, repeated).
_SUBJECT_PREFIX_RE = re.compile(
    r"^(?:Re|Fwd|Fw|Aw|AW|Antw|RE|FW|FWD)(?:\[\d+\])?\s*:\s*",
    re.IGNORECASE,
)

# Regex for stripping leading bracket ticket tags like [ticket-123]
_SUBJECT_TICKET_RE = re.compile(r"^\[[^\]]*\]\s*")


def normalize_subject(s: str) -> str:
    """Normalize an email subject for coalescing comparison.

    Strips leading Re/Fwd/AW/Antw prefixes (repeated), bracket ticket tags,
    collapses whitespace, and lowercases.

    Args:
        s: Raw subject string.

    Returns:
        Normalized subject string (lowercase, no reply prefix, no ticket tag).
    """
    s = s.strip()
    # Strip reply/forward prefixes repeatedly
    while True:
        stripped = _SUBJECT_PREFIX_RE.sub("", s)
        if stripped == s:
            break
        s = stripped
    # Strip bracket ticket tags
    s = _SUBJECT_TICKET_RE.sub("", s)
    # Collapse whitespace
    s = " ".join(s.split())
    return s.lower()


def _query_non_terminal_sessions(project_key: str) -> list:
    """Query AgentSession for non-terminal sessions in a project.

    Bounded to sessions created within the last COALESCE_MAX_AGE_SECONDS
    (48 hours) to prevent unbounded scans.

    Args:
        project_key: The project key to filter by.

    Returns:
        List of AgentSession objects.
    """
    from models.agent_session import AgentSession

    # C3 (#1817): AgentSession's 30-day TTL means a status-index ghost member
    # (hash expired, index membership survives) can in principle outlive its
    # backing session. query.filter() below already never returns such a
    # ghost as a live record (popoto silently drops empty hashes), so
    # subject-coalescing can never attach to a session that no longer exists
    # -- this call only accelerates removing the stale index entry instead of
    # waiting for the nightly popoto-index-cleanup sweep. Rate-limited
    # internally; see models/ghost_reconcile.py.
    from models.ghost_reconcile import reconcile_ghost_members
    from models.session_lifecycle import NON_TERMINAL_STATUSES

    reconcile_ghost_members(AgentSession)

    min_created_at = time.time() - COALESCE_MAX_AGE_SECONDS
    sessions = []
    for status in NON_TERMINAL_STATUSES:
        try:
            batch = list(AgentSession.query.filter(project_key=project_key, status=status))
            sessions.extend(batch)
        except Exception as e:
            logger.debug("[email] coalesce query failed for status=%s: %s", status, e)
    # Filter by age (Python-side, since Popoto may not support gte on float fields)
    sessions = [s for s in sessions if (getattr(s, "created_at", 0) or 0) >= min_created_at]
    return sessions


def find_coalescing_session_id(
    project_key: str,
    customer_id: str,
    normalized_subject: str,
) -> str | None:
    """Find an existing session to coalesce into by subject-line match.

    Scoped to (project_key, customer_id) to limit false-positive blast radius
    to a single customer's own recent correspondence.

    Applies a 48-hour age bound to prevent stale sessions from resurrecting.
    Empty normalized_subject never coalesces.

    Args:
        project_key: The project key.
        customer_id: The customer ID (from resolver).
        normalized_subject: The normalized inbound subject line.

    Returns:
        session_id of the most recently created matching session, or None.
    """
    if not normalized_subject or not normalized_subject.strip():
        return None

    try:
        sessions = _query_non_terminal_sessions(project_key)
    except Exception as e:
        logger.warning(f"[email] find_coalescing_session_id query failed: {e}")
        return None

    min_created_at = time.time() - COALESCE_MAX_AGE_SECONDS
    matching = []
    for s in sessions:
        extra = getattr(s, "extra_context", None) or {}
        if extra.get("customer_id") != customer_id:
            continue
        stored_subject = normalize_subject(extra.get("email_subject", ""))
        if stored_subject != normalized_subject:
            continue
        created = getattr(s, "created_at", 0) or 0
        if created < min_created_at:
            continue
        matching.append(s)

    if not matching:
        return None

    # Pick most recently created
    best = max(matching, key=lambda s: getattr(s, "created_at", 0) or 0)
    age_hrs = (time.time() - (getattr(best, "created_at", 0) or 0)) / 3600
    logger.info(
        f"[email] coalescing matched session={best.session_id} age={age_hrs:.1f}h limit=48h"
    )
    return best.session_id


# =============================================================================
# IMAP polling loop
# =============================================================================


def _unmark_seen_sync(imap_config: dict, uid: bytes) -> None:
    """Remove the \\Seen flag for a single message (sync, runs in a thread).

    The connection ``_fetch_unseen`` used to mark the message \\Seen is
    always closed (``conn.logout()``) before its caller returns, so a fresh
    short-lived connection is opened here scoped to one STORE command.
    Best-effort: any failure is logged by the caller, never raised, so an
    un-mark failure can never crash the poll loop (untrusted-input domain —
    infra errors must be classified and handled explicitly).
    """
    host = imap_config["host"]
    port = imap_config["port"]
    user = imap_config["user"]
    password = imap_config["password"]
    use_ssl = imap_config.get("ssl", True)
    if use_ssl:
        conn = imaplib.IMAP4_SSL(host, port, timeout=IMAP_SOCKET_TIMEOUT)
    else:
        conn = imaplib.IMAP4(host, port, timeout=IMAP_SOCKET_TIMEOUT)
    try:
        conn.login(user, password)
        conn.select("INBOX")
        conn.uid("store", uid, "-FLAGS", "\\Seen")
    finally:
        try:
            conn.logout()
        except Exception:  # noqa: S110 -- best-effort IMAP logout
            pass


async def _unmark_seen(imap_config: dict, uid: bytes) -> None:
    """Restore the \\Seen flag removal for ``uid`` so the next IMAP poll retries it.

    Called from the ``ResolverUnavailableError`` branch of ``_process_inbound_email``
    (issue #1817 A2) — a resolver outage must never permanently drop a
    customer email, and the message is already \\Seen by the time this runs
    (``_fetch_unseen`` marks it before fetch, as a concurrency guard against
    re-processing on overlapping polls). Failures are logged, not raised.
    """
    try:
        await asyncio.to_thread(_unmark_seen_sync, imap_config, uid)
    except Exception as e:
        logger.warning(f"[email] Failed to un-mark \\Seen for uid={uid!r}: {e}")


def _arm_resolver_unavailable_alert_if_persistent(project_key: str, message_id: str) -> None:
    """Arm the email:resolver_unavailable operator alert once the resolver has
    failed EMAIL_RESOLVER_ALERT_AFTER consecutive times for this project.

    Derives the threshold from the EXISTING resolver:failures:{project_key}
    counter (bridge/routing.py::_on_resolver_failure) rather than introducing
    a second, parallel tally (issue #1817 A2 builder note). The alert value
    is ``"{first_seen_ts}:{last_message_id}"`` — first_seen_ts is set once,
    on the poll that first crosses the threshold, and preserved on every
    subsequent re-arm so operators can see how long the outage has persisted;
    last_message_id is refreshed each time so operators can see the most
    recently stuck message. Cleared by bridge/routing.py::resolve_customer on
    the first successful resolve after the outage.
    """
    from bridge.routing import get_resolver_failure_count

    try:
        failures = get_resolver_failure_count(project_key)
        if failures < EMAIL_RESOLVER_ALERT_AFTER:
            return
        r = _get_redis()
        existing = r.get(REDIS_RESOLVER_UNAVAILABLE_KEY)
        if existing:
            if isinstance(existing, bytes):
                existing = existing.decode("utf-8", errors="replace")
            first_seen = existing.split(":", 1)[0]
        else:
            first_seen = str(time.time())
        r.set(REDIS_RESOLVER_UNAVAILABLE_KEY, f"{first_seen}:{message_id}")
        logger.critical(
            f"[email] Resolver persistently unavailable for project={project_key!r} "
            f"({failures} consecutive failures >= {EMAIL_RESOLVER_ALERT_AFTER}) — armed "
            f"{REDIS_RESOLVER_UNAVAILABLE_KEY} (first_seen={first_seen}, "
            f"last_msg_id={message_id!r})"
        )
    except Exception as e:
        logger.warning(f"[email] Failed to arm resolver_unavailable alert: {e}")


async def _process_inbound_email(
    parsed: dict,
    config: dict,
    imap_uid: bytes | None = None,
    imap_config: dict | None = None,
) -> None:
    """Process a single parsed inbound email.

    Resolves the sender to a project, optionally runs the customer_resolver to
    identify the sender, checks for thread continuation (In-Reply-To first, then
    subject-line coalescing), and enqueues an AgentSession.

    Args:
        parsed: Dict from parse_email_message() with keys:
                from_addr, subject, body, message_id, in_reply_to
        config: The loaded projects.json config dict.
        imap_uid: Optional IMAP UID bytes for the message. ``_fetch_unseen``
                  always marks a fetched message \\Seen before it reaches this
                  function (a concurrency guard against re-processing on
                  overlapping polls); if the customer resolver turns out to be
                  unavailable, this UID is used to un-mark \\Seen (see
                  ``_unmark_seen``) so the next poll retries the message
                  instead of silently losing it (issue #1817 A2).
        imap_config: Optional IMAP connection config (host/port/user/password/ssl),
                     required alongside ``imap_uid`` to un-mark \\Seen — the
                     connection ``_fetch_unseen`` used is always closed by the
                     time this function runs, so a fresh short-lived connection
                     is opened on demand.
    """
    from agent.agent_session_queue import enqueue_agent_session
    from agent.byob_skill_triggers import infer_requires_real_chrome
    from bridge.routing import (
        ACTIVE_PROJECTS,
        ResolverUnavailableError,
        find_project_for_email,
        resolve_customer,
    )
    from config.enums import SessionType

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

    # --- Dynamic customer resolver (optional) ---
    # If the project declares a customer_resolver, use it to identify the sender.
    # Projects without a resolver keep the existing static-allow-list flow unchanged.
    customer_id: str | None = None
    email_persona: str = project.get("email", {}).get("persona", "teammate")

    if project.get("customer_resolver"):
        try:
            customer_id = await resolve_customer(from_addr, project, imap_uid=imap_uid)
        except ResolverUnavailableError as e:
            # Infrastructure error (e.g. an expired OAuth token) — this is NOT
            # "not a customer." Leave the message unseen so the next IMAP poll
            # retries it, and track the outage for the operator alert
            # (issue #1817 A2).
            logger.warning(
                f"[email] Resolver unavailable for {from_addr!r} "
                f"(msg_id={message_id!r}, project={project_key}): {e}. "
                "Leaving message unseen for retry."
            )
            if imap_uid is not None and imap_config is not None:
                await _unmark_seen(imap_config, imap_uid)
            _arm_resolver_unavailable_alert_if_persistent(project_key, message_id)
            return
        if customer_id is None:
            # Resolver ran successfully and definitively found no match —
            # this IS "not a customer." Drop cleanly; the message stays \Seen.
            logger.info(
                f"[email] Resolver returned None for {from_addr!r}, discarding "
                f"(project={project_key})"
            )
            return
        # Customer resolved — force customer-service persona for this session
        email_persona = "customer-service"
        logger.info(
            f"[email] Resolver identified customer_id={customer_id!r} "
            f"for {from_addr!r} (project={project_key})"
        )

    # Derive session type from email.persona (default: teammate for human-facing email)
    # customer-service maps to TEAMMATE so it never orchestrates eng sessions
    _non_eng_personas = ("teammate", "customer-service")
    session_type = SessionType.TEAMMATE if email_persona in _non_eng_personas else SessionType.ENG

    working_dir = project.get("working_directory") or config.get("defaults", {}).get(
        "working_directory", "~/src"
    )

    # --- Session coalescing (precedence: In-Reply-To > subject-line > new) ---
    existing_session_id = None

    # (1) In-Reply-To: check Redis msgid map
    if in_reply_to:
        try:
            r = _get_redis()
            existing_session_id = r.get(f"email:msgid:{in_reply_to}")
        except Exception as e:
            logger.warning(f"[email] Redis lookup for In-Reply-To failed: {e}")

    # (2) Subject-line coalescing (only for customer-resolver sessions)
    if not existing_session_id and customer_id:
        normalized = normalize_subject(subject or "")
        if normalized:
            existing_session_id = find_coalescing_session_id(project_key, customer_id, normalized)
            if existing_session_id:
                logger.info(
                    f"[email] Subject-line coalescing: session={existing_session_id} "
                    f"subject={normalized!r}"
                )

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

    # --- Email customer-service triage layer (#1573) ---
    # For projects with both a customer_resolver and an email.customer_service
    # config block, run the two-tier triage pipeline inline. In shadow mode
    # (Phase 1 default) it classifies + writes an audit note but sends nothing
    # and does NOT short-circuit — the AgentSession spawn below remains the
    # operator path. Auto/draft lanes (Phase >= 2) short-circuit and we return
    # before enqueueing. The layer is inert (returns None) for any project
    # without the config, so existing behavior is unchanged. Fail-safe: any
    # exception here logs and falls through to the normal spawn.
    if customer_id is not None:
        try:
            from tools.email_cs.handler import handle_customer_email

            cs_outcome = await handle_customer_email(
                parsed, project, customer_id, session_id=session_id
            )
            if cs_outcome is not None and cs_outcome.short_circuit:
                logger.info(
                    f"[email] email-cs handled session {session_id} "
                    f"(disposition={cs_outcome.disposition.value}, "
                    f"category={cs_outcome.category.value}); skipping AgentSession spawn"
                )
                return
        except Exception as e:
            logger.error(
                f"[email] email-cs handler raised (non-fatal, falling through to AgentSession): {e}"
            )

    # BYOB scheduler-gate inference (#1274): scan body + subject for
    # registered triggers (e.g. "linkedin"). Email sessions go through a
    # separate enqueue path from telegram_bridge.py, so the same inference
    # hook is wired here independently. Fails closed -- exception-safe.
    _byob_text = f"{subject or ''}\n{body or ''}"
    _byob_real_chrome = infer_requires_real_chrome(_byob_text)
    if _byob_real_chrome:
        logger.info(f"[email] byob_inference_set_real_chrome session_id={session_id}")

    # Build extra_context — include customer_id when resolved
    extra_context: dict = {
        "transport": "email",
        "email_message_id": message_id,
        "email_from": from_addr,
        "email_to_addrs": parsed.get("to_addrs", []),
        "email_cc_addrs": parsed.get("cc_addrs", []),
        "email_subject": subject,
    }
    if customer_id is not None:
        extra_context["customer_id"] = customer_id

    # Inbound attachments: hand the agent readable on-disk paths (plus metadata)
    # so it can open the files. Only include attachments that were actually
    # persisted (path set by _persist_attachments); the read/fallback path never
    # populates this since it doesn't run through _process_inbound_email.
    _email_attachments = [
        _public_attachment(a) for a in (parsed.get("attachments") or []) if a.get("path")
    ]

    # Wedge guard: if the body references attachments but none (or only some) were
    # recovered, surface that explicitly so the agent can ask the sender to resend.
    # Inform-not-block policy (see #1775); full injection inspection deferred to #1630.
    if _body_references_attachments(body) and (
        not _email_attachments or parsed.get("attachments_truncated")
    ):
        # Inform the agent of unrecoverable/truncated attachments — agent uses context
        # to ask sender to resend.
        # Policy: inform-not-block (see #1775); full injection inspection deferred to #1630.
        extra_context["attachments_unrecoverable"] = True
        extra_context["attachments_truncated"] = bool(parsed.get("attachments_truncated"))
        extra_context["attachments_recovered_count"] = len(_email_attachments)
        # attachments_referenced (bool) signals body references attachments without a precise count.
        # A real referenced-count is deferred to when parse-time extraction is implemented (#1630).
        extra_context["attachments_referenced"] = True

    if _email_attachments:
        extra_context["email_attachments"] = _email_attachments

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
            session_type=session_type,
            requires_real_chrome=_byob_real_chrome,
            extra_context_overrides=extra_context,
        )
        logger.info(f"[email] Enqueued session {session_id} for {from_addr}")
    except Exception as e:
        logger.error(f"[email] Failed to enqueue session for {from_addr}: {e}")


def _build_imap_sender_query(known_senders: list[str]) -> str:
    """Build an IMAP search query that matches any of the known sender terms.

    Each term is either an exact address ("tom@yuda.me") or a domain token
    ("@psyoptimal.com"). IMAP FROM search does substring matching, so both
    work correctly.

    Returns an IMAP search string suitable for passing to conn.uid("search").
    For a single sender, returns 'FROM "addr"'. For multiple, returns a
    left-associative OR tree: '(OR (FROM "a") (OR (FROM "b") (FROM "c")))'.
    """
    if not known_senders:
        return ""
    if len(known_senders) == 1:
        return f'FROM "{known_senders[0]}"'
    # Build right-associative OR tree from the end
    result = f'(OR (FROM "{known_senders[-2]}") (FROM "{known_senders[-1]}"))'
    for term in reversed(known_senders[:-2]):
        result = f'(OR (FROM "{term}") {result})'
    return result


async def _poll_imap(imap_config: dict, known_senders: list[str]) -> list[tuple[bytes, bytes]]:
    """Connect to IMAP and fetch unseen messages from known senders only.

    Filters at the IMAP search level using FROM criteria built from
    known_senders so messages from unknown addresses are never fetched and
    remain UNSEEN for other machines polling the same inbox.

    Marks fetched messages as SEEN immediately to prevent duplicate processing
    across concurrent polls on this machine. The UID is returned alongside
    each message's raw bytes so a downstream resolver-unavailable outcome
    (issue #1817 A2) can un-mark \\Seen and let the next poll retry it,
    instead of the message being silently and permanently dropped.

    Returns a list of (uid, raw_message_bytes) tuples.
    """
    if not known_senders:
        return []

    host = imap_config["host"]
    port = imap_config["port"]
    user = imap_config["user"]
    password = imap_config["password"]
    use_ssl = imap_config.get("ssl", True)
    sender_query = _build_imap_sender_query(known_senders)

    def _fetch_unseen() -> list[tuple[bytes, bytes]]:
        if use_ssl:
            conn = imaplib.IMAP4_SSL(host, port, timeout=IMAP_SOCKET_TIMEOUT)
        else:
            conn = imaplib.IMAP4(host, port, timeout=IMAP_SOCKET_TIMEOUT)
        try:
            conn.login(user, password)
            conn.select("INBOX")

            # Search only for unseen messages from known senders (UIDs are stable)
            status, data = conn.uid("search", None, f"UNSEEN {sender_query}")
            if status != "OK" or not data or not data[0]:
                return []

            uids = data[0].split()
            if not uids:
                return []

            # Cap per-poll batch to avoid hanging on inboxes with thousands of unseen messages.
            # Take the most recent N (UIDs are ascending, so slice from the end).
            if len(uids) > IMAP_MAX_BATCH:
                uids = uids[-IMAP_MAX_BATCH:]

            messages: list[tuple[bytes, bytes]] = []
            for uid in uids:
                # Mark as SEEN before fetching to prevent re-processing on concurrent polls
                conn.uid("store", uid, "+FLAGS", "\\Seen")
                status, msg_data = conn.uid("fetch", uid, "(RFC822)")
                if status == "OK" and msg_data:
                    for response_part in msg_data:
                        if isinstance(response_part, tuple):
                            messages.append((uid, response_part[1]))
            return messages
        finally:
            try:
                conn.logout()
            except Exception:  # noqa: S110 -- best-effort IMAP logout
                pass

    return await asyncio.to_thread(_fetch_unseen)


def _is_permanent_imap_auth_error(err_text: str) -> bool:
    """True if an IMAP4.error message matches a known permanent-auth signature.

    Distinguishes a revoked/invalid credential (issue #1817 A3) — which will
    never self-resolve by retrying — from a transient network/server blip,
    which should keep the existing exponential backoff.
    """
    return any(sig in err_text for sig in IMAP_PERMANENT_AUTH_SIGNATURES)


def _set_auth_failed_alert(err_text: str) -> None:
    """Arm the email:auth_failed operator alert (A3). Best-effort."""
    try:
        r = _get_redis()
        r.set(REDIS_AUTH_FAILED_KEY, f"{time.time()}:{err_text[:200]}")
    except Exception as e:
        logger.warning(f"[email] Failed to set {REDIS_AUTH_FAILED_KEY} alert: {e}")


def _clear_auth_failed_alert() -> None:
    """Clear the email:auth_failed operator alert on a successful poll. Best-effort."""
    try:
        r = _get_redis()
        r.delete(REDIS_AUTH_FAILED_KEY)
    except Exception as e:
        logger.warning(f"[email] Failed to clear {REDIS_AUTH_FAILED_KEY} alert: {e}")


async def _email_inbox_loop(imap_config: dict, config: dict) -> None:
    """Main IMAP polling loop.

    Polls the IMAP inbox every IMAP_POLL_INTERVAL seconds. On each successful
    poll, updates email:last_poll_ts in Redis for health monitoring.

    Implements exponential backoff on connection failures (up to 5 minutes
    max) — EXCEPT a permanent IMAP auth failure (revoked app password,
    disabled account; issue #1817 A3), which arms the email:auth_failed
    operator alert and keeps retrying at the current (non-escalating)
    interval instead of backing off to 5 minutes: a revoked credential won't
    fix itself, and stretching the retry interval only delays detecting a
    manual fix.
    """
    from bridge.routing import get_known_email_search_terms

    backoff = IMAP_POLL_INTERVAL
    max_backoff = 300  # 5 minutes

    while True:
        try:
            # Re-read known senders each iteration so config reloads are reflected
            known_senders = get_known_email_search_terms()
            messages = await _poll_imap(imap_config, known_senders)

            # Update health timestamp
            try:
                r = _get_redis()
                r.set(REDIS_LAST_POLL_KEY, str(time.time()))
            except Exception as e:
                logger.warning(f"[email] Failed to update health timestamp: {e}")

            if messages:
                logger.info(f"[email] Fetched {len(messages)} unseen message(s)")
                for uid, raw_bytes in messages:
                    parsed = parse_email_message(raw_bytes)
                    if parsed is None:
                        continue
                    # Persist attachment bytes to disk + vault BEFORE recording
                    # history or enqueueing — this is the ONLY write side-effect
                    # path (the read-only CLI fallback never reaches here, so it
                    # never writes; critique C1). After this call each attachment
                    # dict has a real `path` and its transient `_payload` stripped.
                    if parsed.get("attachments"):
                        try:
                            _persist_attachments(parsed)
                        except Exception as e:
                            logger.warning(f"[email] Attachment persistence failed: {e}")
                    # Write-through to the history cache BEFORE enqueueing the
                    # AgentSession so the CLI can observe the message even when
                    # routing skips session creation. Failures are logged only.
                    _record_history(parsed)
                    _record_thread(parsed)
                    try:
                        await _process_inbound_email(
                            parsed, config, imap_uid=uid, imap_config=imap_config
                        )
                    except Exception as e:
                        logger.error(
                            f"[email] Error processing email from "
                            f"{parsed.get('from_addr', 'unknown')}: {e}"
                        )

            # Reset backoff on success — a successful poll also proves IMAP
            # auth is healthy again, so clear any armed auth_failed alert.
            backoff = IMAP_POLL_INTERVAL
            _clear_auth_failed_alert()

        except imaplib.IMAP4.error as e:
            err_text = str(e)
            if _is_permanent_imap_auth_error(err_text):
                _set_auth_failed_alert(err_text)
                logger.critical(
                    f"[email] Permanent IMAP auth failure: {e}. Armed "
                    f"{REDIS_AUTH_FAILED_KEY} operator alert. Retrying at the "
                    f"current interval ({backoff}s) — not backing off further, "
                    "since a revoked credential will not self-resolve."
                )
                # Deliberately do NOT double backoff (A3): an expired/revoked
                # credential needs a human to rotate it, and stretching the
                # retry interval toward max_backoff only delays detecting a
                # manual fix once it lands.
            elif "Too many simultaneous connections" in err_text:
                # Ghost connections from prior network drops linger on Gmail's side.
                # Jump straight to max backoff so they have time to expire.
                backoff = max_backoff
                logger.error(f"[email] IMAP error: {e}. Retrying in {backoff}s...")
            else:
                backoff = min(backoff * 2, max_backoff)
                logger.error(f"[email] IMAP error: {e}. Retrying in {backoff}s...")

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

    # Populate ACTIVE_PROJECTS (email bridge runs standalone, not via telegram_bridge)
    import bridge.routing as _routing_module

    if not _routing_module.ACTIVE_PROJECTS:
        from bridge.telegram_bridge import _get_active_projects

        _routing_module.ACTIVE_PROJECTS = _get_active_projects()
        logger.info(f"[email] Active projects: {_routing_module.ACTIVE_PROJECTS}")

    addr_map, domain_map = build_email_to_project_map(config)
    _routing_module.EMAIL_TO_PROJECT.update(addr_map)
    _routing_module.EMAIL_DOMAIN_TO_PROJECT.update(domain_map)

    logger.info(
        f"[email] Email bridge starting. "
        f"IMAP host={imap_config['host']}, poll interval={IMAP_POLL_INTERVAL}s, "
        f"contacts={len(_routing_module.EMAIL_TO_PROJECT)}, "
        f"domains={len(_routing_module.EMAIL_DOMAIN_TO_PROJECT)}"
    )

    # Run IMAP poll loop and the email outbox relay concurrently. The relay is
    # a cheap 100 ms polling loop and shares the same event loop so there is no
    # additional process to supervise. See docs/plans/valor-email-cli.md Task 2.
    from bridge.email_relay import run_email_relay

    await asyncio.gather(
        _email_inbox_loop(imap_config, config),
        run_email_relay(),
    )


def main() -> None:
    """Entry point for ``python -m bridge.email_bridge``."""
    import os
    import sys
    from pathlib import Path

    from dotenv import load_dotenv

    # Mirror telegram_bridge.py env loading: repo .env first, vault .env second.
    # Under launchd (VALOR_LAUNCHD=1), env vars are injected directly into the
    # plist by install_email_bridge.sh — skip dotenv entirely to avoid macOS
    # TCC hangs on the iCloud-synced ~/Desktop/Valor/.env that .env symlinks to.
    if not os.environ.get("VALOR_LAUNCHD"):
        load_dotenv(Path(__file__).parent.parent / ".env")
        load_dotenv(Path.home() / "Desktop" / "Valor" / ".env")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )

    asyncio.run(run_email_bridge())


if __name__ == "__main__":
    main()
