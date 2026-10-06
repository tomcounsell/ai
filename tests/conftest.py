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

A test marked `macos` needs macOS itself (`sandbox-exec`, `sandbox_check`,
`/bin/ps -E`, the Command Line Tools' git, `security`) and is skipped off
Darwin, so the verification VM runs the rest. A test marked `container`
needs Apple's `container` and is skipped where it cannot be run: not
installed, or denied by the sandbox the suite runs under (the check profile
denies it). Every other test takes the machine lock and the image records
in a directory of the session's own, never the machine's, which the check
profile denies too.
"""

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

os.environ["VALOR_WORK"] = tempfile.mkdtemp(prefix="valor-test-work-")
atexit.register(shutil.rmtree, os.environ["VALOR_WORK"], True)
os.environ["VALOR_PERFORMING_DIR"] = tempfile.mkdtemp(prefix="valor-test-performing-")
atexit.register(shutil.rmtree, os.environ["VALOR_PERFORMING_DIR"], True)
# The ports reserved for the task services a test provisions (`tests/ports.py`).
_fd, os.environ["VALOR_TEST_RESERVED_PORTS"] = tempfile.mkstemp(prefix="valor-test-reserved-ports-")
os.close(_fd)
# The task Postgres and Redis ports the kernel chooses, in the tests' own span when it
# is set, so a kernel the tests start listens where the tests' servers do.
if os.environ.get("VALOR_TEST_PORTS"):
    os.environ.setdefault("VALOR_PG_PORTS", os.environ["VALOR_TEST_PORTS"])
    os.environ.setdefault("VALOR_REDIS_PORTS", os.environ["VALOR_TEST_PORTS"])
# Scratch clusters start from `settings.pg_bin`, the Homebrew PostgreSQL 18 by
# default; where it is absent (the verification VM), Debian's PostgreSQL 18.
if "VALOR_PG_BIN" not in os.environ and not Path("/opt/homebrew/opt/postgresql@18/bin/initdb").exists():
    os.environ["VALOR_PG_BIN"] = "/usr/lib/postgresql/18/bin"

from core import binaries, container, db  # settings read VALOR_WORK on import
from core.settings import settings
from tests import ports

atexit.register(ports.release)

# The skip reason of a `macos` test; `core.container` counts the skips that
# carry it.
MACOS_ONLY = "macOS only"


def pytest_collection_modifyitems(config, items):
    darwin = sys.platform == "darwin"
    runtime = container.present()
    for item in items:
        if not darwin and item.get_closest_marker("macos"):
            item.add_marker(pytest.mark.skip(reason=f"{MACOS_ONLY}: needs macOS itself"))
        if not runtime and item.get_closest_marker("container"):
            item.add_marker(pytest.mark.skip(reason=f"needs Apple's container at {binaries.CONTAINER}"))


_STATE = Path(tempfile.mkdtemp(prefix="valor-test-container-"))
atexit.register(shutil.rmtree, _STATE, True)


@pytest.fixture(autouse=True)
def session_machine_lock(request, monkeypatch):
    """Outside a `container` test, the machine lock and the kernel's image
    records are the session's own."""
    if request.node.get_closest_marker("container") is None:
        monkeypatch.setattr(container, "LOCK", _STATE / "container.lock")
        monkeypatch.setattr(container, "BUILDER_OWNER", _STATE / "builder.owner")
        monkeypatch.setattr(container, "IMAGES", _STATE / "images.json")


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
    same session; then release the ports reserved for them."""
    yield
    if "dsn" not in request.fixturenames:
        ports.release()
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
    ports.release()


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
    its traceback, in the output of the command whose failure it raised, or
    in what it wrote to stderr (a kernel step logs its failure there), is
    reported skipped, naming the denial (`tests/denials.py`)."""
    report = yield
    if report.failed and call.excinfo is not None:
        from tests import denials

        said = [getattr(call.excinfo.value, a, None) for a in ("stderr", "stdout")]
        text = "\n".join(
            [
                str(report.longrepr),
                *(o.decode(errors="replace") if isinstance(o, bytes) else str(o) for o in said if o),
                *(body for title, body in report.sections if "stderr" in title),
            ]
        )
        why = denials.reason(text)
        if why is not None:
            report.outcome = "skipped"
            report.longrepr = (str(item.path), item.location[1] or 0, f"Skipped: {why}")
    return report
