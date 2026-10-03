"""Real Postgres: a fresh test database (`settings.test_database`) per session.

The ledger can never be emptied, so each session drops and recreates the
database as its owner. `db.migrate` touches no role password, password
file, or `pg_hba.conf`, so a test run leaves the machine cluster's
credentials as it found them. Every test marks its live spend with
`@pytest.mark.spend(usd=...)`.

The effect lock files (`settings.performing_dir`) go to a temporary
directory of the session's own, set in the environment before the settings
are built, so the suite and every process it starts write nothing under the
kernel key directory.
"""

import os
import shutil
import tempfile

_PERFORMING = tempfile.mkdtemp(prefix="valor-test-performing-")
os.environ["VALOR_PERFORMING_DIR"] = _PERFORMING

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


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_PERFORMING, ignore_errors=True)
