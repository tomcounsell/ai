"""Real Postgres: a fresh `valor_rebuild_test` database per session.

The ledger can never be emptied, so each session drops and recreates the
database as its owner. Every test marks its live spend with
`@pytest.mark.spend(usd=...)`.
"""

import os

import pytest

from core import db

TEST_DB = os.environ.get("VALOR_TEST_DB", "valor_rebuild_test")


@pytest.fixture(scope="session")
def dsn() -> str:
    return db.migrate(TEST_DB, fresh=True)


@pytest.fixture(scope="session")
def owner_dsn(dsn) -> str:
    from core.settings import settings

    return settings.dsn(owner=True, database=TEST_DB)
