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

A sibling **Step 3.14** (`scripts/update/redis_replication.py::apply_redis_replication()`)
seeds the **replication + Sentinel** topology (Fix #5) immediately after durability —
durability before availability. Unlike the durability step it is **bootstrap-only /
seed-once** (not idempotent re-apply) and a clean no-op on every client-only machine
and every established cluster. See [Replication + Sentinel Failover](#replication--sentinel-failover-fix-5).

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
| **Fix #4** | Move hot-path Redis calls off the asyncio event loop to prevent loop blocking under load. | [#1826](https://github.com/tomcounsell/ai/issues/1826), **implemented** (see [Off-Loop Redis Access](#off-loop-redis-access-fix-4)) |
| **Fix #5** | Redis replication + Sentinel on a second host for primary-loss failover. | [#1827](https://github.com/tomcounsell/ai/issues/1827), **implemented** (see [Replication + Sentinel Failover](#replication--sentinel-failover-fix-5)) |

The current AOF floor (Fix #1) is the minimum durability guarantee: at most 1 second
of loss on a hard crash. Fix #2 (SQLite export, below) covers total data-dir loss
that AOF cannot. Fix #4 (off-loop access) and Fix #5 (replication) are both
documented below.

## Durable Secondary Store (Fix #2)

AOF (Fix #1) bounds write loss to ~1 second *inside a running Redis process*. It does
**nothing** for total loss of the Redis data directory: `FLUSHALL`, disk failure, an
accidental `rm -rf` of the data dir, or a fresh machine that comes up with an empty
Redis. In every one of those cases every `AgentSession` record is gone with no second
copy to rehydrate from. Fix #2 ([#1825](https://github.com/tomcounsell/ai/issues/1825))
closes that gap with a transactional SQLite secondary store (`agent/session_archive.py`)
that every `AgentSession` is periodically exported to, plus a guarded cold-start
restore path.

**Data ownership is unchanged:** Redis remains the single source of truth. SQLite is a
strictly-secondary, write-only-in-normal-operation copy, read only on a cold start.

### Archive location

The archive lives at `data/session_archive.db` (WAL mode, stdlib `sqlite3`). It is
**machine-local** — each machine has its own Redis, so each machine has its own
archive — and is **never synced or committed**: `*.db`, `*.db-shm`, `*.db-wal`, and
`data/` are all already covered by `.gitignore`. There is nothing to propagate between
machines and no migration step for existing installations; the file is created
automatically on first export.

### Export cadence

Two independent write paths keep the archive current:

1. **Periodic sweep (`export_all()`).** A `worker-session-archive` daemon thread in
   `worker/__main__.py` wakes every `SESSION_ARCHIVE_INTERVAL` seconds (default **300**,
   env-overridable) and upserts every current `AgentSession` in one
   `BEGIN IMMEDIATE ... COMMIT` transaction — a crash-safe, consistent snapshot. Each
   row is serialized inside its own `try/except` first, so one pathological session can
   never abort the whole sweep; a row that fails to serialize is logged with its `id`
   and skipped.
2. **Terminal-transition hook (`export_session()`).** `models/session_lifecycle.py`'s
   `finalize_session` calls `export_session(session)` as its **last** side effect,
   unconditionally after the authoritative `session.save()` succeeds, inside
   `try/except` — an archive failure logs and is swallowed; it never breaks the
   terminal transition. This gives every session that reaches a terminal status an
   immediate, single-row upsert instead of waiting for the next periodic sweep.

Because these two writers run on **different threads** (the daemon thread and the
asyncio event-loop thread that runs `finalize_session`), the archive never shares one
SQLite connection across threads — every public entry point (`export_session`,
`export_all`, `restore_if_empty`, `get_archive_status`) opens its own connection and
closes it in a `finally`. WAL mode plus a busy-timeout then serialize the two
independent connections at the SQLite level. The terminal hook additionally opens its
connection with a **tight** `SESSION_ARCHIVE_ONLOOP_BUSY_TIMEOUT_MS` (default **250ms**,
an order of magnitude below the periodic sweep's `SESSION_ARCHIVE_BUSY_TIMEOUT_MS`
default of 5000ms) so a WAL-lock held by the concurrent sweep can never stall the event
loop for more than ~250ms — a lock timeout there is swallowed, and the next periodic
sweep re-covers the row.

Each session is serialized by enumerating `session._meta.fields` (never a
hand-maintained list), converting `datetime` values to ISO-8601 strings, and storing
the whole field map **verbatim** as a JSON `payload` column (no size cap — truncation
would silently corrupt round-trip fidelity). The real `id`, `session_id`,
`project_key`, `status`, and `updated_at` are also promoted to real columns for
queryability.

### Empty-Redis restore guard

At worker startup, `restore_if_empty()` runs — below the `if dry_run: return` guard and
before the Step 1 index rebuild — and rehydrates the archive back into Redis **only
when Redis is provably empty**. The guard is intentionally strict and layered:

**Both-must-be-empty rule.** On a cold start with no pending resume in progress,
restore proceeds **iff BOTH** are true: `AgentSession.query.all()` returns zero
records **AND** a bounded `SCAN` for keys matching `AgentSession*` returns zero keys
(this catches orphaned index-set members a `query.all()` might not surface). `DBSIZE`
is checked and logged as advisory confirmation only — it is never a gate, because AOF
or partial loss could wipe `AgentSession*` keys while leaving `Memory:*`/bloom keys
behind, and restore should still proceed in that case. If even a single `AgentSession`
record or index key exists, Redis is treated as partially-populated and restore is a
strict no-op — it never merges, and it never clobbers.

**`id`-key preservation.** Every archived row is keyed on the real `AgentSession.id`
(the `AutoKeyField` primary key), never on the `agent_session_id` property alias.
Restore reconstructs each row via `AgentSession(id=<archived id>, **other_fields).save()`
— the archived `id` is passed **explicitly** so the `AutoKeyField` is preserved rather
than regenerated. This matters because `AgentSession._normalize_kwargs` pops and
discards any `agent_session_id` kwarg; keying on the alias would mint a fresh UUID on
every restore and silently dangle every `parent_agent_session_id` parent/child link.

**`restore_in_progress` sentinel, live-count-gated bypass, and poison-row quarantine.**
A restore that fails partway through a large rehydrate loop must be resumable on the
next boot without ever risking a clobber of a Redis that has since been repopulated (by
normal operation, or by a previous partial restore). Three mechanisms work together:

1. Before writing the first row, the archive's `_meta` table sets
   `restore_in_progress = 1` and records `expected_row_count` (the number of
   archived rows expected to land). This sentinel is durable — it survives a Redis
   wipe because it lives in the SQLite file, not Redis.
2. The sentinel alone can never force a restore. On every boot, if the sentinel is
   set, the guard **bypasses the empty-Redis check only while the freshly recomputed
   `len(AgentSession.query.all()) < expected_row_count`** — i.e. Redis is still
   demonstrably short of the archived set — **and** `resume_attempts` is under
   `SESSION_ARCHIVE_RESUME_ATTEMPT_CAP` (default **5**). A raw count match is
   necessary but not sufficient: unrelated new sessions created since an interrupted
   restore could otherwise pad the live count up to `expected_row_count` while a
   genuinely un-rehydrated archived row is still missing — so before declaring
   completion the guard also cross-checks that every archived, non-quarantined `id`
   is actually present among the live Redis ids. Only when both the count is met
   *and* no archived row is missing is the sentinel recognized as stale, cleared, and
   restore no-op'd (`skipped_reason="restore_already_complete"`) — a stuck sentinel
   can never overwrite an already-whole Redis, and a still-missing poison row can
   never be masked by coincidental live traffic. If any archived row is missing
   despite the count match, the resume continues instead of declaring completion. If
   the resume-attempt cap is exceeded, restore is declared *wedged*, logged as an
   operator error, surfaced on the dashboard/doctor freshness block, and stops
   bypassing the guard.
3. Each archived row also carries a per-row failure counter. A row that fails to
   `.save()` more than `SESSION_ARCHIVE_ROW_ATTEMPT_CAP` times (default **3**) is
   written to the `_restore_quarantine` table and skipped on every future resume. The
   sentinel clears once `restored + quarantined == expected_row_count` — so a single
   permanently-unrestorable row is quarantined rather than wedging the whole restore
   forever, while every other row still lands.

The net effect: restore either completes cleanly (sentinel cleared, any poison rows
quarantined) or is cleanly declared wedged after a bounded number of attempts — and at
no point can a persisted flag override a live, populated Redis.

A restored session may carry a stale `claude_pid` (the pid of a worker process that no
longer exists after the data-dir loss). Such rows in `running` status flow through the
worker's existing dead-worker sweep and interrupted-session recovery exactly as any
pre-existing `running` session would after a normal restart — restore does not need to
scrub `claude_pid` itself.

### Dashboard, health, and doctor freshness surfaces

`get_archive_status()` is the single read-only status function (never raises — a
missing or corrupt DB returns a `healthy=False` shape) that all three operator surfaces
delegate to:

- **`dashboard.json`** exposes an `archive` block (`db_path`, `exists`, `row_count`,
  `last_export_ts`, `last_export_age_s`, `last_periodic_export_age_s`, `kind`, `healthy`),
  mirroring the existing email/heartbeat freshness pattern.
- **`/health`** surfaces the same freshness fields for external monitoring.
- **`session-archive-freshness`** (`tools/doctor.py`) fails actionably when the archive
  doesn't exist yet (fix: start the worker) or when the archive is stale relative to
  `SESSION_ARCHIVE_FRESHNESS_THRESHOLD_S` (default `2 × SESSION_ARCHIVE_INTERVAL`).

**Freshness keys off the periodic sweep, not the terminal hook.** The terminal-transition
hook writes `last_export_ts` on *every* session completion, so it stays fresh regardless of
whether the periodic sweep thread is alive. A dead `worker-session-archive` daemon thread
would therefore read healthy forever if freshness keyed off the shared timestamp. To close
that silent-green gap, `export_all()` advances a **separate `last_periodic_export_ts`**, and
`healthy` keys off *that* age — a stalled sweep surfaces as stale even while terminal exports
keep firing. Before the first sweep has run (cold-start transient), freshness falls back to
the terminal timestamp so a just-booted worker is not falsely reported stale.

### Operational runbook

**Export and live restore are fully automatic** — the periodic daemon thread and the
terminal-transition hook drive every export, and the guarded startup step drives every
live restore. There is no manual "run an export" or "run a restore" action; the runbook
below covers **inspection only**.

Inspect the archive's current freshness and row count:

```bash
valor-session-archive status
```

Check what a restore *would* do — the exact guard decision (would it restore, skip, or
resume?) and, if it would proceed, how many rows — without writing anything:

```bash
valor-session-archive restore --dry-run
```

`restore --dry-run` always calls the read-only guard evaluation; there is no CLI path
to trigger a live (writing) restore or a manual export — those write paths are
deliberately not exposed as CLI subcommands (see No-Gos in
`docs/plans/session-archive-sqlite.md`), since exposing them would only duplicate the
automatic paths and add a footgun.

If the archive is missing or stale, check that the worker is running
(`./scripts/valor-service.sh worker-status`) — the periodic export thread and terminal
hook only run inside the worker process.

## Off-Loop Redis Access (Fix #4)

The resilient client (Fix #3) bounds *recovery* from a slow or restarting Redis: a
transient outage reconnects instead of raising. It does not bound *loop occupancy*.
Popoto is a synchronous, redis-py-based ORM, so any Popoto call issued directly from
an `async def` blocks the whole asyncio event loop for the call's duration. Under a
slow Redis, that block is compounded by the Fix #3 retry policy (up to
`socket_timeout × retries` per call), and everything on the loop (every session,
every monitor, the [worker liveness dead-man's-switch tick](worker-liveness-recovery.md))
freezes in lockstep.

Fix #4 moves the one Redis call that runs on every idle iteration of the worker's
drain loop off the event loop, onto a dedicated thread pool, so a slow Redis degrades
that call's *latency* without freezing the loop for everyone else.

### The bulkhead: `_redis_io_pool`

`agent/redis_offload.py` defines `_redis_io_pool`, a dedicated
`ThreadPoolExecutor` isolated from the shared asyncio default executor (the pool
granite probes and `session_executor` also offload work onto). Isolating it means a
slow Redis call can never starve those unrelated offloads.

Worker count defaults to **2**, tunable via `REDIS_IO_POOL_MAX_WORKERS`, clamped to a
minimum of 1 so a misconfigured `0` can never produce a zero-worker pool that
deadlocks every offloaded call. A serialized drain-loop awaiter issues one offload at
a time, so a single drain loop needs about one concurrent offload; 2 workers cover the
realistic overlap of two drain loops idle-checking concurrently without
over-provisioning the pool.

**Invariant:** the combined worker count of this pool, the `_reflection_pool`
(`agent/reflection_scheduler.py`), and the shared default executor's peak usage must
stay at or below the redis-py `ConnectionPool` capacity built by
`configure_resilient_redis()`. That pool is unbounded today (no `max_connections`
set), so any small worker count here is safe by construction. If `max_connections`
is ever introduced on that pool, it must be sized to cover all three, or offloaded
calls will block on `BlockingConnectionPool` checkout, reintroducing the very stall
this fix removes.

### The offload seam: `offload_redis()`

`offload_redis(fn, *args, **kwargs)` is an `async def` in `agent/redis_offload.py`
that runs a synchronous Popoto/redis-py callable on `_redis_io_pool` via
`loop.run_in_executor(...)`, times the call, and records the latency sample. It is
used at exactly **one** call site: the worker drain loop's hot-path idle-check in
`agent/agent_session_queue.py`. No other Popoto call in the repo routes through it.

Thread-safety rests on redis-py's `ConnectionPool`, which is thread-safe by default.
Each offloaded call checks out its own connection, so there is no shared mutable
client state for concurrent calls to corrupt. This mirrors the pattern this repo
already relies on for the enqueue path's `asyncio.to_thread` offloads (below).

Any exception `fn` raises propagates unchanged to the caller; latency is still
recorded for a failing attempt so it doesn't silently disappear from the metric.

### Cut-over site: the drain-loop idle-check

The one instrumented cut-over is the per-worker drain loop's idle-check in
`agent/agent_session_queue.py`, the query that decides whether a worker has pending
work before it waits on its notify event. The ordering at that site is
**clear-then-check**: `event.clear()` runs *before* the offloaded
`AgentSession.query.filter(..., status="pending")` call, not after it.

This ordering exists because adding an `await` at the check opens a real yield point
that the old synchronous check-before-clear ordering did not have. Under the old
ordering, a producer could enqueue work and call `event.set()` while the idle-check's
offload was in flight, and the subsequent `event.clear()` would then swallow that
wakeup, parking the worker on `event.wait()` with pending work it never sees.
Clearing first removes the hole: because Redis is the source of truth, any enqueue
that fires after the clear is either observed by the query (the worker `continue`s
instead of waiting) or leaves the event set, so the following `event.wait()` returns
immediately. No wakeup is lost in either case.

### Operator metric: drain-loop idle-check latency

`ui/app.py::dashboard_json` exposes a `redis_offload` block under `health`, labeled
*drain-loop idle-check latency*:

```json
"redis_offload": {
  "label": "drain-loop idle-check latency",
  "p95_latency_s": 0.02,
  "max_latency_s": 0.14,
  "last_latency_s": 0.02
}
```

`p95_latency_s` and `max_latency_s` are computed over a rolling
`REDIS_LATENCY_WINDOW_S` window (default 300s), not a lifetime high-water mark. A
single slow blip ages out of the window instead of latching the dashboard red
forever. `offload_redis` also logs a WARNING whenever a single call exceeds
`REDIS_OFFLOAD_SLOW_THRESHOLD` (default 1.0s), giving an early signal that Redis is
degrading before it threatens the liveness tick.

This block measures the read hot path's bulkhead-isolated seam only, not all Redis
I/O in the process. See the grandfathered call sites below.

### What's left un-instrumented, and why

- **The six pre-existing enqueue-path `asyncio.to_thread` offloads**
  (`agent/agent_session_queue.py`): `transition_status`, `_init_stage_states`,
  `POPOTO_REDIS_DB.publish`, session reads, and the pubsub listen thread. These are
  grandfathered rather than migrated onto `offload_redis`. They already run off the
  loop via the shared default executor; folding them into the metric would measure
  write-path latency alongside the one read hot path this fix targets, diluting the
  signal without changing loop-freeze risk (they were already off-loop).
- **`_reflection_pool`** (`agent/reflection_scheduler.py`) is the template this
  bulkhead mirrors, but its own scans are out of scope for this metric. It measures
  the drain-loop's one hot-path seam, not every bulkheaded executor in the process.
- **The worker startup scans** (`worker/__main__.py`: the Redis-verify scan,
  cleanup/recovery helpers, and the pending-sessions kick scan) are deliberately left
  synchronous and on-loop. The [worker liveness dead-man's-switch beacon](worker-liveness-recovery.md)
  is not armed until after every startup scan completes, so offloading them would
  protect no liveness while risking a startup re-ordering hazard (some of these scans
  have a load-bearing execution order). They stay exactly as they were before this fix.

### Executor vs. async client

A real async Redis client (`redis.asyncio`) was rejected in favor of
`run_in_executor` on a bounded pool. Popoto's entire ORM (models, query builder,
index/class sets, `save`/`delete`) is synchronous and third-party; adapting it to an
async client would mean reimplementing that ORM against a different client, creating
a parallel async ORM alongside the sync one used everywhere else in the codebase. The
executor approach is the established in-repo pattern: it already mirrors
`_reflection_pool`'s bulkhead and the enqueue path's `to_thread` offloads, both of
which already rely on the same thread-safety guarantee.

### Rollback: `REDIS_OFFLOAD_ENABLED`

`REDIS_OFFLOAD_ENABLED` (default `true`) is a complete kill switch. When set to
`false`, `offload_redis` runs the wrapped callable inline on the event loop instead
of dispatching it to `_redis_io_pool`: a full, instant revert to the pre-cut-over
synchronous behavior at the one site this module serves, with no code change
required.

## Replication + Sentinel Failover (Fix #5)

AOF (Fix #1) bounds write loss *within a running Redis process on one host*. It
does nothing for the loss of the **host itself** (hardware, disk, power, network
partition). Fix #5 adds a **replica on a second host** plus **Redis Sentinel**
monitoring that promotes the replica automatically when the primary is lost — with
**zero application code change** (Option A, below).

This ships the **config templates** (`config/redis/`), the bootstrap-only `/update`
propagation step (`scripts/update/redis_replication.py`), and the
`redis-replication-health` doctor check. **Provisioning the hosts and cutting
`REDIS_URL` over to the stable address are operator steps** (see the runbook).

### Topology

```
                 ┌─────────────────────────────────────────────┐
   REDIS_URL ───▶│  Stable address (HAProxy TCP / keepalived VIP)│
                 │     always routes to the current master       │
                 └───────────────┬───────────────────────────────┘
                                 │ (repoints on promotion)
            ┌────────────────────┴────────────────────┐
            ▼                                          ▼
   ┌─────────────────┐   async replication   ┌─────────────────┐
   │  Redis PRIMARY  │ ────────────────────▶ │  Redis REPLICA  │
   │   (host A)      │                        │   (host B)      │
   │  AOF + noevict  │                        │  AOF + noevict  │
   └────────┬────────┘                        └────────┬────────┘
            │                                          │
   ┌────────┴───────┐   ┌────────────────┐   ┌─────────┴──────┐
   │  Sentinel 1    │   │  Sentinel 2    │   │  Sentinel 3    │
   │  (host A)      │   │  (WITNESS — C) │   │  (host B)      │
   └────────────────┘   └────────────────┘   └────────────────┘
        >= 3 Sentinels on 3 independent machines, quorum = 2
```

**Production target:** **3 Sentinels on 3 independent machines, quorum = 2.** The
third machine is a lightweight **witness** that runs only a Sentinel (no Redis
data). Never co-locate a Sentinel with the master it watches such that one host
loss removes both a Redis node and the quorum's tie-breaker.

**Two-host fallback (degraded):** a strict two-host layout works with quorum = 2,
but has **no split-brain protection** — a network partition between the two
co-equal hosts can promote the replica while the old primary still accepts writes,
diverging data. Ship it only with explicit operator acknowledgement; it is **not**
fully HA.

### Async replication model — RPO and RTO

Redis replication is **asynchronous**. The primary acknowledges a write to the
client *before* shipping it to the replica.

- **RPO (data loss window) > 0.** Writes acknowledged by the primary but not yet
  replicated are **permanently lost** on promotion. A replica's AOF **cannot**
  recover them — AOF only persists writes the replica actually *received*; it
  bounds loss from a *replica* crash, not the unreplicated-write window from the
  *primary's* failure. The RPO is the in-flight replication lag, not zero.
- **RTO (interruption window) ≈ `down-after-milliseconds` + `failover-timeout`.**
  During this window there is no writable master. The **#1814 resilient client**
  (`Retry(ExponentialBackoff(cap=10, base=1), retries=3)`, `health_check_interval=30`)
  already retries through the transient `ConnectionError`/`TimeoutError`, so the
  application reconnects to the promoted master once the stable address repoints —
  no new locking or app change required.

### Option A — stable-address front (the chosen architecture)

Sentinel orchestrates *promotion* but does **not** route client traffic. After a
failover the new master has a **different host/port**. This system has **17 Redis
connection mechanisms** (16 raw `redis.Redis.from_url(REDIS_URL)` sites + the single
Popoto bootstrap seam in `config/redis_bootstrap.py`) — **none** are Sentinel-aware.

**Option A** puts a **stable address** in front of the cluster — an **HAProxy TCP
frontend** (`config/redis/haproxy-redis.cfg.template`) that health-checks each
backend and routes only to the node reporting `role:master`, or a **keepalived
VIP** that floats to whichever host holds the master. `REDIS_URL` points at that
fixed address, so promotion is transparent to all 17 mechanisms — **zero Python
change**. HAProxy works cross-subnet; the keepalived VIP requires L2 adjacency but
removes the extra proxy hop. The operator picks per their network.

> Option B (Sentinel-aware "smart clients" via `redis.sentinel.Sentinel(...)` on
> every connection path) was **rejected**: it would couple all 17 mechanisms to the
> Sentinel protocol, a large blast radius that contradicts the infra/config-only
> scope. It is not built.

### Config templates

All templates live in [`config/redis/`](../../config/redis/) and use `<PLACEHOLDER>`
tokens — **no live host values are committed**. See that directory's `README.md` for
the token table and substitution examples.

| Template | Role |
|----------|------|
| `redis-replica.conf.template` | `replicaof`, `replica-read-only yes`, plus the #1814 durability posture (AOF + `noeviction`). |
| `sentinel.conf.template` | `sentinel monitor`, `down-after-milliseconds 5000`, `failover-timeout 60000`, `parallel-syncs 1`. |
| `haproxy-redis.cfg.template` | TCP frontend; `tcp-check` PING + `info replication` expecting `role:master`. keepalived-VIP alternative documented inline. |

### How `/update` propagates the config

`scripts/update/redis_replication.py::apply_redis_replication()` runs at **Step
3.14** of `scripts/update/run.py` (durability at 3.13 → availability at 3.14). It is
**BOOTSTRAP-ONLY / seed-once**, NOT an idempotent re-apply, because replication
topology is **runtime-mutable and Sentinel-owned**: re-applying a static template on
an established cluster would demote a promoted master.

1. **Role gate.** Acts only on a host opted in via the marker file
   `data/redis-replication-enabled` (mirrors `data/auto-revert-enabled`). Absent on
   every client-only machine → clean `skipped`.
2. **Presence check / early-exit.** Skips and touches nothing if a Sentinel already
   monitors the master, if the node already reports `role:slave`, or if it reports
   `role:master` with connected replicas.
3. **Hard invariant.** **NEVER** `CONFIG SET replicaof` on a `role:master` node — in
   fact the step never issues `CONFIG SET replicaof` at all. Seeding a virgin
   opted-in node is **file-only**: it stages a `redis-replica.conf` stub into Redis's
   config dir and returns `applied_with_warning` so the operator substitutes the
   placeholders and restarts. The invariant therefore holds **by construction**.
4. **Non-fatal.** Absent `redis-cli`, an unreachable Redis, or a write failure all
   return a result and log a warning; the step never raises or blocks `/update`.

The `REDIS_URL` *value* (pointing at the VIP/HAProxy instead of `localhost`) flows
through the existing `.env` vault sync (`scripts/update/env_sync.py`); no new
propagation machinery is needed.

### Doctor check

`python -m tools.doctor` includes a `redis-replication-health` check that is
**role-gated** on the same `data/redis-replication-enabled` marker:

- **Client-only machine (no marker, the default):** neutral SKIP (`passed=True`,
  "client-only machine (skipped)"). A standalone single-node localhost Redis is the
  expected posture and is **never** flagged as a failure or false-green.
- **Opted-in node:** asserts `role` via `redis-cli INFO replication`
  (`master_link_status:up` for a replica, `connected_slaves` for a master) and probes
  Sentinel reachability. Degrades gracefully to a neutral SKIP when `redis-cli` is
  absent or Redis is unreachable. Never raises.

## Operational Runbook: Failover

> **[EXTERNAL] operator steps.** Host provisioning, placeholder substitution, and the
> live `REDIS_URL`/VIP cutover require real machines and are operator actions — not
> performed by `/update` or the agent.

### 1. Opt the host in and bring up a replica

```bash
# On the replica host (host B): mark it a Redis node so /update seeds config.
touch data/redis-replication-enabled

# Substitute placeholders and start the replica.
sed -e 's/<PRIMARY_HOST>/<host-A-ip>/' -e 's/<PRIMARY_PORT>/6379/' \
    config/redis/redis-replica.conf.template > /etc/redis/redis-replica.conf
redis-server /etc/redis/redis-replica.conf
```

### 2. Bring up the Sentinels

On **each** of the 3 machines (primary, replica, witness):

```bash
sed -e 's/<MASTER_NAME>/valor-redis/' \
    -e 's/<PRIMARY_HOST>/<host-A-ip>/' -e 's/<PRIMARY_PORT>/6379/' \
    -e 's/<QUORUM>/2/' \
    config/redis/sentinel.conf.template > /etc/redis/sentinel.conf
redis-sentinel /etc/redis/sentinel.conf
```

> **Two-host fallback (split-brain caveat):** if only two hosts exist, run quorum = 2
> across the two Sentinels. This works but gives **no split-brain protection** — a
> partition can diverge data. Production target remains **≥ 3 Sentinels on 3
> machines**. Acknowledge the limitation before relying on it.

### 3. Verify replication

```bash
redis-cli -h <host-A-ip> INFO replication      # primary: role:master, connected_slaves:1
redis-cli -h <host-B-ip> INFO replication      # replica: role:slave, master_link_status:up
```

### 4. Verify the quorum

```bash
redis-cli -p 26379 SENTINEL ckquorum valor-redis    # want: OK ... can reach quorum
redis-cli -p 26379 SENTINEL master valor-redis      # inspect the monitored master
```

### 5. Manual failover (drill or maintenance)

```bash
redis-cli -p 26379 SENTINEL failover valor-redis
# Watch promotion: the former replica becomes role:master.
redis-cli -h <host-B-ip> INFO replication           # now role:master
```

### 6. Post-failover verification — `REDIS_URL` reaches the promoted master

```bash
# Through the stable address that REDIS_URL points at (VIP/HAProxy bind):
redis-cli -u "$REDIS_URL" INFO replication | grep role    # want: role:master
python -c "import redis,os; print(redis.Redis.from_url(os.environ['REDIS_URL']).execute_command('ROLE')[0])"
```

A fresh `redis.Redis.from_url(REDIS_URL)` must connect to the **promoted** master. If
it still hits the dead primary, the stable-address layer is misconfigured (Risk 2):
re-check the HAProxy health check / VIP move script.
