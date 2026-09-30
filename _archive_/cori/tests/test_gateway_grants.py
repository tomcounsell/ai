"""The three gateway tables are append-only by grant and by trigger, the log
is readable per space through a minted read token, and the bodies and the
token hashes are readable by nobody but the kernel.

Plan 04 task 2; seams §5.1, §6.
"""

import hashlib
import uuid

import psycopg
import pytest

from kernel.spaces import bind_read_token, mint_read_token
from tests.conftest import requires_postgres

pytestmark = requires_postgres


async def seed_log(conn, space: str, *, event: str = "request") -> None:
    await conn.execute(
        "INSERT INTO gateway_log (brief_id, generation, space_id, call, event, model) "
        "VALUES (%s, 1, %s, 1, %s, 'claude-opus-5')",
        (uuid.uuid4().hex, space, event),
    )


async def seed_body(conn) -> str:
    sha = hashlib.sha256(uuid.uuid4().bytes).hexdigest()
    await conn.execute(
        "INSERT INTO request_bodies (sha256, body) VALUES (%s, '{}'::jsonb)", (sha,)
    )
    return sha


async def seed_token(conn, space: str) -> str:
    sha = hashlib.sha256(uuid.uuid4().bytes).hexdigest()
    await conn.execute(
        "INSERT INTO gateway_tokens (token_sha256, brief_id, generation, model_ref, "
        "space_id) VALUES (%s, %s, 1, 'claude-opus-5', %s)",
        (sha, uuid.uuid4().hex, space),
    )
    return sha


async def test_kernel_cannot_update_delete_or_truncate(kernel, space):
    """The grant is the first lock and the trigger is the second, on all
    three tables (migration 0001's pattern)."""
    await seed_log(kernel, space)
    sha = await seed_body(kernel)
    token_sha = await seed_token(kernel, space)
    await kernel.commit()

    statements = [
        ("UPDATE gateway_log SET reason = 'x' WHERE space_id = %s", (space,)),
        ("DELETE FROM gateway_log WHERE space_id = %s", (space,)),
        ("TRUNCATE gateway_log", ()),
        ("UPDATE request_bodies SET body = '{}'::jsonb WHERE sha256 = %s", (sha,)),
        ("DELETE FROM request_bodies WHERE sha256 = %s", (sha,)),
        ("TRUNCATE request_bodies", ()),
        (
            "UPDATE gateway_tokens SET brief_id = 'x' WHERE token_sha256 = %s",
            (token_sha,),
        ),
        ("DELETE FROM gateway_tokens WHERE token_sha256 = %s", (token_sha,)),
        ("TRUNCATE gateway_tokens", ()),
    ]
    for sql, params in statements:
        with pytest.raises(
            (psycopg.errors.InsufficientPrivilege, psycopg.errors.RaiseException)
        ):
            await kernel.execute(sql, params)
        await kernel.rollback()

    rows = await (
        await kernel.execute(
            "SELECT count(*) FROM gateway_log WHERE space_id = %s", (space,)
        )
    ).fetchall()
    assert rows == [(1,)]


async def test_context_ro_reads_one_space_of_the_log(kernel, context, space):
    """A minted read token binds the render to one space, and the rows of
    every other space are invisible."""
    other = space + "-other"
    await seed_log(kernel, space)
    await seed_log(kernel, other)
    token = await mint_read_token(kernel, space)
    await kernel.commit()

    await bind_read_token(context, token)
    rows = await (
        await context.execute(
            "SELECT space_id FROM gateway_log WHERE space_id IN (%s, %s)",
            (space, other),
        )
    ).fetchall()
    assert rows == [(space,)]
    await context.rollback()


async def test_context_ro_cannot_read_bodies_or_tokens(kernel, context, space):
    """Request bodies hold conversation text and token rows hold credential
    hashes; seams §5.1 grants the render neither."""
    await seed_body(kernel)
    await seed_token(kernel, space)
    token = await mint_read_token(kernel, space)
    await kernel.commit()

    await bind_read_token(context, token)
    for table in ("request_bodies", "gateway_tokens"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await context.execute(f"SELECT * FROM {table}")
        await context.rollback()


async def test_an_unknown_token_row_carries_no_brief_and_no_space(kernel):
    """Seams §5.1: a probe with a token no row backs is still a row."""
    await kernel.execute(
        "INSERT INTO gateway_log (event, reason) VALUES ('refused', 'unknown_token')"
    )
    rows = await (
        await kernel.execute(
            "SELECT brief_id, generation, space_id FROM gateway_log "
            "WHERE reason = 'unknown_token' ORDER BY id DESC LIMIT 1"
        )
    ).fetchall()
    assert rows == [(None, None, None)]
    await kernel.rollback()
