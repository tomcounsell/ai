"""Real Postgres: a fresh test database (`settings.test_database`) per session.

The ledger can never be emptied, so each session drops and recreates the
database as its owner. `db.migrate` touches no role password, password
file, or `pg_hba.conf`, so a test run leaves the machine cluster's
credentials as it found them. Every test marks its live spend with
`@pytest.mark.spend(usd=...)`.
"""

import pytest

from core import db
from core.settings import settings

TEST_DB = settings.test_database


@pytest.fixture(scope="session")
def dsn() -> str:
    return db.migrate(TEST_DB, fresh=True)


@pytest.fixture(scope="session")
def owner_dsn(dsn) -> str:
    return settings.dsn(owner=True, database=TEST_DB)
