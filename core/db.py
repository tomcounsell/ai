"""Connections and the schema.

The kernel connects as `valor_kernel`, which can read and append and never
update or delete the ledger. Only `migrate` connects as the owner.
"""

from pathlib import Path

import psycopg
from psycopg import sql

from core.settings import settings

SCHEMA = Path(__file__).with_name("schema.sql")


async def connect(dsn: str | None = None) -> psycopg.AsyncConnection:
    """Autocommit, so a read never holds a transaction open and every
    `conn.transaction()` block is a real transaction, committed on exit."""
    return await psycopg.AsyncConnection.connect(dsn or settings.dsn(), autocommit=True)


def migrate(database: str | None = None, *, fresh: bool = False) -> str:
    """Create the kernel role and the database if missing, then apply the
    schema as the owner. `fresh` drops the database first; tests use it,
    since the ledger itself can never be emptied. Returns the kernel DSN."""
    database = database or settings.database
    with psycopg.connect(settings.dsn(owner=True, database="postgres"), autocommit=True) as conn:
        role = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (settings.kernel_role,)).fetchone()
        if role is None:
            conn.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(settings.kernel_role)))
        if fresh:
            conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(database)))
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database,)).fetchone()
        if exists is None:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    with psycopg.connect(settings.dsn(owner=True, database=database)) as conn:
        conn.execute(SCHEMA.read_text())
    return settings.dsn(database=database)
