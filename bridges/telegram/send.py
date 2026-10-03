"""`telegram.send_message`: perform, lookup, and the notice send.

A payload is `{text, reply_to, topic_id, files: [{path, sha256}]}`. Text
is split by the port's `split_text` (Telegram's limit, in UTF-16 code
units); files follow as documents. Each message
carries a `random_id` derived from the broker's key (which ends in the
effect id) and its position, so a repeat of one effect repeats its ids and
two identical effects differ.

What a failure means, as the broker reads it:
- a definite refusal (`FloodWait`, `Refused`, `NotConnected`) of the first
  message is raised as is: the broker asks `lookup`, finds nothing, and
  writes `failed`;
- a flood wait on a later message, once earlier ones are on screen, is
  waited out for exactly the seconds Telegram gives, and the send goes on;
- a send in doubt (`InDoubt`, `DuplicateRandomId`) raises `broker.Unknown`:
  no outcome is written and the outbox's reconcile settles it through
  `lookup`.

Before a send's first message, the chat's newest message id is recorded
in `telegram-sends.json` under the effect id (or `notice:<id>`). `lookup`
reads the chat's history above that id and keeps the account's own
messages: message ids within a chat only grow, so the send is there
whatever the Mac's clock and Telegram's dates say. When some of a send's
messages are on screen and the rest are not, `lookup` sends the rest,
each under its own `random_id`, so a message Telegram already holds is
refused as a duplicate rather than shown twice; the send then settles as
done. A send whose start record was lost with the file is
`Unknown`, never `done`: the chat's history cannot tell it from an earlier
identical message. A notice's text carries its own id, so a notice whose
record was lost is looked up over the whole chat. When finishing is refused, or a file of the send is gone, the send
settles as done with the messages that are on screen. A notice is finished
the same way.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bridges.telegram import inbound
from bridges.telegram.wire import (
    DuplicateRandomId,
    FloodWait,
    InDoubt,
    Msg,
    Refused,
    Wire,
    WireError,
)

log = logging.getLogger("valor.telegram")


def random_id(key: str, n: int) -> int:
    """A signed 64-bit id from SHA-256 of `key:n`; never zero."""
    value = int.from_bytes(hashlib.sha256(f"{key}:{n}".encode()).digest()[:8], "big", signed=True)
    return value or 1


class Flood:
    """A flood wait, held in memory for the process."""

    def __init__(self):
        self.until = 0.0

    def hit(self, seconds: float) -> None:
        self.until = max(self.until, time.monotonic() + seconds)

    def active(self) -> bool:
        return self.until > time.monotonic()

    async def wait(self) -> None:
        left = self.until - time.monotonic()
        if left > 0:
            await asyncio.sleep(left)


@dataclass(frozen=True)
class Expected:
    kind: str  # "text" or "file"
    text: str = ""
    name: str = ""
    size: int | None = None
    reply_to: str | None = None
    topic_id: str | None = None


def read_file(entry: dict[str, str]) -> tuple[str, bytes]:
    """The file's bytes, read once and hashed; raises before anything is
    sent when they are not the bytes Tom approved."""
    path = Path(entry["path"])
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != entry["sha256"]:
        raise ValueError(f"{path.name} changed after approval")
    return path.name, data


@dataclass(frozen=True)
class Part:
    kind: str  # "text" or "file"
    text: str = ""
    name: str = ""
    data: bytes = b""


class Sender:
    def __init__(self, wire: Wire, kernel, starts):
        self.wire = wire
        self.kernel = kernel
        # effect id or `notice:<id>` -> the newest message id in the chat
        # before the send's first message
        self.starts = starts
        self.split = kernel.split_text
        self.flood = Flood()
        self._attempts: dict[tuple[str, int], int] = {}
        self._logged: set[str] = set()

    # -- the performer -----------------------------------------------------

    async def perform(self, action, key: str) -> dict[str, Any]:
        chat = int(action.target)
        p = action.payload
        parts = self._parts(p.get("text") or "", p.get("files") or [])
        reply = int(p["reply_to"]) if p.get("reply_to") else None
        topic = int(p["topic_id"]) if p.get("topic_id") else None
        await self._start(effect_of(key), chat)
        sent = []
        try:
            for n, part in enumerate(parts):
                sent.append(
                    await self._put(
                        chat, part, random_id(key, n), reply if n == 0 else None, topic, wait_out=n > 0
                    )
                )
        except (InDoubt, DuplicateRandomId) as e:
            raise self.kernel.Unknown(f"send in doubt: {e}") from None
        return {"sent": sent}

    async def lookup(self, action, key: str, since: str) -> dict[str, Any] | None:
        """The send under `key`, found by message id; `since` is not read.
        None when no message of it is on screen; when some are, the rest
        are sent under their own `random_id`s and the whole is returned."""
        p = action.payload
        chat = int(action.target)
        reply, topic = p.get("reply_to"), p.get("topic_id")
        expected = self._expected(p.get("text") or "", p.get("files") or [], reply, topic)
        try:
            after = self._after(effect_of(key))
            if after is None:
                return None  # the record is written before the first message
            if after == LOST:
                # Only Telegram could say, and its history cannot tell this
                # send from an earlier identical message of the account's own.
                raise self.kernel.Unknown(f"the start of send {key} was lost with the file")
            found = await self._scan(chat, expected, after)
            if all(f is None for f in found):
                return None
            if all(f is not None for f in found):
                return {"sent": found}
            try:
                parts = self._parts(p.get("text") or "", p.get("files") or [])
            except (OSError, ValueError) as e:
                log.warning("send %s cannot be finished: %s", key, e)
                return {"sent": [f for f in found if f is not None]}
            return {"sent": await self._finish(chat, parts, found, key, reply, topic)}
        except WireError as e:
            if isinstance(e, FloodWait):
                self.flood.hit(e.seconds)
            raise self.kernel.Unknown(f"lookup could not read Telegram: {e}") from None

    async def _finish(self, chat, parts, found, key, reply, topic) -> list[dict[str, str]]:
        """Send the parts not on screen, each under its own `random_id`."""
        out = list(found)
        for n, part in enumerate(parts):
            if out[n] is not None:
                continue
            try:
                out[n] = await self._put(
                    chat,
                    part,
                    random_id(key, n),
                    int(reply) if (reply and n == 0) else None,
                    int(topic) if topic else None,
                    wait_out=False,
                )
            except (InDoubt, DuplicateRandomId) as e:
                raise self.kernel.Unknown(f"finishing the send is in doubt: {e}") from None
            except Refused as e:
                log.warning("send %s cannot be finished: %s", key, e)
                break
        return [o for o in out if o is not None]

    async def _put(self, chat, part: Part, rid: int, reply, topic, *, wait_out: bool) -> dict[str, str]:
        """One message. With `wait_out` (a later message of a send whose
        earlier ones are on screen) a flood wait is waited out for exactly
        Telegram's seconds and the message sent; without it the flood wait
        is raised."""
        while True:
            await self.flood.wait()
            try:
                if part.kind == "text":
                    mid = await self.wire.send_text(
                        chat, part.text, random_id=rid, reply_to=reply, topic_id=topic
                    )
                else:
                    mid = await self.wire.send_file(
                        chat, part.data, part.name, random_id=rid, reply_to=reply, topic_id=topic
                    )
                return self._entry(chat, mid)
            except FloodWait as e:
                self.flood.hit(e.seconds)
                if not wait_out:
                    raise

    def _parts(self, text: str, files: list[dict]) -> list[Part]:
        out = [Part("text", text=t) for t in self.split(text)]
        for f in files:
            name, data = read_file(f)
            out.append(Part("file", name=name, data=data))
        return out

    # -- notices -----------------------------------------------------------

    async def notice(self, item, outbox) -> None:
        """Send one due notice to the chat its row names, finishing what
        is already on screen; mark it sent through the outbox."""
        chat = int(item.chat_id)
        key = notice_key(item.notice_id)
        expected = self._expected(item.text, [], item.reply_to, None)
        try:
            after = self._after(key)
            if after is None:
                await self._start(key, chat)
                found = [None] * len(expected)
            else:
                found = await self._scan(chat, expected, max(after, 0))
            sent = await self._finish_notice(item, chat, key, expected, found)
        except self.kernel.Unknown as e:
            self._log_once(item.notice_id, f"notice {item.notice_id}: {e}")
            return
        except WireError as e:
            if isinstance(e, FloodWait):
                self.flood.hit(e.seconds)
            self._log_once(item.notice_id, f"notice {item.notice_id} not sent: {e}")
            return
        await outbox.sent(item, sent)
        self.starts.drop([key])

    async def _finish_notice(self, item, chat, key, expected, found) -> list[dict[str, str]]:
        """Send each part not on screen. A part Telegram holds under its
        `random_id` but that is not on screen goes again under a new id,
        so Tom sees it."""
        out = list(found)
        reply = int(item.reply_to) if item.reply_to else None
        for n, part in enumerate(self.split(item.text)):
            while out[n] is None:
                attempt = self._attempts.get((item.notice_id, n), 0)
                rid = random_id(key if attempt == 0 else f"{key}:{attempt}", n)
                try:
                    await self.flood.wait()
                    mid = await self.wire.send_text(
                        chat, part, random_id=rid, reply_to=reply if n == 0 else None, topic_id=None
                    )
                    out[n] = self._entry(chat, mid)
                except DuplicateRandomId:
                    again = await self._scan(chat, expected, max(self._after(key) or 0, 0))
                    if again[n] is None:
                        self._attempts[(item.notice_id, n)] = attempt + 1
                    out[n] = again[n]
                except InDoubt as e:
                    raise self.kernel.Unknown(f"send in doubt: {e}") from None
        for n in range(len(out)):
            self._attempts.pop((item.notice_id, n), None)
        return out

    # -- the records -------------------------------------------------------

    async def _start(self, key: str, chat: int) -> None:
        """Record the chat's newest message id before the send's first message."""
        if self.starts.get(key) is None:
            page = await self.wire.history(chat, limit=1)
            self.starts.set(key, page[0].id if page else 0)

    def _after(self, key: str) -> int | None:
        """The id a scan for `key` starts above; None when the send never
        began; `LOST` when its record went with the file."""
        after = self.starts.get(key)
        if after is None and self.starts.lost:
            return LOST
        return after

    def keep_only(self, in_flight: set[str]) -> None:
        """Drop the record of every send the ledger has settled. The
        records of sends still in flight, when the file was lost, are marked
        `LOST`."""
        if self.starts.lost:
            for key in in_flight:
                if self.starts.get(key) is None:
                    self.starts.set(key, LOST)
            self.starts.lost = False
        self.starts.drop([k for k in self.starts.names() if k not in in_flight])

    # -- scanning ----------------------------------------------------------

    def _expected(self, text: str, files: list[dict], reply_to, topic_id) -> list[Expected]:
        out = []
        reply = str(reply_to) if reply_to else None
        topic = str(topic_id) if topic_id else None
        for part in self.split(text):
            out.append(
                Expected("text", text=part.strip(), reply_to=reply if not out else None, topic_id=topic)
            )
        for f in files:
            path = Path(f["path"])
            size = path.stat().st_size if path.exists() else None
            out.append(
                Expected(
                    "file", name=path.name, size=size, reply_to=reply if not out else None, topic_id=topic
                )
            )
        return out

    async def _scan(self, chat: int, expected: list[Expected], after_id: int) -> list[dict[str, str] | None]:
        """For each expected part, the message that carries it among the
        account's own messages with ids above `after_id` and not already
        claimed by a recorded send, or None. Two matches for one part:
        `broker.Unknown`."""
        own = await self.wire.own(chat, after_id=after_id)
        async with self.kernel.conn() as conn:
            claimed = await self.kernel.claimed(conn, "telegram", str(chat))
        pool = [m for m in reversed(own) if str(m.id) not in claimed]  # oldest first
        found: list[dict[str, str] | None] = []
        for exp in expected:
            matches = [m for m in pool if _matches(m, exp)]
            if len(matches) > 1:
                raise self.kernel.Unknown(f"{len(matches)} messages in chat {chat} match one part")
            if matches:
                pool.remove(matches[0])
                found.append(self._entry(chat, matches[0].id))
            else:
                found.append(None)
        return found

    def _entry(self, chat: int, message_id: int) -> dict[str, str]:
        return {"channel": "telegram", "chat_id": str(chat), "message_id": str(message_id)}

    def _log_once(self, notice_id: str, text: str) -> None:
        if notice_id not in self._logged:
            self._logged.add(notice_id)
            log.warning(text)


LOST = -1  # a send's start record that was lost with the file


def effect_of(key: str) -> str:
    """The effect id a broker key ends in."""
    return key.rsplit(":", 1)[-1]


def notice_key(notice_id: str) -> str:
    return f"notice:{notice_id}"


def _matches(m: Msg, exp: Expected) -> bool:
    topic, reply = inbound.topic_and_reply(m)
    if topic != exp.topic_id:
        return False
    if exp.reply_to is not None and reply != exp.reply_to:
        return False
    if exp.reply_to is None and reply is not None:
        return False
    if exp.kind == "text":
        return m.media is None and m.text.strip() == exp.text
    return m.media is not None and m.media.name == exp.name and (exp.size is None or m.media.size == exp.size)
