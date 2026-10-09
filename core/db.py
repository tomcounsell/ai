"""Connections and the schema.

The kernel connects as `valor_kernel`, which can read and append and never
update or delete the ledger. Only `migrate` connects as the owner: it applies the schema, the verdict
constraint, correction 1, and the seeded guards. Every
connection string names the kernel's password file (`settings.pg_passfile`)
and never a password; the credential itself is `core/credentials.py`'s.
"""

from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from core import corrections, guards, machine
from core.settings import settings

SCHEMA = Path(__file__).with_name("schema.sql")


async def connect(dsn: str | None = None, *, application_name: str | None = None) -> psycopg.AsyncConnection:
    """Autocommit, so a read never holds a transaction open and every
    `conn.transaction()` block is a real transaction, committed on exit.
    `application_name` names the process in `pg_stat_activity`."""
    extra = {"application_name": application_name} if application_name else {}
    return await psycopg.AsyncConnection.connect(dsn or settings.dsn(), autocommit=True, **extra)


def migrate(
    database: str | None = None,
    *,
    fresh: bool = False,
    schema: Path = SCHEMA,
    host: str | None = None,
    port: int | None = None,
    passfile: str | None = None,
) -> str:
    """Create the kernel and memory roles and the database if missing,
    apply `schema` as the owner, record correction 1 if the ledger has
    none, give the schema `memory` to memory's role, and refuse every
    effect still held for an approval (`_refuse_held`). `fresh`
    drops the database first; tests use it, since the ledger itself can
    never be emptied. Touches no role password, no password file, and no
    `pg_hba.conf` (that is `credentials.secure_login`). Returns the kernel
    DSN."""
    database = database or settings.database
    where = {"host": host, "port": port, "passfile": passfile}
    with psycopg.connect(settings.dsn(owner=True, database="postgres", **where), autocommit=True) as conn:
        for name in (settings.kernel_role, settings.memory_role):
            role = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (name,)).fetchone()
            if role is None:
                conn.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(name)))
        if fresh:
            conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(database)))
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database,)).fetchone()
        if exists is None:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    with psycopg.connect(settings.dsn(owner=True, database=database, **where)) as conn:
        conn.execute(schema.read_text())
        conn.commit()
        verdict_constraint(conn)
        _seed_correction_one(conn)
        guards.seed(conn)
        _refuse_held(conn)
        memory_schema(conn)
    return settings.dsn(database=database, **where)


def memory_schema(conn: psycopg.Connection) -> None:
    """The schema `memory`, and every table in it, owned by memory's role:
    it creates its own tables there and reaches nothing else. A schema or
    table the owner holds (a restore made without `--no-owner` skipped, or
    a table made before the role) is given back. An owner that is not a
    superuser (a workspace's or the VM's `app`) holds no SET on a role it
    created, which giving a schema to that role needs, so it grants the
    role to itself first."""
    role = sql.Identifier(settings.memory_role)
    with conn.transaction():
        can_set = conn.execute("SELECT pg_has_role(current_user, %s, 'SET')", (settings.memory_role,))
        if not can_set.fetchone()[0]:
            conn.execute(sql.SQL("GRANT {} TO CURRENT_USER").format(role))
        conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS memory AUTHORIZATION {}").format(role))
        conn.execute(sql.SQL("ALTER SCHEMA memory OWNER TO {}").format(role))
        tables = conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'memory' AND tableowner <> %s",
            (settings.memory_role,),
        ).fetchall()
        for (table,) in tables:
            conn.execute(sql.SQL("ALTER TABLE memory.{} OWNER TO {}").format(sql.Identifier(table), role))


def verdict_constraint(
    conn: psycopg.Connection, expression: str | None = None, name: str | None = None
) -> bool:
    """Put the verdict enum's CHECK constraint in place, from
    `machine.constraint_sql`. Its name carries a digest of its SQL: the
    current name in place means nothing to do; otherwise every older one is
    dropped and the current one added in one transaction. It is added `NOT
    VALID`, so rows already written are never rechecked: a verdict value may
    be removed from `VERDICTS` without history refusing the change, and the
    constraint still refuses every new row outside the enum. Returns whether
    it changed anything."""
    expression = expression or machine.constraint_sql()
    name = name or machine.constraint_name()
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('verdict-constraint', 0))")
        names = [
            r[0]
            for r in conn.execute(
                "SELECT conname FROM pg_constraint WHERE conrelid = 'events'::regclass "
                "AND conname LIKE 'events_verdict_in_enum%%'"
            ).fetchall()
        ]
        if names == [name]:
            return False
        for old in names:
            conn.execute(sql.SQL("ALTER TABLE events DROP CONSTRAINT {}").format(sql.Identifier(old)))
        conn.execute(
            sql.SQL("ALTER TABLE events ADD CONSTRAINT {} CHECK ({}) NOT VALID").format(
                sql.Identifier(name), sql.SQL(expression)
            )
        )
    return True


def _seed_correction_one(conn: psycopg.Connection) -> None:
    """Correction 1, the governance paragraph, global and direct, unless the
    ledger already holds a correction numbered 1."""
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (corrections.STREAM,))
        found = conn.execute(
            "SELECT 1 FROM events WHERE task_id = %s AND type = 'correction.recorded' "
            "AND payload->>'number' = '1'",
            (corrections.STREAM,),
        ).fetchone()
        if found is None:
            conn.execute(
                "INSERT INTO events (task_id, type, payload) VALUES (%s, 'correction.recorded', %s)",
                (
                    corrections.STREAM,
                    Jsonb(
                        corrections.payload(
                            1,
                            corrections.governance_paragraph(),
                            by="tom",
                            via="CLAUDE.md, seeded by migrate",
                        )
                    ),
                ),
            )


HELD_REASON = "held for an approval the kernel no longer takes; request it again"


def _refuse_held(conn: psycopg.Connection) -> int:
    """One `effect.refused`, `at: migrate`, for each `effect.held` with no
    intent, outcome, or refusal and no `release.requested` naming a bridge
    `owner` (a bridge's outbox releases that one; a kernel-owned release
    is one the kernel no longer performs, so that row is refused). It carries the held row's
    action, so a refused merge is still the task's merge effect and
    `verdicts.ensure_merge` requests it again. Each is written under its
    task's lock, the selector read again inside it, so a second run writes
    nothing. Returns how many it wrote."""
    select = (
        "SELECT h.task_id, h.payload FROM events h WHERE h.type = 'effect.held' {} AND NOT EXISTS ("
        "SELECT 1 FROM events e WHERE e.payload->>'effect_id' = h.payload->>'effect_id' "
        "AND e.type IN ('effect.intent', 'effect.outcome', 'effect.refused')) AND NOT EXISTS ("
        "SELECT 1 FROM events r WHERE r.type = 'release.requested' "
        "AND r.payload->>'effect_id' = h.payload->>'effect_id' AND r.payload->>'owner' <> 'kernel') "
        "ORDER BY h.id"
    )
    written = 0
    found = conn.execute(select.format("")).fetchall()
    conn.commit()  # each refusal below is its own transaction
    for task_id, held in found:
        with conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"task:{task_id}",))
            still = conn.execute(
                select.format("AND h.payload->>'effect_id' = %s"), (held["effect_id"],)
            ).fetchone()
            if still is None:
                continue
            conn.execute(
                "INSERT INTO events (task_id, type, payload) VALUES (%s, 'effect.refused', %s)",
                (task_id, Jsonb({**held, "reason": HELD_REASON, "at": "migrate"})),
            )
            written += 1
    return written
