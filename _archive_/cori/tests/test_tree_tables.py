"""The tree's four tables carry the locks of migration 0001: grants, the
append-only trigger, and RLS keyed to a read token. Plan 02 task 4."""

import uuid

import psycopg
import pytest

from tests.conftest import requires_postgres

pytestmark = requires_postgres

TABLES = ("objectives", "objective_revisions", "briefs", "budget_ledger")


async def seed(conn, space: str) -> dict[str, str]:
    """One row per table for `space`; returns the ids written."""
    oid, bid = uuid.uuid4().hex, uuid.uuid4().hex
    await conn.execute(
        "INSERT INTO objectives (id, parent_id, space_id, conversation_id, depth) "
        "VALUES (%s, NULL, %s, 'c1', 0)",
        (oid, space),
    )
    await conn.execute(
        "INSERT INTO objective_revisions (space_id, objective_id, revision, contract) "
        "VALUES (%s, %s, 1, '{}'::jsonb)",
        (space, oid),
    )
    await conn.execute(
        "INSERT INTO briefs (id, space_id, objective_id, agent_class, generation, "
        "brief, gateway_token_sha256, issued_at) "
        "VALUES (%s, %s, %s, 'Executor', 1, '{}'::jsonb, 'ab', now())",
        (bid, space, oid),
    )
    await conn.execute(
        "INSERT INTO budget_ledger (space_id, node_id, brief_id, kind, usd_micros) "
        "VALUES (%s, %s, %s, 'allocate', 5)",
        (space, oid, bid),
    )
    return {"objective": oid, "brief": bid}


async def count(conn, table: str, space: str) -> int:
    row = await (
        await conn.execute(
            f"SELECT count(*) FROM {table} WHERE space_id = %s", (space,)
        )
    ).fetchone()
    return row[0]


async def test_kernel_rw_inserts_and_selects_all_four(kernel, space):
    await seed(kernel, space)
    await kernel.commit()
    for table in TABLES:
        assert await count(kernel, table, space) == 1


@pytest.mark.parametrize("table", TABLES)
async def test_kernel_rw_cannot_update_delete_or_truncate(kernel, space, table):
    await seed(kernel, space)
    await kernel.commit()
    for sql in (
        f"UPDATE {table} SET space_id = 'x' WHERE space_id = %s",
        f"DELETE FROM {table} WHERE space_id = %s",
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await kernel.execute(sql, (space,))
        await kernel.rollback()
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        await kernel.execute(f"TRUNCATE {table} CASCADE")
    await kernel.rollback()


@pytest.mark.parametrize("table", TABLES)
async def test_trigger_refuses_the_owner_too(kernel, migrator, space, table):
    await seed(kernel, space)
    await kernel.commit()
    for sql in (
        f"UPDATE {table} SET space_id = 'x' WHERE space_id = %s",
        f"DELETE FROM {table} WHERE space_id = %s",
    ):
        with pytest.raises(psycopg.errors.RaiseException) as info:
            await migrator.execute(sql, (space,))
        assert f"{table} is append-only" in str(info.value)
        await migrator.rollback()
    with pytest.raises(psycopg.errors.RaiseException) as info:
        await migrator.execute(f"TRUNCATE {table} CASCADE")
    assert "append-only" in str(info.value)
    await migrator.rollback()


async def test_context_ro_reads_one_space_with_a_token(kernel, context, space):
    other = space + "-other"
    await seed(kernel, space)
    await seed(kernel, other)
    token = uuid.uuid4().hex
    await kernel.execute(
        "INSERT INTO read_tokens VALUES (%s, %s, now() + interval '1 minute')",
        (token, space),
    )
    await kernel.commit()
    await context.execute("SELECT set_config('cori.read_token', %s, false)", (token,))
    for table in TABLES:
        rows = await (
            await context.execute(
                f"SELECT DISTINCT space_id FROM {table} WHERE space_id IN (%s, %s)",
                (space, other),
            )
        ).fetchall()
        assert rows == [(space,)], table


async def test_context_ro_reads_nothing_without_a_token(kernel, context, space):
    await seed(kernel, space)
    await kernel.commit()
    for table in TABLES:
        assert await count(context, table, space) == 0, table


async def test_context_ro_cannot_insert(context, space):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        await context.execute(
            "INSERT INTO objectives (id, space_id, conversation_id, depth) "
            "VALUES ('x', %s, 'c', 0)",
            (space,),
        )
    await context.rollback()


async def test_brief_with_null_objective_inserts(kernel, space):
    bid = uuid.uuid4().hex
    await kernel.execute(
        "INSERT INTO briefs (id, space_id, objective_id, agent_class, generation, "
        "brief, gateway_token_sha256, issued_at) "
        "VALUES (%s, %s, NULL, 'Scribe', 1, '{}'::jsonb, 'ab', now())",
        (bid, space),
    )
    await kernel.commit()
    row = await (
        await kernel.execute("SELECT objective_id FROM briefs WHERE id = %s", (bid,))
    ).fetchone()
    assert row == (None,)


async def test_ledger_grant_with_approval_inserts(kernel, space):
    await kernel.execute(
        "INSERT INTO budget_ledger (space_id, node_id, kind, usd_micros, approval_id) "
        "VALUES (%s, 'n1', 'grant', 7, 'a1')",
        (space,),
    )
    await kernel.commit()
    row = await (
        await kernel.execute(
            "SELECT kind, brief_id, approval_id FROM budget_ledger "
            "WHERE space_id = %s",
            (space,),
        )
    ).fetchone()
    assert row == ("grant", None, "a1")


async def test_ledger_checks_refuse_negative_and_a_fifth_kind(kernel, space):
    for kind, amount in (("allocate", -1), ("refund", 1)):
        with pytest.raises(psycopg.errors.CheckViolation):
            await kernel.execute(
                "INSERT INTO budget_ledger (space_id, node_id, kind, usd_micros) "
                "VALUES (%s, 'n1', %s, %s)",
                (space, kind, amount),
            )
        await kernel.rollback()


async def test_objectives_depth_check_and_revision_uniqueness(kernel, space):
    with pytest.raises(psycopg.errors.CheckViolation):
        await kernel.execute(
            "INSERT INTO objectives (id, space_id, conversation_id, depth) "
            "VALUES ('x', %s, 'c', -1)",
            (space,),
        )
    await kernel.rollback()
    ids = await seed(kernel, space)
    with pytest.raises(psycopg.errors.UniqueViolation):
        await kernel.execute(
            "INSERT INTO objective_revisions (space_id, objective_id, revision, "
            "contract) VALUES (%s, %s, 1, '{}'::jsonb)",
            (space, ids["objective"]),
        )
    await kernel.rollback()
