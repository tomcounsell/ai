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

Ownership is queryable, and it is enforced (issue #2628). :func:`claimed_test_dbs`
returns the set of db numbers this process actually owns, and the flush guard in
``tests/conftest.py`` permits ``flushdb()`` only against that set. Before #2628 a
call site was trusted to re-derive its own db number, and re-deriving it wrong was
silent: two call sites kept computing the pre-#2060 ``gw{N} -> db{N+1}`` answer and
began wiping the datasets of unrelated live pytest processes, which is what made
the suite's failure set rotate run to run. A test that needs a SECOND database asks
for one through :func:`claim_scratch_test_db` (or the ``scratch_test_db`` fixture)
so it is owned too; nothing computes a db number for itself.

Exhaustion policy (issue #2628): when every slot is held by a live process the
claim polls the pool for ``TEST_DB_CLAIM_WAIT_S`` seconds and then raises. It does
NOT fall back to the legacy derivation any more — a colliding database is strictly
worse than a clear failure, because it silently produces the cross-process
corruption this module exists to prevent. The failure is sticky
(``_CLAIM_FAILURE``), so the wait is paid once per process rather than once per
caller. The registry-unreachable path keeps its legacy fallback: no lock can be
taken there at all, so a collision is unavoidable and degrading loudly beats
refusing to run.
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

# How long to keep polling for a free slot before giving up (#2628).
# Provisional / tunable — override via TEST_DB_CLAIM_WAIT_S. Deliberately short:
# in the contention state a wait actually targets (two concurrent ``-n auto``
# runs on a 10-core box demand 20 of 15 slots, each run taking ~20 minutes) no
# slot frees inside any tolerable window, so a long wait only delays an
# identical error. 30 s absorbs a sibling that is already tearing down and stays
# well inside pytest's ``--timeout=420`` ceiling.
_TEST_DB_CLAIM_WAIT_S = int(os.environ.get("TEST_DB_CLAIM_WAIT_S", "30"))

# Interval between pool sweeps while waiting, so a freed slot is picked up
# promptly instead of after one long sleep.
_TEST_DB_CLAIM_POLL_S = 1.0

# Process-lifetime cache of this process's claimed db number, and the held lock
# fds (kept open so the flocks persist until the process exits or releases).
_CLAIMED_TEST_DB: int | None = None
_CLAIM_LOCK_FDS: list[int] = []

# Every db number this process owns — the primary claim, the scratch claim, and
# the registry-unreachable fallback number alike. This is the authority the
# conftest flush guard consults, so a number that is returned but NOT registered
# here would have its own legitimate flush denied. Populate it on every path that
# returns a db (#2628).
_CLAIMED_DB_NUMS: set[int] = set()

# The optional SECOND db, for the rare test that needs a database other than its
# own. Memoized like the primary claim: an un-memoized scratch claim would take a
# fresh pool slot per requesting test, with no release path, and walk the pool
# into the exhaustion error below.
_CLAIMED_SCRATCH_DB: int | None = None

# Sticky exhaustion memo (#2628). ``_CLAIMED_TEST_DB`` is assigned only on
# success paths, so without this every later caller would re-enter the poll and
# pay the wait again — 30 s x ~1200 tests per worker, invisible to
# ``--timeout=420`` because no single test exceeds it.
_CLAIM_FAILURE: str | None = None


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

    The db NUMBER is registered in ``_CLAIMED_DB_NUMS`` in the same step that
    appends the fd, so ownership is observable strictly before any caller can
    act on the returned number — otherwise the very first flush of a freshly
    claimed db would race the guard that authorises it (#2628, Race 3).
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
    # Best-effort by design: the file's CONTENTS are never read back anywhere in
    # this module, so a failed write cannot affect who owns the slot.
    try:
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n{int(time.time())}\n".encode())
    except OSError:
        pass
    _CLAIM_LOCK_FDS.append(fd)  # keep open -> hold the flock for the process
    _CLAIMED_DB_NUMS.add(n)  # ownership is observable before the number is used
    return True


def _exhaustion_message() -> str:
    return (
        f"all {_TEST_DB_POOL_MAX} test-DB slots are held by live processes and none "
        f"freed within {_TEST_DB_CLAIM_WAIT_S}s. Too many concurrent pytest runs on "
        "this machine. Free orphaned workers with `scripts/reap-xdist.sh --apply`, or "
        "wait for a sibling run to finish, then retry. Refusing to fall back to a "
        "colliding database: that is what wipes another live run's data mid-test "
        "(#2628)."
    )


def claim_test_db() -> int:
    """Return this process's unique test db, claiming one on first call (#2060).

    Memoized for the process lifetime. Polls the pool for up to
    ``_TEST_DB_CLAIM_WAIT_S`` seconds and then raises ``RuntimeError`` rather
    than returning a database another live process owns (#2628). The registry-
    unreachable path still degrades to the legacy derivation with a WARNING —
    there no lock can be taken at all, so refusing to run buys nothing.
    """
    global _CLAIMED_TEST_DB, _CLAIM_FAILURE
    # Sticky failure first, AHEAD of the memo: ``_CLAIMED_TEST_DB`` is assigned
    # only on success, so without this a failed claim would re-enter the poll on
    # every later call and pay the wait again (#2628).
    if _CLAIM_FAILURE is not None:
        raise RuntimeError(_CLAIM_FAILURE)
    if _CLAIMED_TEST_DB is not None:
        return _CLAIMED_TEST_DB
    try:
        claim_dir = _test_db_claim_dir()
    except OSError as e:
        _CLAIMED_TEST_DB = _legacy_test_db_num()
        # Register the number even though no flock backs it: the flush guard
        # denies any db absent from this set, so skipping this would turn a
        # documented graceful degradation into a whole-process setup outage.
        _CLAIMED_DB_NUMS.add(_CLAIMED_TEST_DB)
        _logger.warning(
            "test-db claim registry unavailable (%s); falling back to legacy db=%d",
            e,
            _CLAIMED_TEST_DB,
        )
        return _CLAIMED_TEST_DB
    # Bounded poll. Race 1 (hold-and-wait): two concurrent runs can each hold a
    # partial allocation and wait on the other, so the wait MUST be bounded —
    # that is what turns a deadlock into a loud, diagnosable failure. The
    # structural escalation, deliberately deferred until a deadlock is actually
    # observed, is all-or-nothing allocation by the xdist controller.
    started = time.monotonic()
    while True:
        for n in range(1, _TEST_DB_POOL_MAX + 1):
            if _try_claim_db_slot(claim_dir, n):
                _CLAIMED_TEST_DB = n
                return n
        # Read the budget as a module attribute on every iteration so tests can
        # monkeypatch it to 0 — a default argument or an import-time local would
        # make the patch a silent no-op and every exhaustion test would block.
        if time.monotonic() - started >= _TEST_DB_CLAIM_WAIT_S:
            break
        time.sleep(_TEST_DB_CLAIM_POLL_S)
    _CLAIM_FAILURE = _exhaustion_message()
    _logger.error("%s", _CLAIM_FAILURE)
    raise RuntimeError(_CLAIM_FAILURE)


def claimed_test_dbs() -> frozenset[int]:
    """Every Redis db number this process owns, empty before any claim.

    This is the authority the conftest flush guard consults: a ``flushdb()``
    against a db outside this set is another live process's data (#2628). Empty
    means "this process owns nothing", which after the session-start claim can
    only be the xdist controller (which runs no tests) or the window before
    ``pytest_configure`` — so denying on empty denies nothing legitimate.
    """
    return frozenset(_CLAIMED_DB_NUMS)


def claim_scratch_test_db() -> int | None:
    """Claim a SECOND owned db for a test that needs one, or ``None``.

    For the rare test whose subject is divergence between two databases. Taken
    from the same flock pool as the primary claim, so it is owned and therefore
    flushable. Returns ``None`` when the pool is exhausted — never a derived or
    borrowed number, because an unowned "scratch" db is exactly the bug this
    module exists to stop.

    Memoized per process: one scratch slot, held for the session, released by
    :func:`release_test_db_claim`. Without the memo each requesting test would
    consume a fresh slot with no release path and walk the pool into
    :func:`claim_test_db`'s exhaustion error.
    """
    global _CLAIMED_SCRATCH_DB
    if _CLAIMED_SCRATCH_DB is not None:
        return _CLAIMED_SCRATCH_DB
    try:
        claim_dir = _test_db_claim_dir()
    except OSError:
        return None
    for n in range(1, _TEST_DB_POOL_MAX + 1):
        if n in _CLAIMED_DB_NUMS:
            continue
        if _try_claim_db_slot(claim_dir, n):
            _CLAIMED_SCRATCH_DB = n
            return n
    return None


def release_test_db_claim() -> None:
    """Release this process's held claim locks (idempotent).

    Registered with atexit and invoked by a session-scoped finalizer in
    conftest. Closing the fd releases the flock, freeing the slot for reuse. The
    lock file itself is left in place (reused by the next claimant); its
    presence is not ownership — the flock is.
    """
    global _CLAIMED_TEST_DB, _CLAIMED_SCRATCH_DB, _CLAIM_FAILURE
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
    _CLAIMED_DB_NUMS.clear()
    _CLAIMED_TEST_DB = None
    _CLAIMED_SCRATCH_DB = None
    _CLAIM_FAILURE = None


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

    **``POPOTO_TEST_DB`` is deliberately NOT inherited** (#2628), which is the
    mirror image of the ``REDIS_URL`` rule above. ``REDIS_URL`` is shared on
    purpose so a child reads and writes the parent's db. ``POPOTO_TEST_DB`` is
    read only by popoto's bundled pytest plugin, i.e. only by a NESTED PYTEST
    child — and such a child claims its own slot, so inheriting the parent's
    number would point its per-test ``flushdb()`` at the parent's database. An
    explicit caller value still wins, since ``extra`` is merged afterwards.
    """
    env = {**os.environ}
    env.pop("POPOTO_TEST_DB", None)
    env.update({k: str(v) for k, v in extra.items()})
    env["REDIS_URL"] = redis_test_url()
    if project_root is not None:
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{project_root}{os.pathsep}{existing}" if existing else project_root
    return env
