"""A stand-in for the port, for the bridge's tests while `core/bridge.py`
and `core/intake.py` are 2.1's to build.

`StandIn` holds received records, sent entries, and outcomes, in memory or
in a JSON file (so a record outlives a killed child process). `kernel()`
returns the `bridges.telegram.kernel.Kernel` shape over it. `Outbox`
yields what a test queues and runs a release the way the port describes:
perform; on an error ask lookup with the intent's `at`; `Unknown` leaves
the effect in flight for `reconcile`.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from bridges.telegram.kernel import Kernel
from core.broker import Unknown

MAX_TEXT = 4096  # Telegram's message length, in UTF-16 code units


@dataclass(frozen=True)
class Inbound:
    channel: str
    chat_id: str
    chat_kind: str
    message_id: str
    sender_id: str
    sender_name: str
    sent_at: str
    kind: str = "message"
    text: str = ""
    reply_to: str | None = None
    thread: list[dict] = field(default_factory=list)
    topic_id: str | None = None
    attachments: list[dict] = field(default_factory=list)
    headers: dict[str, str | list[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class Received:
    received_id: str
    duplicate: bool


@dataclass(frozen=True)
class Release:
    effect_id: str
    at: str


@dataclass(frozen=True)
class NoticeDue:
    notice_id: str
    task_id: str
    chat_id: str
    text: str
    reply_to: str | None
    at: str


@dataclass(frozen=True)
class Action:
    action_type: str
    target: str
    payload: dict[str, Any]


def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def split_text(text: str, limit: int = MAX_TEXT) -> list[str]:
    """Parts of at most `limit` UTF-16 units, at the last newline, else
    space, before the limit; whitespace-only parts dropped."""
    parts = []
    while utf16_len(text) > limit:
        units, cut = 0, len(text)
        for i, ch in enumerate(text):
            units += 2 if ord(ch) > 0xFFFF else 1
            if units > limit:
                cut = i
                break
        window = text[:cut]
        at = window.rfind("\n")
        if at <= 0:
            at = window.rfind(" ")
        if at > 0:
            cut = at + 1
        parts.append(text[:cut])
        text = text[cut:]
    parts.append(text)
    return [p for p in parts if p.strip()]


class StandIn:
    def __init__(self, path: Path | None = None, owned: list[str] | None = None, inbound_dir: str = "/tmp"):
        self.path = path
        self.owned_chats = list(owned or [])
        self.inbound_dir = inbound_dir
        self.received: list[dict] = []
        self.claimed_ids: dict[str, list[str]] = {}
        self.fail_receive = 0
        self.receive_calls = 0
        if path and path.exists():
            self.received = json.loads(path.read_text())["received"]

    def _save(self) -> None:
        if self.path:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"received": self.received}))
            tmp.replace(self.path)

    def claim(self, chat: str, ids: list[str]) -> None:
        self.claimed_ids.setdefault(chat, []).extend(ids)

    def ids(self, chat: str) -> list[str]:
        return [r["message_id"] for r in self.received if r["chat_id"] == chat]

    async def receive(self, conn, inbound: Inbound) -> Received:
        self.receive_calls += 1
        if self.fail_receive:
            self.fail_receive -= 1
            raise RuntimeError("the database is down")
        for r in self.received:
            if (r["channel"], r["chat_id"], r["message_id"]) == (
                inbound.channel,
                inbound.chat_id,
                inbound.message_id,
            ):
                return Received(r["received_id"], True)
        rid = f"r{len(self.received) + 1}"
        self.received.append({"received_id": rid, "verified": True, **asdict(inbound)})
        self._save()
        return Received(rid, False)

    async def highest(self, conn, channel: str, chat_id: str) -> int | None:
        ids = [int(i) for i in self.ids(chat_id)]
        return max(ids) if ids else None

    async def recorded(self, conn, channel: str, chat_id: str, ids: list[str]) -> set[str]:
        return set(ids) & set(self.ids(chat_id))

    async def claimed(self, conn, channel: str, chat_id: str) -> set[str]:
        return set(self.claimed_ids.get(chat_id, []))

    def owns(self, channel: str, id: str) -> bool:
        return id in self.owned_chats

    def owned(self, channel: str) -> list[str]:
        return list(self.owned_chats)

    def kernel(self, serve_tick_s: float = 60) -> Kernel:
        @asynccontextmanager
        async def conn():
            yield self

        return Kernel(
            Inbound=Inbound,
            Release=Release,
            NoticeDue=NoticeDue,
            Unknown=Unknown,
            split_text=split_text,
            receive=self.receive,
            recorded=self.recorded,
            highest=self.highest,
            claimed=self.claimed,
            owns=self.owns,
            owned=self.owned,
            conn=conn,
            inbound_dir=self.inbound_dir,
            serve_tick_s=serve_tick_s,
        )


class Outbox:
    """Yields queued items; `perform` runs one release as the port does."""

    def __init__(self, store: StandIn, bridge):
        self.store = store
        self.perform_fn, self.lookup_fn = bridge.performers()["telegram.send_message"]
        self.queue: asyncio.Queue = asyncio.Queue()
        self.effects: dict[str, tuple[Action, str]] = {}
        self.outcomes: dict[str, tuple[str, Any]] = {}
        self.in_flight: set[str] = set()
        self.notices: dict[str, list[dict]] = {}

    def release(self, effect_id: str, action: Action, at: str) -> Release:
        self.effects[effect_id] = (action, at)
        item = Release(effect_id, at)
        self.queue.put_nowait(item)
        return item

    def notice(self, item: NoticeDue) -> None:
        self.queue.put_nowait(item)

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.queue.get()

    def key(self, effect_id: str) -> str:
        return f"task-1:{effect_id}"

    async def perform(self, item: Release):
        action, at = self.effects[item.effect_id]
        key = self.key(item.effect_id)
        self.in_flight.add(item.effect_id)
        try:
            result = await self.perform_fn(action, key)
        except Unknown:
            return None
        except Exception as e:  # noqa: BLE001 - the port asks lookup on any other error
            try:
                found = await self.lookup_fn(action, key, at)
            except Unknown:
                return None
            return self._settle(item.effect_id, action, ("done", found) if found else ("failed", str(e)))
        return self._settle(item.effect_id, action, ("done", result))

    async def reconcile(self) -> None:
        for effect_id in sorted(self.in_flight):
            action, at = self.effects[effect_id]
            try:
                found = await self.lookup_fn(action, self.key(effect_id), at)
            except Unknown:
                continue
            self._settle(effect_id, action, ("done", found) if found else ("failed", "not sent"))

    def _settle(self, effect_id: str, action: Action, outcome):
        self.in_flight.discard(effect_id)
        self.outcomes[effect_id] = outcome
        if outcome[0] == "done":
            self.store.claim(action.target, [s["message_id"] for s in outcome[1]["sent"]])
        return outcome

    async def sent(self, item: NoticeDue, sent: list[dict]) -> None:
        self.notices[item.notice_id] = sent
        self.store.claim(item.chat_id, [s["message_id"] for s in sent])


async def until(cond, timeout: float = 5.0) -> None:
    """Wait for `cond()` to hold, for live updates that arrive on their own."""
    deadline = asyncio.get_running_loop().time() + timeout
    while not cond():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition did not hold in time")
        await asyncio.sleep(0.02)


@asynccontextmanager
async def connected(
    url: str, chats: list[str], inbound_dir: str = "/tmp", store: StandIn | None = None, **kw
):
    """A bridge on the emulator over a stand-in store, fresh unless given."""
    from bridges.telegram.bridge import TelegramBridge
    from tests.telegram_emulator import EmulatorWire

    store = store or StandIn(owned=chats, inbound_dir=inbound_dir)
    wire = EmulatorWire(url)
    bridge = TelegramBridge(wire, store.kernel(), **kw)
    await bridge.connect()
    try:
        yield bridge, store
    finally:
        await wire.close()
