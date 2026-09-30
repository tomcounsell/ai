"""Migration 0002: the two indexes and a reject_mutation() that names the
table it fires on. Seams §6; plan findings 2026-09-19, finding 4."""

import psycopg
import pytest

from tests.conftest import requires_postgres

pytestmark = requires_postgres


async def test_events_indexes_exist(migrator):
    rows = await (
        await migrator.execute(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'events'"
        )
    ).fetchall()
    assert {r[0] for r in rows} == {
        "events_pkey",
        "events_space_id_idx",
        "events_type_id_idx",
        "events_payload_gin",
    }


async def _update_refused_with(conn, sql, params=()) -> str:
    with pytest.raises(psycopg.errors.RaiseException) as info:
        await conn.execute(sql, params)
    await conn.rollback()
    return info.value.diag.message_primary


async def test_reject_mutation_names_the_firing_table(migrator, space):
    await migrator.execute("CREATE TEMP TABLE tmp_append_only (id int)")
    await migrator.execute("INSERT INTO tmp_append_only VALUES (1)")
    await migrator.execute(
        "CREATE TRIGGER tmp_append_only_guard BEFORE UPDATE OR DELETE "
        "ON tmp_append_only FOR EACH ROW EXECUTE FUNCTION reject_mutation()"
    )
    await migrator.execute(
        "INSERT INTO events (space_id, type) VALUES (%s, 'message.sent')", (space,)
    )
    await migrator.commit()

    message = await _update_refused_with(migrator, "UPDATE tmp_append_only SET id = 2")
    assert message.startswith("tmp_append_only is append-only"), message
    message = await _update_refused_with(
        migrator, "UPDATE events SET type = 'y' WHERE space_id = %s", (space,)
    )
    assert message.startswith("events is append-only"), message
