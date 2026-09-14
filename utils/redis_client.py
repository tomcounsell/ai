"""The one place production code obtains a Redis client for non-ORM keys.

Popoto owns the canonical connection, ``popoto.redis_db.POPOTO_REDIS_DB``. Its
connection pool is the single fact about *which Redis* this process talks to:
``config/redis_bootstrap.py`` rebuilds it with retry policy at startup, and
``tests/conftest.py`` repoints it at a per-process claimed test database. A
client derived from that pool's identity follows both for free. A client built
by hand from ``REDIS_URL`` does neither: it resolves its own database at call
time, and under test it landed on production db 0 (issue #3003).

Three accessors, one pool of truth:

``text_redis()``
    A ``decode_responses=True`` client on popoto's host, port, database, and
    credentials, with request/response socket timeouts from
    ``settings.timeouts.redis_socket_s``. Cached per process and rebuilt the
    moment popoto's pool identity changes, so the conftest swap and the
    resilient bootstrap are both honoured without any caller knowing. This is
    what the outbox writers, relays, liveness stamps, dedup claims, and the
    dashboard use: freeform keys whose values are text.

``bytes_redis()``
    ``POPOTO_REDIS_DB`` itself, for code that already speaks bytes (the
    customer-resolver cache).

``derived_redis(**overrides)``
    A fresh, uncached client on popoto's identity with the caller's own
    connection kwargs. For the two pubsub connections in
    ``agent/agent_session_queue.py`` whose ``socket_timeout`` contracts differ
    from every request/response client and must not be shared.

Popoto-managed keys never go through any of these. Model rows are read and
written through the ORM (``Model.query.filter()``, ``instance.save()``,
``instance.delete()``); see ``docs/features/raw-redis-guard.md``.

The popoto import stays inside the function bodies on purpose: importing this
module opens no connection, so import-light CLI tools and hooks can depend on
it without paying for Redis at import time.
"""

from __future__ import annotations

import threading
from typing import Any

import redis

__all__ = ["bytes_redis", "derived_redis", "text_redis"]

# Connection kwargs that name *which* Redis a pool points at. Everything else
# on the pool (retry policy, keepalive, protocol) is per-client policy and is
# chosen by each accessor below rather than copied.
_IDENTITY_KEYS = ("host", "port", "db", "username", "password", "path")

_lock = threading.Lock()
_cached_text_client: redis.Redis | None = None
_cached_text_identity: tuple | None = None


def _popoto_client() -> redis.Redis:
    from popoto.redis_db import POPOTO_REDIS_DB

    return POPOTO_REDIS_DB


def _identity_kwargs(client: redis.Redis) -> dict[str, Any]:
    """Host/port/db/auth (or unix socket path) of ``client``'s live pool."""
    kw = client.connection_pool.connection_kwargs
    identity: dict[str, Any] = {}
    if kw.get("path"):
        identity["unix_socket_path"] = kw["path"]
    else:
        identity["host"] = kw.get("host", "localhost")
        identity["port"] = int(kw.get("port", 6379) or 6379)
    identity["db"] = int(kw.get("db", 0) or 0)
    for key in ("username", "password"):
        if kw.get(key) is not None:
            identity[key] = kw[key]
    return identity


def bytes_redis() -> redis.Redis:
    """Popoto's canonical client: raw bytes responses."""
    return _popoto_client()


def derived_redis(**overrides: Any) -> redis.Redis:
    """A new client on popoto's Redis identity with the caller's connection kwargs.

    Uncached: the caller owns the connection's lifetime and closes it.
    """
    return redis.Redis(**{**_identity_kwargs(_popoto_client()), **overrides})


def text_redis() -> redis.Redis:
    """The shared ``decode_responses=True`` client on popoto's Redis identity."""
    global _cached_text_client, _cached_text_identity

    from config.settings import settings

    popoto_client = _popoto_client()
    identity = _identity_kwargs(popoto_client)
    identity_key = (id(popoto_client.connection_pool), tuple(sorted(identity.items())))
    with _lock:
        if _cached_text_client is not None and _cached_text_identity == identity_key:
            return _cached_text_client
        previous = _cached_text_client
        timeout = float(settings.timeouts.redis_socket_s)
        _cached_text_client = redis.Redis(
            **identity,
            decode_responses=True,
            socket_timeout=timeout,
            socket_connect_timeout=timeout,
        )
        _cached_text_identity = identity_key
    if previous is not None:
        previous.close()
    return _cached_text_client
