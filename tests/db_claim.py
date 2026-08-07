"""Per-process test-Redis-db claim, and the subprocess env derived from it.

This module owns the single definition of "which Redis db does this pytest
process own". ``tests/conftest.py``'s autouse ``redis_test_db`` fixture points
Popoto at that db; any test that shells out to a subprocess must point that
subprocess at the SAME db via :func:`subprocess_env`.

Why this is a module and not part of ``conftest.py`` (issue #2605): the claim is
memoized in a module global and backed by held file locks. A second copy of that
state — which is exactly what ``from tests.conftest import ...`` risks, since
pytest loads ``conftest.py`` through its own import machinery — would claim a
second db and silently split a process in two. One plain module, imported the
normal way by both conftest and the tests, cannot fork that state.

Background on the claim itself (issue #2060). The test db used to be partitioned
ONLY by xdist worker id WITHIN one pytest run: ``gw{N} -> db{N+1}`` and
``master -> db1``. That is unique across workers in a single run, but NOT across
concurrent pytest PROCESSES: a background full-suite run's ``gw0`` and a
standalone ``pytest ::test`` (master) both derive ``db1``. Because
``redis_test_db`` calls ``flushdb()`` at every test's setup AND teardown, two
processes that landed on the same db number wipe each other's data mid-test.

Fix: each pytest PROCESS atomically claims a UNIQUE db number from the pool
``[1..TEST_DB_POOL_MAX]`` via an ``fcntl.flock`` on a per-db lock file in a
machine-global registry dir. The lock is held (fd kept open) for the whole
process lifetime, so no other live process can claim the same db. When a process
dies — cleanly or via SIGKILL — the OS releases its flocks automatically, so a
crashed run never strands a db (no PID-liveness heuristic or reaper needed).
Graceful fallback to the legacy ``worker_id+1`` derivation if the pool is
exhausted or the registry is unreachable — never worse than before.
"""

from __future__ import annotations

import atexit
import fcntl
import logging
import os
import time

_logger = logging.getLogger("tests.db_claim")

# Usable test DBs are 1..TEST_DB_POOL_MAX (db0 is production, guarded in
# conftest). Redis ships with 16 logical DBs by default, so 15 test slots.
# Provisional / tunable — override via TEST_DB_POOL_MAX if the Redis instance is
# configured with more databases (take with a grain of salt; must be < the
# server's ``databases`` setting or flushdb() on the claimed db raises).
_TEST_DB_POOL_MAX = int(os.environ.get("TEST_DB_POOL_MAX", "15"))

# Process-lifetime cache of this process's claimed db number, and the held lock
# fds (kept open so the flocks persist until the process exits or releases).
_CLAIMED_TEST_DB: int | None = None
_CLAIM_LOCK_FDS: list[int] = []


def _test_db_claim_dir() -> str:
    """Machine-global registry dir for per-db claim locks.

    The collision is machine-wide (every worktree/process — and every repo on
    the box — hits the SAME Redis server on localhost:REDIS_PORT), so the
    registry must be shared across ALL pytest processes on the machine, keyed
    only by the Redis port so a non-default port gets its own pool.

    The base is a fixed ``/tmp`` (deliberately NOT ``tempfile.gettempdir()`` /
    ``$TMPDIR``): a launchd worker has ``TMPDIR`` unset → ``/tmp`` while an
    interactive shell has ``TMPDIR=/var/folders/.../T``. Keying off ``$TMPDIR``
    would let those two compute DIFFERENT registry dirs and never coordinate —
    the exact footgun the machine-global full-suite lock (#2064) calls out.
    """
    port = os.environ.get("REDIS_PORT", "6379")
    d = os.path.join("/tmp", f"valor-pytest-db-claims-{port}")  # noqa: S108 - see docstring
    os.makedirs(d, exist_ok=True)
    return d


def _legacy_test_db_num() -> int:
    """The pre-#2060 derivation, retained as the single fallback definition.

    Reads ``PYTEST_XDIST_WORKER`` from the environment rather than
    ``request.config.workerinput``: xdist sets it in every worker process, so
    this stays correct while needing no pytest objects, which is what lets
    :func:`claim_test_db` be callable from plain helper code.
    """
    worker_id = os.environ.get("PYTEST_XDIST_WORKER", "")
    if worker_id.startswith("gw"):
        return int(worker_id[2:]) + 1  # gw0->db1, gw1->db2, etc.
    return 1  # No xdist or master process


def _try_claim_db_slot(claim_dir: str, n: int) -> bool:
    """Atomically claim db ``n`` via a held ``flock``. True if this process wins.

    A non-blocking exclusive flock is single-winner across processes on one
    machine, and the kernel releases it when the owning process dies — so a
    dead owner's slot is instantly reclaimable with no PID bookkeeping. The fd
    is intentionally leaked into ``_CLAIM_LOCK_FDS`` to hold the lock for the
    process lifetime.
    """
    path = os.path.join(claim_dir, f"{n}.lock")
    try:
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        # Held by another live process — not ours.
        os.close(fd)
        return False
    # We own the lock. Record pid/ts for human debugging only (NOT correctness).
    try:
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n{int(time.time())}\n".encode())
    except OSError:
        pass
    _CLAIM_LOCK_FDS.append(fd)  # keep open -> hold the flock for the process
    return True


def claim_test_db() -> int:
    """Return this process's unique test db, claiming one on first call (#2060).

    Memoized for the process lifetime. Falls back to the legacy per-worker
    derivation (logging a WARNING) if the registry is unreachable or every slot
    in the pool is held by a live process.
    """
    global _CLAIMED_TEST_DB
    if _CLAIMED_TEST_DB is not None:
        return _CLAIMED_TEST_DB
    try:
        claim_dir = _test_db_claim_dir()
    except OSError as e:
        _CLAIMED_TEST_DB = _legacy_test_db_num()
        _logger.warning(
            "test-db claim registry unavailable (%s); falling back to legacy db=%d",
            e,
            _CLAIMED_TEST_DB,
        )
        return _CLAIMED_TEST_DB
    for n in range(1, _TEST_DB_POOL_MAX + 1):
        if _try_claim_db_slot(claim_dir, n):
            _CLAIMED_TEST_DB = n
            return n
    # Pool exhausted — more concurrent pytest processes than test DBs. Fall back
    # to the legacy derivation (which may collide, i.e. no worse than pre-#2060).
    _CLAIMED_TEST_DB = _legacy_test_db_num()
    _logger.warning(
        "all %d test-DB slots held by live processes; falling back to legacy db=%d "
        "(may collide with a concurrent process)",
        _TEST_DB_POOL_MAX,
        _CLAIMED_TEST_DB,
    )
    return _CLAIMED_TEST_DB


def release_test_db_claim() -> None:
    """Release this process's held claim locks (idempotent).

    Registered with atexit and invoked by a session-scoped finalizer in
    conftest. Closing the fd releases the flock, freeing the slot for reuse. The
    lock file itself is left in place (reused by the next claimant); its
    presence is not ownership — the flock is.
    """
    global _CLAIMED_TEST_DB
    while _CLAIM_LOCK_FDS:
        fd = _CLAIM_LOCK_FDS.pop()
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            os.close(fd)
        except OSError:
            pass
    _CLAIMED_TEST_DB = None


atexit.register(release_test_db_claim)


def redis_test_url(host: str = "127.0.0.1") -> str:
    """``redis://host:port/N`` for THIS process's claimed test db."""
    port = os.environ.get("REDIS_PORT", "6379")
    return f"redis://{host}:{port}/{claim_test_db()}"


def subprocess_env(*, project_root: str | None = None, **extra) -> dict[str, str]:
    """Environment for a subprocess that must see the same state as its parent.

    Two hazards, both observed in issue #2605's rotating
    ``TestKillCommandIntegration`` failures:

    **Redis db.** Popoto reads ``REDIS_URL`` at import time, so a subprocess
    picks its db from the environment, not from the parent's patched client.
    Deriving that db from ``PYTEST_XDIST_WORKER`` (the pre-#2060 rule) is wrong
    whenever the parent's claim did not land on ``worker_id + 1`` — which is the
    normal case as soon as any other pytest process on the machine holds a lower
    slot. The parent then writes to its claimed db while its own subprocess
    writes to a db some *other* live process is flushing at every test setup and
    teardown, and a row pushed moments earlier vanishes: ``Session push-XXXX not
    found``. Routing through :func:`claim_test_db` makes parent and child share
    one db by construction.

    **Checkout.** ``python -m`` puts the subprocess's cwd first on ``sys.path``,
    so passing ``cwd=<checkout under test>`` already resolves repo modules from
    that checkout. That is incidental, though: the shared venv carries a ``.pth``
    naming the main checkout, so the moment cwd stops winning, a worktree's tests
    would silently exercise main's code and could not fail for the right reason.
    Passing ``project_root`` pins it explicitly via ``PYTHONPATH``.
    """
    env = {**os.environ, **{k: str(v) for k, v in extra.items()}}
    env["REDIS_URL"] = redis_test_url()
    if project_root is not None:
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{project_root}{os.pathsep}{existing}" if existing else project_root
    return env
