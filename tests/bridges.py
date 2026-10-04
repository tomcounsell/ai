"""Fake bridges and an operator's settings, for the kernel and bridge tests.

`configure` sets settings fields for one test on the one shared `settings`
object every module imports, and puts them back after. `FakeBridge` is a
bridge with no platform: `perform` records the send under its key, and
`lookup` finds it there.
"""

import asyncio
import contextlib
from typing import Any

from core import broker, db, ledger, tasks
from core.bridge import Outbox, bound_performers, declared_performers
from core.settings import settings

OPERATOR = "42"
OPERATOR_CHAT = "1000"
OPERATOR_EMAIL = "tom@example.com"


@contextlib.contextmanager
def configure(**values: Any):
    old = {k: getattr(settings, k) for k in values}
    for k, v in values.items():
        object.__setattr__(settings, k, v)
    try:
        yield settings
    finally:
        for k, v in old.items():
            object.__setattr__(settings, k, v)


def operator(tmp_path, **more: Any):
    """This machine, the operator, and an empty projects directory."""
    projects = tmp_path / "projects"
    projects.mkdir(exist_ok=True)
    return configure(
        machine="m-test",
        default_machine="m-test",
        operator_telegram_id=OPERATOR,
        operator_email=(OPERATOR_EMAIL,),
        operator_channel="telegram",
        operator_chat=OPERATOR_CHAT,
        projects_dir=str(projects),
        work_dir=str(tmp_path / "work"),
        serve_tick_s=0.2,
        **more,
    )


class FakeBridge:
    def __init__(self, channel: str = "telegram"):
        self.channel = channel
        self.store: dict[str, dict[str, Any]] = {}
        self.performed: list[str] = []
        self.ticks = 0
        self.ticked = asyncio.Event()
        self.fail: Exception | None = None
        self.lookup_fail: Exception | None = None
        self.hang: asyncio.Event | None = None
        self.next_id = 500

    def performers(self):
        return {
            d.action_type: (self.perform, self.lookup)
            for d in declared_performers()
            if d.owner == self.channel
        }

    async def perform(self, action: broker.Action, key: str) -> dict[str, Any]:
        self.performed.append(key)
        self.next_id += 1
        result = {
            "sent": [{"channel": self.channel, "chat_id": action.target, "message_id": str(self.next_id)}]
        }
        self.store[key] = result
        if self.hang is not None:
            await self.hang.wait()
        if self.fail is not None:
            raise self.fail
        return result

    async def lookup(self, action: broker.Action, key: str, since: str) -> dict[str, Any] | None:
        if self.lookup_fail is not None:
            raise self.lookup_fail
        return self.store.get(key)

    async def run(self, outbox: Outbox) -> None:
        async for _ in outbox:
            return

    async def tick(self) -> None:
        self.ticks += 1
        self.ticked.set()


def declared() -> broker.Performers:
    return broker.Performers(*declared_performers())


async def new_task(dsn: str, **kw) -> str:
    async with await db.connect(dsn) as conn:
        return await tasks.start(conn, tasks.Brief(instruction="test", max_effect_class="act", **kw))


@contextlib.asynccontextmanager
async def outbox(dsn: str, bridge: FakeBridge):
    conn = await db.connect(dsn)
    perform_conn = await db.connect(dsn)
    listener = await db.connect(dsn)
    await listener.execute("LISTEN valor_events")
    box = Outbox(bridge, bound_performers(bridge, conn), conn, perform_conn, listener, dsn)
    try:
        yield box
    finally:
        for c in (box.listener, perform_conn, conn):
            await c.close()


async def rows(dsn: str, task_id: str) -> list[dict[str, Any]]:
    async with await db.connect(dsn) as conn:
        return await ledger.read(conn, task_id)


async def of_type(dsn: str, type_: str, **match: str) -> list[dict[str, Any]]:
    async with await db.connect(dsn) as conn:
        found = await (
            await conn.execute("SELECT task_id, payload FROM events WHERE type = %s ORDER BY id", (type_,))
        ).fetchall()
    return [{"task_id": t, **p} for t, p in found if all(str(p.get(k)) == v for k, v in match.items())]
