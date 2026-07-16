"""
Shared test fixtures for Valor AI tests.
"""

import atexit
import fcntl
import logging
import os
import subprocess
import sys
import time
from unittest.mock import MagicMock

import pytest

_logger = logging.getLogger("tests.conftest")

# ---------------------------------------------------------------------------
# Production-Sentry guard
# ---------------------------------------------------------------------------
# bridge/telegram_bridge.py calls sentry_sdk.init() at module import time,
# gated only on SENTRY_DSN — which its dotenv load pulls from the real .env.
# 31 test files import that module (directly or transitively), so every
# pytest process was shipping deliberate failure-path logger.error() calls
# into production Sentry as real events (issue #1460: 1,650+ events/period
# across VALOR-2M/6/1Y/2J, all traced to test payloads). Pre-seed an empty
# DSN at conftest import time — before any test module import — so the
# bridge's `if _sentry_dsn:` guard stays falsy. load_dotenv(override=False)
# never replaces a key already present in os.environ, so the real .env
# cannot re-pollute it. Production code is untouched.
os.environ["SENTRY_DSN"] = ""


# ---------------------------------------------------------------------------
# Production-Redis (db=0) flush guard
# ---------------------------------------------------------------------------
# A flushdb() against db=0 -- or any flushall() -- wipes the production dataset
# (memories, Telegram history, chats, knowledge docs). The redis_test_db
# fixture below isolates Popoto to a per-worker test db (db>=1), but test code
# that constructs its OWN redis client bypasses that isolation: bare
# redis.Redis() and redis.Redis.from_url(".../0") both default to db=0. On
# 2026-06-03 exactly this footgun flushed production. We monkeypatch
# flushdb/flushall on the sync and async Redis classes at conftest import time
# (before collection) so any attempt to flush db=0 -- or call flushall, which
# wipes EVERY db -- raises instead of destroying data. This patch lives in
# conftest.py, so it only affects pytest runs; production code is untouched.
def _install_redis_db0_flush_guard() -> None:
    try:
        import redis
        import redis.asyncio as aioredis
    except Exception:
        return

    def _db_of(client) -> int:
        try:
            return int(client.connection_pool.connection_kwargs.get("db", 0) or 0)
        except Exception:
            # If we cannot determine the db, assume the dangerous one (db=0).
            return 0

    def _make_guarded_flushdb(orig):
        def _guarded_flushdb(self, *args, **kwargs):
            if _db_of(self) == 0:
                raise RuntimeError(
                    "Refusing flushdb() on Redis db=0 (production) during tests. "
                    "Use the autouse redis_test_db fixture, or build clients on the "
                    "per-worker test db (see redis_url / tests/conftest.py). "
                    "This guard exists because a db=0 flush wiped production on 2026-06-03."
                )
            return orig(self, *args, **kwargs)

        _guarded_flushdb._db0_guarded = True
        return _guarded_flushdb

    def _make_guarded_flushall(orig):
        def _guarded_flushall(self, *args, **kwargs):
            raise RuntimeError(
                "Refusing flushall() during tests -- it wipes ALL Redis dbs, including "
                "production db=0. Flush the per-worker test db with flushdb() instead. "
                "See tests/conftest.py."
            )

        _guarded_flushall._db0_guarded = True
        return _guarded_flushall

    for mod in (redis, aioredis):
        cls = mod.Redis
        if not getattr(cls.flushdb, "_db0_guarded", False):
            cls.flushdb = _make_guarded_flushdb(cls.flushdb)
        if not getattr(cls.flushall, "_db0_guarded", False):
            cls.flushall = _make_guarded_flushall(cls.flushall)


_install_redis_db0_flush_guard()


# ---------------------------------------------------------------------------
# Centralized claude_agent_sdk mock
# ---------------------------------------------------------------------------
# Several test files need ``import agent.*`` which transitively imports
# ``claude_agent_sdk``.  When the real SDK is not installed the import
# would fail during pytest collection.  Previously each test file had its
# own module-level ``sys.modules["claude_agent_sdk"] = MagicMock()`` which
# persisted across the pytest session and contaminated later tests.
#
# Centralizing the mock here (conftest.py is always imported before test
# modules are collected) means:
# 1. Only one place manages the mock -- no 7 scattered copies
# 2. The autouse fixture below restores sys.modules after each test
# 3. Tests that need the real SDK (e.g. test_cross_wire_fixes.py) get
#    a clean sys.modules state
# ---------------------------------------------------------------------------
# Check if the real SDK is importable (installed), not just loaded.
# If it's installed, don't inject a mock -- let tests use the real SDK.
# If it's NOT installed, inject a MagicMock so that ``import agent.*``
# succeeds during test collection.
try:
    import claude_agent_sdk  # noqa: F401

    _SDK_IMPORTABLE = True
except ImportError:
    _SDK_IMPORTABLE = False

_SDK_PRESENT_AT_STARTUP = "claude_agent_sdk" in sys.modules
_SDK_ORIGINAL_VALUE = sys.modules.get("claude_agent_sdk")

if not _SDK_IMPORTABLE:
    sys.modules["claude_agent_sdk"] = MagicMock()


@pytest.fixture(autouse=True)
def mock_claude_sdk_cleanup():
    """Restore sys.modules["claude_agent_sdk"] to pre-collection state after each test.

    Problem: Seven test files previously injected a MagicMock into
    sys.modules at module level (during collection, before any fixture
    runs).  The mock persisted for the entire pytest session, contaminating
    later tests (e.g. test_cross_wire_fixes.py) that expect the real SDK.

    Solution: At conftest import time (before test files are collected) we
    snapshot whether the real SDK exists.  After each test function we
    restore that original state.  If the SDK entry was swapped during the
    test (i.e. a mock was injected where the real SDK was, or vice versa),
    we also evict cached ``agent.*`` modules so they get re-imported
    cleanly against the restored SDK.
    """
    sdk_before_test = sys.modules.get("claude_agent_sdk")

    yield

    sdk_after_test = sys.modules.get("claude_agent_sdk")

    # Restore the SDK entry to its pre-collection state
    if _SDK_PRESENT_AT_STARTUP:
        sys.modules["claude_agent_sdk"] = _SDK_ORIGINAL_VALUE
    else:
        sys.modules.pop("claude_agent_sdk", None)

    # Only evict agent.* modules if the SDK entry was swapped during the
    # test.  Blanket eviction after every test is too aggressive and
    # breaks module-level state for unrelated tests.
    if sdk_after_test is not sdk_before_test:
        agent_modules = [key for key in sys.modules if key == "agent" or key.startswith("agent.")]
        for mod_key in agent_modules:
            del sys.modules[mod_key]


@pytest.fixture(autouse=True)
def agent_hooks_consistency_guard():
    """Detect and repair a corrupt `agent` package/submodule cache state.

    Problem: ``monkeypatch.setattr("agent.hooks.pre_tool_use.SOME_ATTR", ...)``
    (a dotted-string target) resolves via attribute-walk: import ``agent``,
    then ``getattr(agent, "hooks")``, then ``getattr(hooks, "pre_tool_use")``,
    etc. CPython only rebinds a submodule as an attribute on its parent
    package the moment that submodule is freshly imported -- ``sys.modules``
    is just a flat name->module cache and does not, by itself, keep the
    attribute tree in sync.

    If some other test (or fixture, e.g. ``mock_claude_sdk_cleanup`` above,
    which selectively evicts ``agent.*`` keys) replaces or partially rebuilds
    ``sys.modules["agent"]`` while ``sys.modules["agent.hooks"]`` survives
    from an earlier import, the new ``agent`` module object never gets
    ``hooks`` re-bound onto it. The cache then reports both modules as
    "loaded" while the parent-child link between them is severed:
    ``"agent" in sys.modules and "agent.hooks" in sys.modules and not
    hasattr(sys.modules["agent"], "hooks")``. Any dotted-string
    ``monkeypatch.setattr`` that walks through ``agent.hooks`` then raises
    ``AttributeError: 'module' object at agent.hooks has no attribute
    'hooks'`` during test setup, before the test body ever runs.

    This is a distinct corruption vector from the one ``mock_claude_sdk_cleanup``
    guards against (SDK entry swaps specifically), so it needs its own
    independent, always-on check rather than being folded into that fixture.

    Fix: evicting *every* ``agent.*`` key from ``sys.modules`` (not just the
    two implicated in the check) is what actually self-heals, because the
    next ``import agent.hooks.pre_tool_use`` then performs a full fresh
    import: Python imports ``agent``, then imports ``agent.hooks`` and binds
    it onto the freshly-imported ``agent`` object, then imports
    ``agent.hooks.pre_tool_use`` and binds it onto the freshly-imported
    ``agent.hooks`` object. A full eviction guarantees every link in that
    chain gets rebuilt together and consistently; evicting only some of the
    keys (or leaving stale ones in place) would just reproduce the same
    partial-tree problem on the next import.

    No-op (untouched) when ``agent`` isn't imported at all, or when it *is*
    imported and its ``hooks`` attribute is intact -- only the corrupt state
    triggers eviction.
    """
    if (
        "agent" in sys.modules
        and "agent.hooks" in sys.modules
        and not hasattr(sys.modules["agent"], "hooks")
    ):
        for name in [key for key in sys.modules if key == "agent" or key.startswith("agent.")]:
            del sys.modules[name]

    yield


# Cache of popoto modules that hold a `POPOTO_REDIS_DB` symbol. Built lazily
# and refreshed only when sys.modules grows OR a cached module identity has
# changed, so we don't walk all of sys.modules per test (was ~1500 entries ×
# thousands of tests). See _popoto_modules_with_redis_db for why both triggers
# are required.
_POPOTO_MODULE_CACHE: dict[str, object] = {}
_POPOTO_MODULE_CACHE_LEN: int = -1


def _popoto_modules_with_redis_db():
    """Return the list of popoto submodules holding a `POPOTO_REDIS_DB` symbol.

    The cache is rebuilt when EITHER of two independent staleness signals
    fires:

    1. `len(sys.modules) != _POPOTO_MODULE_CACHE_LEN` -- catches brand-new,
       lazily-imported db-holder modules that were never cached before. A
       pure identity check over already-cached names cannot see these: if a
       module hasn't been cached yet, there's no entry to compare identity
       against, so an `any()` over the existing cache is vacuously False.

    2. `any(sys.modules.get(name) is not mod for name, mod in cache.items())`
       -- catches an equal-count eviction-then-reimport, where a module
       object is replaced under the SAME name (e.g. `mock_claude_sdk_cleanup`
       evicts `agent.*` from sys.modules between tests, and a later import
       creates a new module object with the same dotted name). `len` alone
       is non-monotonic under this eviction/reimport cycle -- it can produce
       an EQUAL total module count with a DIFFERENT (stale) module object
       cached under an unchanged name, so a len-only cache would silently
       keep serving a module whose `POPOTO_REDIS_DB` binding was never
       repointed to the test db, causing writes to land on db=0 while other
       code paths derive db=1 from the canonical `rdb.POPOTO_REDIS_DB` --
       a "split-brain" (see tests/integration/test_tool_budget_enforcement.py
       flake and issue #2037).

    Neither signal alone is sufficient -- they are OR'd together.
    """
    import sys as _sys

    global _POPOTO_MODULE_CACHE, _POPOTO_MODULE_CACHE_LEN
    cur_len = len(_sys.modules)
    stale = cur_len != _POPOTO_MODULE_CACHE_LEN or any(
        _sys.modules.get(name) is not mod for name, mod in _POPOTO_MODULE_CACHE.items()
    )
    if stale:
        _POPOTO_MODULE_CACHE = {
            name: mod
            for name, mod in _sys.modules.items()
            if mod is not None and name.startswith("popoto") and hasattr(mod, "POPOTO_REDIS_DB")
        }
        _POPOTO_MODULE_CACHE_LEN = cur_len
    return list(_POPOTO_MODULE_CACHE.values())


# ---------------------------------------------------------------------------
# Per-process test-DB claim (issue #2060)
# ---------------------------------------------------------------------------
# The test DB used to be partitioned ONLY by xdist worker id WITHIN one pytest
# run: ``gw{N} -> db{N+1}`` and ``master -> db1``. That is unique across workers
# in a single run, but NOT across concurrent pytest PROCESSES: a background
# full-suite run's ``gw0`` and a standalone ``pytest ::test`` (master) both
# derive ``db1``. Because ``redis_test_db`` calls ``flushdb()`` at every test's
# setup AND teardown, two processes that landed on the same db number wipe each
# other's data mid-test. That was the root cause of the intermittent
# ``test_cli_hook_denies_over_budget_exit_2`` fail-open (issue #2060): the test
# wrote an over-budget AgentSession, a concurrent process flushed the shared db,
# and the CLI-hook subprocess then resolved no session -> exit 0 instead of 2.
#
# Fix: each pytest PROCESS atomically claims a UNIQUE db number from the pool
# ``[1..TEST_DB_POOL_MAX]`` via an ``fcntl.flock`` on a per-db lock file in a
# machine-global registry dir. The lock is held (fd kept open) for the whole
# process lifetime, so no other live process can claim the same db. When a
# process dies — cleanly or via SIGKILL — the OS releases its flocks
# automatically, so a crashed run never strands a db (no PID-liveness heuristic
# or reaper needed). Both ``redis_test_db`` and ``_redis_test_db_num`` read the
# same claimed number. Graceful fallback to the legacy ``worker_id+1``
# derivation if the pool is exhausted or the registry is unreachable — never
# worse than before.
#
# Note: the ``_run_cli_hook`` subprocess in test_tool_budget_enforcement.py
# already derives its db from ``POPOTO_REDIS_DB.connection_pool.connection_kwargs['db']``,
# so it inherits the claimed db automatically — no change needed there.

# Usable test DBs are 1..TEST_DB_POOL_MAX (db0 is production, guarded above).
# Redis ships with 16 logical DBs by default, so 15 test slots. Provisional /
# tunable — override via TEST_DB_POOL_MAX if the Redis instance is configured
# with more databases (take with a grain of salt; must be < the server's
# ``databases`` setting or flushdb() on the claimed db raises).
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


def _legacy_test_db_num(request) -> int:
    """The pre-#2060 derivation, retained as the single fallback definition."""
    worker_id = getattr(request.config, "workerinput", {}).get("workerid", "")
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


def _claim_test_db(request) -> int:
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
        _CLAIMED_TEST_DB = _legacy_test_db_num(request)
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
    _CLAIMED_TEST_DB = _legacy_test_db_num(request)
    _logger.warning(
        "all %d test-DB slots held by live processes; falling back to legacy db=%d "
        "(may collide with a concurrent process)",
        _TEST_DB_POOL_MAX,
        _CLAIMED_TEST_DB,
    )
    return _CLAIMED_TEST_DB


def _release_test_db_claim() -> None:
    """Release this process's held claim locks (idempotent).

    Registered with atexit and invoked by a session-scoped finalizer. Closing
    the fd releases the flock, freeing the slot for reuse. The lock file itself
    is left in place (reused by the next claimant); its presence is not
    ownership — the flock is.
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


atexit.register(_release_test_db_claim)


@pytest.fixture(scope="session", autouse=True)
def _test_db_claim_release():
    """Release the process's claimed test db at session end (atexit backstops)."""
    yield
    _release_test_db_claim()


@pytest.fixture(autouse=True)
def redis_test_db(request):
    """Switch popoto to a dedicated test Redis client for ALL tests.

    autouse=True ensures this runs for every test, even those that don't
    explicitly request the fixture. This prevents accidental writes to db=0
    if a test imports a popoto model without requesting isolation.

    Under pytest-xdist, each worker (gw0, gw1, ...) gets its own Redis database
    (db=1, db=2, ...) to prevent cross-worker contamination from flushdb().
    Without xdist, uses db=1 as before.

    CRITICAL: We replace the POPOTO_REDIS_DB object with a new Redis client
    pointed at the test db, rather than using SELECT on the production connection.
    SELECT is unsafe with connection pools — if the pool recycles a connection,
    the new connection defaults back to db=0 and flushdb() wipes production data.

    Also resets the async Redis connection to use the same db, since popoto v1.0.0b2
    maintains a separate _POPOTO_ASYNC_REDIS_DB connection.
    """
    import sys as _sys

    # Fast path: if popoto isn't imported yet, this test cannot touch Redis
    # via Popoto. Pure-logic tests that run before any popoto import skip
    # flushdb + the patching dance entirely.
    if "popoto.redis_db" not in _sys.modules:
        yield
        return

    import popoto.redis_db as rdb
    import redis
    import redis.asyncio as aioredis

    # Per-PROCESS unique test db (issue #2060). Replaces the old per-worker
    # ``gw{N}->db{N+1}`` / master->db1 derivation, which collided across
    # concurrent pytest processes and let one process's flushdb() wipe another's
    # data mid-test. ``_claim_test_db`` is memoized per process, so every test in
    # this process uses the same claimed db.
    test_db = _claim_test_db(request)

    # Save original connections
    original_sync = rdb.POPOTO_REDIS_DB
    original_async = getattr(rdb, "_POPOTO_ASYNC_REDIS_DB", None)

    # Create a NEW Redis client pointed at the test db (not SELECT on the pool)
    test_client = redis.Redis(db=test_db)
    rdb.POPOTO_REDIS_DB = test_client
    test_client.flushdb()

    # Popoto submodules use `from ..redis_db import POPOTO_REDIS_DB`, which
    # captures the binding at import time. Assigning rdb.POPOTO_REDIS_DB above
    # does not update those local bindings, so we must patch every popoto
    # module that has a local POPOTO_REDIS_DB symbol. Without this, sync
    # reads/writes route to whichever db was active at import (often
    # production), and async vs. sync reads diverge.
    _patched_popoto_modules: list[tuple[object, object]] = []
    for _mod in _popoto_modules_with_redis_db():
        _patched_popoto_modules.append((_mod, _mod.POPOTO_REDIS_DB))
        _mod.POPOTO_REDIS_DB = test_client

    # Reset async Redis connection to point at the same test db.
    rdb._POPOTO_ASYNC_REDIS_DB = aioredis.Redis(db=test_db)

    yield

    # Flush test db and restore original production connections
    test_client.flushdb()
    test_client.close()
    rdb.POPOTO_REDIS_DB = original_sync
    rdb._POPOTO_ASYNC_REDIS_DB = original_async
    for _mod, _orig in _patched_popoto_modules:
        _mod.POPOTO_REDIS_DB = _orig


# ---------------------------------------------------------------------------
# Shared helper: per-worker Redis URL for tests that need raw Redis clients
# ---------------------------------------------------------------------------
# Tests that point a non-popoto Redis client (or set REDIS_URL for code under
# test) must use the SAME per-worker db that `redis_test_db` picks, otherwise
# `pytest -n auto` collides across xdist workers. Hardcoding `db=1` breaks
# parallel runs.
# ---------------------------------------------------------------------------


def _redis_test_db_num(request):
    """Return the per-process claimed test db number (matches redis_test_db, #2060)."""
    return _claim_test_db(request)


@pytest.fixture
def redis_test_url(request):
    """Return the xdist-aware ``redis://localhost:6379/N`` URL for tests.

    Use this in any fixture that constructs a raw ``redis.Redis`` client or
    sets ``REDIS_URL`` for code under test. Matches the db number chosen by
    the autouse ``redis_test_db`` fixture so ``pytest -n auto`` is safe.
    """
    return f"redis://localhost:6379/{_redis_test_db_num(request)}"


# ---------------------------------------------------------------------------
# Test helper: create AgentSession with backward-compatible field names
# ---------------------------------------------------------------------------


def create_test_session(**kwargs):
    """Create an AgentSession with backward-compatible field names.

    Accepts the old individual field names (message_text, sender_name, sender_id,
    telegram_message_id, chat_title, revival_context, classification_type,
    classification_confidence, work_item_slug) and maps them into the new
    consolidated DictFields.
    """
    from datetime import UTC, datetime

    from models.agent_session import AgentSession

    # Extract property-based fields that map to initial_telegram_message
    msg_text = kwargs.pop("message_text", None)
    sender_name = kwargs.pop("sender_name", None)
    sender_id = kwargs.pop("sender_id", None)
    telegram_message_id = kwargs.pop("telegram_message_id", None)
    chat_title = kwargs.pop("chat_title", None)

    # Extract property-based fields that map to extra_context
    revival_context = kwargs.pop("revival_context", None)
    classification_type = kwargs.pop("classification_type", None)
    classification_confidence = kwargs.pop("classification_confidence", None)

    # Extract property-based fields that map to slug
    work_item_slug = kwargs.pop("work_item_slug", None)

    # Build initial_telegram_message if any telegram fields provided
    if "initial_telegram_message" not in kwargs:
        itm = {}
        if msg_text is not None:
            itm["message_text"] = msg_text
        if sender_name is not None:
            itm["sender_name"] = sender_name
        if sender_id is not None:
            itm["sender_id"] = sender_id
        if telegram_message_id is not None:
            itm["telegram_message_id"] = telegram_message_id
        if chat_title is not None:
            itm["chat_title"] = chat_title
        if itm:
            kwargs["initial_telegram_message"] = itm

    # Build extra_context if any context fields provided
    if "extra_context" not in kwargs:
        ec = {}
        if revival_context is not None:
            ec["revival_context"] = revival_context
        if classification_type is not None:
            ec["classification_type"] = classification_type
        if classification_confidence is not None:
            ec["classification_confidence"] = classification_confidence
        if ec:
            kwargs["extra_context"] = ec

    # Map work_item_slug to slug
    if work_item_slug is not None and "slug" not in kwargs:
        kwargs["slug"] = work_item_slug

    # Ensure created_at uses datetime
    if "created_at" not in kwargs:
        kwargs["created_at"] = datetime.now(tz=UTC)

    return AgentSession.create(**kwargs)


# ---------------------------------------------------------------------------
# Auto-apply feature markers based on test filename
# ---------------------------------------------------------------------------
# Centralised here so it applies to ALL test directories (unit, integration,
# e2e, tools, performance, ai_judge).  Run a specific feature's tests with:
#     pytest -m sdlc
#     pytest -m "messaging or sessions"
# ---------------------------------------------------------------------------
FEATURE_MAP = {
    "bridge": "messaging",
    "messenger": "messaging",
    "telegram": "messaging",
    "duplicate_delivery": "messaging",
    "transcript": "messaging",
    "dedup": "messaging",
    "markdown": "messaging",
    "media_handling": "messaging",
    "routing": "messaging",
    "pm_channels": "messaging",
    "unthreaded": "messaging",
    "file_extraction": "messaging",
    "message_pipeline": "messaging",
    "reply_delivery": "messaging",
    "pipeline": "sdlc",
    "sdlc": "sdlc",
    "observer": "sdlc",
    "stop_hook": "sdlc",
    "stop_reason": "sdlc",
    "post_tool_use": "sdlc",
    "pre_tool_use": "sdlc",
    "skill_outcome": "sdlc",
    "skills_audit": "sdlc",
    "steering": "sdlc",
    "cross_repo_build": "sdlc",
    "session_status": "sessions",
    "session_stuck": "sessions",
    "session_watchdog": "sessions",
    "stall_detection": "sessions",
    "pending_stall": "sessions",
    "pending_recovery": "sessions",
    "escape_hatch": "sessions",
    "lifecycle": "sessions",
    "session_continuity": "sessions",
    "goal_gates": "sessions",
    "open_question": "sessions",
    "agent_session": "sessions",
    "agent_session_hierarchy": "jobs",
    "agent_session_scheduler": "jobs",
    "agent_session_queue": "jobs",
    "agent_session_health": "jobs",
    "enqueue": "jobs",
    "reflection": "reflections",
    "config": "config",
    "context_modes": "context",
    "session_tags": "context",
    "auto_continue": "classifiers",
    "intake_classifier": "classifiers",
    "work_request_classifier": "classifiers",
    "message_quality": "classifiers",
    "stage_aware_auto_continue": "classifiers",
    "validate_commit": "validation",
    "validate_verification": "validation",
    "validate_test_impact": "validation",
    "validate_sdlc": "validation",
    "verification_parser": "validation",
    "features_readme": "validation",
    "build_validation": "validation",
    "checkpoint": "validation",
    "docs_auditor": "validation",
    "branch_manager": "git",
    "worktree_manager": "git",
    "git_state": "git",
    "workspace_safety": "git",
    "symlinks": "git",
    "sdk_client": "sdk",
    "sdk_permissions": "sdk",
    "workflow_sdk": "sdk",
    "code_impact": "impact",
    "doc_impact": "impact",
    "cross_repo_gh": "impact",
    "cross_wire": "impact",
    "model_relationships": "models",
    "redis_models": "models",
    "summarizer": "summarizer",
    "telemetry": "monitoring",
    "health_check": "monitoring",
    "bridge_watchdog": "monitoring",
    "connectivity": "monitoring",
    "silent_failures": "monitoring",
    "remote_update": "config",
    "benchmarks": "monitoring",
    "classifier": "classifiers",
    "code_execution": "tools",
    "link_analysis": "tools",
    "doc_summary": "tools",
    "image_analysis": "tools",
    "search": "tools",
    "test_judge": "tools",
    "ai_judge": "tools",
    "telegram_history": "tools",
}


def pytest_collection_modifyitems(items):
    """Auto-apply feature markers based on test file name."""
    for item in items:
        filename = item.nodeid.split("::")[0].split("/")[-1].replace("test_", "").replace(".py", "")
        for pattern, marker_name in FEATURE_MAP.items():
            if pattern in filename:
                item.add_marker(getattr(pytest.mark, marker_name))
                break


@pytest.fixture
def sample_config():
    """Sample project configuration matching ~/Desktop/Valor/projects.json structure."""
    return {
        "projects": {
            "valor": {
                "name": "Valor AI",
                "description": "AI coworker system",
                "machine": "TestMachine",
                "telegram": {
                    "groups": ["Dev: Valor"],
                    "respond_to_all": False,
                    "respond_to_mentions": True,
                    "respond_to_dms": True,
                    "mention_triggers": ["@valor", "valor", "hey valor"],
                    # Registered bot peer (issue #1574): recorded to history,
                    # never spawns a session; home of the settle_profile.
                    "bots": [
                        {
                            "id": 8837490628,
                            "username": "cyndra_staff_bot",
                            "name": "Bruce @ Internal Staff",
                            "under_test": True,
                            "settle_profile": {
                                "cleanup_progress": False,
                                "quiet_window_seconds": 5,
                                "default_timeout_seconds": 600,
                                "status_patterns": [
                                    "^⏳",
                                    "^(💻|🔎|🔧|📖|⚙️|📝) \\w+:",
                                ],
                            },
                        }
                    ],
                },
                "github": {"org": "tomcounsell", "repo": "ai"},
                "context": {
                    "tech_stack": ["Python", "Claude Agent SDK", "Telethon"],
                    "description": "Focus on agentic systems",
                },
            },
            "popoto": {
                "name": "Popoto",
                "description": "Redis ORM for Python",
                "telegram": {
                    "groups": ["Dev: Popoto"],
                    "respond_to_all": False,
                    "respond_to_mentions": True,
                    "respond_to_dms": False,
                },
                "github": {"org": "tomcounsell", "repo": "popoto"},
                "context": {
                    "tech_stack": ["Python", "Redis"],
                    "description": "Focus on Redis data modeling",
                },
            },
            "django-project-template": {
                "name": "Django Project Template",
                "description": "Modern Django template",
                "telegram": {
                    "groups": ["Dev: Django Template"],
                    "respond_to_all": True,  # Responds to all messages
                    "respond_to_mentions": True,
                    "respond_to_dms": False,
                },
                "github": {"org": "tomcounsell", "repo": "django-project-template"},
                "context": {
                    "tech_stack": ["Django", "PostgreSQL", "Redis"],
                    "description": "Focus on Django best practices",
                },
            },
        },
        "defaults": {
            "telegram": {
                "respond_to_all": False,
                "respond_to_mentions": True,
                "respond_to_dms": True,
                "mention_triggers": ["@valor", "valor", "hey valor"],
            },
            "response": {
                "typing_indicator": True,
                "max_response_length": 4000,
                "timeout_seconds": 300,
            },
        },
    }


@pytest.fixture
def valor_project(sample_config):
    """Extract Valor project config with _key added."""
    project = sample_config["projects"]["valor"].copy()
    project["_key"] = "valor"
    return project


@pytest.fixture
def popoto_project(sample_config):
    """Extract Popoto project config with _key added."""
    project = sample_config["projects"]["popoto"].copy()
    project["_key"] = "popoto"
    return project


@pytest.fixture
def django_project(sample_config):
    """Extract Django project config with _key added."""
    project = sample_config["projects"]["django-project-template"].copy()
    project["_key"] = "django-project-template"
    return project


# ---------------------------------------------------------------------------
# xdist worker reaper
# ---------------------------------------------------------------------------
# pytest-xdist workers run via
#   `python -c "import sys; exec(eval(sys.stdin.readline()))"`
# which installs no signal handlers. If the parent pytest process dies
# (timeouts, agent tooling interrupting, a keyboard interrupt racing
# with teardown) the workers get reparented to init and stay alive
# consuming memory. On a 10-CPU box each leaked worker is ~15-25MB of
# RAM, and one crash loop can leave 60+ zombies.
#
# The shell-level `scripts/pytest-clean.sh` covers the happy path. The
# controller-level reaper below covers the case where the controller
# itself exits without the wrapper's trap firing (e.g. SIGKILL of the
# wrapper, or a pytest crash).
#
# IMPORTANT: this reaper runs on the CONTROLLER (xdist master), not in
# the workers. It kills workers by matching the standard xdist worker
# argv regex.
XDIST_WORKER_RE = r"exec\(eval\(sys\.stdin\.readline\(\)\)\)"


def _ppid_of(pid: int) -> int | None:
    """Parent PID via `ps` (no psutil). None on any failure."""
    try:
        result = subprocess.run(
            ["ps", "-o", "ppid=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        out = result.stdout.strip()
        return int(out) if out else None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, ValueError):
        return None


def _ours_or_orphan(pid: int) -> bool:
    """True if this process is an ancestor of pid, or pid is orphaned.

    On a shared machine two pytest controllers can run concurrently; a
    machine-wide reap from one run kills the other run's live workers
    (mass `node down: Not properly terminated`). So only reap workers we
    own (our pid appears in the ancestry chain) or workers already
    re-parented to init (direct PPID 1 — their controller is gone, no
    live run owns them). Anything else belongs to someone else's run.
    """
    me = os.getpid()
    current = pid
    for _ in range(32):  # ancestry depth cap; chains are short in practice
        parent = _ppid_of(current)
        if parent is None:
            return False
        if current == pid and parent == 1:
            return True  # orphaned worker, controller already gone
        if parent == me:
            return True
        if parent <= 1:
            return False  # walked past init without meeting us: not ours
        current = parent
    return False


def _reap_xdist_workers() -> None:
    """Find and kill xdist worker processes owned by this run.

    Uses `pgrep` so we don't need psutil. Scoped to our own descendants
    plus init-orphaned workers (see _ours_or_orphan). Idempotent. Catches
    every exception so a reap failure never blocks pytest teardown. For a
    deliberate machine-wide sweep, use scripts/reap-xdist.sh.
    """
    try:
        result = subprocess.run(
            ["pgrep", "-f", XDIST_WORKER_RE],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return

    pids = [p for p in result.stdout.split() if p.isdigit() and _ours_or_orphan(int(p))]
    if not pids:
        return

    for pid in pids:
        try:
            os.kill(int(pid), 15)  # SIGTERM
        except (OSError, ValueError):
            pass
    time.sleep(0.5)
    for pid in pids:
        try:
            os.kill(int(pid), 9)  # SIGKILL survivors
        except (OSError, ValueError):
            pass


def pytest_unconfigure(config):
    """Run the reap on pytest's normal teardown path.

    Only the controller runs this hook; workers have `workerinput`
    set on their config. The wrap in a try/except keeps the reap
    out of the way on non-xdist runs and on import failures.
    """
    try:
        import xdist  # noqa: F401
    except ImportError:
        return
    if getattr(config, "workerinput", None):
        # We are a worker; workers have no business reaping siblings.
        return
    _reap_xdist_workers()


# atexit covers the case where pytest's unconfigure hook didn't fire
# (e.g. the controller segfaulted, or the test runner killed the
# process group). atexit runs on the normal Python interpreter exit
# path, which is the strongest hook we can install at module load
# time.
#
# Gated on PYTEST_XDIST_WORKER being unset so the worker processes
# (which also import this conftest via xdist's path resolution) don't
# re-register. The controller sets this env var to the worker name
# (e.g. "gw0") once it forks; before that, it's unset, so this code
# only runs in the controller and in non-xdist runs.
if "PYTEST_XDIST_WORKER" not in os.environ:
    atexit.register(_reap_xdist_workers)
