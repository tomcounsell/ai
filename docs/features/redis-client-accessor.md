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
connection pool is the single fact about which Redis this process talks to:
`config/redis_bootstrap.py` rebuilds it with retry policy at startup, and
`tests/conftest.py` repoints it at a per-process claimed test database. A client
derived from that pool's identity follows both. A client built by hand from
`REDIS_URL` did neither. It resolved its own database at call time and, under
test, twenty-three such sites wrote to production db 0 (measured during #2805:
`tests/unit/test_dedup.py` alone left `bridge:msgclaim:*` keys in db 0).

## The three accessors

| Accessor | Returns | Used by |
|----------|---------|---------|
| `text_redis()` | A `decode_responses=True` client on popoto's host, port, db, and credentials, with request/response socket timeouts from `settings.timeouts.redis_socket_s`. Cached per process and rebuilt the moment popoto's pool identity changes. | Outbox writers (`agent/output_handler.py`, `agent/session_completion.py`, `tools/send_message.py`, `tools/react_with_emoji.py`, `tools/valor_telegram.py`, `tools/valor_email.py`, `reflections/pm_briefings/delivery.py`), relays (`bridge/telegram_relay.py`, `bridge/email_relay.py`, `bridge/email_bridge.py`), `bridge/liveness.py`, `bridge/dedup.py`, `bridge/email_dead_letter.py`, `tools/email_history`, `ui/app.py` |
| `bytes_redis()` | `POPOTO_REDIS_DB` itself. | `bridge/routing.py` (the resolver cache decodes its own values) |
| `derived_redis(**overrides)` | A fresh, uncached client on popoto's identity with the caller's connection kwargs. | The pubsub probe and listener in `agent/agent_session_queue.py`, whose `socket_timeout` contracts differ from every request/response client and must not be shared |

Each module keeps a one-line `_get_redis()` (or `_get_redis_connection()`)
seam that delegates to the accessor. That seam is where the test suite injects
doubles, so the conversion changed no test patch target. The two output
handlers (`TelegramRelayOutputHandler`, `EmailOutputHandler`) honour an
explicitly assigned `self._redis` for the same reason and otherwise resolve
through `text_redis()`; their `redis_url` constructor parameter is gone because
no production caller ever passed one.

## What prevents recurrence

`tests/unit/test_redis_client_accessor.py::TestNoRawClientsInProduction` walks
`agent/`, `bridge/`, `tools/`, `reflections/`, `ui/`, `worker/`, `models/`, and
`config/` by AST and fails on any `redis.Redis(...)`, `redis.StrictRedis(...)`,
`redis.from_url(...)`, or `redis.Redis.from_url(...)` call, under any import
alias, outside `utils/redis_client.py`. The same file proves the contract
end to end: with `REDIS_URL` pointed at db 0 for the duration of the call, a
converted site's write still lands in the claimed test database.

## Related

- [Raw-Redis Guard](raw-redis-guard.md): the ORM-only rule for Popoto-managed keys.
- [Redis Flush Hardening](redis-flush-hardening.md): the production-flush guards the accessor keeps out of reach by never resolving db 0 on its own.
