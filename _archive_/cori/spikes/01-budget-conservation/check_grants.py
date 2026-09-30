"""Prove what the kernel role can and cannot do, statement by statement."""

import asyncio

import psycopg

from kernel import dsn_for


async def attempt(conn, label, sql):
    try:
        async with conn.transaction():
            await conn.execute(sql)
        print(f"  ALLOWED  {label}")
        return True
    except psycopg.Error as e:
        msg = str(e).splitlines()[0]
        print(f"  refused  {label}: {msg}")
        return False


async def main():
    async with await psycopg.AsyncConnection.connect(dsn_for("cori_kernel")) as conn:
        print("role cori_kernel (SELECT + INSERT only):")
        await attempt(
            conn,
            "INSERT node",
            "INSERT INTO tree.nodes (id, parent_id, budget) VALUES ('g-root', NULL, 10)",
        )
        await attempt(
            conn,
            "INSERT event",
            "INSERT INTO tree.budget_events (kind, node_id, amount) VALUES ('consume','g-root',1)",
        )
        assert not await attempt(
            conn,
            "UPDATE node budget",
            "UPDATE tree.nodes SET budget = 999 WHERE id='g-root'",
        )
        assert not await attempt(
            conn, "UPDATE event amount", "UPDATE tree.budget_events SET amount = 0"
        )
        assert not await attempt(conn, "DELETE event", "DELETE FROM tree.budget_events")
        assert not await attempt(conn, "DELETE node", "DELETE FROM tree.nodes")
        assert not await attempt(conn, "TRUNCATE events", "TRUNCATE tree.budget_events")
        assert not await attempt(
            conn,
            "ALTER TABLE (drop trigger)",
            "DROP TRIGGER events_append_only ON tree.budget_events",
        )
        fu = await attempt(
            conn,
            "SELECT ... FOR UPDATE on node",
            "SELECT id FROM tree.nodes WHERE id='g-root' FOR UPDATE",
        )
        print(
            f"  -> SELECT FOR UPDATE under insert-only role: {'works' if fu else 'REFUSED'}"
        )

    async with await psycopg.AsyncConnection.connect(dsn_for("cori_kernel_fu")) as conn:
        print(
            "role cori_kernel_fu (SELECT + INSERT + UPDATE grant, trigger rejects UPDATE):"
        )
        await attempt(
            conn,
            "SELECT ... FOR UPDATE on node",
            "SELECT id FROM tree.nodes WHERE id='g-root' FOR UPDATE",
        )
        assert not await attempt(
            conn,
            "UPDATE node budget",
            "UPDATE tree.nodes SET budget = 999 WHERE id='g-root'",
        )
        assert not await attempt(
            conn, "UPDATE event amount", "UPDATE tree.budget_events SET amount = 0"
        )
        assert not await attempt(conn, "DELETE node", "DELETE FROM tree.nodes")


asyncio.run(main())
