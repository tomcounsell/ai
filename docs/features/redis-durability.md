# Redis Durability Model

Redis is the durable operational store for this system. All agent sessions,
memories, Telegram/email message history, and the bloom filter live in Popoto,
and Popoto is backed by Redis. This document describes the durability
configuration, the resilient client, and the roadmap for higher durability tiers.

## Current Durability Model

### AOF Floor (Fix #1)

**What:** Append-Only File persistence (`appendonly yes`, `appendfsync everysec`) is
pinned in `redis.conf` and propagated to every machine by `/update`
(`scripts/update/redis_persistence.py`).

**Guarantee:** At most 1 second of write loss on a hard crash or power failure.
The previous state was RDB-only (configured `save 3600 1` — up to 1 hour of loss
per restart). AOF and RDB are kept together as two complementary durability layers.

**Verification:** `redis-cli INFO persistence` must show `aof_enabled:1`. The doctor
check (`python -m tools.doctor`) and `/update` both assert this post-condition.

### Eviction Policy (Fix #6)

**What:** `maxmemory-policy noeviction` is pinned in `redis.conf` and propagated
by the same `/update` step.

**Guarantee:** Redis will never silently drop durable keys (sessions, index sets,
bloom-filter keys) under memory pressure. With `noeviction`, Redis returns an error
on writes when memory is full rather than silently evicting data. This is the safer
behavior for a system where eviction is indistinguishable from a legitimate delete.

**Durable-key audit:** The following key categories carry no TTL and must never be
evicted:

| Key pattern | Purpose | TTL |
|-------------|---------|-----|
| `AgentSession:*` | All session fields (status, logs, steering queue) | None |
| `AgentSession.index.*` | Popoto index sets for query | None |
| `Memory:*` | Subconscious memory records | None |
| `bloom:*` | Bloom filter for memory existence check | None |
| `email:*` / `telegram:*` | Message history cache | None |

All of these are safe under `noeviction`.

### Resilient Client (Fix #3)

**What:** `config/redis_bootstrap.py::configure_resilient_redis()` rebuilds the
global Popoto client with retry/backoff/health-check settings on worker and bridge
startup.

**Why:** Popoto's import-time client (`popoto/redis_db.py`) uses bare socket
timeouts and no retry — a Redis restart or brief outage at process boot previously
crashed the worker or bridge with an unhandled import-time exception. The only
supported app-boundary seam for reconfiguring the Popoto client is
`set_REDIS_DB_settings(**kwargs)` (documented in `popoto/redis_db.py:133`).

**Configuration applied:**

```python
from redis.retry import Retry
from redis.backoff import ExponentialBackoff

Retry(ExponentialBackoff(cap=10, base=1), retries=3)
# applied with retry_on_error=[ConnectionError, TimeoutError, ConnectionResetError]
# health_check_interval=30  (background ping every 30s)
# socket_timeout=5, socket_connect_timeout=5
```

**Degrade-don't-die guarantee:** If Redis is unreachable when
`configure_resilient_redis()` is called at startup, the function logs a WARNING
(`"Starting in degraded mode"`) and returns without raising. The process starts.
Individual Popoto operations will fail (and retry via the retry policy) until
Redis recovers.

**Test isolation:** Under pytest (`PYTEST_CURRENT_TEST` env var set), the function
is a no-op so the `redis_test_db` fixture in `tests/conftest.py` retains full
control of `POPOTO_REDIS_DB`.

## Update System Integration

The `/update` skill propagates all three directives to every machine:

```
scripts/update/redis_persistence.py::apply_redis_persistence()
```

Called at Step 3.13 in `scripts/update/run.py`. The step:
1. Checks that `redis-cli` is on PATH (skips non-fatally if not).
2. Pings Redis (skips non-fatally if down).
3. Applies `CONFIG SET appendonly yes`, `CONFIG SET appendfsync everysec`,
   `CONFIG SET maxmemory-policy noeviction` at runtime.
4. Runs `CONFIG REWRITE` to persist directives into `redis.conf` so they survive
   a `redis-server` restart.
5. If `CONFIG REWRITE` fails (Redis started without `--config`), writes a stub
   `redis.conf` to the Redis data directory and emits a loud WARNING so the
   operator knows to restart Redis with that file.
6. Post-condition asserts `aof_enabled:1` and `maxmemory-policy == noeviction`.

The step is non-fatal: failure is logged as a warning but does not block the rest
of `/update`. The doctor check catches drift independently.

## Doctor Check

`python -m tools.doctor` includes a `redis-durability` check that asserts:
- `aof_enabled:1` via `redis-cli INFO persistence`
- `maxmemory-policy == noeviction` via `redis-cli CONFIG GET maxmemory-policy`

A failing check prints an actionable fix message pointing at `/update`.

## Operational Runbook

### Check durability on the current machine

```bash
redis-cli INFO persistence | grep aof_enabled    # want: aof_enabled:1
redis-cli CONFIG GET maxmemory-policy             # want: noeviction
python -m tools.doctor                           # Redis durability check
```

### Re-apply on a machine after a fresh Redis install

```bash
# Option A: run /update (preferred — applies all config in one step)
/update

# Option B: manual apply
redis-cli CONFIG SET appendonly yes
redis-cli CONFIG SET appendfsync everysec
redis-cli CONFIG SET maxmemory-policy noeviction
redis-cli CONFIG REWRITE
```

### Redis was started without a config file (macOS Homebrew default)

If `CONFIG REWRITE` fails with "ERR The server is running without a config file":

```bash
# The /update step writes a stub redis.conf to the Redis data dir.
# Find the data dir:
redis-cli CONFIG GET dir
# Start Redis with the stub:
redis-server <data-dir>/redis.conf
```

## Deferred Durability Work

The following improvements are explicitly deferred to separate issues:

| Fix | Description | Issue |
|-----|-------------|-------|
| **Fix #2** | Durable secondary store (SQLite export of `AgentSession` + restore path). Covers total Redis data-dir loss that AOF cannot — the AOF floor only bounds write loss within a running Redis process. | TBD |
| **Fix #4** | Move hot-path Redis calls off the asyncio event loop (async client or `run_in_executor`) to prevent loop blocking under load. | TBD |
| **Fix #5** | Redis replication + Sentinel on a second host for primary-loss failover. | TBD |

The current AOF floor (Fix #1) is the minimum durability guarantee: at most 1 second
of loss on a hard crash. Fix #2 (SQLite export) is the next step to cover total
data-dir loss. Fix #5 (replication) covers primary-host failure.
