"""The event table is append-only by grant (first lock) and by trigger
(second lock). Tech stack §3."""

import psycopg
import pytest

from tests.conftest import requires_postgres

pytestmark = requires_postgres


async def _refused(conn, sql, params=(), by=psycopg.errors.InsufficientPrivilege):
    """The grant raises InsufficientPrivilege; the trigger raises
    RaiseException. Which one fires says which lock was reached."""
    with pytest.raises(by):
        await conn.execute(sql, params)
    await conn.rollback()


async def test_kernel_can_insert_and_select(kernel, space):
    await kernel.execute(
        "INSERT INTO events (space_id, type, payload) VALUES (%s, 'hello', '{}')",
        (space,),
    )
    await kernel.commit()
    row = await (
        await kernel.execute("SELECT type FROM events WHERE space_id = %s", (space,))
    ).fetchone()
    assert row == ("hello",)


async def test_grant_refuses_update_delete_truncate_for_kernel(kernel, space):
    await kernel.execute(
        "INSERT INTO events (space_id, type) VALUES (%s, 'x')", (space,)
    )
    await kernel.commit()
    await _refused(kernel, "UPDATE events SET type = 'y' WHERE space_id = %s", (space,))
    await _refused(kernel, "DELETE FROM events WHERE space_id = %s", (space,))
    await _refused(kernel, "TRUNCATE events")


async def test_trigger_refuses_update_and_delete_even_for_owner(migrator, space):
    """The migrator has the grant, so only the trigger stands in the way."""
    await migrator.execute(
        "INSERT INTO events (space_id, type) VALUES (%s, 'x')", (space,)
    )
    await migrator.commit()
    by = psycopg.errors.RaiseException
    await _refused(
        migrator, "UPDATE events SET type = 'y' WHERE space_id = %s", (space,), by
    )
    await _refused(migrator, "DELETE FROM events WHERE space_id = %s", (space,), by)


async def test_owner_can_disable_trigger_to_destroy_a_space(migrator, space):
    """The one sanctioned path: a migration run by the migrator, inside one
    transaction, that disables the trigger, deletes the space's rows, and
    appends the tombstone."""
    await migrator.execute(
        "INSERT INTO events (space_id, type) VALUES (%s, 'x'), (%s, 'x')",
        (space, space),
    )
    await migrator.execute("ALTER TABLE events DISABLE TRIGGER events_append_only")
    cur = await migrator.execute("DELETE FROM events WHERE space_id = %s", (space,))
    deleted = cur.rowcount
    await migrator.execute("ALTER TABLE events ENABLE TRIGGER events_append_only")
    await migrator.execute(
        "INSERT INTO events (space_id, type, payload) "
        "VALUES (%s, 'space.destroyed', jsonb_build_object('events', %s))",
        (space, deleted),
    )
    await migrator.commit()
    rows = await (
        await migrator.execute("SELECT type FROM events WHERE space_id = %s", (space,))
    ).fetchall()
    assert deleted == 2 and rows == [("space.destroyed",)]
