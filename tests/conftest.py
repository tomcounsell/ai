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
denies it). A `container` test shares the machine lock and the runtime with
every kernel on the machine, as the runtime is one per machine; every other
test takes the machine lock in a directory of the session's own, never the
machine's, which the check profile denies too, and the runtime reads as
absent to it. Every test names its images under `valor-test` and labels
them with `container.owner` of the session's test database, so it never
deletes, retags, or records an image a kernel built. A `container` test
records its images in a file the test sessions share (`KEPT`), every other
test in the session's own directory. At session end, under the machine
lock, the base and dependency images the session recorded are kept for the
next session and every other image it recorded is deleted
(`release_images`).
"""

import asyncio
import atexit
import os
import re
import shutil
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
    "VALOR_MACHINE", "VALOR_DEFAULT_MACHINE", "VALOR_EMAIL_ADDRESS", "VALOR_EMAIL_SINCE",
    "VALOR_IMAP_HOST", "VALOR_IMAP_PORT", "VALOR_SMTP_HOST", "VALOR_SMTP_PORT", "VALOR_MAIL_CAFILE",
    "VALOR_INBOUND", "VALOR_PROJECTS", "VALOR_SERVE_TICK_S", "VALOR_UI_PORT", "VALOR_LOCAL_PORT",
    "VALOR_DB", "VALOR_MEMORY_DSN",
)  # fmt: skip
for _name in MACHINE_ONLY:
    os.environ.pop(_name, None)

# Under pytest-xdist each worker is a session of its own: the test database
# is `<VALOR_TEST_DB>_<worker>` (every name derived from it follows: the
# image label and repository, the `_expiry`, `_serve`, `_rollout`, `_twice`,
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
# The ports reserved for the task services a test provisions (`tests/ports.py`).
_fd, os.environ["VALOR_TEST_RESERVED_PORTS"] = tempfile.mkstemp(prefix="valor-test-reserved-ports-")
os.close(_fd)
# Scratch clusters start from `settings.pg_bin`, the Homebrew PostgreSQL 18 by
# default; where it is absent (the verification VM), Debian's PostgreSQL 18.
if "VALOR_PG_BIN" not in os.environ and not Path("/opt/homebrew/opt/postgresql@18/bin/initdb").exists():
    os.environ["VALOR_PG_BIN"] = "/usr/lib/postgresql/18/bin"

from core import binaries, container, db, memory  # settings read VALOR_WORK on import
from core.settings import settings
from tests import ports

atexit.register(ports.release)

# The skip reason of a `macos` test; `core.container` counts the skips that
# carry it.
MACOS_ONLY = "macOS only"


def pytest_configure(config):
    """The controller of a parallel run starts no test: it hands its
    workers the ports it was given, which each worker slices."""
    if not WORKER and getattr(config.option, "numprocesses", None):
        for name, value in _GIVEN.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


# Before xdist's own hook, which reads `xdist_group` to place each test on a worker.
@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    darwin = sys.platform == "darwin"
    runtime = container.present()
    for item in items:
        if item.get_closest_marker("container"):
            # One worker runs every `container` test: they share the machine
            # lock, and one worker builds one base image.
            item.add_marker(pytest.mark.xdist_group("container"))
        if not darwin and item.get_closest_marker("macos"):
            item.add_marker(pytest.mark.skip(reason=f"{MACOS_ONLY}: needs macOS itself"))
        if not runtime and item.get_closest_marker("container"):
            item.add_marker(pytest.mark.skip(reason=f"needs Apple's container at {binaries.CONTAINER}"))


TEST_DB = settings.test_database
# The `valor.db` label of every image and VM the session makes.
LABEL = container.owner(f"dbname={TEST_DB}")

_STATE = Path(tempfile.mkdtemp(prefix="valor-test-container-"))
atexit.register(shutil.rmtree, _STATE, True)
# A `container` test's image records outlive the session, so the next one
# finds the base and dependency images the last one built (`release_images`).
KEPT = Path.home() / "Library" / "Caches" / "valor-test"
container.IMAGES = _STATE / "images.json"
if sys.platform == "darwin" and container.present():
    KEPT.mkdir(parents=True, exist_ok=True, mode=0o700)
    container.IMAGES = KEPT / "images.json"
container.REPO = "valor-test"


@pytest.fixture(autouse=True)
def session_machine_lock(request, monkeypatch):
    """Outside a `container` test, the machine lock and the builder's owner
    file are the session's own, and the runtime reads as absent, as under
    the check profile: the session's lock does not hold the machine's
    runtime, so the sweep every run starts with never stops it under a
    kernel's verification."""
    if request.node.get_closest_marker("container") is None:
        monkeypatch.setattr(container, "LOCK", _STATE / "container.lock")
        monkeypatch.setattr(container, "IMAGES", _STATE / "images.json")
        monkeypatch.setattr(container, "BUILDER_OWNER", _STATE / "builder.owner")
        monkeypatch.setattr(container, "present", lambda: False)


# What a session keeps at its end: the base image and the dependency images,
# each tagged by its content (`container.base_tag`, `container.deps_key`).
_KEPT_TAG = re.compile(r"^valor-test/[a-z0-9._-]+:[0-9a-f]{64}$")


def kept_label() -> str:
    """The record label of a kept image: the base image's hash it was built
    on, so a change to `core/images/base/` makes every kept image stale."""
    return "kept:" + container.base_tag().rsplit(":", 1)[1][:12]


def release_images(label: str) -> None:
    """At session end, under the machine lock: the base and dependency
    images recorded under `label` are kept for the next session, their
    records relabelled `kept_label()`; every other image recorded under
    `label`, and every kept image of a stale base, is deleted with the
    system started, then stopped, as `container.verify` holds the runtime,
    and its record dropped."""
    current = kept_label()

    def mine(images):
        tags = [tag for tag, rec in images.items() if rec.get("db") == label]
        stale = [
            t for t, rec in images.items() if rec.get("db", "").startswith("kept:") and rec["db"] != current
        ]
        return [t for t in tags if _KEPT_TAG.match(t)], [t for t in tags if not _KEPT_TAG.match(t)] + stale

    keep, gone = mine(container._images())
    if not keep and not gone:
        return

    async def go():
        stop = asyncio.ensure_future(asyncio.Event().wait())
        async with container.machine_lock(stop):
            images = container._images()
            keep, gone = mine(images)
            for tag in keep:
                container._record(tag, images[tag]["digest"], current)
            if not gone:
                return
            try:
                await container.start(stop)
                for tag in gone:
                    await container.short(stop, "image", "delete", tag)
                    container._record(tag, None, "")
            finally:
                await container.system_stopped(stop)

    asyncio.run(go())


def pytest_sessionfinish(session, exitstatus):
    release_images(LABEL)


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
