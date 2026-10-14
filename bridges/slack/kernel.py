"""The port, gathered from `core/` for the bridge.

The bridge reaches the kernel only through this one object. The tests
build it over the test database.
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
    ReactionDue: type
    Unknown: type
    Failed: type
    split_text: Any  # text -> posts within Slack's limit
    receive: Any
    recorded: Any
    lowest_ts: Any
    threads: Any
    root_of: Any
    claimed: Any
    owns: Any
    owned: Any
    conn: Any  # an async context manager giving one connection
    inbound_dir: str
    serve_tick_s: float


def from_core(dsn: str | None = None) -> Kernel:
    """The port; `dsn` names the database, the ledger when none."""
    from core import bridge, broker, db, intake
    from core.settings import settings

    @asynccontextmanager
    async def conn():
        c = await db.connect(dsn)
        try:
            yield c
        finally:
            await c.close()

    return Kernel(
        Inbound=intake.Inbound,
        Release=bridge.Release,
        NoticeDue=bridge.NoticeDue,
        ReactionDue=bridge.ReactionDue,
        Unknown=broker.Unknown,
        Failed=broker.Failed,
        split_text=lambda text: bridge.split_text("slack", text),
        receive=intake.receive,
        recorded=intake.recorded,
        lowest_ts=intake.lowest_ts,
        threads=intake.threads,
        root_of=intake.root_of,
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


def seen_path() -> Path:
    """Where each chat's next gap-fill pass starts."""
    return key_dir() / "slack-seen.json"


def keyfile() -> Path:
    """`SLACK_APP_TOKEN` and `SLACK_USER_TOKEN`, each under its team id."""
    return key_dir() / "slack-keys"
