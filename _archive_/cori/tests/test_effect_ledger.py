"""The `effect_ledger` table and the writers that are its only door.

The table carries the locks of migration 0001 and the uniqueness rule of
seams §5.3: one row per event, never an update, so an effect's state is a
fold over its rows. Plan 07 tasks 2 and 3.
"""

import uuid

import psycopg
import pytest

from broker import ledger
from schemas.effect import ConnectorRead, PushBranch
from tests.conftest import requires_postgres

pytestmark = requires_postgres

TABLE = "effect_ledger"


async def insert(conn, space, *, effect_id=None, event="intent", **over) -> str:
    effect_id = effect_id or uuid.uuid4().hex
    row = {
        "effect_id": effect_id,
        "space_id": space,
        "objective_id": uuid.uuid4().hex,
        "brief_id": uuid.uuid4().hex,
        "generation": 1,
        "action_type": "push_branch",
        "effect_class": "propose",
        "idempotency_key": "yudame/cori-sandbox#cori/x@" + "a" * 40,
        "target": "github.com/yudame",
        "payload_sha256": "b" * 64,
        "payload": "{}",
        "event": event,
    }
    row.update(over)
    columns = ", ".join(row)
    holders = ", ".join(["%s"] * len(row))
    await conn.execute(
        f"INSERT INTO {TABLE} ({columns}) VALUES ({holders})", tuple(row.values())
    )
    return effect_id


async def privileges(conn, role: str) -> set[str]:
    rows = await (
        await conn.execute(
            "SELECT privilege_type FROM information_schema.role_table_grants "
            "WHERE table_name = %s AND grantee = %s",
            (TABLE, role),
        )
    ).fetchall()
    return {r[0] for r in rows}


# ---------------------------------------------------------------------------
# Task 2: the table


async def test_grants_are_exact(migrator):
    assert await privileges(migrator, "kernel_rw") == {"INSERT", "SELECT"}
    assert await privileges(migrator, "context_ro") == {"SELECT"}


async def test_update_refused_by_grant_then_trigger(kernel, migrator, space):
    await insert(kernel, space)
    await kernel.commit()
    for sql in (
        f"UPDATE {TABLE} SET error = 'edited' WHERE space_id = %s",
        f"DELETE FROM {TABLE} WHERE space_id = %s",
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await kernel.execute(sql, (space,))
        await kernel.rollback()
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        await kernel.execute(f"TRUNCATE {TABLE}")
    await kernel.rollback()

    # The trigger is the second lock: the owner is refused too.
    for sql in (
        f"UPDATE {TABLE} SET error = 'edited' WHERE space_id = %s",
        f"DELETE FROM {TABLE} WHERE space_id = %s",
    ):
        with pytest.raises(psycopg.errors.RaiseException) as info:
            await migrator.execute(sql, (space,))
        assert "append-only" in str(info.value)
        await migrator.rollback()
    with pytest.raises(psycopg.errors.RaiseException):
        await migrator.execute(f"TRUNCATE {TABLE}")
    await migrator.rollback()


async def test_context_ro_reads_one_space(kernel, context, space):
    other = space + "-other"
    await insert(kernel, space)
    await insert(kernel, other)
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
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        await insert(context, space)
    await context.rollback()


async def test_one_row_per_effect_id_and_event(kernel, space):
    effect_id = await insert(kernel, space, event="intent")
    await insert(kernel, space, effect_id=effect_id, event="outcome")
    await kernel.commit()
    with pytest.raises(psycopg.errors.UniqueViolation):
        await insert(kernel, space, effect_id=effect_id, event="intent")
    await kernel.rollback()


async def test_the_checks_bound_class_event_and_kind(kernel, space):
    for over in (
        {"effect_class": "class 1"},
        {"event": "committed"},
        {"event": "outcome", "outcome_kind": "maybe"},
        {"event": "intent", "outcome_kind": "done"},
    ):
        with pytest.raises(psycopg.errors.CheckViolation):
            await insert(kernel, space, **over)
        await kernel.rollback()


async def test_a_kernel_read_may_leave_the_brief_fields_null(kernel, space):
    await insert(
        kernel,
        space,
        objective_id=None,
        brief_id=None,
        generation=None,
        action_type="connector_read",
        effect_class="read",
        target="mailto:tom@yuda.me",
        idempotency_key="gmail:tom@yuda.me:id:abc:1758412800",
    )
    await kernel.commit()


# ---------------------------------------------------------------------------
# Task 3: the writers and the fold


def push_fields(space: str, **over) -> dict:
    objective_id = over.pop("objective_id", uuid.uuid4().hex)
    brief_id = over.pop("brief_id", uuid.uuid4().hex)
    action = PushBranch(
        space=space,
        objective_id=objective_id,
        brief_id=brief_id,
        repo="yudame/cori-sandbox",
        branch=f"cori/{objective_id}",
        source_dir="/tmp/work",
        head_sha="a" * 40,
    )
    fields = {
        "action_type": action.action_type,
        "effect_class": action.effect_class,
        "space": space,
        "target": action.target(),
        "idempotency_key": action.idempotency_key,
        "payload": action.model_dump(mode="json"),
        "objective_id": objective_id,
        "brief_id": brief_id,
        "generation": 1,
    }
    fields.update(over)
    return fields


def read_fields(space: str, **over) -> dict:
    action = ConnectorRead(
        space=space,
        objective_id=None,
        brief_id=None,
        connector="gmail",
        account="tom@yuda.me",
        query="from:@psyoptimal.com after:1758412800",
        idempotency_key="gmail:tom@yuda.me:from:@psyoptimal.com after:1758412800:99",
    )
    fields = {
        "action_type": action.action_type,
        "effect_class": action.effect_class,
        "space": space,
        "target": action.target(),
        "idempotency_key": action.idempotency_key,
        "payload": action.model_dump(mode="json"),
    }
    fields.update(over)
    return fields


async def events_of(conn, effect_id: str) -> list[str]:
    rows = await (
        await conn.execute(
            f"SELECT event FROM {TABLE} WHERE effect_id = %s ORDER BY id",
            (effect_id,),
        )
    ).fetchall()
    return [r[0] for r in rows]


async def test_fold_per_effect_id(kernel, space):
    """`intent` then a close is closed; `intent` alone is dangling; a second
    intent for one effect id is refused by the table."""
    fields = push_fields(space)
    closed = await ledger.intent(kernel, **fields)
    await ledger.outcome(
        kernel, effect_id=closed, kind="done", result={"sha": "a" * 40}, **fields
    )
    open_fields = push_fields(space)
    still_open = await ledger.intent(kernel, **open_fields)
    turned_down = await ledger.refused(
        kernel, error="above_space_ceiling", **push_fields(space)
    )
    await kernel.commit()

    assert await events_of(kernel, closed) == ["intent", "outcome"]
    assert await events_of(kernel, still_open) == ["intent"]
    assert await events_of(kernel, turned_down.effect_id) == ["refused"]
    assert turned_down.kind == "refused"

    ids = [row["effect_id"] for row in await ledger.dangling(kernel)]
    assert still_open in ids
    assert closed not in ids and turned_down.effect_id not in ids

    assert await ledger.open_intent_for_key(kernel, open_fields["idempotency_key"]) == (
        still_open
    )
    assert await ledger.open_intent_for_key(kernel, fields["idempotency_key"]) is None
    earlier = await ledger.closed_for_key(kernel, fields["idempotency_key"])
    assert earlier is not None
    assert earlier.effect_id == closed and earlier.result == {"sha": "a" * 40}
    assert await ledger.closed_for_key(kernel, open_fields["idempotency_key"]) is None

    with pytest.raises(psycopg.errors.UniqueViolation):
        await ledger.intent(kernel, effect_id=closed, **fields)
    await kernel.rollback()


async def test_a_failed_close_is_not_a_closed_key(kernel, space):
    """`failed` and `unknown` say the effect did not land, so neither short
    circuits a later request for the key (plan 07, `closed_for_key`)."""
    for kind in ("failed", "unknown"):
        fields = push_fields(space)
        effect_id = await ledger.intent(kernel, **fields)
        await ledger.outcome(
            kernel, effect_id=effect_id, kind=kind, error="boom", **fields
        )
        await kernel.commit()
        assert await ledger.closed_for_key(kernel, fields["idempotency_key"]) is None


async def test_a_recovered_reconcile_closes_the_key(kernel, space):
    fields = push_fields(space)
    effect_id = await ledger.intent(kernel, **fields)
    await ledger.reconciled(
        kernel,
        effect_id=effect_id,
        kind="recovered",
        result={"sha": "a" * 40},
        **fields,
    )
    await kernel.commit()
    earlier = await ledger.closed_for_key(kernel, fields["idempotency_key"])
    assert earlier is not None and earlier.kind == "recovered"
    assert effect_id not in [row["effect_id"] for row in await ledger.dangling(kernel)]


async def test_kernel_read_rows_have_null_brief_fields(kernel, space):
    """A row written with the three brief fields None reads back with a
    payload `ConnectorRead.model_validate` accepts (amendment G)."""
    fields = read_fields(space)
    effect_id = await ledger.intent(kernel, **fields)
    await ledger.outcome(
        kernel, effect_id=effect_id, kind="done", result={"items": []}, **fields
    )
    await kernel.commit()

    row = await (
        await kernel.execute(
            f"SELECT objective_id, brief_id, generation, payload, payload_sha256 "
            f"FROM {TABLE} WHERE effect_id = %s AND event = 'intent'",
            (effect_id,),
        )
    ).fetchone()
    objective_id, brief_id, generation, payload, payload_sha256 = row
    assert (objective_id, brief_id, generation) == (None, None, None)
    rebuilt = ConnectorRead.model_validate(payload)
    assert rebuilt.objective_id is None and rebuilt.brief_id is None
    assert payload_sha256 == rebuilt.payload_sha256()
