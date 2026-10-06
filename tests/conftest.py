"""Real Postgres: a fresh test database (`settings.test_database`) per session.

The ledger can never be emptied, so each session drops and recreates the
database as its owner. `db.migrate` touches no role password, password
file, or `pg_hba.conf`, so a test run leaves the machine cluster's
credentials as it found them. Every test marks its live spend with
`@pytest.mark.spend(usd=...)`. A session's task directories (turn output
among them) live in a temporary `VALOR_WORK`, never the machine's.

The effect lock files (`settings.performing_dir`) go to a temporary
directory of the session's own, set in the environment before the settings
are built, so the suite and every process it starts write nothing under the
kernel key directory.
"""

import atexit
import os
import shutil
import tempfile

import pytest

os.environ["VALOR_WORK"] = tempfile.mkdtemp(prefix="valor-test-work-")
atexit.register(shutil.rmtree, os.environ["VALOR_WORK"], True)
os.environ["VALOR_PERFORMING_DIR"] = tempfile.mkdtemp(prefix="valor-test-performing-")
atexit.register(shutil.rmtree, os.environ["VALOR_PERFORMING_DIR"], True)
# The task Postgres and Redis ports the kernel chooses, in the tests' own span when it
# is set, so a kernel the tests start listens where the tests' servers do.
if os.environ.get("VALOR_TEST_PORTS"):
    os.environ.setdefault("VALOR_PG_PORTS", os.environ["VALOR_TEST_PORTS"])
    os.environ.setdefault("VALOR_REDIS_PORTS", os.environ["VALOR_TEST_PORTS"])

from core import db  # settings read VALOR_WORK on import
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


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    """A failure that met a denial of the sandbox the suite runs under, in
    its traceback or in what it wrote to stderr (a kernel step logs its
    failure there), is reported skipped, naming the denial
    (`tests/denials.py`)."""
    report = yield
    if report.failed and call.excinfo is not None:
        from tests import denials

        text = "\n".join(
            [str(report.longrepr), *(body for title, body in report.sections if "stderr" in title)]
        )
        why = denials.reason(text)
        if why is not None:
            report.outcome = "skipped"
            report.longrepr = (str(item.path), item.location[1] or 0, f"Skipped: {why}")
    return report
