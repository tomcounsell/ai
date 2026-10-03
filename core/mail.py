"""Email in the kernel: the reply-all a turn's `email.send` may ask for, and
the message an `email.send` payload becomes.

`email_message` is the one builder: the email bridge's performer sends
what it builds, and `email_encoded_bytes` measures what it builds, so the
request-time size refusal measures the message that is sent. The bridge reaches both through `core.bridge`.
"""

import hashlib
import mimetypes
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path
from typing import Any

from core.settings import settings

# Both fixed in width, so a message built with them is as long as the one
# sent with the real Message-ID and date.
_PLACEHOLDER_DATE = datetime(2026, 1, 1, tzinfo=UTC)


def message_id(key: str, address: str) -> str:
    """The Message-ID of the send whose broker key is `key`: a retry of one
    effect repeats it, and two effects never share it."""
    domain = address.rsplit("@", 1)[-1] or "localhost"
    return f"<valor.{hashlib.sha256(key.encode()).hexdigest()[:32]}@{domain}>"


def email_message(
    payload: dict[str, Any],
    blobs: list[tuple[str, bytes]],
    *,
    message_id: str,
    sender: str,
    date: datetime,
) -> EmailMessage:
    """The MIME for an `email.send` payload: a UTF-8 `text/plain` body and
    one part per file, `blobs` holding each file's name and the bytes to
    send. The subject and `References` are the payload's, unchanged."""
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = ", ".join(payload["to"])
    if payload.get("cc"):
        msg["Cc"] = ", ".join(payload["cc"])
    msg["Subject"] = payload.get("subject") or ""
    msg["Date"] = format_datetime(date.astimezone(UTC))
    msg["Message-ID"] = message_id
    if payload.get("in_reply_to"):
        msg["In-Reply-To"] = payload["in_reply_to"]
    if payload.get("references"):
        msg["References"] = " ".join(payload["references"])
    msg.set_content(payload.get("body") or "", charset="utf-8")
    for name, data in blobs:
        ctype, encoding = mimetypes.guess_type(name)
        if ctype is None or encoding is not None:
            ctype = "application/octet-stream"
        maintype, _, subtype = ctype.partition("/")
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    return msg


def serialized(msg: EmailMessage) -> bytes:
    """The bytes `smtplib.send_message` puts on the wire before dot-stuffing."""
    import io
    from email.generator import BytesGenerator

    out = io.BytesIO()
    BytesGenerator(out).flatten(msg, linesep="\r\n")
    return out.getvalue()


def email_encoded_bytes(payload: dict[str, Any], address: str | None = None) -> int:
    """The length of the message `payload` sends from `address` (Valor's,
    by default), its files read as they are now: the size Gmail's 25 MB
    limit is on. Raises `OSError` for a file that cannot be read."""
    address = settings.email_address if address is None else address
    blobs = [(Path(f["path"]).name, Path(f["path"]).read_bytes()) for f in payload.get("files") or []]
    msg = email_message(
        payload, blobs, message_id=message_id("", address), sender=address, date=_PLACEHOLDER_DATE
    )
    return len(serialized(msg))


def _subject(subject: str) -> str:
    subject = (subject or "").strip()
    if not subject:
        return "Re: (no subject)"
    return subject if subject.lower().startswith("re:") else f"Re: {subject}"


def reply_all(row: dict[str, Any], own: str) -> dict[str, Any]:
    """The recipients and threading of a reply to all of a received email
    (`row`, a `message.received` payload). `to` is the sender; `cc` is every
    `To` and `Cc` address but Valor's own (`own`) and the sender's,
    lowercased, in order, without repeats."""
    headers = row.get("headers") or {}
    sender = (row.get("sender_id") or "").lower()
    own = own.lower()
    cc: list[str] = []
    for addr in [*(headers.get("to") or []), *(headers.get("cc") or [])]:
        addr = addr.lower()
        if addr and addr not in (own, sender) and addr not in cc:
            cc.append(addr)
    return {
        "to": [sender],
        "cc": cc,
        "subject": _subject(headers.get("subject") or ""),
        "in_reply_to": row["message_id"],
        "references": [t["id"] for t in row.get("thread") or []] + [row["message_id"]],
    }
