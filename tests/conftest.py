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


@pytest.fixture(scope="session")
def mail(tmp_path_factory):
    """Dovecot behind a TLS terminator, and the local SMTP server, started
    once (`tests/mailserver.py`). Fails naming what is missing."""
    from tests import mailserver

    try:
        servers = mailserver.start(tmp_path_factory.mktemp("mail"))
    except (RuntimeError, OSError) as e:
        pytest.fail(f"the local mail servers did not start: {e}")
    yield servers
    mailserver.stop(servers)


@pytest.fixture
def mailbox(mail):
    """The servers with empty folders and default behavior."""
    mail.reset()
    return mail
