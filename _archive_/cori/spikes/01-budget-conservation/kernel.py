"""Minimal budget kernel for spike 01.

Two lock strategies are exposed so the spike can compare them:

- ``row``: SELECT ... FOR UPDATE on the parent node (what tech-stack section 3
  specifies). Requires UPDATE privilege on ``tree.nodes``.
- ``advisory``: pg_advisory_xact_lock keyed on the parent id. Needs no table
  privilege beyond SELECT and INSERT.
"""

from __future__ import annotations

import os

import psycopg

DSN = os.environ.get("CORI_SPIKE_DSN", "postgresql://127.0.0.1:5499/cori_spike_budget")


class BudgetExceeded(Exception):
    pass


def dsn_for(role: str) -> str:
    return DSN.replace("://", f"://{role}:kernel@")


REMAINING_SQL = """
SELECT n.budget - COALESCE(SUM(e.amount), 0)
FROM tree.nodes n LEFT JOIN tree.budget_events e ON e.node_id = n.id
WHERE n.id = %s
GROUP BY n.budget
"""


async def create_root(conn: psycopg.AsyncConnection, node_id: str, budget: int) -> None:
    async with conn.transaction():
        await conn.execute(
            "INSERT INTO tree.nodes (id, parent_id, budget) VALUES (%s, NULL, %s)",
            (node_id, budget),
        )


async def _lock_parent(conn: psycopg.AsyncConnection, parent: str, lock: str) -> None:
    if lock == "row":
        cur = await conn.execute(
            "SELECT id FROM tree.nodes WHERE id = %s FOR UPDATE", (parent,)
        )
        if await cur.fetchone() is None:
            raise KeyError(parent)
    elif lock == "advisory":
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (parent,))
    elif lock == "none":
        pass
    else:
        raise ValueError(lock)


async def delegate(
    conn: psycopg.AsyncConnection,
    parent: str,
    child: str,
    amount: int,
    lock: str = "advisory",
) -> int:
    """Allocate ``amount`` of ``parent``'s remaining budget to a new ``child``.

    Returns the parent's remaining budget after the allocation. Raises
    BudgetExceeded (and rolls back) if the parent cannot cover it.
    """
    async with conn.transaction():
        await _lock_parent(conn, parent, lock)
        cur = await conn.execute(REMAINING_SQL, (parent,))
        row = await cur.fetchone()
        if row is None:
            raise KeyError(parent)
        remaining = int(row[0])
        if amount > remaining:
            raise BudgetExceeded(f"{parent}: {amount} > {remaining}")
        await conn.execute(
            "INSERT INTO tree.nodes (id, parent_id, budget) VALUES (%s, %s, %s)",
            (child, parent, amount),
        )
        await conn.execute(
            "INSERT INTO tree.budget_events (kind, node_id, child_id, amount)"
            " VALUES ('allocate', %s, %s, %s)",
            (parent, child, amount),
        )
        return remaining - amount


async def consume(
    conn: psycopg.AsyncConnection, node: str, amount: int, lock: str = "advisory"
) -> int:
    """Record that ``node`` spent ``amount`` of its own remaining budget."""
    async with conn.transaction():
        await _lock_parent(conn, node, lock)
        cur = await conn.execute(REMAINING_SQL, (node,))
        row = await cur.fetchone()
        if row is None:
            raise KeyError(node)
        remaining = int(row[0])
        if amount > remaining:
            raise BudgetExceeded(f"{node}: {amount} > {remaining}")
        await conn.execute(
            "INSERT INTO tree.budget_events (kind, node_id, amount)"
            " VALUES ('consume', %s, %s)",
            (node, amount),
        )
        return remaining - amount


async def audit(conn: psycopg.AsyncConnection) -> list[tuple[str, int, int]]:
    """Every node whose ledger outflow exceeds its budget. Empty means conserved."""
    cur = await conn.execute("""
        SELECT n.id, n.budget, COALESCE(SUM(e.amount), 0) AS outflow
        FROM tree.nodes n LEFT JOIN tree.budget_events e ON e.node_id = n.id
        GROUP BY n.id, n.budget
        HAVING COALESCE(SUM(e.amount), 0) > n.budget
        """)
    return [(r[0], int(r[1]), int(r[2])) for r in await cur.fetchall()]
