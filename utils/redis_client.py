"""The one place production code obtains a Redis client for non-ORM keys.

Popoto owns the canonical connection, ``popoto.redis_db.POPOTO_REDIS_DB``. Its
connection pool is the single fact about *which Redis* this process talks to:
``config/redis_bootstrap.py`` rebuilds it at startup and ``tests/conftest.py``
repoints it at a per-process claimed test database. A client derived from that
pool follows the **repoint** -- whichever host, port, and database the pool now
names. It does *not* inherit the pool's retry policy, keepalive, or
health-check interval: those are per-client connection policy rather than
identity, and each accessor below chooses its own. A client built by hand from
``REDIS_URL`` follows neither: it resolves its own database at call time, and
under test it landed on production db 0 (issue #3003).

Three accessors, one pool of truth:

``text_redis()``
    A ``decode_responses=True`` client on popoto's host, port, database,
    credentials, and TLS posture, with request/response socket timeouts from
    ``settings.timeouts.redis_socket_s``. Cached per process and rebuilt the
    moment popoto's pool identity changes, so the conftest swap and the
    resilient bootstrap are both honoured without any caller knowing. This is
    what the outbox writers, relays, liveness stamps, dedup claims, and the
    dashboard use: freeform keys whose values are text.

    It builds its **own** bounded pool (``settings.redis.max_connections``,
    ``settings.redis.health_check_interval_s``) rather than sharing popoto's or
    accepting redis-py's effectively unbounded default. This one client serves
    the bridge event loop, ``asyncio.to_thread`` relay workers, and the FastAPI
    threadpool at once -- exactly the burst profile a cap exists for.

``bytes_redis()``
    ``POPOTO_REDIS_DB`` itself, for code that already speaks bytes (the
    customer-resolver cache). Two consequences of handing back popoto's own
    object: its caller must **never** ``.close()`` it (that would disconnect the
    ORM's pool out from under every model in the process), and its calls share
    popoto's ``BlockingConnectionPool``, whose checkout *blocks* on exhaustion
    rather than raising. ``socket_timeout`` does not cover a pool-checkout
    block, so a ``bytes_redis()`` call must never run directly on an event loop
    -- wrap it in ``asyncio.to_thread``.

``derived_redis(**overrides)``
    A fresh, uncached client on popoto's identity with the caller's own
    connection kwargs. For the two pubsub connections in
    ``agent/agent_session_queue.py`` whose ``socket_timeout`` contracts differ
    from every request/response client and must not be shared. Unlike
    ``bytes_redis()``, the caller owns this connection's lifetime and closes it.

``scan_keys(client, match)``
    A bounded, cursor-based key sweep. Production code must never issue a
    full-keyspace ``KEYS``: it is O(keyspace) on a Redis that has carried
    millions of keys (#2207), and every accessor here now carries a socket
    timeout where the old hand-built clients carried none. A ``KEYS`` that
    crosses that timeout raises ``TimeoutError`` on whatever path issued it --
    which, on a send path with a blanket handler, is indistinguishable from an
    empty queue. ``scan_keys`` bounds both the per-round-trip work and the total
    returned, so neither failure mode is reachable.

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

__all__ = ["bytes_redis", "derived_redis", "scan_keys", "text_redis"]

# Socket timeouts are per-client *policy*, chosen by each accessor below. They
# ride along in popoto's connection_kwargs, so they must be dropped from the
# identity copy rather than inherited.
_POLICY_KEYS = ("socket_timeout", "socket_connect_timeout")

_lock = threading.Lock()
_cached_text_client: redis.Redis | None = None
_cached_text_identity: tuple | None = None


def _popoto_client() -> redis.Redis:
    from popoto.redis_db import POPOTO_REDIS_DB

    return POPOTO_REDIS_DB


def _identity_kwargs(client: redis.Redis) -> dict[str, Any]:
    """Host/port/db/auth/TLS (or unix socket path) of ``client``'s live pool.

    Built on popoto's ``sibling_client_kwargs`` whitelist rather than a local
    key list, so redis-py's churn in what ``Redis.__init__`` accepts stays
    popoto's problem. Two things that whitelist cannot see are layered on here:

    * **Unix sockets.** redis-py stores the socket as ``path`` in a
      ``UnixDomainSocketConnection`` pool's kwargs, but the constructor takes
      ``unix_socket_path``. The whitelist looks for the constructor spelling
      and so misses the pool spelling.
    * **TLS.** A ``rediss://`` pool expresses TLS in ``connection_class``
      (``SSLConnection``), not in ``connection_kwargs``. Copying kwargs alone
      would hand every derived client a *plaintext* socket against a TLS port.
    """
    from popoto.redis_db import sibling_client_kwargs

    pool = client.connection_pool
    kw = pool.connection_kwargs
    identity: dict[str, Any] = sibling_client_kwargs(kw)
    for key in _POLICY_KEYS:
        identity.pop(key, None)

    if kw.get("path"):
        identity["unix_socket_path"] = kw["path"]
        # host/port are meaningless for a unix socket and redis-py rejects the
        # combination.
        identity.pop("host", None)
        identity.pop("port", None)
    else:
        identity.setdefault("host", "localhost")
        identity["port"] = int(identity.get("port") or 6379)
        if _pool_is_tls(pool):
            identity["ssl"] = True
            for key in ("ssl_cert_reqs", "ssl_check_hostname", "ssl_password"):
                if kw.get(key) is not None:
                    identity[key] = kw[key]

    identity["db"] = int(kw.get("db", 0) or 0)
    return identity


def _pool_is_tls(pool: Any) -> bool:
    """Whether ``pool`` speaks TLS, which lives in its connection *class*."""
    connection_class = getattr(pool, "connection_class", None)
    if connection_class is None:
        return False
    ssl_connection = getattr(redis.connection, "SSLConnection", None)
    if ssl_connection is None:
        return False
    return isinstance(connection_class, type) and issubclass(connection_class, ssl_connection)


def scan_keys(client: redis.Redis, match: str) -> tuple[list[Any], bool]:
    """Cursor-scan ``client`` for keys matching ``match``, bounded on both axes.

    The replacement for ``client.keys(pattern)`` in production code. ``KEYS``
    is O(keyspace) and single-shot: on a Redis holding millions of keys it can
    exceed the accessors' socket timeout, and the resulting ``TimeoutError``
    surfaces as a failure of whatever call site issued it. ``SCAN`` bounds the
    work per round trip (``settings.redis.scan_count``) so no single command
    can blow the timeout.

    Returns:
        ``(keys, truncated)``. ``truncated`` is True when the sweep stopped at
        ``settings.redis.scan_key_limit`` with the cursor still open -- the
        caller has a partial view and should expect the remainder on its next
        pass. Callers that drain a queue can ignore it; callers that reason
        about the *absence* of a key must not.
    """
    from config.settings import settings

    count = int(settings.redis.scan_count)
    limit = int(settings.redis.scan_key_limit)

    keys: list[Any] = []
    cursor = 0
    while True:
        cursor, batch = client.scan(cursor=cursor, match=match, count=count)
        keys.extend(batch)
        if len(keys) >= limit:
            return keys[:limit], True
        if cursor == 0:
            return keys, False


def bytes_redis() -> redis.Redis:
    """Popoto's canonical client: raw bytes responses.

    This is popoto's own client *object*, not a copy. The caller must never
    ``.close()`` it -- that disconnects the ORM's pool for the whole process --
    and must not call it directly on an event loop, because it shares popoto's
    ``BlockingConnectionPool`` whose checkout blocks on exhaustion. Use
    ``asyncio.to_thread``. Contrast ``derived_redis()``, whose caller *does*
    own the lifetime.
    """
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
            # Its own bounded pool. redis-py's default is 2**31 connections;
            # this client fronts the bridge event loop, the relays'
            # to_thread workers, and the dashboard threadpool at once, so an
            # unbounded pool is a route to the server's maxclients.
            max_connections=int(settings.redis.max_connections),
            health_check_interval=int(settings.redis.health_check_interval_s),
        )
        _cached_text_identity = identity_key
    if previous is not None:
        previous.close()
    return _cached_text_client
