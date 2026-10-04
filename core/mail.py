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


def _base64_bytes(n: int) -> int:
    """The length of `n` bytes as an attachment body: base64 in lines of 76
    characters, each ending CRLF."""
    full, rest = divmod(n, 57)
    return full * 78 + (-(-rest // 3) * 4 + 2 if rest else 0)


def email_encoded_bytes(payload: dict[str, Any], sizes: list[int], address: str | None = None) -> int:
    """The length of the message `payload` sends from `address` (Valor's,
    by default), the size Gmail's 25 MB limit is on. `sizes` holds each
    file's size, taken by the kernel without reading it: the message is
    built with empty files and each one's base64 body added."""
    address = settings.email_address if address is None else address
    names = [Path(f["path"]).name for f in payload.get("files") or []]
    msg = email_message(
        payload,
        [(name, b"") for name in names],
        message_id=message_id("", address),
        sender=address,
        date=_PLACEHOLDER_DATE,
    )
    return len(serialized(msg)) + sum(_base64_bytes(n) - _base64_bytes(0) for n in sizes)


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
