"""Alembic environment. Raw SQL only: no metadata, no autogenerate.

Migrations run under a bootstrap connection named by CORI_MIGRATOR_DSN. The
first migration creates the roles, so the bootstrap role must be allowed to
CREATE ROLE and CREATE EXTENSION. Locally that is the Homebrew superuser
(your macOS user, trust auth on localhost); in CI it is `postgres`.
"""

import os

from alembic import context
from sqlalchemy import create_engine

DEFAULT_DSN = "postgresql://localhost:5432/cori"


def dsn() -> str:
    url = os.environ.get("CORI_MIGRATOR_DSN", DEFAULT_DSN)
    # SQLAlchemy needs the driver named; the app uses psycopg 3 directly.
    return url.replace("postgresql://", "postgresql+psycopg://", 1)


def run_migrations_online() -> None:
    engine = create_engine(dsn())
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=None,
            transaction_per_migration=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise SystemExit("offline mode is not supported; migrations run against a live DB")

run_migrations_online()
