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

import psycopg

from core import intake
from core.settings import settings

from . import parse, smtp
from .config import Config
from .stop import Ends

log = logging.getLogger("valor.email")

# RFC 3501 5.4: a server may log out a client idle for 30 minutes, so
# RFC 2177 has the client end IDLE and issue it again within 29.
IDLE_REISSUE_S = 29 * 60

# The kernel's route(4) messages for an interface or an address changing
# (`net/route.h`: RTM_IFINFO "iface going up/down etc.", RTM_NEWADDR and
# RTM_DELADDR "address being added to / removed from iface"). `route -n
# monitor` prints each as a line that begins with its name; the other
# messages it prints (a failed lookup, a route cached for one connection)
# are not a change of the path.
INTERFACE_CHANGES = ("RTM_IFINFO", "RTM_NEWADDR", "RTM_DELADDR")
ROUTE_MONITOR = ("route", "-n", "monitor")

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


def connect(cfg: Config, ends: Ends | None = None) -> imaplib.IMAP4_SSL:
    return smtp.imap_connect(cfg, ends)


def select_inbox(conn: imaplib.IMAP4) -> str:
    """`SELECT INBOX`; returns its UIDVALIDITY."""
    typ, data = conn.select("INBOX")
    if typ != "OK":
        raise imaplib.IMAP4.error(f"SELECT INBOX failed: {data}")
    typ, data = conn.response("UIDVALIDITY")
    # The count SELECT reports is what the search that follows reads.
    conn.untagged_responses.pop("EXISTS", None)
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


def idle(conn: imaplib.IMAP4, on_enter=None) -> bool:
    """IDLE on the selected INBOX until the server reports a new message
    (`EXISTS`, True) or `IDLE_REISSUE_S` passes (False). `on_enter` is
    called once the server has accepted IDLE. The connection has no read
    bound of its own: a path that dies is ended by the network change
    (`network_changes`) or by the `DONE` at the re-issue.

    Mail that arrived while the connection ran another command (a search,
    a fetch) was announced inside that command's responses, which IDLE
    never sees again: it is read first, and True is returned without
    idling so the caller searches again."""
    if conn.untagged_responses.pop("EXISTS", None):
        return True
    with conn.idle(duration=IDLE_REISSUE_S) as idler:
        if on_enter:
            on_enter()
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


def _open(cfg: Config, ends: Ends, opened: list) -> imaplib.IMAP4_SSL:
    """`connect`, the connection also kept in `opened`: a stop that lands as
    the thread returns drops the call's result, and `watch` still closes it."""
    opened.append(connect(cfg, ends))
    return opened[0]


async def _output(*command: str) -> str:
    """What `command` prints, or "" when it cannot run or fails."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
    except OSError:
        return ""
    out, _ = await proc.communicate()
    return out.decode(errors="replace") if proc.returncode == 0 else ""


async def path_state() -> str:
    """The interface that carries the default route (`route -n get default`)
    and what `ifconfig` reports for it: its status and addresses. "none"
    when there is no default route. The same text twice means the path the
    connection runs over did not change."""
    for line in (await _output("route", "-n", "get", "default")).splitlines():
        key, _, name = line.strip().partition(":")
        if key == "interface" and name.strip():
            return f"{name.strip()}\n{await _output('ifconfig', name.strip())}"
    return "none"


async def network_changes(changed: asyncio.Event, command=ROUTE_MONITOR, state=path_state) -> None:
    """Sets `changed` when the path the connection runs over changes. The
    kernel reports every interface or address change (`INTERFACE_CHANGES`,
    read from `route -n monitor`), including the many that do not touch the
    mail path (a container's bridge, `awdl0`, `utun`, a temporary IPv6
    address); on each, `state` (the default route's interface and its
    status and addresses) is read, and `changed` is set only when that
    differs from the last read. A path that drops with no reset sends the
    client nothing, so this is the signal that the connection may be dead.
    If the monitor cannot start or ends, one line is logged and the watch
    relies on the re-issue alone; it is not started again, since a monitor
    that ends at once would need a timer to keep from spinning."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
    except OSError:
        log.exception("email watch: the route monitor did not start; a dead path is found at the re-issue")
        return
    try:
        last = await state()
        async for line in proc.stdout:
            if line.decode(errors="replace").startswith(INTERFACE_CHANGES):
                now = await state()
                if now != last:
                    last = now
                    changed.set()
        log.warning("email watch: the route monitor ended; a dead path is found at the re-issue")
    finally:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()


async def _until_changed(work, ends: Ends, changed: asyncio.Event | None):
    """Awaits `work`; if `changed` is set first, the connection is ended so
    the call blocked on it returns, and `work` raises."""
    if changed is None:
        return await work
    task, waiter = asyncio.ensure_future(work), asyncio.ensure_future(changed.wait())
    try:
        await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
        if not task.done():
            ends.end()
        return await task
    finally:
        waiter.cancel()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def _session(cfg: Config, db, ends: Ends, opened: list, entered: list) -> None:
    """One connection: receive, then wait, for as long as it holds.
    `entered` gains an item each time the server accepts IDLE."""
    conn = await ends.call(_open, cfg, ends, opened)
    while True:
        await poll(cfg, conn, db, ends)
        await ends.call(idle, conn, lambda: entered.append(True))


async def watch(
    cfg: Config, db, retry: asyncio.Event, connect=None, changed: asyncio.Event | None = None
) -> None:
    """Receives what is unseen, then IDLEs until new mail or the re-issue
    time, then again, on one IMAP connection. A failure is one line in the
    log; the watch reconnects when `retry` is next set (the bridge's
    `tick`, on each outbox wake). Two events reconnect at once, since each
    says a working connection is gone: `changed` being set (the network
    changed, see `network_changes`), and a transport failure (`OSError`, an
    IMAP abort) after the server accepted IDLE. A server that refuses IDLE,
    ends the session as it begins, or fails before it idled waits for the
    tick. `db` is the database connection intake records into; when
    `connect` (a coroutine function) is given and `db` has dropped, the
    same wake replaces it with `connect()`, and the connection the watch
    made last is closed when it ends."""
    made = None
    by_event = False  # this connection replaces one the network change ended
    try:
        while True:
            ends, opened, entered = Ends(), [], []
            if connect is not None and (db is None or db.closed or db.broken):
                if made is not None:
                    await made.close()
                    made = None
                try:
                    made = db = await connect()
                except psycopg.OperationalError:
                    log.exception("email watch: the database is not reachable; it tries on the next tick")
                    retry.clear()
                    await retry.wait()
                    continue
            if changed is not None:
                changed.clear()

            again = False
            try:
                await _until_changed(_session(cfg, db, ends, opened, entered), ends, changed)
            except Exception as e:
                if changed is not None and changed.is_set():
                    # A reconnect the network started that fails before IDLE
                    # waits for the tick, whatever else changes meanwhile.
                    again = bool(entered) or not by_event
                    log.warning(
                        "email watch: the network changed; %s",
                        "it reconnects at once" if again else "it reconnects on the next tick",
                    )
                else:
                    log.exception("email watch failed")
                    again = bool(entered) and isinstance(e, OSError | imaplib.IMAP4.abort)
                by_event = again and changed is not None and changed.is_set()
            finally:
                ends.end()
                for c in opened:
                    drop(c)
                ends.close()
            if again:
                continue
            by_event = False
            retry.clear()
            await retry.wait()
    finally:
        if made is not None:
            await made.close()


async def _thread(ends: Ends | None, fn, *args):
    """`fn(*args)` in a worker thread; with `ends`, a stop ends it."""
    return await (ends.call(fn, *args) if ends else asyncio.to_thread(fn, *args))


async def poll(cfg: Config, conn: imaplib.IMAP4, db, ends: Ends | None = None) -> int:
    """Receives INBOX's unseen mail from owned senders over `conn`, into
    the bridge's database connection `db`, and leaves INBOX selected.
    Returns how many messages were received. A failed connection raises;
    a message that fails is logged with its UID and left unseen."""
    uidvalidity = await _thread(ends, select_inbox, conn)
    owned = list(intake.owned("email"))
    if not owned:
        return 0
    uids = await _thread(ends, search, conn, cfg.since, owned)
    heads = await _thread(ends, headers, conn, uids)
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
                await _thread(ends, mark_seen, conn, u)
                seen.add(u)

    received = 0
    directory = Path(settings.inbound_dir) / "email"
    for u in mine:
        if u in seen:
            continue
        try:
            raw, internal = await _thread(ends, fetch, conn, u)
            parsed = parse.parse_email_message(raw, internal)
            parse.persist(parsed, directory)
            result = await intake.receive(db, inbound(parsed, u, uidvalidity))
        except imaplib.IMAP4.abort, psycopg.OperationalError:
            raise  # the IMAP connection or the database's is gone; the watch replaces it
        except Exception:
            log.exception("email uid %s (uidvalidity %s) not received; left unseen", u.decode(), uidvalidity)
            continue
        await _thread(ends, mark_seen, conn, u)
        received += 0 if getattr(result, "duplicate", False) else 1
    return received
