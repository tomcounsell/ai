"""Spike 01's bench in pytest form: sixteen tasks carve one root at once and
the ledger never over-allocates. Plan 02 task 6 and Properties."""

import asyncio
import random

import psycopg

import kernel.tree as tree
from schemas.budget import Budget
from schemas.ids import new_id
from tests.conftest import dsn, requires_postgres
from tests.tree_fakes import bind, contract, make_space

pytestmark = requires_postgres

ROOT = 20_000_000
TASKS = 16
ATTEMPTS = 60

AUDIT = """
WITH live AS (
    SELECT node_id,
           SUM(CASE WHEN kind = 'allocate' THEN usd_micros ELSE 0 END)
         - SUM(CASE WHEN kind = 'release' THEN usd_micros ELSE 0 END) AS allocated
    FROM budget_ledger WHERE node_id = %(root)s GROUP BY node_id
)
SELECT node_id, allocated FROM live WHERE allocated > %(budget)s
"""


async def carve(root, counts):
    rng = random.Random()
    async with await psycopg.AsyncConnection.connect(dsn("kernel_rw")) as conn:
        for _ in range(ATTEMPTS):
            amount = Budget(usd_micros=rng.randint(1, 100_000))
            try:
                await tree._allocate(conn, root, new_id(), amount)
                counts["ok"] += 1
            except tree.BudgetExceeded:
                counts["refused"] += 1
            await conn.commit()


async def test_sixteen_tasks_never_over_allocate(kernel, space, monkeypatch):
    bind(monkeypatch, {space: make_space(space)})
    root = await tree.open_objective(
        kernel, space=space, conversation_id="c1", contract=contract(budget=ROOT)
    )
    await kernel.commit()
    counts = {"ok": 0, "refused": 0}
    await asyncio.gather(*(carve(root, counts) for _ in range(TASKS)))
    assert counts["ok"] + counts["refused"] == TASKS * ATTEMPTS
    assert counts["ok"] > 0
    cur = await kernel.execute(
        "SELECT COALESCE(SUM(usd_micros), 0), COUNT(*) FROM budget_ledger "
        "WHERE node_id = %s AND kind = 'allocate'",
        (root,),
    )
    allocated, n = await cur.fetchone()
    assert n == counts["ok"]
    assert allocated <= ROOT
    assert (await tree.remaining(kernel, root)).usd_micros == ROOT - allocated
    cur = await kernel.execute(AUDIT, {"root": root, "budget": ROOT})
    assert await cur.fetchall() == []
    await kernel.commit()
