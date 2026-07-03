"""Resilient Popoto Redis client bootstrap.

Rebuilds the global Popoto Redis client with retry / backoff / health-check
settings so the worker and bridge survive transient Redis restarts without
crashing or requiring a manual restart.

**Why this module exists:**
Popoto is a pip-installed third-party package (``popoto/redis_db.py``). Its
import-time code builds a ``redis.Redis`` client with bare socket timeouts and
no retry logic — an unreachable Redis at import time raises immediately and
crashes the process. The only supported app-boundary seam for reconfiguration
is ``popoto.redis_db.set_REDIS_DB_settings(**kwargs)`` which rebuilds the global
``POPOTO_REDIS_DB``. This module wraps that seam.

**Usage (call once at worker / bridge startup):**::

    from config.redis_bootstrap import configure_resilient_redis
    configure_resilient_redis()

**Degrade-don't-die guarantee:**
If Redis is unreachable when this function is called, it logs a WARNING and
returns without raising — the process starts in a degraded state. All
subsequent Popoto calls will fail individually (with ``ConnectionError``), but
they too are caught by the retry policy configured here; a transient Redis
restart triggers exponential backoff rather than an immediate exception.

**Test isolation:**
Under pytest (``PYTEST_CURRENT_TEST`` is set), the function is a no-op so the
``redis_test_db`` fixture in ``tests/conftest.py`` retains full control of
``POPOTO_REDIS_DB``. The bootstrap must NOT fight the test fixture.

**Thread safety / run-once:**
A module-level sentinel ensures the bootstrap runs at most once per process
even if multiple modules import and call it (worker and bridge startup both
call it). The sentinel is intentionally NOT reset by tests — tests rely on
the no-op guard instead.
"""

from __future__ import annotations

import logging
import os
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Run-once sentinel. Set to True after the first successful (or skipped) call.
_BOOTSTRAPPED = False

# Module-level settings reference — exposed so tests can patch it.
# Populated lazily on first call to configure_resilient_redis().
_settings = None


def configure_resilient_redis() -> None:
    """Rebuild the Popoto global Redis client with retry / backoff / health-check.

    Idempotent: subsequent calls after the first are no-ops (run-once guard).
    Under pytest (``PYTEST_CURRENT_TEST`` env var set), always a no-op so the
    test fixture retains control of ``POPOTO_REDIS_DB``.

    Calls ``popoto.redis_db.set_REDIS_DB_settings(...)`` with:
    - ``Retry(ExponentialBackoff(cap=10, base=1), 3)`` on ``ConnectionError``,
      ``TimeoutError``, and ``ConnectionResetError``
    - ``health_check_interval=30`` (background ping every 30 s)
    - Original socket timeouts preserved (5 s each)

    The ``set_REDIS_DB_settings`` call is wrapped in ``try/except`` — if Redis
    is down at call time, the call itself may succeed (redis-py connects lazily)
    or fail; either way we log the outcome and return without raising.
    """
    global _BOOTSTRAPPED

    # No-op under pytest — test fixture owns POPOTO_REDIS_DB.
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return

    # Run-once guard.
    if _BOOTSTRAPPED:
        return
    _BOOTSTRAPPED = True

    try:
        from redis.backoff import ExponentialBackoff
        from redis.exceptions import ConnectionError as RedisConnectionError
        from redis.exceptions import TimeoutError as RedisTimeoutError
        from redis.retry import Retry
    except ImportError as exc:
        logger.error(
            "[redis_bootstrap] redis-py retry primitives unavailable (%s) — "
            "popoto client will use bare socket timeouts. "
            "Ensure redis>=4.1.0 is installed.",
            exc,
        )
        return

    # Derive connection params from REDIS_URL (same logic as popoto's own
    # import-time code and config/settings.py::RedisSettings).
    # _settings is a module-level reference so tests can patch it.
    global _settings
    if _settings is None:
        from config.settings import settings as _loaded_settings

        _settings = _loaded_settings

    redis_url = _settings.redis.url or "redis://localhost:6379/0"
    parsed = urlparse(redis_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 6379
    db_str = (parsed.path or "/0").lstrip("/")
    db = int(db_str) if db_str.isdigit() else 0
    password = parsed.password or None

    retry = Retry(
        ExponentialBackoff(cap=10, base=1),
        retries=3,
    )

    try:
        import popoto.redis_db as _rdb

        _rdb.set_REDIS_DB_settings(
            host=host,
            port=port,
            db=db,
            password=password,
            retry=retry,
            retry_on_error=[RedisConnectionError, RedisTimeoutError, ConnectionResetError],
            health_check_interval=30,
            socket_timeout=5,
            socket_connect_timeout=5,
        )

        # Propagate the new client to all popoto submodules that cached
        # POPOTO_REDIS_DB at import time (same pattern as conftest.redis_test_db).
        import sys

        new_client = _rdb.POPOTO_REDIS_DB
        for name, mod in list(sys.modules.items()):
            if (
                mod is not None
                and name.startswith("popoto")
                and hasattr(mod, "POPOTO_REDIS_DB")
                and mod is not _rdb
            ):
                mod.POPOTO_REDIS_DB = new_client

        logger.info(
            "[redis_bootstrap] Resilient Popoto client configured "
            "(host=%s port=%d db=%d retry=3×ExponentialBackoff health_check=30s)",
            host,
            port,
            db,
        )
    except Exception as exc:
        # Degrade-don't-die: log a warning, do NOT raise.
        logger.warning(
            "[redis_bootstrap] Could not configure resilient Popoto client "
            "(Redis may be down at startup): %s. "
            "Starting in degraded mode — Redis operations will fail until Redis recovers.",
            exc,
        )
