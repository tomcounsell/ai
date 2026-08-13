"""Ambient production-Redis flush guard (Layer 1 of #2645).

On 2026-06-03 a `flushdb()`/`flushall()` against Redis db=0 wiped the
production dataset (memories, Telegram history, chats, knowledge docs). On
2026-08-07 an ad-hoc debug script did it again, this time from a
harness-created worktree that `tests/conftest.py`'s pytest-only guard never
reaches, destroying 25,825 production keys.

`tests/conftest.py::_install_redis_db0_flush_guard` only protects `pytest`
runs -- it installs at conftest import time, which never happens for a bare
`python -c "..."` debug script, a one-off REPL session, or any process that
never imports the test suite. This module promotes the same shape to
**interpreter scope**: it is armed from every Python process started inside a
provisioned repo venv (via a `.pth` shim calling `arm()`, see
`docs/features/redis-flush-hardening.md`), so the guard is live for exactly
the kind of ad-hoc script that caused both incidents, not only for pytest.

Guarded operation, not guarded construction (D1): connection construction
stays unrestricted -- db=0 is legitimate production traffic for the 21
first-party call sites spike-3 found. `flushdb()` on db=0 and `flushall()`
anywhere raise instead. The override is `REDIS_PRODUCTION_FLUSH_OK=1`, read
at call time, never at import time.

Public surface: `arm()`, `install()`, `is_installed(cls=None)`.
"""

from __future__ import annotations

import importlib.abc
import importlib.util
import os
import sys
import sysconfig
import threading
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Startup budget (D2a-ii). Provisional and tunable -- the point of this
# constant is that a regression in the guard's own startup cost is loud
# (asserted by a pytest case), not that the number is sacred. Override for a
# slow machine via the env var below.
#
# Calibration, measured on this machine rather than guessed:
#   shipped lazy `arm()` path .............  6.3 ms idle
#                                           10.4 ms min-of-15 at load avg 51
#                                           18.1 ms worst of that same run
#   eager `install()` regression .......... 103.8 ms min (a 513x jump, because
#                                           `install()` imports redis and
#                                           redis.asyncio, which pull asyncio
#                                           and ssl)
# `-X importtime` reports wall clock, so a budget near the shipped cost turns
# this into a load sensor that fires spuriously on a machine running several
# agents. 30 ms sits above the loaded measurement band and far below the
# regression it exists to catch, so the gate stays quiet when the code is
# right and still fires hard when the mechanism is wrong.
# ---------------------------------------------------------------------------
_STARTUP_BUDGET_MS = float(os.environ.get("REDIS_FLUSH_GUARD_STARTUP_BUDGET_MS", "30"))

# ---------------------------------------------------------------------------
# Idempotence registry (D6a). Keyed on the patched class object itself, never
# on an attribute walk -- tests/conftest.py's wrapper does not carry
# `_prod_flush_guarded` forward onto the callable it wraps, so an attribute
# check would re-wrap on every `install()` call under pytest.
# ---------------------------------------------------------------------------
_INSTALLED: set[type] = set()

# Loaded by file path (Finding B), never `import scripts.update...` -- see
# `_load_pth_installer`'s docstring for why. Exposed as a module-level
# constant so tests can monkeypatch it to a stub installer.
_PTH_INSTALLER_PATH = (
    Path(__file__).resolve().parent.parent / "scripts" / "update" / "redis_flush_guard_pth.py"
)

_INCIDENT_DATES = "2026-06-03 and 2026-08-07"


# ---------------------------------------------------------------------------
# db resolution -- fail closed (mirrors tests/conftest.py:110-115 exactly)
# ---------------------------------------------------------------------------
def _db_of(client: Any) -> int:
    """Return the db this client is bound to, or 0 if it cannot be determined.

    Fails closed: a client we cannot introspect is treated as the dangerous
    db (0) rather than assumed safe.
    """
    try:
        return int(client.connection_pool.connection_kwargs.get("db", 0) or 0)
    except Exception:
        return 0


def _override_active() -> bool:
    """Read the escape hatch at CALL time, not import time (D1).

    Only the exact string "1" disarms. "", "0", "false", "no" all leave the
    guard armed -- this is the one place a truthiness bug would silently
    disable the whole layer, so the comparison is exact-equality, not
    ``bool(...)``.
    """
    return os.environ.get("REDIS_PRODUCTION_FLUSH_OK") == "1"


def _flushdb_message(db: int) -> str:
    return (
        f"Refusing flushdb() on Redis db={db} (production). Set "
        "REDIS_PRODUCTION_FLUSH_OK=1 to override deliberately, or point the "
        "client at a non-zero test db instead (see tests/conftest.py's "
        "redis_test_db fixture / tests/db_claim.py's redis_test_url helper). "
        "This guard exists because "
        f"a db=0 flush wiped production on {_INCIDENT_DATES}."
    )


def _flushall_message() -> str:
    return (
        "Refusing flushall() -- it wipes EVERY Redis db, including db=0 "
        "(production). Set REDIS_PRODUCTION_FLUSH_OK=1 to override "
        "deliberately, or flush only your own db with flushdb() after "
        "pointing the client at a non-zero test db instead (see "
        "tests/conftest.py's redis_test_db fixture / tests/db_claim.py's "
        "redis_test_url helper). This "
        f"guard exists because a db=0 flush wiped production on {_INCIDENT_DATES}."
    )


# ---------------------------------------------------------------------------
# install() -- patches flushdb/flushall on redis.Redis and redis.asyncio.Redis
# ---------------------------------------------------------------------------
def _install_on_class(cls: type, *, is_async: bool) -> None:
    if cls in _INSTALLED:
        return

    orig_flushdb = cls.flushdb
    orig_flushall = cls.flushall

    if is_async:

        async def _guarded_flushdb(self, *args, **kwargs):
            db = _db_of(self)
            if db == 0 and not _override_active():
                raise RuntimeError(_flushdb_message(db))
            return await orig_flushdb(self, *args, **kwargs)

        async def _guarded_flushall(self, *args, **kwargs):
            if not _override_active():
                raise RuntimeError(_flushall_message())
            return await orig_flushall(self, *args, **kwargs)
    else:

        def _guarded_flushdb(self, *args, **kwargs):
            db = _db_of(self)
            if db == 0 and not _override_active():
                raise RuntimeError(_flushdb_message(db))
            return orig_flushdb(self, *args, **kwargs)

        def _guarded_flushall(self, *args, **kwargs):
            if not _override_active():
                raise RuntimeError(_flushall_message())
            return orig_flushall(self, *args, **kwargs)

    # Solely a liveness signal for the doctor's subprocess probe (a clean
    # interpreter, no conftest wrapper on top). Never read for an in-process
    # idempotence or liveness decision (D6a) -- _INSTALLED is the only thing
    # that governs that.
    _guarded_flushdb._prod_flush_guarded = True
    _guarded_flushall._prod_flush_guarded = True

    cls.flushdb = _guarded_flushdb
    cls.flushall = _guarded_flushall

    # Registered as installed only after BOTH assignments succeed. Doing it up
    # front made a class that failed to patch look installed forever: the early
    # return above meant install() never retried it, and is_installed() said
    # True for an unpatched class. Not reachable with real redis-py classes,
    # but "the guard reports itself healthy while inert" is the one lie this
    # module must never tell.
    _INSTALLED.add(cls)


def install() -> bool:
    """Eagerly patch redis.Redis and redis.asyncio.Redis. Never raises.

    Returns falsy if `redis` cannot be imported. This is the entry point unit
    tests and the doctor's subprocess probe use directly; `arm()` is what the
    `.pth` shim calls so a process that never touches Redis pays nothing for
    this.
    """
    try:
        import redis
    except Exception:
        return False

    try:
        _self_heal()
    except Exception:  # noqa: S110 -- D2b: a failed self-heal never propagates
        pass

    try:
        _install_on_class(redis.Redis, is_async=False)

        try:
            import redis.asyncio as aioredis
        except Exception:
            aioredis = None
        if aioredis is not None:
            _install_on_class(aioredis.Redis, is_async=True)
    except Exception:
        # install() must never raise -- a broken guard is worse than no guard.
        return False

    return True


def is_installed(cls: type | None = None) -> bool:
    """True if the guard is installed on `cls`, or on both redis classes if
    `cls` is omitted (False if `redis` cannot even be imported)."""
    if cls is not None:
        return cls in _INSTALLED
    try:
        import redis
    except Exception:
        return False
    if redis.Redis not in _INSTALLED:
        return False
    try:
        import redis.asyncio as aioredis
    except Exception:
        return True
    return aioredis.Redis in _INSTALLED


# ---------------------------------------------------------------------------
# Self-heal (D2b/D2b-i): install the `.pth` into the venv running THIS
# interpreter when it is missing. Scoped to the current venv only -- this
# runs on an interpreter-startup path, so it never walks other venvs.
# ---------------------------------------------------------------------------
def _current_site_packages() -> str | None:
    try:
        paths = sysconfig.get_paths()
        return paths.get("purelib") or paths.get("platlib")
    except Exception:
        return None


def _load_pth_installer(path: Path | str):
    """Load the `.pth` installer module by FILE PATH, never by package import.

    `scripts/update/__init__.py` does `from .run import UpdateConfig,
    run_update`, and `scripts/update/run.py` inserts PROJECT_ROOT at
    `sys.path[0]` and eagerly imports ~30 submodules as a side effect of
    package import. `import scripts.update.redis_flush_guard_pth` would drag
    that whole system into every interpreter start and mutate `sys.path[0]`
    on a path that must stay cheap. Loading by file path via importlib avoids
    importing the `scripts.update` package at all.
    """
    try:
        path = Path(path)
        if not path.is_file():
            return None
        spec = importlib.util.spec_from_file_location("_rfg_pth_installer", path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception:
        return None


def _self_heal() -> None:
    """Best-effort: install the `.pth` into the current venv if missing.

    Always wrapped by callers in try/except -- a failure to self-heal must
    never propagate. Skipped entirely when not running from a venv, when
    site-packages cannot be resolved, when the `.pth` is already present (the
    common hot-path case: a single `os.path.exists` stat), or when
    site-packages is not writable.
    """
    if sys.prefix == sys.base_prefix:
        return  # not running from a venv

    site_packages = _current_site_packages()
    if not site_packages or not os.path.isdir(site_packages):
        return

    pth_path = os.path.join(site_packages, "zzz_redis_flush_guard.pth")
    if os.path.exists(pth_path):
        return  # already healed -- the one stat() on the hit path

    if not os.access(site_packages, os.W_OK):
        return  # read-only site-packages

    installer = _load_pth_installer(_PTH_INSTALLER_PATH)
    if installer is None or not hasattr(installer, "install_into"):
        return  # installer not built yet, or unloadable -- clean no-op

    installer.install_into(sys.prefix)


# ---------------------------------------------------------------------------
# arm() -- the lazy entry point the `.pth` shim calls (D2a, D2a-i)
# ---------------------------------------------------------------------------
# RLock, not Lock: the swap in _RedisArmingFinder.find_spec holds the lock
# across a call to importlib.util.find_spec(), which for a dotted name
# ("redis.asyncio") imports the parent package as a side effect. If that
# parent-package import re-enters our finder's find_spec on the SAME thread
# (e.g. via a third-party import hook further down the chain touching
# "redis" again), a plain Lock would deadlock; RLock allows the same thread
# to re-acquire it. Cross-thread contention still blocks correctly.
_FINDER_LOCK = threading.RLock()

_TARGET_NAMES = frozenset({"redis", "redis.asyncio"})

# Re-entrancy flag: install() itself does `import redis`, which must not
# re-trigger the "run install() after exec_module" wrapper.
_ARMING = False

_finder_instance: _RedisArmingFinder | None = None


class _RedisArmingFinder(importlib.abc.MetaPathFinder):
    """Calls install() after the FIRST real import of redis/redis.asyncio.

    Per D2a-i: find_spec() runs before the module object exists, so calling
    install() there would patch a half-initialized module (or recurse, since
    install() itself imports redis). Instead this finder removes itself,
    delegates to the real finders to get the real spec, reinserts itself, and
    wraps the spec's loader so install() runs AFTER exec_module returns --
    i.e. against a fully initialized module.
    """

    def find_spec(self, fullname, path=None, target=None):  # noqa: D102
        if fullname not in _TARGET_NAMES:
            return None

        try:
            with _FINDER_LOCK:
                try:
                    sys.meta_path.remove(self)
                    spec = importlib.util.find_spec(fullname)
                finally:
                    # Unconditional re-insertion: if find_spec() raised above,
                    # this still runs, so the finder can never be permanently
                    # (silently) disarmed. The membership test prevents ever
                    # inserting a duplicate.
                    if self not in sys.meta_path:
                        sys.meta_path.insert(0, self)
        except Exception:
            # The real import proceeds unguarded rather than failing outright.
            return None

        if spec is None or spec.loader is None:
            return spec

        orig_exec_module = spec.loader.exec_module

        def _exec_module_and_arm(module):
            orig_exec_module(module)
            global _ARMING
            if _ARMING:
                return
            _ARMING = True
            try:
                install()
            except Exception:  # noqa: S110 -- D2a-i: never break an unrelated `import redis`
                pass
            finally:
                _ARMING = False

        try:
            spec.loader.exec_module = _exec_module_and_arm
        except Exception:  # noqa: S110 -- D2a-i: an unwrappable loader imports unguarded
            pass

        return spec


def _ensure_finder_installed() -> None:
    global _finder_instance
    with _FINDER_LOCK:
        if _finder_instance is None:
            _finder_instance = _RedisArmingFinder()
        if _finder_instance not in sys.meta_path:
            sys.meta_path.insert(0, _finder_instance)


def arm() -> None:
    """Lazily arm the guard. Called by the `.pth` boot shim and by
    `tools/__init__.py`. Does NOT import `redis` (see
    test_arm_does_not_import_redis) -- it only inserts a meta-path finder
    (and performs a self-heal `.pth` check, which is a single `stat()`, not
    an import). `install()` runs later, on the first real import of `redis`
    or `redis.asyncio`, or immediately below if one of them is already
    loaded.
    """
    try:
        _self_heal()
    except Exception:  # noqa: S110 -- D2b: a failed self-heal never propagates
        pass

    _ensure_finder_installed()

    if "redis" in sys.modules or "redis.asyncio" in sys.modules:
        try:
            install()
        except Exception:  # noqa: S110 -- D2a: arming must never break the caller
            pass
