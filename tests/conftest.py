"""Real Postgres: a fresh test database (`settings.test_database`) per session.

The ledger can never be emptied, so each session drops and recreates the
database as its owner. A session's task directories and effect lock files
live in temporary directories of its own, set before the settings are
built, so the suite writes nothing under the machine's `VALOR_WORK` or the
kernel key directory. Under pytest-xdist each worker gets its own database
(`<VALOR_TEST_DB>_<worker>`) and an equal slice of `VALOR_TEST_PORTS`. Every
test marks its live spend with `@pytest.mark.spend(usd=...)`.
"""

import atexit
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# The machine's own settings (its operator channel and chat, its machine
# name, its mailbox, its projects and inbound directory, its UI and local
# page ports) never reach the tests: a test sets the ones it needs. Left in
# the shell, `VALOR_OPERATOR_CHANNEL=local` refuses every Telegram send a
# test's child makes.
MACHINE_ONLY = (
    "VALOR_OPERATOR_CHANNEL", "VALOR_OPERATOR_CHAT", "VALOR_OPERATOR_TELEGRAM_ID", "VALOR_OPERATOR_EMAIL",
    "VALOR_OPERATOR_SLACK_IDS",
    "VALOR_MACHINE", "VALOR_DEFAULT_MACHINE", "VALOR_EMAIL_ADDRESS", "VALOR_EMAIL_SINCE",
    "VALOR_IMAP_HOST", "VALOR_IMAP_PORT", "VALOR_SMTP_HOST", "VALOR_SMTP_PORT", "VALOR_MAIL_CAFILE",
    "VALOR_INBOUND", "VALOR_PROJECTS", "VALOR_SERVE_TICK_S", "VALOR_UI_PORT", "VALOR_LOCAL_PORT",
    "VALOR_DB", "VALOR_MEMORY_DSN",
)  # fmt: skip
for _name in MACHINE_ONLY:
    os.environ.pop(_name, None)

# Under pytest-xdist each worker is a session of its own: the test database
# is `<VALOR_TEST_DB>_<worker>` (every name derived from it follows: the
# `_expiry`, `_serve`, `_rollout`, `_twice`,
# `_prio` databases, the advisory locks in them), and the worker listens in
# an equal slice of the run's `VALOR_TEST_PORTS`. With no span given, each
# worker (or a run with none) takes 20 ports from 6600 up. The controller
# of a parallel run restores what it was given (`pytest_configure`), so a
# worker slices the run's span, not the controller's default.
DEFAULT_PORTS, PORTS_PER_WORKER = 6600, 20
_GIVEN = {k: os.environ.get(k) for k in ("VALOR_TEST_PORTS", "VALOR_PG_PORTS", "VALOR_REDIS_PORTS")}
WORKER = os.environ.get("PYTEST_XDIST_WORKER")


def worker_ports(given: str | None, index: int, count: int) -> str:
    """The `LOW-HIGH` span worker `index` of `count` listens in: its equal
    slice of `given`, or 20 ports of its own from 6600 up."""
    if not given:
        low = DEFAULT_PORTS + index * PORTS_PER_WORKER
        return f"{low}-{low + PORTS_PER_WORKER - 1}"
    low, high = (int(p) for p in given.split("-"))
    size = (high - low + 1) // count
    if size < 1:
        raise RuntimeError(f"VALOR_TEST_PORTS {given} cannot be cut into {count} slices")
    return f"{low + index * size}-{low + (index + 1) * size - 1}"


if WORKER:
    os.environ["VALOR_TEST_DB"] = f"{os.environ.get('VALOR_TEST_DB', 'valor_rebuild_test')}_{WORKER}"
    _span = worker_ports(
        _GIVEN["VALOR_TEST_PORTS"],
        int(WORKER.removeprefix("gw")),
        int(os.environ["PYTEST_XDIST_WORKER_COUNT"]),
    )
    # The task Postgres and Redis ports the kernel chooses are the worker's
    # slice too, not the controller's whole span.
    for _name in ("VALOR_TEST_PORTS", "VALOR_PG_PORTS", "VALOR_REDIS_PORTS"):
        os.environ[_name] = _span
else:
    os.environ.setdefault("VALOR_TEST_PORTS", worker_ports(None, 0, 1))
    # The task Postgres and Redis ports the kernel chooses, in the tests' own
    # span, so a kernel the tests start listens where the tests' servers do.
    os.environ.setdefault("VALOR_PG_PORTS", os.environ["VALOR_TEST_PORTS"])
    os.environ.setdefault("VALOR_REDIS_PORTS", os.environ["VALOR_TEST_PORTS"])

os.environ["VALOR_WORK"] = tempfile.mkdtemp(prefix="valor-test-work-")
atexit.register(shutil.rmtree, os.environ["VALOR_WORK"], True)
os.environ["VALOR_PERFORMING_DIR"] = tempfile.mkdtemp(prefix="valor-test-performing-")
atexit.register(shutil.rmtree, os.environ["VALOR_PERFORMING_DIR"], True)
# A bridge the tests run under `serve` answers history reads on a port the
# OS chooses, never the live bridges' (`core.bridge.reads`).
os.environ["VALOR_TELEGRAM_READ_PORT"] = os.environ["VALOR_SLACK_READ_PORT"] = "0"
# Nothing answers on port 1, so a bridge under test never reaches the live
# voice process; a test that wants transcripts replaces `kernel.transcribe`.
os.environ["VALOR_VOICE_PORT"] = "1"
# The ports reserved for the task services a test provisions (`tests/ports.py`).
_fd, os.environ["VALOR_TEST_RESERVED_PORTS"] = tempfile.mkstemp(prefix="valor-test-reserved-ports-")
os.close(_fd)
# Scratch clusters start from `settings.pg_bin`, the Homebrew PostgreSQL 18 by
# default; where it is absent (the verification VM), Debian's PostgreSQL 18.
if "VALOR_PG_BIN" not in os.environ and not Path("/opt/homebrew/opt/postgresql@18/bin/initdb").exists():
    os.environ["VALOR_PG_BIN"] = "/usr/lib/postgresql/18/bin"

# On macOS `/usr/bin/git` is xcrun's shim, which finds the developer
# directory again on every call and doubles what each of the suite's
# thousands of git calls costs; the tests run the git it runs.
if sys.platform == "darwin" and "VALOR_GIT" not in os.environ and shutil.which("git") == "/usr/bin/git":
    _found = subprocess.run(["xcrun", "-f", "git"], capture_output=True, text=True, check=False)
    if _found.stdout.strip():
        os.environ["VALOR_GIT"] = _found.stdout.strip()

from core import db, memory  # settings read VALOR_WORK on import
from core.settings import settings
from tests import ports

atexit.register(ports.release)


def pytest_configure(config):
    """The controller of a parallel run starts no test: it hands its
    workers the ports it was given, which each worker slices."""
    if not WORKER and getattr(config.option, "numprocesses", None):
        for name, value in _GIVEN.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


TEST_DB = settings.test_database


@pytest.fixture(scope="session")
def dsn() -> str:
    """The fresh test database. Memory's popoto state (installed backend,
    cached tables, pools) is forgotten on each side of it, so none outlives
    the database it named."""
    memory.reset()
    made = db.migrate(TEST_DB, fresh=True)
    memory.reset()
    yield made
    memory.reset()


@pytest.fixture
def fresh(request) -> str:
    """A database of this test module's own (`test_serve` -> `<test db>_serve`)."""
    return db.migrate(f"{TEST_DB}_{request.module.__name__.rsplit('test_', 1)[-1]}", fresh=True)


@pytest.fixture(autouse=True)
def no_repo_env(tmp_path_factory, monkeypatch):
    """Every test's tasks read the repository `.env` from an empty home, never
    from this Mac's own `~/src` checkouts (`workspace.repo_env`)."""
    from core import workspace

    monkeypatch.setattr(workspace, "HOME", tmp_path_factory.getbasetemp() / "home")


@pytest.fixture(autouse=True)
def no_launch_agents(tmp_path_factory, monkeypatch):
    """Every test reads its installed launchd jobs from an empty folder,
    never this Mac's own (`rollout.services`)."""
    from core import permissions

    monkeypatch.setattr(permissions, "AGENTS", tmp_path_factory.getbasetemp() / "LaunchAgents")


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


@pytest.fixture
def emu():
    """A Telegram emulator of this test's own (`tests/telegram_emulator.py`)."""
    from tests.telegram_emulator import Emulator

    e = Emulator().start()
    yield e
    e.stop()
