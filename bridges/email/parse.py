"""Raw mail bytes to the fields of an inbound record. Pure, except `persist`,
which writes attachments. No network.

Adapted from `bridge/email_bridge.py` on `main`.
"""

import base64
import binascii
import email
import email.header
import email.message
import email.utils
import hashlib
import mimetypes
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

_ID = re.compile(r"<[^<>\s]+>")


def decode_header_value(value: str | None) -> str:
    """An RFC 2047 header value as plain text."""
    if not value:
        return ""
    out = []
    for part, charset in email.header.decode_header(str(value)):
        if isinstance(part, bytes):
            try:
                out.append(part.decode(charset or "utf-8", errors="replace"))
            except LookupError:
                out.append(part.decode("utf-8", errors="replace"))
        else:
            out.append(part)
    return "".join(out).strip()


def extract_addresses(raw: str | None) -> list[str]:
    """Every address in a header value, lowercased."""
    if not raw:
        return []
    return [addr.lower().strip() for _, addr in email.utils.getaddresses([str(raw)]) if addr.strip()]


def _decode_text(part: email.message.Message) -> str:
    payload = part.get_payload(decode=True)
    if not payload:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


class _Text(HTMLParser):
    """HTML reduced to its text, `script` and `style` dropped."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        elif tag in ("br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_text(html: str) -> str:
    parser = _Text()
    parser.feed(html)
    parser.close()
    lines = (re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in "".join(parser.parts).split("\n"))
    return "\n".join(line for line in lines if line)


def is_attachment_part(part: email.message.Message) -> bool:
    """A leaf part with an `attachment` disposition or a filename."""
    if part.is_multipart():
        return False
    if "attachment" in str(part.get("Content-Disposition", "")).lower():
        return True
    try:
        return bool(part.get_filename())
    except Exception:  # noqa: BLE001  a malformed header is not an attachment name
        return False


def extract_body(msg: email.message.Message) -> str:
    """The first `text/plain` part that is not an attachment; else the first
    `text/html` part reduced to text; else empty."""
    parts = list(msg.walk()) if msg.is_multipart() else [msg]
    for part in parts:
        if part.get_content_type() == "text/plain" and not is_attachment_part(part):
            return _decode_text(part).strip()
    for part in parts:
        if part.get_content_type() == "text/html" and not is_attachment_part(part):
            return html_text(_decode_text(part))
    return ""


def sanitize_attachment_filename(raw: str | None, index: int, content_type: str) -> str:
    """A safe basename: directories dropped, only `[A-Za-z0-9._-]` kept,
    leading and trailing dots and underscores stripped."""
    base = Path(raw or "").name
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", base).strip("._")
    if not cleaned:
        ext = mimetypes.guess_extension(content_type or "") or ""
        cleaned = f"attachment_{index}{ext}"
    return cleaned


def _part_bytes(part: email.message.Message) -> bytes:
    """A part's decoded bytes; base64 is decoded strictly, so a part that
    does not decode raises instead of yielding garbage."""
    cte = str(part.get("Content-Transfer-Encoding", "")).strip().lower()
    if cte == "base64":
        raw = part.get_payload(decode=False)
        if not isinstance(raw, str):
            raise ValueError("a base64 part with no text payload")
        try:
            return base64.b64decode("".join(raw.split()), validate=True)
        except binascii.Error as e:
            raise ValueError(f"base64 does not decode: {e}") from None
    payload = part.get_payload(decode=True)
    if payload is None:
        raise ValueError("no decodable payload")
    return payload


@dataclass
class Parsed:
    """The fields of an `intake.Inbound` for one message, and the bytes of
    each attachment (None where it did not decode), in the same order."""

    fields: dict[str, Any]
    payloads: list[bytes | None] = field(default_factory=list)


def _ids(value: str | None) -> list[str]:
    return _ID.findall(re.sub(r"\r?\n[ \t]+", " ", str(value or "")))


def _sent_at(date: str | None, internaldate: datetime | None) -> str:
    when = None
    if date:
        try:
            when = email.utils.parsedate_to_datetime(str(date))
        except TypeError, ValueError, IndexError:
            when = None
    if when is not None and when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    when = when or internaldate or datetime.now(UTC)
    return when.astimezone(UTC).isoformat()


def parse_email_message(raw: bytes, internaldate: datetime | None = None) -> Parsed:
    """One message as an inbound record's fields. Empty mail and mail with
    no `From` are kept (the latter with an empty sender, so never owned or
    verified). A message with no `Message-ID` is given `sha256:<digest of
    its bytes>`. `headers` carries the raw `From` and
    `Authentication-Results` values as received, topmost first."""
    msg = email.message_from_bytes(raw)
    froms = [str(v) for v in msg.get_all("From") or []]
    auth = [str(v) for v in msg.get_all("Authentication-Results") or []]
    sender_name, sender = "", ""
    if froms:
        pairs = [(n, a) for n, a in email.utils.getaddresses([froms[0]]) if a]
        if pairs:
            sender_name, sender = decode_header_value(pairs[0][0]), pairs[0][1].lower().strip()
    own = _ids(msg.get("Message-ID"))
    message_id = own[0] if own else f"sha256:{hashlib.sha256(raw).hexdigest()}"
    references = _ids(msg.get("References"))
    in_reply_to = _ids(msg.get("In-Reply-To"))
    subject = decode_header_value(msg.get("Subject"))
    body = extract_body(msg)

    attachments: list[dict[str, Any]] = []
    payloads: list[bytes | None] = []
    for index, part in enumerate(p for p in msg.walk() if is_attachment_part(p)):
        mime = part.get_content_type() or "application/octet-stream"
        try:
            filename = part.get_filename()
        except Exception:  # noqa: BLE001  a malformed name falls back to a generated one
            filename = None
        name = sanitize_attachment_filename(decode_header_value(filename), index, mime)
        try:
            data = _part_bytes(part)
        except Exception as e:  # noqa: BLE001  a part that does not decode is listed as skipped
            attachments.append({"name": name, "mime": mime, "bytes": 0, "skipped": str(e)})
            payloads.append(None)
            continue
        attachments.append({"name": name, "mime": mime, "bytes": len(data)})
        payloads.append(data)

    fields = {
        "channel": "email",
        "chat_id": references[0] if references else message_id,
        "chat_kind": "email",
        "message_id": message_id,
        "sender_id": sender,
        "sender_name": sender_name,
        "sent_at": _sent_at(msg.get("Date"), internaldate),
        "kind": "message",
        "text": f"{subject}\n\n{body}",
        "reply_to": in_reply_to[0] if in_reply_to else None,
        "thread": [{"id": i} for i in references],
        "attachments": attachments,
        "headers": {
            "subject": subject,
            "date": str(msg.get("Date") or ""),
            "from": froms,
            "authentication_results": auth,
            "to": extract_addresses(", ".join(str(v) for v in msg.get_all("To") or [])),
            "cc": extract_addresses(", ".join(str(v) for v in msg.get_all("Cc") or [])),
        },
    }
    return Parsed(fields, payloads)


def persist(parsed: Parsed, directory: str | Path) -> None:
    """Write each decoded attachment under `directory`, named by the sha256
    of its bytes with its sanitized extension, and set its `path`. The
    same bytes land in the same file once."""
    directory = Path(directory)
    for att, data in zip(parsed.fields["attachments"], parsed.payloads, strict=True):
        if data is None:
            continue
        suffix = re.sub(r"[^A-Za-z0-9]", "", Path(att["name"]).suffix)[:16]
        target = directory / (hashlib.sha256(data).hexdigest() + (f".{suffix.lower()}" if suffix else ""))
        if not target.exists():
            directory.mkdir(parents=True, exist_ok=True)
            staged = target.with_name(f".{target.name}.{os.getpid()}")
            staged.write_bytes(data)
            os.replace(staged, target)
        att["path"] = str(target)
