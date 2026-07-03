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
| **Fix #2** | Durable secondary store (SQLite export of `AgentSession` + restore path). Covers total Redis data-dir loss that AOF cannot — the AOF floor only bounds write loss within a running Redis process. | TBD |
| **Fix #4** | Move hot-path Redis calls off the asyncio event loop (async client or `run_in_executor`) to prevent loop blocking under load. | TBD |
| **Fix #5** | Redis replication + Sentinel on a second host for primary-loss failover. | [#1827](https://github.com/tomcounsell/ai/issues/1827) — **implemented** (see [Replication + Sentinel Failover](#replication--sentinel-failover-fix-5)) |

The current AOF floor (Fix #1) is the minimum durability guarantee: at most 1 second
of loss on a hard crash. Fix #2 (SQLite export) is the next step to cover total
data-dir loss. Fix #5 (replication) covers primary-host failure and is documented
below.

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
