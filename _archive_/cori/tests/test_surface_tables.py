"""`conversations`, `cards`, and `approvals` are append-only by grant and by
trigger, partitioned by space, and hold one decision per card. Plan 10 task
2; seams §6; tech stack §3.

The pattern is migration 0001's, so these tests are `tests/test_append_only.py`
and `tests/test_rls.py` repeated for the three tables this plan creates.
"""

import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from tests.conftest import requires_postgres

pytestmark = requires_postgres

TABLES = ("conversations", "cards", "approvals")
NOW = datetime.now(UTC)


async def seed(conn, space, *, suffix=""):
    """One conversation, one card on it, one approval of that card."""
    conversation = f"conv-{uuid.uuid4().hex[:8]}{suffix}"
    card = f"card-{uuid.uuid4().hex[:8]}{suffix}"
    approval = f"appr-{uuid.uuid4().hex[:8]}{suffix}"
    await conn.execute(
        "INSERT INTO conversations (id, space_id, opened_at) VALUES (%s, %s, %s)",
        (conversation, space, NOW),
    )
    await conn.execute(
        "INSERT INTO cards (id, kind, space_id, conversation_id, regards, "
        "objective_id, contract_revision, fields, options, note, expires_at, "
        "issued_at, issued) VALUES (%s, 'question', %s, %s, 'obj-1', 'obj-1', 1, "
        "'{}'::jsonb, '[]'::jsonb, '', %s, %s, true)",
        (card, space, conversation, NOW + timedelta(hours=1), NOW),
    )
    await conn.execute(
        "INSERT INTO approvals (id, card_id, kind, space_id, objective_id, "
        "contract_revision, raw_message, session_id, decided_at) "
        "VALUES (%s, %s, 'answered', %s, 'obj-1', 1, 'yes', 's1', %s)",
        (approval, card, space, NOW),
    )
    await conn.commit()
    return conversation, card, approval


async def _refused(conn, sql, params=(), by=psycopg.errors.InsufficientPrivilege):
    with pytest.raises(by):
        await conn.execute(sql, params)
    await conn.rollback()


async def test_kernel_rw_grants_are_insert_and_select_only(kernel, space):
    """The grant is the first lock: SELECT and INSERT and nothing else."""
    await seed(kernel, space)
    rows = await (
        await kernel.execute(
            "SELECT table_name, privilege_type FROM information_schema.table_privileges "
            "WHERE grantee = 'kernel_rw' AND table_name = ANY(%s) ORDER BY 1, 2",
            (list(TABLES),),
        )
    ).fetchall()
    assert sorted(rows) == sorted(
        (table, privilege) for table in TABLES for privilege in ("INSERT", "SELECT")
    )


async def test_update_and_delete_refused_by_grant_then_trigger(kernel, migrator, space):
    """`kernel_rw` is stopped by the grant; the migrator, which has the
    grant, is stopped by the trigger."""
    conversation, card, approval = await seed(kernel, space)
    await _refused(kernel, "UPDATE conversations SET space_id = 'x'")
    await _refused(kernel, "DELETE FROM cards WHERE id = %s", (card,))
    await _refused(kernel, "UPDATE approvals SET kind = 'approved'")
    await _refused(kernel, "TRUNCATE cards")

    by = psycopg.errors.RaiseException
    await _refused(migrator, "UPDATE cards SET note = 'edited'", (), by)
    await _refused(migrator, "DELETE FROM approvals WHERE id = %s", (approval,), by)
    await _refused(
        migrator,
        "UPDATE conversations SET space_id = 'x' WHERE id = %s",
        (conversation,),
        by,
    )


async def test_context_ro_reads_one_space_only(kernel, context, space):
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


async def test_context_ro_with_no_token_reads_nothing(kernel, context, space):
    await seed(kernel, space)
    for table in TABLES:
        rows = await (
            await context.execute(
                f"SELECT 1 FROM {table} WHERE space_id = %s", (space,)
            )
        ).fetchall()
        assert rows == [], table


async def test_second_approval_for_a_card_violates_unique(kernel, space):
    """One decision per card is a constraint the grant enforces before any
    code does."""
    _, card, _ = await seed(kernel, space)
    with pytest.raises(psycopg.errors.UniqueViolation):
        await kernel.execute(
            "INSERT INTO approvals (id, card_id, kind, space_id, objective_id, "
            "contract_revision, raw_message, session_id, decided_at) "
            "VALUES (%s, %s, 'expired', %s, 'obj-1', 1, '', 'kernel', %s)",
            (f"appr-{uuid.uuid4().hex[:8]}", card, space, NOW),
        )
    await kernel.rollback()
