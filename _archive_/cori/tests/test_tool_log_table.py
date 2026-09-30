"""The `tool_log` table carries the locks of migration 0001 and the two
uniqueness rules of seams §5.2. Plan 05 task 3."""

import uuid

import psycopg
import pytest

from tests.conftest import requires_postgres

pytestmark = requires_postgres

TABLE = "tool_log"


async def insert(conn, space, brief, *, seq=1, event="tool.start", generation=1):
    await conn.execute(
        f"INSERT INTO {TABLE} (brief_id, generation, space_id, seq, event, tool) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (brief, generation, space, seq, event, None if event == "terminal" else "bash"),
    )


async def privileges(conn, role: str) -> set[str]:
    rows = await (
        await conn.execute(
            "SELECT privilege_type FROM information_schema.role_table_grants "
            "WHERE table_name = %s AND grantee = %s",
            (TABLE, role),
        )
    ).fetchall()
    return {r[0] for r in rows}


async def test_kernel_rw_holds_exactly_insert_and_select(migrator):
    assert await privileges(migrator, "kernel_rw") == {"INSERT", "SELECT"}


async def test_context_ro_holds_exactly_select(migrator):
    assert await privileges(migrator, "context_ro") == {"SELECT"}


async def test_kernel_rw_cannot_update_delete_or_truncate(kernel, space):
    brief = uuid.uuid4().hex
    await insert(kernel, space, brief)
    await kernel.commit()
    for sql in (
        f"UPDATE {TABLE} SET seq = 9 WHERE space_id = %s",
        f"DELETE FROM {TABLE} WHERE space_id = %s",
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await kernel.execute(sql, (space,))
        await kernel.rollback()
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        await kernel.execute(f"TRUNCATE {TABLE}")
    await kernel.rollback()


async def test_trigger_refuses_the_owner_too(kernel, migrator, space):
    brief = uuid.uuid4().hex
    await insert(kernel, space, brief)
    await kernel.commit()
    for sql in (
        f"UPDATE {TABLE} SET seq = 9 WHERE space_id = %s",
        f"DELETE FROM {TABLE} WHERE space_id = %s",
    ):
        with pytest.raises(psycopg.errors.RaiseException) as info:
            await migrator.execute(sql, (space,))
        assert f"{TABLE} is append-only" in str(info.value)
        await migrator.rollback()
    with pytest.raises(psycopg.errors.RaiseException) as info:
        await migrator.execute(f"TRUNCATE {TABLE}")
    assert "append-only" in str(info.value)
    await migrator.rollback()


async def test_duplicate_brief_seq_event_is_refused(kernel, space):
    brief = uuid.uuid4().hex
    await insert(kernel, space, brief, seq=1, event="tool.start")
    await insert(kernel, space, brief, seq=1, event="tool.end")
    await kernel.commit()
    with pytest.raises(psycopg.errors.UniqueViolation):
        await insert(kernel, space, brief, seq=1, event="tool.start")
    await kernel.rollback()


async def test_second_terminal_for_one_generation_is_refused_by_the_index(
    kernel, space
):
    brief = uuid.uuid4().hex
    await insert(kernel, space, brief, seq=None, event="terminal", generation=1)
    await kernel.commit()
    with pytest.raises(psycopg.errors.UniqueViolation):
        await insert(kernel, space, brief, seq=None, event="terminal", generation=1)
    await kernel.rollback()
    # The next generation of the same Brief has its own terminal.
    await insert(kernel, space, brief, seq=None, event="terminal", generation=2)
    await kernel.commit()


async def test_event_outside_the_five_is_refused(kernel, space):
    with pytest.raises(psycopg.errors.CheckViolation):
        await insert(kernel, space, uuid.uuid4().hex, event="tool.begin")
    await kernel.rollback()


async def test_context_ro_reads_one_space_with_a_token(kernel, context, space):
    other = space + "-other"
    await insert(kernel, space, uuid.uuid4().hex)
    await insert(kernel, other, uuid.uuid4().hex)
    token = uuid.uuid4().hex
    await kernel.execute(
        "INSERT INTO read_tokens VALUES (%s, %s, now() + interval '1 minute')",
        (token, space),
    )
    await kernel.commit()
    await context.execute("SELECT set_config('cori.read_token', %s, false)", (token,))
    rows = await (
        await context.execute(
            f"SELECT DISTINCT space_id FROM {TABLE} WHERE space_id IN (%s, %s)",
            (space, other),
        )
    ).fetchall()
    assert rows == [(space,)]


async def test_context_ro_reads_nothing_without_a_token_and_cannot_insert(
    kernel, context, space
):
    await insert(kernel, space, uuid.uuid4().hex)
    await kernel.commit()
    row = await (
        await context.execute(
            f"SELECT count(*) FROM {TABLE} WHERE space_id = %s", (space,)
        )
    ).fetchone()
    assert row[0] == 0
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        await insert(context, space, uuid.uuid4().hex)
    await context.rollback()
