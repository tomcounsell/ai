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

Modules whose tests inject a double keep a one-line `_get_redis()` (or
`_get_redis_connection()`) seam that delegates to the accessor — the relays,
`bridge/email_bridge.py`, `bridge/dedup.py`, `bridge/liveness.py`,
`bridge/routing.py`, `bridge/email_dead_letter.py`, `monitoring/bridge_watchdog.py`,
`ui/app.py` and the `tools/` senders. That seam is where the suite patches, so
the conversion changed no test patch target.

It is not universal, and nothing requires it to be. `agent/lock_policy.py`,
`agent/side_effects.py`, `agent/enqueue_idempotency.py`,
`agent/codex_dev_lease.py`, `agent/session_completion.py` and
`bridge/dead_letters.py` call `text_redis()` inline at the use site, with no
injection point. Those modules' tests exercise a real client against the
claimed test database instead of a double, so the seam would buy nothing. Add
one when a test needs to inject, not as a matter of form.

Note that `_get_redis` is a *name*, not a contract: `bridge/routing.py`'s
returns `bytes_redis()` while every other module's returns `text_redis()`.
Those two have materially different pool semantics (see above), so a guard
reasoning about these seams must resolve them per module rather than by name.

The two output
handlers (`TelegramRelayOutputHandler`, `EmailOutputHandler`) honour an
explicitly assigned `self._redis` for the same reason and otherwise resolve
through `text_redis()`; their `redis_url` constructor parameter is gone because
no production caller ever passed one.

## `scan_keys`: the bounded sweep that replaced `KEYS`

`utils.redis_client.scan_keys(client, match)` is the fourth export and the
only sanctioned way for production code to enumerate keys by pattern.

`KEYS` is O(keyspace) and single-shot. This Redis has carried millions of keys
(#2207), and every accessor now carries a socket timeout where the old
hand-built clients carried none — so a `KEYS` that crosses that timeout raises
`TimeoutError` on the calling path. On a send path with a blanket `except
Exception`, that is indistinguishable from an empty queue. `scan_keys` cursors
instead, `settings.redis.scan_count` keys per round trip, so no single command
can blow the timeout.

It returns `(keys, truncated)`. `truncated` is True only when the sweep stopped
at `settings.redis.scan_key_limit` **with the cursor still open**. The limit
bounds that truncating path, not the length of the list: the cursor is checked
first, so a sweep that closes the cursor on the same round trip that carries it
past the limit returns every key it saw — possibly more than `scan_key_limit`
of them — with `truncated` False, and `scan_count` is a hint rather than a
page-size guarantee, so the overshoot is not bounded to one key. Trimming that
list would turn a true report of a complete sweep into a silent partial result
claiming completeness, which is strictly worse than a few extra keys. Keys are
deduplicated in
first-seen order, because `SCAN` guarantees at-least-once and not exactly-once
delivery — a key present for the whole iteration can still come back twice if
the keyspace rehashes mid-sweep, which `keys(pattern)` never did.

What it does **not** bound is the traversal. The only exits are a completed
cursor cycle or `scan_key_limit` *matched* keys, so a sweep matching nothing
still walks the whole keyspace a page at a time — and on an idle outbox polled
every 100ms, that is the common case rather than the rare one. This is not a
regression (`KEYS` was also O(keyspace), and worse for a single-threaded
server), but it is the honest description. A traversal budget was considered
and rejected: it would make an empty result ambiguous between "no keys" and
"gave up", and the absence contract is precisely what a send path depends on.

Callers draining a queue can ignore `truncated` — the remainder arrives next
cycle. Callers reasoning about the **absence** of a key must not.

## The send-path failure contract

The other half of this change, and the half with a 26-hour outage behind it:
every Telegram reply was dropped while health stayed green, because a swallowed
exception on the send path was indistinguishable from an empty queue. A sweep
failure must never reach a relay loop as `sent = 0`.

`bridge/relay_errors.py` carries both pieces:

- **`OutboxUnavailableError`** — raised by a relay cycle that could not read
  its outbox. It is a distinct type precisely so the loop cannot confuse it
  with an idle cycle.
- **`report_send_path_failure(transport, exc)`** — reports the outage to
  Sentry and the log.

Both relays (`bridge/email_relay.py`, `bridge/telegram_relay.py`) catch
`redis.RedisError` **above** their blanket `except Exception`, report through
`report_send_path_failure`, and raise `OutboxUnavailableError`. Each loop
handles that type explicitly and escalates a consecutive-outage counter. The
ordering of those two handlers is load-bearing: the blanket handler was what
turned a dropped send path into a green health report.

The email heartbeat stamps **after** the sweep, not before, so a cycle that
could not read the outbox cannot report itself healthy.

## Tunables

`config/settings.py::RedisSettings`, env prefix `REDIS__`, all four documented
with commented override lines in `.env.example`. All are provisional — tune
against observation, do not treat them as derived values.

| Knob | Default | Governs |
|------|---------|---------|
| `REDIS__MAX_CONNECTIONS` | 128 | Pool ceiling for the `text_redis()` client. redis-py's default is effectively unbounded (2\*\*31), which lets a burst of coroutines and threadpool workers exceed the server's `maxclients`. Mirrors popoto's own cap. |
| `REDIS__HEALTH_CHECK_INTERVAL_S` | 30 | Seconds between liveness PINGs on an idle pooled connection; 0 disables. |
| `REDIS__SCAN_COUNT` | 500 | `SCAN` batch hint — bounds per-round-trip work against `TIMEOUTS__REDIS_SOCKET_S`. |
| `REDIS__SCAN_KEY_LIMIT` | 10000 | Where `scan_keys` gives up and reports truncation. Bounds the truncating path, not the returned list — a completed sweep can exceed it. |

Launchd-managed processes (bridge, worker, email) do not read `.env`, so an
override reaches them only through their plist — step 4 of
[Config Timeout Catalog](config-timeout-catalog.md).

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

Two further guards cover the policy hazards this conversion introduced, both
of which are invisible to the constructor scan because the offending code
calls the accessor correctly and only uses it in the wrong place:

- **`TestBlockingPoolNeverReachesAnEventLoop`** (same file) enforces the
  `bytes_redis()` rule above. It taints every sync function that reaches
  `bytes_redis()` — transitively, and across real import edges — then fails on
  any call to one from an `async def` that is not handed to
  `asyncio.to_thread`. The taint is module-qualified on purpose: six modules
  define `_get_redis` and only `bridge/routing.py`'s returns `bytes_redis()`,
  so a global name set reports five false positives. The transitive step is
  what earns its keep — the one real instance sat a frame above a correctly
  wrapped call, inside a coroutine that offloaded all four of its own Redis
  touches while calling a sync helper that reached the same pool.
- **`TestNoProductionKeysCall`** (`tests/unit/test_relay_send_path_outage.py`)
  fails on a `KEYS` or an unbounded `scan_iter` in either relay or the
  dead-letter module. `.keys` is matched as a bare **attribute**, not a call:
  both relays spelled the defect `asyncio.to_thread(r.keys, PATTERN)`, a
  reference handed to a threadpool that never parses as a call, and a
  call-only matcher reported both send paths clean. The attribute's base must
  resolve to a Redis-client-bound name, so an ordinary `payload.keys()` does
  not read as an outage.

This guard and the `PreToolUse` hook `validate_no_raw_redis_delete.py` cover
different surfaces and both stay. The hook matches the text of a Bash command
an agent is about to run, stopping raw Redis typed against Popoto-managed keys
at the session boundary; it never reads the repository. This test reads
committed source and fails in CI. Neither can do the other's job.

## Related

- [Raw-Redis Guard](raw-redis-guard.md): the ORM-only rule for Popoto-managed keys.
- [Redis Flush Hardening](redis-flush-hardening.md): the production-flush guards the accessor keeps out of reach by never resolving db 0 on its own.
