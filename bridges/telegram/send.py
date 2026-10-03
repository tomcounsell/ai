"""`telegram.send_message`: perform, lookup, and the notice send.

A payload is `{text, reply_to, topic_id, files: [{path, sha256}]}`. Text
is split by the port's `split_text` (Telegram's limit, in UTF-16 code
units); files follow as documents. Each message
carries a `random_id` derived from the broker's key (which ends in the
effect id) and its position, so a repeat of one effect repeats its ids and
two identical effects differ.

What a failure means, as the broker reads it:
- a definite refusal (`FloodWait`, `Refused`, `NotConnected`) is raised as
  is: the broker asks `lookup`, finds nothing, and writes `failed`;
- a send in doubt (`InDoubt`, `DuplicateRandomId`) raises `broker.Unknown`:
  no outcome is written and the outbox's reconcile settles it through
  `lookup`.

Before a key's first send, the chat's newest message id is recorded in
`telegram-sends.json`. `lookup` scans the account's own messages above
that id: message ids within a chat only grow, so the send is there
whatever the Mac's clock and Telegram's dates say.
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


class Sender:
    def __init__(self, wire: Wire, kernel, starts):
        self.wire = wire
        self.kernel = kernel
        # key -> [chat, the newest message id in it before the key's first send]
        self.starts = starts
        self.split = kernel.split_text
        self.flood = Flood()
        self._attempts: dict[str, int] = {}
        self._logged: set[str] = set()

    # -- the performer -----------------------------------------------------

    async def perform(self, action, key: str) -> dict[str, Any]:
        chat = int(action.target)
        p = action.payload
        files = [read_file(f) for f in p.get("files") or []]
        reply = int(p["reply_to"]) if p.get("reply_to") else None
        topic = int(p["topic_id"]) if p.get("topic_id") else None
        sent = []
        n = 0
        try:
            await self._start(key, chat)
            for part in self.split(p.get("text") or ""):
                await self.flood.wait()
                mid = await self.wire.send_text(
                    chat,
                    part,
                    random_id=random_id(key, n),
                    reply_to=reply if n == 0 else None,
                    topic_id=topic,
                )
                sent.append(self._entry(chat, mid))
                n += 1
            for name, data in files:
                await self.flood.wait()
                mid = await self.wire.send_file(
                    chat,
                    data,
                    name,
                    random_id=random_id(key, n),
                    reply_to=reply if n == 0 else None,
                    topic_id=topic,
                )
                sent.append(self._entry(chat, mid))
                n += 1
        except FloodWait as e:
            self.flood.hit(e.seconds)
            raise
        except (InDoubt, DuplicateRandomId) as e:
            raise self.kernel.Unknown(f"send in doubt: {e}") from None
        return {"sent": sent}

    async def lookup(self, action, key: str, since: str) -> dict[str, Any] | None:
        """The send under `key`, found by message id. `since` is not
        needed: ids within a chat only grow, so the scan starts above the
        newest id recorded before the key's first send, whatever the
        clocks say. No record: nothing was sent under the key, because
        the record is written before the first send."""
        p = action.payload
        expected = self._expected(
            p.get("text") or "", p.get("files") or [], p.get("reply_to"), p.get("topic_id")
        )
        try:
            return await self._scan_key(key, expected)
        except WireError as e:
            raise self.kernel.Unknown(f"lookup could not read Telegram: {e}") from None

    # -- notices -----------------------------------------------------------

    async def notice(self, item, outbox) -> None:
        """Send one due notice to the chat its row names, unless it is
        already on screen; mark it sent through the outbox."""
        chat = int(item.chat_id)
        reply = item.reply_to
        expected = self._expected(item.text, [], reply, None)
        try:
            found = await self._scan_key(self._notice_key(item), expected)
            if found is None:
                found = await self._send_notice(item, chat, reply, expected)
        except self.kernel.Unknown as e:
            self._log_once(item.notice_id, f"notice {item.notice_id}: {e}")
            return
        except WireError as e:
            if isinstance(e, FloodWait):
                self.flood.hit(e.seconds)
            self._log_once(item.notice_id, f"notice {item.notice_id} not sent: {e}")
            return
        await outbox.sent(item, found["sent"])
        self._attempts.pop(item.notice_id, None)

    def _notice_key(self, item) -> str:
        return f"notice:{item.notice_id}"

    async def _send_notice(self, item, chat: int, reply, expected) -> dict[str, Any]:
        await self._start(self._notice_key(item), chat)
        while True:
            attempt = self._attempts.get(item.notice_id, 0)
            key = f"notice:{item.notice_id}" if attempt == 0 else f"notice:{item.notice_id}:{attempt}"
            sent = []
            try:
                for n, part in enumerate(self.split(item.text)):
                    await self.flood.wait()
                    mid = await self.wire.send_text(
                        chat,
                        part,
                        random_id=random_id(key, n),
                        reply_to=int(reply) if (reply and n == 0) else None,
                        topic_id=None,
                    )
                    sent.append(self._entry(chat, mid))
            except DuplicateRandomId:
                found = await self._scan_key(self._notice_key(item), expected)
                if found is not None:
                    return found
                # Telegram holds the id but the notice is not on screen: send
                # it again under a new id, so Tom sees it.
                self._attempts[item.notice_id] = attempt + 1
                continue
            except InDoubt as e:
                raise self.kernel.Unknown(f"send in doubt: {e}") from None
            return {"sent": sent}

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

    async def _start(self, key: str, chat: int) -> None:
        """Record the chat's newest message id before the key's first send."""
        if self.starts.get(key) is None:
            page = await self.wire.history(chat, limit=1)
            self.starts.set(key, [chat, page[0].id if page else 0])

    async def _scan_key(self, key: str, expected: list[Expected]) -> dict[str, Any] | None:
        start = self.starts.get(key)
        if start is None:
            return None
        chat, after_id = start
        return await self._scan(chat, expected, after_id)

    async def _scan(self, chat: int, expected: list[Expected], after_id: int) -> dict[str, Any] | None:
        """The messages that carry `expected`, found among the account's own
        messages with ids above `after_id` and not already claimed by a
        recorded send. One match each: found. None at all: None. Two
        matches for one, or some found and some not: `broker.Unknown`."""
        own = await self.wire.own(chat, after_id=after_id)
        async with self.kernel.conn() as conn:
            claimed = await self.kernel.claimed(conn, "telegram", str(chat))
        pool = [m for m in reversed(own) if str(m.id) not in claimed]  # oldest first
        sent: list[dict[str, str] | None] = []
        for exp in expected:
            matches = [m for m in pool if _matches(m, exp)]
            if len(matches) > 1:
                raise self.kernel.Unknown(f"{len(matches)} messages in chat {chat} match one part")
            if matches:
                pool.remove(matches[0])
                sent.append(self._entry(chat, matches[0].id))
            else:
                sent.append(None)
        if all(s is None for s in sent):
            return None
        if any(s is None for s in sent):
            raise self.kernel.Unknown(f"only some parts of the send are in chat {chat}")
        return {"sent": sent}

    def _entry(self, chat: int, message_id: int) -> dict[str, str]:
        return {"channel": "telegram", "chat_id": str(chat), "message_id": str(message_id)}

    def _log_once(self, notice_id: str, text: str) -> None:
        if notice_id not in self._logged:
            self._logged.add(notice_id)
            log.warning(text)


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
