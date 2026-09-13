---
tracking: https://github.com/tomcounsell/ai/issues/3003
status: Shipped
---

# Redis Client Accessor

Production code obtains a Redis client for non-ORM keys from exactly one
module, `utils/redis_client.py`. Popoto-managed rows keep going through the ORM
(see [Raw-Redis Guard](raw-redis-guard.md)); this accessor covers everything
else: the `telegram:outbox:*` and `email:outbox:*` lists, `bridge:*` liveness
stamps and producer claims, `email:*` history and dead letters, the customer
resolver cache, worker slot leases, and the dashboard's counters.

## Why one accessor

Popoto owns the canonical connection, `popoto.redis_db.POPOTO_REDIS_DB`. Its
connection pool's identity (host, port, db, credentials, or unix socket path)
is the single fact about *which* Redis this process talks to:
`config/redis_bootstrap.py` repoints that identity at startup, and
`tests/conftest.py` repoints it at a per-process claimed test database. A
client derived from that pool follows the repoint — whichever Redis and db
popoto's pool now names — for free. It does **not** inherit the pool's retry
policy, keepalive, or health-check interval: those are per-client connection
policy, not identity, and each accessor below chooses its own. A client built
by hand from `REDIS_URL` followed neither the repoint nor any policy. It
resolved its own database at call time and, under test, twenty-four such sites
wrote to production db 0 (measured during #2805: `tests/unit/test_dedup.py`
alone left `bridge:msgclaim:*` keys in db 0).

## The three accessors

| Accessor | Returns | Used by |
|----------|---------|---------|
| `text_redis()` | A `decode_responses=True` client on popoto's host, port, db, and credentials, with request/response socket timeouts from `settings.timeouts.redis_socket_s`. Cached per process and rebuilt the moment popoto's pool identity changes. | Outbox writers (`agent/output_handler.py`, `agent/session_completion.py`, `tools/send_message.py`, `tools/react_with_emoji.py`, `tools/valor_telegram.py`, `tools/valor_email.py`, `reflections/pm_briefings/delivery.py`), relays (`bridge/telegram_relay.py`, `bridge/email_relay.py`, `bridge/email_bridge.py`), `bridge/liveness.py`, `bridge/dedup.py`, `bridge/email_dead_letter.py`, `tools/email_history`, `ui/app.py`, `monitoring/bridge_watchdog.py` |
| `bytes_redis()` | `POPOTO_REDIS_DB` itself. | `bridge/routing.py` (the resolver cache decodes its own values) |
| `derived_redis(**overrides)` | A fresh, uncached client on popoto's identity with the caller's connection kwargs. | The pubsub probe and listener in `agent/agent_session_queue.py`, whose `socket_timeout` contracts differ from every request/response client and must not be shared |

## Connection policy this conversion changed

Routing through the accessor doesn't just fix *which* database a call lands
in; it changes the connection policy each call runs under. Neither the PR nor
the accessors' identity story above covers this, so it's spelled out here:

- **`text_redis()` now has a socket timeout.** Its client carries
  `socket_timeout` / `socket_connect_timeout` from
  `settings.timeouts.redis_socket_s`. The old per-call
  `redis.Redis.from_url(...)` sites carried none. A single Redis command that
  exceeds that timeout now raises `redis.exceptions.TimeoutError` where it
  previously blocked indefinitely. A caller on a send path must not issue an
  unbounded operation against this client, and must not swallow `RedisError`
  into a success-looking return value — a timeout there now means the
  operation didn't happen.
- **`text_redis()` bounds its own pool.** It builds a pool sized and
  health-checked from config, separate from popoto's, rather than taking
  redis-py's unbounded default.
- **`bytes_redis()` shares popoto's pool, not a copy.** It returns popoto's
  own client object, so its callers now share popoto's `BlockingConnectionPool`
  with every ORM operation in the process. On pool exhaustion a checkout
  *blocks* rather than erroring — `socket_timeout` does not cover a
  pool-checkout wait — so a `bytes_redis()` call must never run directly on an
  event loop; it belongs in `asyncio.to_thread`. Its caller must also never
  call `.close()` on it, since that would `disconnect()` the ORM's own pool.
  This is the opposite of `derived_redis()`, whose caller does own the
  connection's lifetime and is expected to close it.

Each module keeps a one-line `_get_redis()` (or `_get_redis_connection()`)
seam that delegates to the accessor. That seam is where the test suite injects
doubles, so the conversion changed no test patch target. The two output
handlers (`TelegramRelayOutputHandler`, `EmailOutputHandler`) honour an
explicitly assigned `self._redis` for the same reason and otherwise resolve
through `text_redis()`; their `redis_url` constructor parameter is gone because
no production caller ever passed one.

## What prevents recurrence

`tests/unit/test_redis_client_accessor.py::TestNoRawClientsInProduction` walks
every top-level production package by AST and fails on any construction of
`Redis`, `StrictRedis`, or `ConnectionPool` (direct call or `.from_url(...)`)
outside `utils/redis_client.py` — which is exempted by explicit path, as the
one sanctioned constructor. It resolves imports first, so it catches the
construction under any module alias (`import redis as _redis`), any bare or
aliased name pulled in via `from redis import Redis`, and any attribute chain
at any nesting depth (`redis.asyncio.Redis(...)`) — not just a direct
`redis.Redis(...)` attribute access.

The package list is enumerated from the repository tree, and
`test_the_scan_covers_every_production_package` asserts it stays that way: a
package that appears in the tree but not in the list fails the suite. That
second test is load-bearing rather than decorative. A hand-maintained package
list fails silently when a package is added, and the failure mode is a guard
reporting green over code it never opened — which is exactly how
`monitoring/bridge_watchdog.py` kept a hand-built client through a sweep that
called itself exhaustive. A new top-level package must now be scanned or
excluded deliberately.

The same file proves the contract end to end: with `REDIS_URL` pointed at db 0
for the duration of the call, a converted site's write still lands in the
claimed test database.

This guard and the `PreToolUse` hook `validate_no_raw_redis_delete.py` cover
different surfaces and both stay. The hook matches the text of a Bash command
an agent is about to run, stopping raw Redis typed against Popoto-managed keys
at the session boundary; it never reads the repository. This test reads
committed source and fails in CI. Neither can do the other's job.

## Related

- [Raw-Redis Guard](raw-redis-guard.md): the ORM-only rule for Popoto-managed keys.
- [Redis Flush Hardening](redis-flush-hardening.md): the production-flush guards the accessor keeps out of reach by never resolving db 0 on its own.
