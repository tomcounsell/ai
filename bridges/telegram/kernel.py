"""The port, gathered from `core/` for the bridge.

The bridge reaches the kernel only through this one object, built from the
modules item 30 allows (`core.bridge`, `core.intake`, `core.broker`,
`core.settings`, `core.db`, `core.credentials`). The tests build the same shape over their own
store.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Kernel:
    Inbound: type
    Release: type
    NoticeDue: type
    Unknown: type
    split_text: Any  # text -> parts within Telegram's limit
    receive: Any
    recorded: Any
    highest: Any
    claimed: Any
    owns: Any
    owned: Any
    conn: Any  # an async context manager giving one connection
    inbound_dir: str
    serve_tick_s: float


def from_core() -> Kernel:
    from core import bridge, broker, db, intake
    from core.settings import settings

    @asynccontextmanager
    async def conn():
        c = await db.connect()
        try:
            yield c
        finally:
            await c.close()

    return Kernel(
        Inbound=intake.Inbound,
        Release=bridge.Release,
        NoticeDue=bridge.NoticeDue,
        Unknown=broker.Unknown,
        split_text=lambda text: bridge.split_text("telegram", text),
        receive=intake.receive,
        recorded=intake.recorded,
        highest=intake.highest,
        claimed=intake.claimed,
        owns=intake.owns,
        owned=intake.owned,
        conn=conn,
        inbound_dir=str(Path(settings.inbound_dir).expanduser()),
        serve_tick_s=settings.serve_tick_s,
    )


def key_dir() -> Path:
    """The kernel key directory: the one holding the database password
    file, which every sandbox profile denies."""
    from core.settings import settings

    return Path(settings.pg_passfile).expanduser().parent


def session_path() -> Path:
    return key_dir() / "telegram.session"


def seen_path() -> Path:
    """The newest message id each chat's last gap-fill pass saw."""
    return key_dir() / "telegram-seen.json"


def keyfile() -> Path:
    return key_dir() / "telegram-keys"
