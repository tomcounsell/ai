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


async def connect(dsn: str | None = None) -> psycopg.AsyncConnection:
    """Autocommit, so a read never holds a transaction open and every
    `conn.transaction()` block is a real transaction, committed on exit."""
    return await psycopg.AsyncConnection.connect(dsn or settings.dsn(), autocommit=True)


def migrate(
    database: str | None = None,
    *,
    fresh: bool = False,
    schema: Path = SCHEMA,
    host: str | None = None,
    port: int | None = None,
    passfile: str | None = None,
) -> str:
    """Create the kernel role and the database if missing, apply `schema`
    as the owner, and record correction 1 if the ledger has none. `fresh`
    drops the database first; tests use it, since the ledger itself can
    never be emptied. Touches no role password, no password file, and no
    `pg_hba.conf` (that is `credentials.secure_login`). Returns the kernel
    DSN."""
    database = database or settings.database
    where = {"host": host, "port": port, "passfile": passfile}
    with psycopg.connect(settings.dsn(owner=True, database="postgres", **where), autocommit=True) as conn:
        role = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (settings.kernel_role,)).fetchone()
        if role is None:
            conn.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(settings.kernel_role)))
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
    return settings.dsn(database=database, **where)


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
