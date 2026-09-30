"""`checks` and `verdicts` are append-only by grant and by trigger and
partitioned by space. Plan 11 task 1; seams §6; tech stack §3.

The pattern is migration 0001's, so these tests are `tests/test_append_only.py`
and `tests/test_rls.py` repeated for the two tables this plan creates.
"""

import uuid

import psycopg
import pytest

from tests.conftest import requires_postgres

pytestmark = requires_postgres

TABLES = ("checks", "verdicts")


async def seed(conn, space):
    """One check row and one verdict row on one objective."""
    objective = f"obj-{uuid.uuid4().hex[:8]}"
    brief = f"brief-{uuid.uuid4().hex[:8]}"
    await conn.execute(
        "INSERT INTO checks (space_id, objective_id, brief_id, name, passed, "
        "output_sha256, detail) VALUES (%s, %s, %s, 'tests', true, %s, '4 passed')",
        (space, objective, brief, "0" * 64),
    )
    await conn.execute(
        "INSERT INTO verdicts (space_id, objective_id, brief_id, verifier_brief_id, "
        "model_ref, prompt_sha256, outcome, predicted_failure, criteria, "
        "scope_findings, summary, sampled_with) VALUES (%s, %s, %s, NULL, 'kernel', "
        "%s, 'fail', 1.0, '[]'::jsonb, '[]'::jsonb, 'tests failed', 1.0)",
        (space, objective, brief, "0" * 64),
    )
    await conn.commit()
    return objective, brief


async def _refused(conn, sql, params=(), by=psycopg.errors.InsufficientPrivilege):
    with pytest.raises(by):
        await conn.execute(sql, params)
    await conn.rollback()


async def test_kernel_rw_can_insert_and_select_only(kernel, migrator, space):
    objective, _ = await seed(kernel, space)
    for table in TABLES:
        rows = await (
            await kernel.execute(
                f"SELECT count(*) FROM {table} WHERE objective_id = %s", (objective,)
            )
        ).fetchone()
        assert rows == (1,), table
    grants = await (
        await kernel.execute(
            "SELECT table_name, privilege_type FROM information_schema.table_privileges "
            "WHERE grantee = 'kernel_rw' AND table_name = ANY(%s) ORDER BY 1, 2",
            (list(TABLES),),
        )
    ).fetchall()
    assert sorted(grants) == sorted(
        (table, privilege) for table in TABLES for privilege in ("INSERT", "SELECT")
    )
    await _refused(kernel, "UPDATE checks SET passed = false")
    await _refused(kernel, "DELETE FROM checks WHERE objective_id = %s", (objective,))
    await _refused(kernel, "UPDATE verdicts SET outcome = 'pass'")
    await _refused(kernel, "DELETE FROM verdicts WHERE objective_id = %s", (objective,))
    await _refused(kernel, "TRUNCATE verdicts")

    by = psycopg.errors.RaiseException
    await _refused(migrator, "UPDATE checks SET passed = false", (), by)
    await _refused(
        migrator, "DELETE FROM verdicts WHERE objective_id = %s", (objective,), by
    )


async def test_context_ro_reads_one_space_by_token(kernel, context, space):
    other = space + "-other"
    await seed(kernel, space)
    await seed(kernel, other)
    token = uuid.uuid4().hex
    await kernel.execute(
        "INSERT INTO read_tokens VALUES (%s, %s, now() + interval '1 minute')",
        (token, space),
    )
    await kernel.commit()

    for table in TABLES:
        rows = await (
            await context.execute(
                f"SELECT 1 FROM {table} WHERE space_id IN (%s, %s)", (space, other)
            )
        ).fetchall()
        assert rows == [], table
    await context.execute("SELECT set_config('cori.read_token', %s, false)", (token,))
    for table in TABLES:
        rows = await (
            await context.execute(
                f"SELECT DISTINCT space_id FROM {table} WHERE space_id IN (%s, %s)",
                (space, other),
            )
        ).fetchall()
        assert rows == [(space,)], table
