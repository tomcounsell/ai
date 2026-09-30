"""Row-level security keyed to a read token. Tech stack §3.

The kernel mints a per-render, per-space token into `read_tokens`, a table
context_ro has no privilege on. context_ro presents it in the session
variable `cori.read_token`; the policy resolves it to exactly one space
through a SECURITY DEFINER function. Any role can set the variable, so the
property under test is narrower: context_ro cannot mint a token, a guessed
value reads nothing, and an expired token reads nothing."""

import uuid

from tests.conftest import requires_postgres

pytestmark = requires_postgres


async def test_context_ro_reads_one_space_only(kernel, context, space):
    other = space + "-other"
    await kernel.execute(
        "INSERT INTO events (space_id, type) VALUES (%s, 'x'), (%s, 'x')",
        (space, other),
    )
    token = uuid.uuid4().hex
    await kernel.execute(
        "INSERT INTO read_tokens VALUES (%s, %s, now() + interval '1 minute')",
        (token, space),
    )
    await kernel.commit()

    await context.execute("SELECT set_config('cori.read_token', %s, false)", (token,))
    rows = await (
        await context.execute(
            "SELECT space_id FROM events WHERE space_id IN (%s, %s)", (space, other)
        )
    ).fetchall()
    assert rows == [(space,)]


async def test_no_token_reads_nothing(kernel, context, space):
    await kernel.execute(
        "INSERT INTO events (space_id, type) VALUES (%s, 'x')", (space,)
    )
    await kernel.commit()
    rows = await (
        await context.execute("SELECT 1 FROM events WHERE space_id = %s", (space,))
    ).fetchall()
    assert rows == []


async def test_guessed_token_reads_nothing(kernel, context, space):
    await kernel.execute(
        "INSERT INTO events (space_id, type) VALUES (%s, 'x')", (space,)
    )
    await kernel.commit()
    await context.execute("SELECT set_config('cori.read_token', %s, false)", (space,))
    rows = await (
        await context.execute("SELECT 1 FROM events WHERE space_id = %s", (space,))
    ).fetchall()
    assert rows == []


async def test_context_ro_cannot_read_or_mint_tokens(context):
    import psycopg

    for sql in (
        "SELECT * FROM read_tokens",
        "INSERT INTO read_tokens VALUES ('t', 's', now())",
    ):
        try:
            await context.execute(sql)
        except psycopg.errors.InsufficientPrivilege:
            await context.rollback()
        else:
            raise AssertionError(f"context_ro was allowed: {sql}")


async def test_expired_token_reads_nothing(kernel, context, space):
    await kernel.execute(
        "INSERT INTO events (space_id, type) VALUES (%s, 'x')", (space,)
    )
    token = uuid.uuid4().hex
    await kernel.execute(
        "INSERT INTO read_tokens VALUES (%s, %s, now() - interval '1 second')",
        (token, space),
    )
    await kernel.commit()
    await context.execute("SELECT set_config('cori.read_token', %s, false)", (token,))
    rows = await (
        await context.execute("SELECT 1 FROM events WHERE space_id = %s", (space,))
    ).fetchall()
    assert rows == []
