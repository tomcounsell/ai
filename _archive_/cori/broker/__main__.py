"""`python -m broker reconcile`. Plan 07 task 8; tech stack §4.

The reconcile pass belongs at process start, before any replacement Brief
runs, and `kernel/__main__.py` (integration) is what calls it there. This
command is the same pass by hand: after a crash a person can see what the
ledger left open and what each target said, without starting the kernel.

One line per close, naming the kind, the action, and the key, so the output
reads as the list of effects whose state was in doubt and what became of
them. An `unknown` is the one kind that earns a card, and the card is
`kernel/__main__.py`'s to issue, never this command's.
"""

from __future__ import annotations

import asyncio
import os
import sys

import psycopg

import broker


def default_connect():
    """A `kernel_rw` connection to the local database, the way
    `kernel/runs.py` builds one."""
    host = os.environ.get("CORI_PGHOST", "localhost")
    port = os.environ.get("CORI_PGPORT", "5432")
    database = os.environ.get("CORI_PGDATABASE", "cori")
    return psycopg.AsyncConnection.connect(
        f"postgresql://kernel_rw@{host}:{port}/{database}"
    )


async def reconcile() -> int:
    async with await default_connect() as conn:
        closed = await broker.reconcile_dangling(conn)
    for outcome in closed:
        line = f"{outcome.kind:9} {outcome.effect_id}"
        if outcome.error:
            line = f"{line}  {outcome.error}"
        print(line)
    print(f"{len(closed)} dangling intent(s) closed")
    return 0


def main(argv: list[str]) -> int:
    if argv[:1] != ["reconcile"]:
        print(__doc__, file=sys.stderr)
        return 2
    return asyncio.run(reconcile())


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
