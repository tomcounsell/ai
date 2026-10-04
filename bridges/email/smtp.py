"""`email.send`: the performer and its Sent Mail lookup.

Adapted from `_build_reply_mime` and `_send_smtp` (`bridge/email_bridge.py`)
and `_send_smtp_sync` (`bridge/email_relay.py`) on `main`. One attempt per
perform, with no timer on any command: a command waits until the server
answers or a stop ends the connection (`stop.Ends`). A
send that errors before its end of data line has gone out, or that the
server answers with a 4xx or 5xx reply, is definite and raises
`SendRefused` (a `broker.Failed`): no lookup. A stop is not an error of
the send: it cancels the call, no outcome is written, and the effect
stays in flight for Sent Mail to settle. Once that line has gone,
anything that ends the send without a well-formed final reply is in doubt
and raises `broker.Unknown`, and the effect is settled from Sent Mail.
"""

import hashlib
import imaplib
import re
import smtplib
import ssl
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from core import bridge, broker

from .config import Config
from .stop import Ends

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class SendRefused(broker.Failed):
    """A send the server or the payload definitely refused; nothing was
    stored."""


def imap_date(day) -> str:
    """A date as IMAP's `SINCE` takes it, independent of the locale."""
    return f"{day.day}-{_MONTHS[day.month - 1]}-{day.year}"


def thread_root(payload: dict[str, Any], message_id: str) -> str:
    refs = payload.get("references") or []
    return refs[0] if refs else message_id


def _result(payload: dict[str, Any], message_id: str, **extra) -> dict[str, Any]:
    return {
        "message_id": message_id,
        **extra,
        "sent": [{"channel": "email", "chat_id": thread_root(payload, message_id), "message_id": message_id}],
    }


def _blobs(payload: dict[str, Any]) -> list[tuple[str, bytes]]:
    """Each file read once and hashed; the bytes hashed are the bytes sent."""
    blobs = []
    for f in payload.get("files") or []:
        path = Path(f["path"])
        try:
            data = path.read_bytes()
        except OSError as e:
            raise SendRefused(f"{path}: cannot be read ({e.strerror})") from None
        if hashlib.sha256(data).hexdigest() != f["sha256"]:
            raise SendRefused(f"{path}: its sha256 is not the one approved")
        blobs.append((path.name, data))
    return blobs


def _recipients(payload: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for addr in [*payload.get("to", []), *payload.get("cc", [])]:
        if addr and addr.lower() not in out:
            out.append(addr.lower())
    return out


def _dot_stuffed(data: bytes) -> bytes:
    """The body as DATA sends it, ending in CRLF, without the end of data
    line."""
    body = re.sub(rb"(?m)^\.", b"..", data)
    return body if body.endswith(b"\r\n") else body + b"\r\n"


END_OF_DATA = b".\r\n"


class _SMTP(smtplib.SMTP):
    """`smtplib.SMTP` whose socket is registered with `ends` as it opens,
    before the greeting is read."""

    def __init__(self, ends: Ends | None):
        self.ends = ends
        super().__init__()

    def _get_socket(self, host, port, timeout):
        sock = super()._get_socket(host, port, timeout)
        if self.ends is not None:
            self.ends.register(sock)
        return sock


def perform(cfg: Config, action: broker.Action, key: str, ends: Ends | None = None) -> dict[str, Any]:
    """Send `action` once, blocking. Returns `message_id`, `accepted`,
    `refused` (each refused address with the server's reply), and `sent`.

    The server accepts a message only on the end of data line (RFC 5321
    4.1.1.4: either it accepts with a 250 or it does not accept), so
    anything that ends the send before that line has gone out in full is a
    definite `SendRefused`, as is a 4xx or 5xx reply to it (4.2.1: the
    action did not occur). Once it has gone, an end with no reply, or any
    reply but a 250 or a refusal (a garbled line, which `smtplib` reads as
    code -1, or a line too long for it to read), leaves the send in doubt:
    `broker.Unknown`."""
    payload = action.payload
    message_id = bridge.email_message_id(key, cfg.address)
    smtp, ended = None, False
    try:
        try:
            blobs = _blobs(payload)
            msg = bridge.email_message(
                payload, blobs, message_id=message_id, sender=cfg.address, date=datetime.now(UTC)
            )
            data = bridge.email_serialized(msg)
            smtp = _SMTP(ends)
            smtp.connect(cfg.smtp_host, cfg.smtp_port)
            accepted, refused = _envelope(cfg, smtp, payload, data)
            smtp.sock.sendall(_dot_stuffed(data))
            smtp.sock.sendall(END_OF_DATA)
        except SendRefused:
            raise
        except Exception as e:
            raise SendRefused(
                f"the send of {message_id} ended before the end of data line went, so nothing was "
                f"accepted: {e!r}"
            ) from e
        try:
            code, reply = smtp.getreply()
        except (smtplib.SMTPException, OSError) as e:
            cause = e.__context__ or e
            raise broker.Unknown(
                f"the send of {message_id} is in doubt: the end of data line went and no reply was read: "
                f"{e} (from {cause!r})"
            ) from e
        text = reply.decode(errors="replace")
        if 400 <= code < 600:
            raise SendRefused(f"the message was refused: {code} {text}")
        if code != 250:
            raise broker.Unknown(
                f"the send of {message_id} is in doubt: the end of data line went and the reply is "
                f"neither 250 nor a refusal: {code} {text}"
            )
        ended = True
    finally:
        if smtp is not None:
            _close(smtp, ended)
    return _result(payload, message_id, accepted=accepted, refused=refused)


def _envelope(cfg: Config, smtp: smtplib.SMTP, payload: dict[str, Any], data: bytes):
    """EHLO, STARTTLS, AUTH, MAIL, every RCPT, and DATA up to its 354.
    Returns the accepted and refused recipients. Each waits for the
    server's reply with no timer."""
    smtp.ehlo()
    smtp.starttls(context=cfg.context())
    smtp.ehlo()
    smtp.login(cfg.smtp_user, cfg.smtp_password)
    options = [f"SIZE={len(data)}"] if smtp.has_extn("size") else []
    code, reply = smtp.mail(cfg.address, options)
    if code != 250:
        raise SendRefused(f"MAIL refused: {code} {reply.decode(errors='replace')}")
    accepted, refused = [], {}
    for rcpt in _recipients(payload):
        code, reply = smtp.rcpt(rcpt)
        if code in (250, 251):
            accepted.append(rcpt)
        else:
            refused[rcpt] = f"{code} {reply.decode(errors='replace')}"
    if not accepted:
        raise SendRefused(f"every recipient refused: {refused}")
    code, reply = smtp.docmd("DATA")
    if code != 354:
        raise SendRefused(f"DATA refused: {code} {reply.decode(errors='replace')}")
    return accepted, refused


def _close(smtp: smtplib.SMTP, ended: bool) -> None:
    """After a 250 the send is done: `QUIT` goes and its reply is not
    awaited. Otherwise the
    connection is dropped."""
    try:
        if ended:
            smtp.putcmd("quit")
    except OSError:
        pass
    smtp.close()


class _IMAP(imaplib.IMAP4_SSL):
    """`imaplib.IMAP4_SSL` whose socket is registered with `ends` as it
    opens, before the TLS handshake and the greeting."""

    def __init__(self, host: str, port: int, ssl_context, ends: Ends | None):
        self.ends = ends
        super().__init__(host, port, ssl_context=ssl_context)

    def _create_socket(self, timeout):
        sock = imaplib.IMAP4._create_socket(self, timeout)
        if self.ends is not None:
            self.ends.register(sock)
        return self.ssl_context.wrap_socket(sock, server_hostname=self.host)


def imap_connect(cfg: Config, ends: Ends | None = None) -> imaplib.IMAP4_SSL:
    conn = _IMAP(cfg.imap_host, cfg.imap_port, cfg.context(), ends)
    try:
        conn.login(cfg.imap_user, cfg.imap_password)
    except BaseException:
        conn.shutdown()
        raise
    return conn


def sent_folder(conn: imaplib.IMAP4) -> str:
    """The folder whose flags include `\\Sent`, from a plain `LIST`."""
    typ, data = conn.list('""', "*")
    if typ != "OK":
        raise broker.Unknown(f"LIST failed: {data}")
    for line in data:
        if not isinstance(line, bytes):
            continue
        m = re.match(rb'\((?P<flags>[^)]*)\) (?:"[^"]*"|NIL) (?P<name>.+)$', line)
        if m and b"\\sent" in m.group("flags").lower().split():
            name = m.group("name").decode()
            return name
    raise broker.Unknown("no folder is flagged \\Sent")


def lookup(
    cfg: Config, action: broker.Action, key: str, since: str, ends: Ends | None = None
) -> dict[str, Any]:
    """The send under `key`, found in Sent Mail by its exact Message-ID among
    messages dated from the day before `since` (the intent's time). Raises
    `broker.Unknown` when the mailbox cannot be read, and when the message
    is not there: Gmail may file an accepted message late, so a miss does
    not show the send failed, and the outbox asks again on its next wake."""
    message_id = bridge.email_message_id(key, cfg.address)
    day = datetime.fromisoformat(since).astimezone(UTC).date() - timedelta(days=1)
    try:
        conn = imap_connect(cfg, ends)
        try:
            folder = sent_folder(conn)
            typ, data = conn.select(folder, readonly=True)
            if typ != "OK":
                raise broker.Unknown(f"SELECT {folder} failed: {data}")
            if "X-GM-EXT-1" in conn.capabilities:
                typ, data = conn.uid("SEARCH", "X-GM-RAW", f'"rfc822msgid:{message_id} after:{day:%Y/%m/%d}"')
            else:
                typ, data = conn.uid(
                    "SEARCH", "SINCE", imap_date(day), "HEADER", "Message-ID", f'"{message_id}"'
                )
            if typ != "OK":
                raise broker.Unknown(f"SEARCH failed: {data}")
        finally:
            try:
                conn.logout()
            except imaplib.IMAP4.error, OSError:
                pass
    except (imaplib.IMAP4.error, OSError, ssl.SSLError) as e:
        raise broker.Unknown(f"Sent Mail could not be read: {e}") from e
    if not (data and data[0] and data[0].split()):
        raise broker.Unknown(f"{message_id} is not in {folder}")
    return _result(action.payload, message_id, found_in=folder)
