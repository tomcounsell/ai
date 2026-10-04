"""The Telegram bridge's tests over the real port and the test database.

`chat()` gives a chat id no other test uses, since the test database
lasts the session and intake records each (chat, message id) once.
`machine` makes this machine own the chats a test names, the way the
operator's settings and a project spec do: the first is the operator
chat, the rest a project's. `connected` runs a bridge on the emulator
over `from_core(dsn)`, with its state files in the test's directory, so a
second `connected` there is a restart. `release` puts one approved send
in the ledger for the outbox to yield.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from bridges.telegram.bridge import TelegramBridge
from bridges.telegram.kernel import from_core
from core import broker, db, notices
from core.bridge import Release, bound_performers
from tests import bridges
from tests.telegram_emulator import EmulatorWire

SEND = "telegram.send_message"
_n = itertools.count(1)


def chat(kind: str = "supergroup") -> str:
    """A fresh chat id: negative for a group, positive for a DM."""
    n = next(_n)
    return str(-1_000_000_000_000 - n) if kind == "supergroup" else str(7_000_000 + n)


def emu_chat(id: str, **more: Any) -> dict:
    return {"id": int(id), "kind": "dm" if int(id) > 0 else "supergroup", **more}


def _spec(tmp_path: Path, chats: list[str]) -> Path:
    projects = tmp_path / "projects"
    projects.mkdir(exist_ok=True)
    if chats:
        listed = ", ".join(f'"telegram:{c}"' for c in chats)
        (projects / "tg.toml").write_text(
            f'name = "tg"\nrepo = "unused"\nkind = "plain"\nsuite = "true"\nchats = [{listed}]\n'
        )
    return projects


@contextlib.contextmanager
def machine(tmp_path: Path, chats: list[str]):
    _spec(tmp_path, chats[1:])
    with (
        bridges.operator(tmp_path),
        bridges.configure(operator_chat=chats[0], inbound_dir=str(tmp_path / "inbound")),
    ):
        yield


def child_env(tmp_path: Path, chats: list[str]) -> dict[str, str]:
    """The same machine, for a child process."""
    projects = _spec(tmp_path, chats[1:])
    return {
        **os.environ,
        "VALOR_MACHINE": "m-test",
        "VALOR_DEFAULT_MACHINE": "m-test",
        "VALOR_OPERATOR_CHAT": chats[0],
        "VALOR_PROJECTS": str(projects),
        "VALOR_INBOUND": str(tmp_path / "inbound"),
        "VALOR_SERVE_TICK_S": "0.2",
    }


def state(tmp_path: Path) -> dict[str, Path]:
    return {"seen": tmp_path / "telegram-seen.json", "sends": tmp_path / "telegram-sends.json"}


@asynccontextmanager
async def connected(url: str, dsn: str, tmp_path: Path, **kw):
    """A connected bridge on the emulator over the test database."""
    wire = EmulatorWire(url)
    bridge = TelegramBridge(wire, from_core(dsn), **state(tmp_path), **kw)
    await bridge.connect()
    try:
        yield bridge
    finally:
        for t in list(bridge._inflight.values()):
            t.cancel()
        await bridge.settled()
        await wire.close()


async def received(dsn: str, chat_id: str) -> list[dict[str, Any]]:
    async with await db.connect(dsn) as conn:
        rows = await (
            await conn.execute(
                "SELECT payload FROM events WHERE type = 'message.received' AND task_id = 'telegram' "
                "AND payload->>'chat_id' = %s ORDER BY id",
                (chat_id,),
            )
        ).fetchall()
    return [r[0] for r in rows]


async def ids(dsn: str, chat_id: str) -> list[str]:
    return [r["message_id"] for r in await received(dsn, chat_id)]


def send(target: str, text: str = "", **payload: Any) -> broker.Action:
    return broker.Action(
        SEND, target, {"text": text, "reply_to": None, "topic_id": None, "files": [], **payload}
    )


async def release(dsn: str, action: broker.Action, workspace: str | None = None) -> str:
    """A send held, approved by Tom, and requested of the bridge; the files
    it names are in `workspace`."""
    task = await bridges.new_task(dsn)
    async with await db.connect(dsn) as conn:
        held = await broker.request(conn, bridges.declared(workspace), task, action)
        await broker.approve(conn, held.effect_id, note="approve")
        out = await broker.release(conn, bridges.declared(workspace), held.effect_id)
    assert out.kind == "released", out
    return held.effect_id


async def reconcile(dsn: str, bridge, effect_id: str) -> broker.Outcome | None:
    async with await db.connect(dsn) as conn:
        return await broker.reconcile(conn, bound_performers(bridge, conn), effect_id)


async def outcome(dsn: str, effect_id: str) -> dict[str, Any] | None:
    found = await bridges.of_type(dsn, "effect.outcome", effect_id=effect_id)
    return found[0] if found else None


async def until(cond, timeout: float = 5.0) -> None:
    """Wait for `cond()` (a value or an awaitable) to hold."""
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        got = cond()
        if asyncio.iscoroutine(got):
            got = await got
        if got:
            return
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition did not hold in time")
        await asyncio.sleep(0.02)


async def perform(dsn: str, bridge, effect_id: str) -> broker.Outcome:
    """The outbox yields the release and performs it through the broker."""
    async with bridges.outbox(dsn, bridge) as box:
        (item,) = [i for i in await box.due() if isinstance(i, Release) and i.effect_id == effect_id]
        return await box.perform(item)


async def key_of(dsn: str, effect_id: str) -> str:
    (held,) = await bridges.of_type(dsn, "effect.held", effect_id=effect_id)
    return held["idempotency_key"]


async def notice(dsn: str, chat_id: str, text: str = "waiting on approval") -> tuple[str, str]:
    """One `notice.requested` row for a fresh task, to `chat_id`; (task, notice id)."""
    task = await bridges.new_task(dsn)
    async with await db.connect(dsn) as conn:
        notice_id = await notices.request(
            conn,
            task,
            kind="effect",
            about_key=f"about:{task}",
            text=text,
            channel="telegram",
            chat_id=chat_id,
        )
    return task, notice_id


async def due(box, wanted: str):
    """The outbox's due item for one effect or notice id."""
    (item,) = [
        i
        for i in await box.due()
        if getattr(i, "effect_id", None) == wanted or getattr(i, "notice_id", None) == wanted
    ]
    return item


class Only:
    """The outbox, yielding only the items a test made: the test database
    holds other tests' rows for the same channel."""

    def __init__(self, box, wanted: set[str]):
        self.box, self.wanted = box, wanted

    def __getattr__(self, name):
        return getattr(self.box, name)

    async def __aiter__(self):
        while True:
            for item in await self.box.due():
                if (
                    getattr(item, "effect_id", None) in self.wanted
                    or getattr(item, "notice_id", None) in self.wanted
                ):
                    yield item
            await asyncio.sleep(0.05)
