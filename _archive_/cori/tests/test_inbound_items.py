"""`inbound_items` is insert-only and keyed so a re-read is a no-op, and
recording an item writes its row and its event together. Plan 03 tasks 4
and 8; seams §6, §4."""

import asyncio
import uuid
from datetime import UTC, datetime

import psycopg
import pytest
from hypothesis import given, settings, strategies as st

from kernel.spaces import ingest, record_inbound
from schemas.inbound import InboundItem
from schemas.space import UNASSIGNED_SPACE_ID, Connector, RoutingRule, Space
from tests.conftest import dsn, requires_postgres

pytestmark = requires_postgres


async def seed(conn, space: str, *, external_id: str = "m1") -> None:
    await conn.execute(
        "INSERT INTO inbound_items (item_id, space_id, connector, account, "
        "external_id, headers, received_at, routed_by) "
        "VALUES (%s, %s, 'gmail', 'tom@yuda.me', %s, '{}'::jsonb, now(), NULL)",
        (uuid.uuid4().hex, space, external_id),
    )


async def test_kernel_cannot_update_or_delete(kernel, space):
    """The grant is the first lock and the trigger is the second, as
    migration 0001 gave `events`."""
    await seed(kernel, space)
    await kernel.commit()

    for sql in (
        f"UPDATE inbound_items SET routed_by = 'x' WHERE space_id = '{space}'",
        f"DELETE FROM inbound_items WHERE space_id = '{space}'",
        "TRUNCATE inbound_items",
    ):
        with pytest.raises(
            (psycopg.errors.InsufficientPrivilege, psycopg.errors.RaiseException)
        ):
            await kernel.execute(sql)
        await kernel.rollback()

    rows = await (
        await kernel.execute(
            "SELECT count(*) FROM inbound_items WHERE space_id = %s", (space,)
        )
    ).fetchall()
    assert rows == [(1,)]


async def test_migrator_is_blocked_by_the_trigger_too(migrator, kernel, space):
    """Prereqs finding 6: the trigger blocks the owner as well, and a
    destroy migration disables it inside its own transaction."""
    await seed(kernel, space)
    await kernel.commit()
    with pytest.raises(psycopg.errors.RaiseException):
        await migrator.execute(
            "DELETE FROM inbound_items WHERE space_id = %s", (space,)
        )
    await migrator.rollback()


async def test_the_key_refuses_a_second_row_for_one_message(kernel, space):
    await seed(kernel, space, external_id="dup")
    with pytest.raises(psycopg.errors.UniqueViolation):
        await seed(kernel, space, external_id="dup")
    await kernel.rollback()


async def test_the_same_message_may_have_a_row_in_a_second_space(kernel, space):
    """Row-level security means an item assigned later to another space
    needs a row in that space for that space's render to see it."""
    other = space + "-other"
    await seed(kernel, space, external_id="shared")
    await seed(kernel, other, external_id="shared")
    await kernel.commit()
    rows = await (
        await kernel.execute(
            "SELECT space_id FROM inbound_items WHERE external_id = 'shared' "
            "AND space_id IN (%s, %s) ORDER BY space_id",
            (space, other),
        )
    ).fetchall()
    assert sorted(rows) == sorted([(space,), (other,)])


# ---------------------------------------------------------------------------
# Plan 03 task 8: recording writes the row and the event together


def item(sender: str, *, account: str, external_id: str) -> InboundItem:
    return InboundItem(
        id=uuid.uuid4().hex,
        connector="gmail",
        account=account,
        external_id=external_id,
        headers={"from": sender},
        received_at=datetime(2026, 9, 21, tzinfo=UTC),
        space=UNASSIGNED_SPACE_ID,
        routed_by=None,
    )


def one_space(space_id: str, account: str, domain: str, **narrower) -> Space:
    return Space(
        id=space_id,
        kind="client",
        roots=["/repo"],
        max_effect_class="propose",
        connectors=[
            Connector(
                kind="gmail",
                account=account,
                route=RoutingRule(sender_domain=domain, **narrower),
            )
        ],
    )


async def rows_for(conn, account: str) -> list[tuple]:
    return await (
        await conn.execute(
            "SELECT space_id, external_id, routed_by FROM inbound_items "
            "WHERE account = %s ORDER BY external_id, space_id",
            (account,),
        )
    ).fetchall()


async def events_for(conn, account: str) -> list[tuple]:
    """The inbound events whose payload carries this account's item. The
    lookup is nested, so it is hand SQL rather than `events.read_for`."""
    return await (
        await conn.execute(
            "SELECT space_id, type, payload FROM events "
            "WHERE payload->'item'->>'account' = %s ORDER BY id",
            (account,),
        )
    ).fetchall()


async def test_routed_item_writes_row_and_event(kernel, space):
    account = f"tom+{uuid.uuid4().hex[:8]}@yuda.me"
    spaces = {space: one_space(space, account, "psyoptimal.com")}
    built = item("Ana Ruiz <ana@psyoptimal.com>", account=account, external_id="m-1")

    recorded = await ingest(kernel, spaces, [built])

    rule = f"{space}:gmail:{account}:sender_domain=psyoptimal.com"
    assert [(i.space, i.routed_by) for i in recorded] == [(space, rule)]
    assert await rows_for(kernel, account) == [(space, "m-1", rule)]

    events = await events_for(kernel, account)
    assert [(s, t) for s, t, _ in events] == [(space, "inbound.routed")]
    payload = events[0][2]
    assert payload["rule"] == rule
    assert payload["item"]["external_id"] == "m-1"
    assert payload["item"]["space"] == space
    await kernel.rollback()


async def test_unassigned_item_writes_row_and_event_with_candidates(kernel, space):
    """Two spaces claim it, so routing refuses to pick and the event names
    both. A stranger no rule claims lands in the same place with an empty
    candidate list."""
    account = f"tom+{uuid.uuid4().hex[:8]}@yuda.me"
    other = space + "-b"
    spaces = {
        space: one_space(space, account, "psyoptimal.com"),
        other: one_space(other, account, "psyoptimal.com", label="clients"),
    }
    claimed = item(
        "ana@psyoptimal.com", account=account, external_id="m-both"
    ).model_copy(update={"headers": {"from": "ana@psyoptimal.com", "label": "clients"}})
    stranger = item("bo@acme.com", account=account, external_id="m-none")

    await ingest(kernel, spaces, [claimed, stranger])

    assert await rows_for(kernel, account) == [
        (UNASSIGNED_SPACE_ID, "m-both", None),
        (UNASSIGNED_SPACE_ID, "m-none", None),
    ]
    events = await events_for(kernel, account)
    assert [(s, t) for s, t, _ in events] == [
        (UNASSIGNED_SPACE_ID, "inbound.unassigned"),
        (UNASSIGNED_SPACE_ID, "inbound.unassigned"),
    ]
    assert events[0][2]["candidates"] == sorted([space, other])
    assert events[1][2]["candidates"] == []
    await kernel.rollback()


async def test_ingest_returns_only_new_items(kernel, space):
    account = f"tom+{uuid.uuid4().hex[:8]}@yuda.me"
    spaces = {space: one_space(space, account, "psyoptimal.com")}
    first = item("ana@psyoptimal.com", account=account, external_id="m-2")
    # A second read of the same message mints a fresh id and reaches the same
    # key, which is what makes an overlapping poll window harmless.
    again = item("ana@psyoptimal.com", account=account, external_id="m-2")
    assert again.id != first.id

    assert len(await ingest(kernel, spaces, [first])) == 1
    assert await ingest(kernel, spaces, [again, first]) == []

    assert len(await rows_for(kernel, account)) == 1
    assert len(await events_for(kernel, account)) == 1
    assert await record_inbound(kernel, recorded_copy(first, space)) is False
    await kernel.rollback()


def recorded_copy(built: InboundItem, space_id: str) -> InboundItem:
    return built.model_copy(update={"space": space_id, "routed_by": "whatever"})


SENDERS = st.sampled_from(
    ["ana@psyoptimal.com", "Ana <ANA@PsyOptimal.com>", "bo@acme.com", ""]
)


@settings(max_examples=25, deadline=None)
@given(
    batch=st.lists(
        st.tuples(st.sampled_from(["a", "b", "c"]), SENDERS), min_size=1, max_size=8
    ),
    split=st.integers(0, 8),
)
def test_record_inbound_is_idempotent(batch, split):
    """However the items are batched and repeated, `inbound_items` holds one
    row per distinct key and the inbound events number exactly the rows."""

    async def go():
        account = f"tom+{uuid.uuid4().hex[:8]}@yuda.me"
        space_id = f"space-{uuid.uuid4().hex[:8]}"
        spaces = {space_id: one_space(space_id, account, "psyoptimal.com")}
        items = [
            item(sender, account=account, external_id=external)
            for external, sender in batch
        ]
        expected = {
            (external, space_id if "psyoptimal.com" in sender.lower() else "unassigned")
            for external, sender in batch
        }

        conn = await psycopg.AsyncConnection.connect(dsn("kernel_rw"))
        try:
            # Two calls over the same items, split anywhere, plus a replay of
            # the whole batch: the outcome is the batch's key set either way.
            await ingest(conn, spaces, items[:split])
            await ingest(conn, spaces, items[split:])
            await ingest(conn, spaces, items)

            rows = await rows_for(conn, account)
            assert {(external, space) for space, external, _ in rows} == expected
            assert len(rows) == len(expected)
            assert len(await events_for(conn, account)) == len(rows)
        finally:
            await conn.close()

    asyncio.run(go())
