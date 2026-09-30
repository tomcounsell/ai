"""The partition as a catalog predicate, not a list of tables.

Seams §6 states two rules: every table with a `space_id` column carries
row-level security, and `context_ro` is granted `SELECT` only on tables that
carry `space_id` and the policy. This file asserts both over whatever is in
`public`, so a plan that adds a table and forgets the pattern fails here
rather than in a review. It replaces `tests/test_grants.py`, an exact table
of grants that the tree plan retired; that file broke on every new table,
and a predicate does not.

`test_negatives_are_caught` builds the three tables each predicate exists to
catch, under `migrator` inside a transaction that rolls back, and asserts the
predicate names them. Without it a helper that returned the empty set would
make every test above pass.

Plan 03 task 6.
"""

import psycopg
import pytest

from tests.conftest import requires_postgres

pytestmark = requires_postgres

# Ordinary and partitioned tables are what a policy can filter. A view or a
# materialized view is included in the grant scan alone: row-level security
# does not apply to a materialized view (prereqs finding 2), so the right
# rule for one is that `context_ro` never holds anything on it at all.
FILTERABLE = ("r", "p")
RELATIONS = ("r", "p", "v", "m", "f")


async def _rows(conn, sql: str, params=()) -> list[tuple]:
    return await (await conn.execute(sql, params)).fetchall()


async def space_tables(conn) -> set[str]:
    """Every table in `public` with a `space_id` column."""
    rows = await _rows(
        conn,
        "SELECT c.relname FROM pg_class c "
        "JOIN pg_attribute a ON a.attrelid = c.oid "
        "WHERE c.relnamespace = 'public'::regnamespace "
        "AND c.relkind = ANY(%s) AND a.attname = 'space_id' "
        "AND a.attnum > 0 AND NOT a.attisdropped",
        (list(FILTERABLE),),
    )
    return {r[0] for r in rows}


async def rls_enabled(conn) -> set[str]:
    rows = await _rows(
        conn,
        "SELECT relname FROM pg_class "
        "WHERE relnamespace = 'public'::regnamespace "
        "AND relkind = ANY(%s) AND relrowsecurity",
        (list(FILTERABLE),),
    )
    return {r[0] for r in rows}


async def policies(conn) -> list[tuple[str, str, str, str, str]]:
    """(table, policy, roles, command, using expression)."""
    rows = await _rows(
        conn,
        "SELECT tablename, policyname, roles::text, cmd, coalesce(qual, '') "
        "FROM pg_policies WHERE schemaname = 'public'",
    )
    return [tuple(r) for r in rows]


async def grants(conn) -> list[tuple[str, str, str]]:
    """(relation, grantee, privilege), from the ACL rather than from
    `information_schema`, which shows only what the current role may see."""
    rows = await _rows(
        conn,
        "SELECT c.relname, pg_get_userbyid(a.grantee), a.privilege_type "
        "FROM pg_class c, aclexplode(c.relacl) a "
        "WHERE c.relnamespace = 'public'::regnamespace AND c.relkind = ANY(%s)",
        (list(RELATIONS),),
    )
    return [tuple(r) for r in rows]


async def granted_to(conn, role: str) -> set[str]:
    return {t for t, g, _ in await grants(conn) if g == role}


# ---------------------------------------------------------------------------
# The predicates, each returning the relations that violate it


async def tables_without_rls(conn) -> set[str]:
    """Seams §6, rule one: a `space_id` table carries row-level security and
    a policy that lets `kernel_rw` do what its grant allows."""
    with_rls = await rls_enabled(conn)
    kernel_policy = {
        t for t, _, roles, _, _ in await policies(conn) if "kernel_rw" in roles
    }
    return {
        t
        for t in await space_tables(conn)
        if t not in with_rls or t not in kernel_policy
    }


async def tables_not_filtered_by_token(conn) -> set[str]:
    """Seams §6, rule two, first half: a relation `context_ro` may read has a
    SELECT policy for it resolving the read token."""
    filtered = {
        t
        for t, _, roles, cmd, qual in await policies(conn)
        if "context_ro" in roles
        and cmd in ("SELECT", "ALL")
        and "cori_current_space()" in qual
    }
    return await granted_to(conn, "context_ro") - filtered


async def unpartitioned_tables_granted(conn) -> set[str]:
    """Seams §6, rule two, second half: a relation without `space_id` has no
    row a policy could filter, so `context_ro` is never granted it. A
    materialized view has no policy at all and is caught here too."""
    return await granted_to(conn, "context_ro") - await space_tables(conn)


# ---------------------------------------------------------------------------
# The rules, over whatever is in public today


async def test_every_space_table_has_rls(migrator):
    assert await space_tables(migrator) >= {"events", "read_tokens", "inbound_items"}
    assert await tables_without_rls(migrator) == set()


async def test_every_context_ro_table_is_filtered_by_token(migrator):
    assert await tables_not_filtered_by_token(migrator) == set()


async def test_context_ro_reads_only_space_tables(migrator):
    assert await unpartitioned_tables_granted(migrator) == set()


async def test_context_ro_holds_only_select(migrator):
    held = {(t, p) for t, g, p in await grants(migrator) if g == "context_ro"}
    assert {p for _, p in held} <= {"SELECT"}, sorted(held)


async def test_kernel_rw_never_mutates(migrator):
    held = {
        (t, p)
        for t, g, p in await grants(migrator)
        if g == "kernel_rw" and p in ("UPDATE", "DELETE", "TRUNCATE")
    }
    assert held == set(), sorted(held)


async def test_only_migrator_deletes(migrator):
    holders = {g for _, g, p in await grants(migrator) if p == "DELETE"}
    assert holders <= {"migrator"}, sorted(holders)


# ---------------------------------------------------------------------------
# The negatives, so a helper that returns nothing cannot pass the file


async def test_negatives_are_caught(migrator):
    """One scratch table per predicate, created and dropped inside a
    transaction that rolls back, so the catalog is what it was."""
    async with migrator.transaction(force_rollback=True):
        await migrator.execute("CREATE TABLE scratch_no_rls (space_id text)")
        assert "scratch_no_rls" in await tables_without_rls(migrator)

        await migrator.execute("CREATE TABLE scratch_no_policy (space_id text)")
        await migrator.execute(
            "ALTER TABLE scratch_no_policy ENABLE ROW LEVEL SECURITY"
        )
        await migrator.execute(
            "CREATE POLICY scratch_no_policy_kernel ON scratch_no_policy "
            "TO kernel_rw USING (true) WITH CHECK (true)"
        )
        await migrator.execute("GRANT SELECT ON scratch_no_policy TO context_ro")
        assert "scratch_no_policy" in await tables_not_filtered_by_token(migrator)
        assert "scratch_no_policy" not in await tables_without_rls(migrator)

        await migrator.execute("CREATE TABLE scratch_no_space (id bigint)")
        await migrator.execute("GRANT SELECT ON scratch_no_space TO context_ro")
        assert "scratch_no_space" in await unpartitioned_tables_granted(migrator)

    # Outside the rolled-back transaction the catalog is clean again.
    assert await tables_without_rls(migrator) == set()
    assert await unpartitioned_tables_granted(migrator) == set()
    with pytest.raises(psycopg.errors.UndefinedTable):
        await migrator.execute("SELECT 1 FROM scratch_no_rls")
    await migrator.rollback()
