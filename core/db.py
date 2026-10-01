"""Connections and the schema.

The kernel connects as `valor_kernel`, which can read and append and never
update or delete the ledger. Only `migrate` connects as the owner. Every
connection string names the kernel's password file (`settings.pg_passfile`)
and never a password; the credential itself is `core/credentials.py`'s.
"""

from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from core import corrections
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
        _seed_correction_one(conn)
    return settings.dsn(database=database, **where)


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
