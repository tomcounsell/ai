"""Read tokens: the kernel mints one per render, the render binds it for the
length of one transaction, and it reads exactly one space. Plan 03 tasks 4
and 5; tech stack §3; seams §3.4."""

import asyncio
import uuid

import psycopg
import pytest
from hypothesis import given, settings, strategies as st

from kernel.spaces import MAX_TTL_S, bind_read_token, mint_read_token
from tests.conftest import dsn, requires_postgres

pytestmark = requires_postgres


def run(coro):
    return asyncio.run(coro)


async def connect(role: str):
    return await psycopg.AsyncConnection.connect(dsn(role))


async def insert_rows(kernel, space: str, events: int, items: int) -> None:
    for _ in range(events):
        await kernel.execute(
            "INSERT INTO events (space_id, type) VALUES (%s, 'message.sent')", (space,)
        )
    for _ in range(items):
        await kernel.execute(
            "INSERT INTO inbound_items (item_id, space_id, connector, account, "
            "external_id, headers, received_at, routed_by) "
            "VALUES (%s, %s, 'gmail', 'tom@yuda.me', %s, '{}'::jsonb, now(), NULL)",
            (uuid.uuid4().hex, space, uuid.uuid4().hex),
        )


async def test_read_tokens_has_rls_and_no_context_grant(migrator):
    """Seams §6: every table with a `space_id` carries row-level security.
    `read_tokens` has one and is still the lock, so it gets the `kernel_rw`
    policy and no grant to `context_ro`."""
    row = await (
        await migrator.execute(
            "SELECT relrowsecurity FROM pg_class WHERE relname = 'read_tokens'"
        )
    ).fetchone()
    assert row == (True,)

    policies = await (
        await migrator.execute(
            "SELECT policyname, roles::text FROM pg_policies "
            "WHERE tablename = 'read_tokens'"
        )
    ).fetchall()
    assert policies == [("read_tokens_kernel", "{kernel_rw}")]

    grants = await (
        await migrator.execute(
            "SELECT privilege_type FROM information_schema.role_table_grants "
            "WHERE table_name = 'read_tokens' AND grantee = 'context_ro'"
        )
    ).fetchall()
    assert grants == []


async def test_kernel_can_still_mint_under_the_policy(kernel, space):
    """The policy is USING (true) WITH CHECK (true), so enabling row-level
    security changed nothing for the minter."""
    await kernel.execute(
        "INSERT INTO read_tokens VALUES (%s, %s, now() + interval '1 minute')",
        ("t-" + space, space),
    )
    await kernel.commit()


async def test_cori_current_space_still_resolves(kernel, context, space):
    """The function is SECURITY DEFINER owned by `migrator`, which owns
    `read_tokens` and is not subject to its policies, so the policy on
    `events` still resolves a token."""
    token = "t2-" + space
    await kernel.execute(
        "INSERT INTO read_tokens VALUES (%s, %s, now() + interval '1 minute')",
        (token, space),
    )
    await kernel.execute(
        "INSERT INTO events (space_id, type) VALUES (%s, 'message.sent')", (space,)
    )
    await kernel.commit()
    await context.execute("SELECT set_config('cori.read_token', %s, false)", (token,))
    rows = await (
        await context.execute(
            "SELECT count(*) FROM events WHERE space_id = %s", (space,)
        )
    ).fetchall()
    assert rows == [(1,)]


async def test_context_ro_still_cannot_reach_read_tokens(context):
    for sql in (
        "SELECT * FROM read_tokens",
        "INSERT INTO read_tokens VALUES ('t', 's', now())",
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await context.execute(sql)
        await context.rollback()


# ---------------------------------------------------------------------------
# Plan 03 task 5


@settings(max_examples=25, deadline=None)
@given(
    counts=st.lists(
        st.tuples(st.integers(0, 3), st.integers(0, 3)), min_size=2, max_size=4
    )
)
def test_token_reads_exactly_one_space(counts):
    """A token reads its space and nothing else, in every RLS table, and the
    binding dies with the transaction."""

    async def go():
        spaces = [f"space-{uuid.uuid4().hex[:8]}" for _ in counts]
        kernel = await connect("kernel_rw")
        context = await connect("context_ro")
        try:
            tokens = {}
            for space, (events, items) in zip(spaces, counts):
                await insert_rows(kernel, space, events, items)
                tokens[space] = await mint_read_token(kernel, space)
            await kernel.commit()

            for space, (events, items) in zip(spaces, counts):
                async with context.transaction(force_rollback=True):
                    await bind_read_token(context, tokens[space])
                    assert await count(context, "events") == events
                    assert await count(context, "inbound_items") == items
                # Outside the transaction the binding is gone.
                assert await count(context, "events") == 0
                assert await count(context, "inbound_items") == 0
                await context.rollback()
        finally:
            await kernel.close()
            await context.close()

    run(go())


async def count(conn, table: str) -> int:
    row = await (await conn.execute(f"SELECT count(*) FROM {table}")).fetchone()
    return row[0]


async def test_bind_is_transaction_local(kernel, context, space):
    await kernel.execute(
        "INSERT INTO events (space_id, type) VALUES (%s, 'message.sent')", (space,)
    )
    token = await mint_read_token(kernel, space)
    await kernel.commit()

    async with context.transaction(force_rollback=True):
        await bind_read_token(context, token)
        assert await count(context, "events") == 1
    assert await count(context, "events") == 0
    await context.rollback()


async def test_ttl_out_of_range_refused(kernel, space):
    for bad in (0, -1, MAX_TTL_S + 1):
        with pytest.raises(ValueError, match="ttl_s"):
            await mint_read_token(kernel, space, ttl_s=bad)
    assert await mint_read_token(kernel, space, ttl_s=MAX_TTL_S)
    await kernel.rollback()


async def test_token_is_not_time_ordered(kernel, space):
    """A read token is a secret, so it is `secrets.token_urlsafe` and not an
    id: two tokens minted in a row neither sort together nor share a prefix."""
    tokens = [await mint_read_token(kernel, space) for _ in range(8)]
    await kernel.rollback()
    assert len(set(tokens)) == 8
    assert all(len(t) >= 40 for t in tokens)
    assert len({t[:8] for t in tokens}) == 8
