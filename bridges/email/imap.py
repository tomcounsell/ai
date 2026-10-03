"""The watch: owned senders' unseen mail in INBOX, received once each,
on one IMAP connection that waits in IDLE (RFC 2177) for new mail.

Adapted from `_poll_imap` and `_build_imap_sender_query`
(`bridge/email_bridge.py`) on `main`. Blocking IMAP calls run in
`asyncio.to_thread`; intake runs on the bridge's own connection.
"""

import asyncio
import email
import imaplib
import logging
import re
import socket
from datetime import UTC, datetime
from pathlib import Path

from core import intake
from core.settings import settings

from . import parse, smtp
from .config import Config

log = logging.getLogger("valor.email")

# RFC 3501 5.4: a server may log out a client idle for 30 minutes, so
# RFC 2177 has the client end IDLE and issue it again within 29.
IDLE_REISSUE_S = 29 * 60

_UID = re.compile(rb"UID (\d+)")
_SAFE = re.compile(r"[^A-Za-z0-9@._+-]")


def sender_query(addresses: list[str]) -> str:
    """An `OR` tree of `FROM` terms, one per address. Characters outside
    an address's alphabet are dropped, so no term breaks the query."""
    terms = [f'FROM "{_SAFE.sub("", a)}"' for a in addresses if _SAFE.sub("", a)]
    if not terms:
        raise ValueError("no owned addresses to search for")
    query = terms[-1]
    for term in reversed(terms[:-1]):
        query = f"OR {term} {query}"
    return query


def connect(cfg: Config) -> imaplib.IMAP4_SSL:
    return smtp.imap_connect(cfg)


def select_inbox(conn: imaplib.IMAP4) -> str:
    """`SELECT INBOX`; returns its UIDVALIDITY."""
    typ, data = conn.select("INBOX")
    if typ != "OK":
        raise imaplib.IMAP4.error(f"SELECT INBOX failed: {data}")
    typ, data = conn.response("UIDVALIDITY")
    return (data[0] or b"").decode() if data and data[0] else ""


def search(conn: imaplib.IMAP4, since, addresses: list[str]) -> list[bytes]:
    typ, data = conn.uid("SEARCH", "UNSEEN", "SINCE", smtp.imap_date(since), sender_query(addresses))
    if typ != "OK":
        raise imaplib.IMAP4.error(f"SEARCH failed: {data}")
    return sorted((data[0] or b"").split(), key=int)


def headers(conn: imaplib.IMAP4, uids: list[bytes]) -> dict[bytes, dict]:
    """`Message-ID`, sender, and thread root per UID, without the body."""
    if not uids:
        return {}
    typ, data = conn.uid(
        "FETCH", b",".join(uids), "(UID BODY.PEEK[HEADER.FIELDS (MESSAGE-ID FROM REFERENCES)])"
    )
    if typ != "OK":
        raise imaplib.IMAP4.error(f"FETCH headers failed: {data}")
    out = {}
    for item in data:
        if not isinstance(item, tuple):
            continue
        m = _UID.search(item[0])
        if not m:
            continue
        msg = email.message_from_bytes(item[1])
        senders = parse.extract_addresses(msg.get("From"))
        own = parse._ids(msg.get("Message-ID"))
        refs = parse._ids(msg.get("References"))
        out[m.group(1)] = {
            "sender": senders[0] if senders else "",
            "message_id": own[0] if own else None,
            "chat_id": refs[0] if refs else (own[0] if own else None),
        }
    return out


def fetch(conn: imaplib.IMAP4, uid: bytes) -> tuple[bytes, datetime | None]:
    """The whole message and its INTERNALDATE, leaving it unseen."""
    typ, data = conn.uid("FETCH", uid, "(UID INTERNALDATE BODY.PEEK[])")
    if typ != "OK":
        raise imaplib.IMAP4.error(f"FETCH {uid!r} failed: {data}")
    for item in data:
        if isinstance(item, tuple):
            when = imaplib.Internaldate2tuple(item[0])
            internal = datetime(*when[:6], tzinfo=UTC) if when else None
            return item[1], internal
    raise imaplib.IMAP4.error(f"FETCH {uid!r} returned no message")


def mark_seen(conn: imaplib.IMAP4, uid: bytes) -> None:
    typ, data = conn.uid("STORE", uid, "+FLAGS.SILENT", r"(\Seen)")
    if typ != "OK":
        raise imaplib.IMAP4.error(f"STORE {uid!r} failed: {data}")


def logout(conn: imaplib.IMAP4) -> None:
    try:
        conn.logout()
    except imaplib.IMAP4.error, OSError:
        pass


def idle(conn: imaplib.IMAP4) -> bool:
    """IDLE on the selected INBOX until the server reports a new message
    (`EXISTS`, True) or `IDLE_REISSUE_S` passes (False). The re-issue
    time is the only read bound while idling; the connection has none."""
    with conn.idle(duration=IDLE_REISSUE_S) as idler:
        for typ, data in idler:
            if typ == "BYE":
                raise imaplib.IMAP4.abort(f"the server ended the session: {data}")
            if typ == "EXISTS":
                return True
    return False


def drop(conn: imaplib.IMAP4) -> None:
    """Ends the connection, waking any call blocked on it in another
    thread before its buffers are closed."""
    try:
        conn.sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        conn.shutdown()
    except OSError:
        pass


def inbound(parsed: parse.Parsed, uid: bytes, uidvalidity: str):
    fields = dict(parsed.fields)
    fields["headers"] = {**fields["headers"], "uid": uid.decode(), "uidvalidity": uidvalidity}
    return intake.Inbound(**fields)


async def watch(cfg: Config, db, retry: asyncio.Event) -> None:
    """Receives what is unseen, then IDLEs until new mail or the re-issue
    time, then again, on one IMAP connection. A failure is one line in the
    log; the watch reconnects when `retry` is next set (the bridge's
    `tick`, on each outbox wake)."""
    while True:
        conn = None
        try:
            conn = await asyncio.to_thread(connect, cfg)
            while True:
                await poll(cfg, conn, db)
                await asyncio.to_thread(idle, conn)
        except Exception:
            log.exception("email watch failed; it reconnects on the next tick")
        finally:
            if conn is not None:
                drop(conn)
        retry.clear()
        await retry.wait()


async def poll(cfg: Config, conn: imaplib.IMAP4, db) -> int:
    """Receives INBOX's unseen mail from owned senders over `conn`, into
    the bridge's database connection `db`, and leaves INBOX selected.
    Returns how many messages were received. A failed connection raises;
    a message that fails is logged with its UID and left unseen."""
    uidvalidity = await asyncio.to_thread(select_inbox, conn)
    owned = list(intake.owned("email"))
    if not owned:
        return 0
    uids = await asyncio.to_thread(search, conn, cfg.since, owned)
    heads = await asyncio.to_thread(headers, conn, uids)
    mine = [u for u in uids if u in heads and intake.owns("email", heads[u]["sender"])]

    seen: set[bytes] = set()
    by_root: dict[str, list[bytes]] = {}
    for u in mine:
        if heads[u]["message_id"]:
            by_root.setdefault(heads[u]["chat_id"], []).append(u)
    for root, group in by_root.items():
        done = await intake.recorded(db, "email", root, [heads[u]["message_id"] for u in group])
        for u in group:
            if heads[u]["message_id"] in done:
                await asyncio.to_thread(mark_seen, conn, u)
                seen.add(u)

    received = 0
    directory = Path(settings.inbound_dir) / "email"
    for u in mine:
        if u in seen:
            continue
        try:
            raw, internal = await asyncio.to_thread(fetch, conn, u)
            parsed = parse.parse_email_message(raw, internal)
            parse.persist(parsed, directory)
            result = await intake.receive(db, inbound(parsed, u, uidvalidity))
        except imaplib.IMAP4.abort:
            raise
        except Exception:
            log.exception("email uid %s (uidvalidity %s) not received; left unseen", u.decode(), uidvalidity)
            continue
        await asyncio.to_thread(mark_seen, conn, u)
        received += 0 if getattr(result, "duplicate", False) else 1
    return received
