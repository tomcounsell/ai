"""append, read, read_for against the real store. Seams §3.1."""

import uuid

import psycopg
import pytest
from pydantic import ValidationError

from kernel.events import (
    StaleSchemaVersion,
    UnknownEventType,
    append,
    read,
    read_for,
)
from tests.conftest import requires_postgres

pytestmark = requires_postgres


async def test_append_returns_increasing_ids(kernel, space):
    ids = [
        await append(kernel, space_id=space, type="message.sent", payload={"n": i})
        for i in range(5)
    ]
    await kernel.commit()
    assert ids == sorted(ids) and len(set(ids)) == 5
    events = await read(kernel, space_id=space)
    assert [e.id for e in events] == ids
    assert [e.payload["n"] for e in events] == list(range(5))
    assert all(e.space_id == space and e.schema_version == 1 for e in events)


async def test_read_after_is_exclusive(kernel, space):
    ids = [
        await append(kernel, space_id=space, type="message.sent", payload={})
        for _ in range(3)
    ]
    await kernel.commit()
    assert [e.id for e in await read(kernel, space_id=space, after=ids[0])] == ids[1:]
    assert await read(kernel, space_id=space, after=ids[-1]) == []
    page = await read(kernel, space_id=space, limit=2)
    assert [e.id for e in page] == ids[:2]


async def test_read_filters_by_types(kernel, space):
    await append(kernel, space_id=space, type="message.sent", payload={})
    await append(kernel, space_id=space, type="message.received", payload={})
    await append(kernel, space_id=space, type="turn.started", payload={})
    await kernel.commit()
    got = await read(kernel, space_id=space, types=["message.sent", "turn.started"])
    assert [e.type for e in got] == ["message.sent", "turn.started"]
    assert await read(kernel, space_id=space, types=[]) == []


async def test_read_for_matches_payload_key(kernel, space):
    obj = uuid.uuid4().hex
    a = await append(
        kernel, space_id=space, type="brief.issued", payload={"objective_id": obj}
    )
    await append(
        kernel, space_id=space, type="brief.issued", payload={"objective_id": "z"}
    )
    b = await append(
        kernel,
        space_id=space,
        type="brief.failed",
        payload={"objective_id": obj, "nested": {"objective_id": "z"}},
    )
    await append(kernel, space_id=space, type="turn.started", payload={"turn_id": obj})
    await kernel.commit()
    got = await read_for(kernel, space_id=space, key="objective_id", value=obj)
    assert [e.id for e in got] == [a, b]
    assert await read_for(kernel, space_id=space, key="objective_id", value="q") == []
    assert await read_for(kernel, space_id=space + "-o", key="turn_id", value=obj) == []


async def test_append_refuses_unknown_type(kernel, space):
    with pytest.raises(UnknownEventType):
        await append(kernel, space_id=space, type="not.a.type", payload={})
    assert await read(kernel, space_id=space) == []


async def test_append_refuses_stale_schema_version(kernel, space):
    with pytest.raises(StaleSchemaVersion):
        await append(
            kernel, space_id=space, type="message.sent", payload={}, schema_version=2
        )
    with pytest.raises(StaleSchemaVersion):
        await append(
            kernel, space_id=space, type="message.sent", payload={}, schema_version=0
        )
    assert await read(kernel, space_id=space) == []


async def test_read_raises_on_unknown_type_row(kernel, space):
    cur = await kernel.execute(
        "INSERT INTO events (space_id, type, payload) "
        "VALUES (%s, 'x', '{\"k\": \"v\"}') RETURNING id",
        (space,),
    )
    (row_id,) = await cur.fetchone()
    await kernel.commit()
    with pytest.raises(UnknownEventType) as info:
        await read(kernel, space_id=space)
    assert not isinstance(info.value, ValidationError)
    assert str(row_id) in str(info.value) and "'x'" in str(info.value)
    with pytest.raises(UnknownEventType):
        await read_for(kernel, space_id=space, key="k", value="v")


async def test_append_under_context_ro_is_refused(context, space):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        await append(context, space_id=space, type="message.sent", payload={})
    await context.rollback()


async def test_context_ro_read_sees_token_space_only(kernel, context, space):
    other = space + "-other"
    await append(kernel, space_id=space, type="message.sent", payload={"k": "v"})
    await append(kernel, space_id=other, type="message.sent", payload={"k": "v"})
    token = uuid.uuid4().hex
    await kernel.execute(
        "INSERT INTO read_tokens VALUES (%s, %s, now() + interval '1 minute')",
        (token, space),
    )
    await kernel.commit()

    assert await read(context, space_id=space) == []
    await context.execute("SELECT set_config('cori.read_token', %s, false)", (token,))
    assert [e.space_id for e in await read(context, space_id=space)] == [space]
    assert await read(context, space_id=other) == []
    assert [
        e.space_id for e in await read_for(context, space_id=space, key="k", value="v")
    ] == [space]
    assert await read_for(context, space_id=other, key="k", value="v") == []
