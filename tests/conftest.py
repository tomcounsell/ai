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


@pytest.fixture(autouse=True)
def release_ports(request):
    """After a test that used the ledger, stop the services of every task
    whose workspace still holds ports and record `workspace.removed`, so a
    narrow `VALOR_TEST_PORTS` span is not used up by earlier tests in the
    same session."""
    yield
    if "dsn" not in request.fixturenames:
        return
    from pathlib import Path

    import psycopg
    from psycopg.types.json import Jsonb

    from core import workspace

    with psycopg.connect(request.getfixturevalue("dsn"), autocommit=True) as conn:
        rows = conn.execute(
            "SELECT d.id, d.body->>'mirror' FROM documents d WHERE d.kind = 'task' "
            "AND d.body->'project'->'ports' IS NOT NULL AND NOT EXISTS ("
            "  SELECT 1 FROM events e WHERE e.task_id = d.id AND e.type = 'workspace.removed')"
        ).fetchall()
        for task_id, mirror in rows:
            if mirror:
                workspace.stop_services(task_id, workspace.Layout(Path(mirror).parent))
            conn.execute(
                "INSERT INTO events (task_id, type, payload) VALUES (%s, 'workspace.removed', %s)",
                (task_id, Jsonb({"path": mirror, "by": "tests"})),
            )
