"""The poll: owned senders' unseen mail in INBOX, received once each.

Adapted from `_poll_imap` and `_build_imap_sender_query`
(`bridge/email_bridge.py`) on `main`. Blocking IMAP calls run in
`asyncio.to_thread`; intake runs on the bridge's own connection.
"""

import asyncio
import email
import imaplib
import logging
import re
from datetime import UTC, datetime
from pathlib import Path

from core import intake
from core.settings import settings

from . import parse, smtp
from .config import Config

log = logging.getLogger("valor.email")

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


def inbound(parsed: parse.Parsed, uid: bytes, uidvalidity: str):
    fields = dict(parsed.fields)
    fields["headers"] = {**fields["headers"], "uid": uid.decode(), "uidvalidity": uidvalidity}
    return intake.Inbound(**fields)


async def poll(cfg: Config, db) -> int:
    """One poll on the bridge's connection `db`. Returns how many messages
    were received. A failed login or connection raises; a message that
    fails is logged with its UID and left unseen."""
    owned = list(intake.owned("email"))
    if not owned:
        return 0
    conn = await asyncio.to_thread(connect, cfg)
    try:
        uidvalidity = await asyncio.to_thread(select_inbox, conn)
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
                log.exception(
                    "email uid %s (uidvalidity %s) not received; left unseen", u.decode(), uidvalidity
                )
                continue
            await asyncio.to_thread(mark_seen, conn, u)
            received += 0 if getattr(result, "duplicate", False) else 1
        return received
    finally:
        await asyncio.to_thread(logout, conn)
