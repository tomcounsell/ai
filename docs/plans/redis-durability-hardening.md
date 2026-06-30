---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-06-30
tracking: https://github.com/tomcounsell/ai/issues/1814
last_comment_id:
---

# Redis Durability Hardening

## Problem

Every durable thing in the system — `AgentSession` records, memory, Telegram/email
history, the bloom filter — lives in Popoto, and Popoto is backed by a single
Redis instance with no replication. Three concrete, low-effort gaps make that
Redis a single point of failure with no graceful degradation:

1. **AOF persistence is not durably configured.** AOF happens to be on on the
   current host (`aof_enabled:1`), but it is a runtime `CONFIG SET`, not pinned in
   `redis.conf`, and it is not guaranteed on every machine. A Redis restart or a
   fresh machine reverts to RDB-only — worst case ~1 hour of session-state loss
   per the configured `save 3600 1`. (There is documented precedent: on 2026-06-03
   a `flushdb()` against db=0 wiped production *because AOF was off and the RDB was
   overwritten* — see `tests/unit/test_redis_flush_guard.py`.)

2. **The Popoto Redis client is retry-less and dies at import.**
   `popoto/redis_db.py:112-130` builds the global client at module import with
   `socket_timeout=5`/`socket_connect_timeout=5` but **no** `retry_on_timeout`, **no**
   `health_check_interval`, **no** backoff, and `raise`s on connection failure. If
   Redis is down or restarting at boot, `import popoto` fails and the worker/bridge
   **cannot start at all** — there is no degraded-start path.

3. **No pinned eviction policy.** `maxmemory-policy` is `noeviction` today, but it
   is not pinned in config. If anyone later sets `allkeys-lru` to stop OOM, Redis
   will silently evict index sets, class sets, and **bloom-filter keys**
   (`agent/memory_hook.py:180-188`) — none of which carry TTLs marking them
   transient — causing session loss the cleanup scripts cannot distinguish from
   legitimate deletes.

A false belief compounds all three: `config/personas/segments/work-patterns.md:177`
contains the literal claim *"Redis is used for operational state only, not durable
records. Popoto models handle persistence."* (As an example `memory_search save`
command — see Freshness Check.) This is factually wrong (Popoto *is* Redis) and is
likely why AOF was never pinned.

**Current behavior:** A hard crash / power loss / OOM-kill of `redis-server` loses
every write since the last RDB snapshot. A Redis outage at process boot crashes the
worker and bridge with an unhandled import-time exception. Eviction policy is
unpinned and unguarded.

**Desired outcome:** Bounded-loss durability (AOF `everysec`, pinned in `redis.conf`
and propagated to every machine via `/update`), a Popoto client that survives a
Redis restart and degrades-don't-dies at import, and a pinned `noeviction` policy
that provably cannot drop durable keys — with the persistence model honestly
documented.

## Scope (agreed with supervisor)

**IN SCOPE for this PR (Medium appetite):**
- Fix #1 — Enable/pin Redis AOF (`appendfsync everysec`), keep RDB. `CONFIG SET appendonly yes`
  + persist to `redis.conf` + propagate via `/update`.
- Fix #3 — Add retry/backoff/health-check to the Popoto client + degrade-don't-die at import.
- Fix #6 — Pin `maxmemory-policy noeviction` + audit/document that durable keys carry no
  silent-eviction risk.
- Doc correction — `config/personas/segments/work-patterns.md:177`.
- The required `docs/features/` durability-model page (AOF + secondary-store roadmap + failover).

**DEFERRED (named here; the supervisor splits each into its own new issue, see No-Gos):**
- Fix #2 — Durable secondary store (SQLite export of `AgentSession`), ~1-2d.
- Fix #4 — Move hot-path Redis off the event loop (async / `run_in_executor`), ~2-3d.
- Fix #5 — Redis replication + Sentinel, infra + ops.

## Freshness Check

**Baseline commit:** `aa6b996867e59b5d2c9d95986dcfe17a88dbf192` (main, plan time)
**Issue filed at:** 2026-06-29T09:20:43Z
**Disposition:** Unchanged

**File:line references re-verified (all still hold):**
- `popoto/redis_db.py:112-130` — client built at import with `socket_timeout=5`,
  `socket_connect_timeout=5`, **no** `retry_on_timeout`/`health_check_interval`/backoff,
  and bare `raise` on failure. Confirmed verbatim at
  `.venv/lib/python3.14/site-packages/popoto/redis_db.py` (pip-installed, NOT vendored).
- `agent/agent_session_queue.py:1367-1376` — `AgentSession.query.filter(...)` runs
  synchronously inside the drain loop. Confirmed (this is the hot path; Fix #4,
  moving it off the loop, is DEFERRED — see No-Gos).
- `agent/memory_hook.py:180-188` — bloom-filter access via `Memory._meta.fields["bloom"]`,
  no TTL. Confirmed.
- `config/personas/segments/work-patterns.md:177` — contains the literal false-claim
  string. Confirmed. **Drift note (already surfaced in the issue):** it is an EXAMPLE
  `memory_search save` command inside a "When to Save" teaching section, not a
  standalone architectural assertion. Still corrected, but it is doc copy, not a
  config-driving claim.
- `config/settings.py` — `REDIS_URL` is env-driven (`redis_url` field, ~line 142).
  Confirmed; relevant to Fix #3's app-boundary wrapping.

**Live-host re-verification (plan time):** `aof_enabled:1` / `appendonly yes` is already
true on THIS host (issue's recon noted the same drift). Fix #1 is therefore "pin AOF in
`redis.conf` + propagate via `/update` so it survives restart and is guaranteed on every
machine," not "turn AOF on." `maxmemory-policy` is `noeviction` (unchanged).

**Cited sibling issues/PRs re-checked:** none cited in the issue body.

**Commits on main since issue was filed (touching referenced files):** none.
`git log --since=2026-06-29T09:20:43Z` over `agent/agent_session_queue.py`,
`agent/memory_hook.py`, `config/settings.py`, `work-patterns.md`, and `scripts/update/`
returned zero commits. Premises are intact.

**#1815 overlap (PR #1823, merged into the baseline):** #1815 touched
`worker/__main__.py`, `agent/session_state.py`, `agent/granite_container/pty_pool.py`.
This plan centers on `popoto/redis_db.py` (third-party), a new app-boundary redis
bootstrap module, `redis.conf`, `scripts/update/`, `config/personas/segments/work-patterns.md`,
and a new `docs/features/` page. **No file overlap with #1815.** BUILD should branch
from current main (which already includes #1823) and rebase cleanly — no conflicts expected.

**Active plans in `docs/plans/` overlapping this area:** none with live overlap.

**Notes:** The single biggest planning constraint (issue-surfaced and re-confirmed):
`popoto` is a pip-installed third-party package, so Fix #3 cannot be a plain in-repo
edit. Popoto exposes `set_REDIS_DB_settings(*args, **kwargs)` which rebuilds the
global `POPOTO_REDIS_DB` — that is the clean app-boundary seam (see Technical Approach).

## Prior Art

- **No prior issues or PRs** found via `gh issue list --state closed --search "redis durability AOF persistence"`
  or the equivalent merged-PR search. This is the first durability-hardening pass on Redis.
- **`tests/unit/test_redis_flush_guard.py`** + `tests/conftest.py::_install_redis_db0_flush_guard`
  are direct prior art: a 2026-06-03 production wipe (flushdb on db=0, AOF off, RDB overwritten)
  drove a conftest-level flush guard. That incident is the strongest existing justification for
  Fix #1 — AOF being durably on would have made that wipe recoverable.
- **`tests/conftest.py::redis_test_db`** already monkeypatches `POPOTO_REDIS_DB` across every
  popoto submodule that holds the symbol (lazy module cache, ~lines 149-214). This is the proven
  pattern for swapping the global client and is the template for Fix #3's reconfiguration call.

## Research

No external research needed — the substrate is well-known redis-py and Redis server
config. Confirmed locally that the installed `redis==7.4.0` exposes `redis.retry.Retry`
and `redis.backoff.ExponentialBackoff`, so Fix #3 can use first-class retry config
rather than hand-rolled loops. AOF + RDB hybrid is the standard Redis production config;
`appendfsync everysec` is the durability/throughput balance. No new dependencies.

## Data Flow

This change touches the persistence substrate, not a request path. The relevant flows:

1. **Process boot → `import popoto` → global `POPOTO_REDIS_DB` constructed.** Today an
   unreachable Redis raises here and crashes the process. Fix #3 inserts an app-boundary
   bootstrap (`config/redis_bootstrap.py`, imported early in worker/bridge startup) that
   calls `set_REDIS_DB_settings(...)` with resilient kwargs and tolerates a down Redis,
   logging a degraded-start warning instead of crashing.
2. **Any `AgentSession.query.*` / `Memory.*` call → `POPOTO_REDIS_DB` → redis-server.**
   With `retry_on_timeout` + `health_check_interval`, a transient Redis restart reconnects
   transparently instead of bubbling a hard error.
3. **redis-server write → AOF (`everysec`) + RDB.** Fix #1 guarantees AOF is the durable
   floor on every machine.

## Architectural Impact

- **New dependencies:** none (uses installed `redis==7.4.0` retry primitives).
- **Interface changes:** new `config/redis_bootstrap.py` module with a single
  idempotent `configure_resilient_redis()` entry point. No signature changes to
  existing code; worker/bridge call it once at startup.
- **Coupling:** slightly *reduces* coupling to popoto's import-time client by routing
  client construction through the documented `set_REDIS_DB_settings` seam.
- **Data ownership:** unchanged. Redis remains the single store this PR (the durable
  secondary store, Fix #2, is explicitly deferred).
- **Reversibility:** high. AOF/eviction are config flags (revert via `redis.conf` +
  `CONFIG SET`); the bootstrap module can be made a no-op.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1-2 (scope alignment — confirm the three-fix scope and the deferral split)
- Review rounds: 1 (the `/update` propagation correctness is the thing to review carefully)

This is config + a thin client-bootstrap module + docs. Coding time is small; the
care goes into idempotent cross-machine propagation and not regressing the test
Redis isolation.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `redis-cli` available | `command -v redis-cli` | Apply/verify `CONFIG SET` |
| Redis ≥ 4.0 (AOF+RDB hybrid) | `redis-cli INFO server \| grep redis_version` | Hybrid persistence support |
| Writable Redis config dir | `redis-cli CONFIG GET dir` | Persist AOF/eviction settings via CONFIG REWRITE |
| redis-py retry primitives | `python -c "from redis.retry import Retry; from redis.backoff import ExponentialBackoff"` | Fix #3 resilient client |

## Solution

### Key Elements

- **Pinned AOF (Fix #1):** `appendonly yes` + `appendfsync everysec` applied via
  `CONFIG SET` at runtime AND persisted into `redis.conf` (via `CONFIG REWRITE`), with
  RDB kept as a second layer. Propagated to every machine by a new `/update` step.
- **Resilient Popoto client (Fix #3):** a new `config/redis_bootstrap.py` that calls
  popoto's `set_REDIS_DB_settings(...)` with `retry_on_timeout=True`, an
  `ExponentialBackoff` retry, `health_check_interval=30`, and the existing socket
  timeouts — and a degrade-don't-die guard so a down-at-boot Redis logs a warning
  instead of crashing the worker/bridge. Called once, early, in worker and bridge startup.
- **Pinned eviction policy (Fix #6):** `maxmemory-policy noeviction` pinned in
  `redis.conf` (via `CONFIG REWRITE`) and asserted by the same `/update` step + a doctor
  check, with a documented audit confirming durable keys (sessions, index sets, bloom)
  carry no silent-eviction risk under the pinned policy.
- **Doc correction:** rewrite the false `work-patterns.md:177` example so it no longer
  encodes "Redis is operational-only, not durable."
- **Durability-model feature doc:** a new `docs/features/redis-durability.md` covering
  the AOF floor, the resilient client, the eviction policy, and the secondary-store +
  failover roadmap (the deferred fixes).

### Flow

Process boots → `configure_resilient_redis()` runs early → popoto global client rebuilt
with retry/backoff/health-check → if Redis is down, log degraded-start warning and
continue → all subsequent Popoto ops survive transient Redis restarts → writes land in
AOF (`everysec`) on every machine → eviction can never silently drop durable keys.

### Technical Approach

- **Fix #1 / #6 — Redis server config via a new `scripts/update/redis_persistence.py`
  module** (mirrors the dataclass-result shape of `scripts/update/kokoro.py`), wired
  into `scripts/update/run.py` as a new step. The module, idempotently on every machine:
  1. `redis-cli CONFIG SET appendonly yes`, `CONFIG SET appendfsync everysec`,
     `CONFIG SET maxmemory-policy noeviction` (runtime effect, no restart).
  2. `redis-cli CONFIG REWRITE` so the three directives are persisted into the machine's
     active `redis.conf` and survive a `redis-server` restart. (CONFIG REWRITE writes back
     to the conf file Redis was started with — no manual path crawling needed.)
  3. Post-condition asserts `aof_enabled:1` + the expected `maxmemory-policy`; returns a
     structured result. `run.py` logs applied/skipped/failed. **Skips gracefully** if
     `redis-cli` is unavailable or Redis is not running (non-fatal — must never block `/update`).
- **Fix #3 — `config/redis_bootstrap.py`** with `configure_resilient_redis()`:
  - Builds retry config: `Retry(ExponentialBackoff(cap=10, base=1), retries=3)` with
    `retry_on_error=[ConnectionError, TimeoutError, ConnectionResetError]`.
  - Calls `popoto.redis_db.set_REDIS_DB_settings(host=..., port=..., db=..., retry_on_timeout=True, retry=..., health_check_interval=30, socket_timeout=5, socket_connect_timeout=5)`
    — deriving host/port/db from `REDIS_URL`/settings exactly as popoto's import-time
    code does, so the *only* delta is the resilience kwargs.
  - Wrapped in try/except: on a down-at-boot Redis, log a `logger.warning` degraded-start
    line and return without raising (degraded start, not a crash — see Risk 2).
  - Re-applies the popoto submodule sync the same way `conftest.redis_test_db` does, so
    every cached `POPOTO_REDIS_DB` symbol points at the resilient client.
  - Called once at the top of worker startup (`worker/__main__.py`) and bridge
    startup (`bridge/telegram_bridge.py`), guarded to run at most once and to no-op under pytest.
- **Doctor check:** extend `tools/doctor.py::_check_redis` (or add `_check_redis_durability`)
  to assert `aof_enabled:1` and `maxmemory-policy == noeviction`, with an actionable fix
  string pointing at the `/update` step.
- **Doc correction:** edit `config/personas/segments/work-patterns.md:177` example to a
  truthful learning (e.g. a non-Redis example) so it no longer encodes the false claim.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `config/redis_bootstrap.py` will contain exactly one broad guard (the degrade-don't-die
      path). Test asserts it logs a `logger.warning` (observable) and returns without raising
      when Redis is unreachable — no silent `except: pass`.
- [ ] `scripts/update/redis_persistence.py`'s skip path (no `redis-cli` / Redis down) must
      log and return a `failed`/`skipped` action, asserted by test — not swallowed silently.

### Empty/Invalid Input Handling
- [ ] `configure_resilient_redis()` with an empty/missing `REDIS_URL` must fall back to the
      same `127.0.0.1:6379` default popoto uses; test the empty-string and unset cases.
- [ ] CONFIG REWRITE failure (e.g. Redis started without a conf file) must be caught and
      reported as a non-fatal `failed` action — tested.

### Error State Rendering
- [ ] Degraded-start warning must reach the process log (worker/bridge) so an operator can
      see "started with Redis unavailable" — test asserts the log line, not just no-crash.
- [ ] Doctor durability check must render a red/actionable result (not a crash) when
      `aof_enabled:0` — test the failure rendering path.

## Test Impact

- [ ] `tests/conftest.py::redis_test_db` / `_install_redis_db0_flush_guard` — UPDATE (verify-only):
      the new `configure_resilient_redis()` must NOT fight the test fixture's `POPOTO_REDIS_DB`
      swap or the db0 flush guard. Add a guard so the bootstrap is a no-op (or db-respecting)
      under pytest (`PYTEST_CURRENT_TEST` / test-db detection). Confirm the existing fixture
      still wins; adjust if it does not.
- [ ] `tests/unit/test_redis_flush_guard.py` — UPDATE (verify-only): re-run to confirm AOF/eviction
      config changes and the bootstrap module don't alter db-selection behavior the guard depends on.
- [ ] `tests/unit/test_doctor.py` — UPDATE: add/extend a case for the new redis-durability doctor
      check (asserts `aof_enabled` + `maxmemory-policy`).
- [ ] `tests/unit/test_agent_session_queue_async.py` — UPDATE (verify-only): the hot-path drain loop
      is unchanged by this PR (Fix #4 deferred); confirm the resilient client swap doesn't change
      query semantics.
- [ ] New tests: `tests/unit/test_redis_bootstrap.py` (degrade-don't-die, empty-URL fallback,
      retry kwargs present) and `tests/unit/test_update_redis_persistence.py` (CONFIG REWRITE
      persistence, skip-when-down) — CREATE (greenfield for the new modules).

## Rabbit Holes

- **Vendoring popoto to edit `redis_db.py` directly.** Do NOT. popoto is pip-installed;
  the `set_REDIS_DB_settings` seam is the sanctioned app-boundary fix. Vendoring is a
  separate, much larger project.
- **Moving the hot-path Redis call off the event loop (Fix #4).** Tempting while in the
  area, but it is 2-3d of async refactoring with its own test surface. DEFERRED — do not
  start it here.
- **Standing up the SQLite secondary store (Fix #2).** Greenfield `agent/session_archive.py`
  + a restore path is 1-2d. DEFERRED. This PR's job is the AOF floor, not the second store.
- **Redis replication + Sentinel (Fix #5).** Infra + second-host + ops. DEFERRED.
- **Hand-rolling `redis.conf` path discovery.** Use `CONFIG REWRITE` (writes back to the
  conf Redis was started with) rather than crawling Homebrew/Linux paths. If Redis was
  started without a conf file, `CONFIG REWRITE` errors — catch it, apply runtime `CONFIG SET`,
  and log a warning. Don't build an exhaustive path crawler.

## Risks

### Risk 1: `/update` step silently fails on a machine, leaving AOF off there
**Impact:** That machine reverts to RDB-only on next Redis restart — the exact gap we're closing.
**Mitigation:** The doctor durability check (`aof_enabled:1`, `maxmemory-policy noeviction`)
runs independently of `/update` and renders an actionable red result, so drift is visible.
The `/update` step logs applied/skipped/failed explicitly and asserts the post-condition.

### Risk 2: The resilient-client bootstrap regresses test Redis isolation
**Impact:** A bootstrap that rebuilds `POPOTO_REDIS_DB` could clobber the per-worker test db,
risking a db0 write (the 2026-06-03 incident class).
**Mitigation:** `configure_resilient_redis()` is gated to no-op (or db-respecting) under pytest
and runs only at worker/bridge startup, never at import. The existing flush guard + `redis_test_db`
fixture remain the last line of defense and are re-verified in Test Impact.

### Risk 3: `appendfsync everysec` adds disk I/O
**Impact:** Marginal write-latency increase on the Redis host.
**Mitigation:** `everysec` (not `always`) is the standard durability/throughput balance; AOF is
already effectively on on the primary host with no observed issue. RDB is kept as a second layer.

## Race Conditions

### Race 1: Concurrent bootstrap from worker and bridge in the same process tree
**Location:** `config/redis_bootstrap.py::configure_resilient_redis`
**Trigger:** Worker and bridge both call the bootstrap at startup.
**Data prerequisite:** The global `POPOTO_REDIS_DB` must be fully rebuilt before any
`AgentSession.query.*` runs.
**State prerequisite:** Only one rebuild should win; a half-applied client must never be observed.
**Mitigation:** Idempotent + run-once guard (module-level sentinel). Worker and bridge are
separate processes in production, so cross-process contention is moot; the run-once guard
covers the in-process double-call case. The call is synchronous and completes before the
event loop starts.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1814] **Fix #2 — Durable secondary store (SQLite export of `AgentSession`
  + restore path).** ~1-2d greenfield (`agent/session_archive.py`, hook
  `models/session_lifecycle.py::finalize_session`). Tracked under parent #1814 until the
  supervisor splits it into its own issue. Covers total Redis data-dir loss, which AOF does not.
- [SEPARATE-SLUG #1814] **Fix #4 — Move hot-path Redis off the event loop**
  (`agent/agent_session_queue.py:1367-1376`, startup scans) via async client / `run_in_executor`.
  ~2-3d. Tracked under parent #1814 until the supervisor splits it into its own issue.
- [SEPARATE-SLUG #1814] **Fix #5 — Redis replication + Sentinel** on a second host for
  primary-loss failover. Infra + ops. Tracked under parent #1814 until the supervisor splits it.
- [EXTERNAL] Applying AOF/maxmemory-policy on machines the agent cannot reach — those apply on
  their next `/update` run on that machine.

<!-- NOTE for the supervisor: Fix #2/#4/#5 above are DEFERRED per the agreed scope decision and
are currently tagged against the parent #1814 so the validator's `gh issue view` resolves. The
agreed plan is for the supervisor to split each into its own new issue; once those exist, BUILD/PM
should update each tag to the real new number. This plan step deliberately does NOT create them. -->

## Update System

**This plan is heavily `/update`-coupled — this is the core of Fix #1 and #6.**

- **New step in `scripts/update/run.py`:** add a Redis-persistence step (after the
  dependency/migration steps, before service restart) that invokes a new module
  `scripts/update/redis_persistence.py`. The module:
  - Applies `CONFIG SET appendonly yes`, `CONFIG SET appendfsync everysec`,
    `CONFIG SET maxmemory-policy noeviction` at runtime via `redis-cli`.
  - Runs `CONFIG REWRITE` so those three directives persist into the active `redis.conf`
    and survive a `redis-server` restart.
  - Post-condition asserts `aof_enabled:1` + the expected policy; logs applied/skipped/failed.
  - Returns a `dataclass` result; `run.py` logs it.
  - Is **non-fatal**: if `redis-cli` is absent or Redis is down, it logs and skips —
    it must never block the rest of `/update`.
- **No new Python deps to propagate** (uses installed `redis==7.4.0` + system `redis-cli`).
- **No `scripts/update/migrations.py` change** — this is server config, not a Popoto schema
  change. (Per repo convention, IF any persisted Popoto field is introduced during build,
  add and register a migration — but the in-scope work introduces none.)
- **Migration for existing installations:** the first `/update` run after this PR applies
  the config on every machine; the doctor durability check confirms it landed.

## Agent Integration

No agent integration required — this is bridge/worker-internal infrastructure.

- **No new CLI entry point** in `pyproject.toml [project.scripts]`.
- **No new MCP server / `.mcp.json` change.**
- The Popoto client change (`config/redis_bootstrap.py`) is internal to every process
  that imports popoto (worker, bridge, tools, dashboard). It is invoked at worker startup
  (`worker/__main__.py`) and bridge startup (`bridge/telegram_bridge.py`); no agent-facing
  surface changes. Integration coverage is the worker/bridge startup tests confirming the
  resilient client is active and degrade-don't-die works.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/redis-durability.md` — the durability model: AOF (`everysec`)
      floor + RDB, the resilient Popoto client (retry/backoff/health-check, degrade-don't-die),
      the pinned `noeviction` policy + durable-key eviction audit, and the deferred roadmap
      (secondary SQLite store, off-loop hot path, replication + Sentinel).
- [ ] Add an entry to `docs/features/README.md` index table.

### Inline Documentation
- [ ] Docstring on `config/redis_bootstrap.py::configure_resilient_redis` explaining the
      `set_REDIS_DB_settings` seam and why it exists (popoto is third-party).
- [ ] Comment in `scripts/update/redis_persistence.py` on the CONFIG REWRITE persistence + idempotency.

### Correction
- [ ] Rewrite the false example at `config/personas/segments/work-patterns.md:177` so it no
      longer asserts "Redis is operational-only, not durable. Popoto models handle persistence."

## Success Criteria

- [ ] AOF pinned in `redis.conf` (via CONFIG REWRITE) and applied on every machine via `/update`;
      `redis-cli INFO persistence` shows `aof_enabled:1`.
- [ ] `maxmemory-policy noeviction` pinned and applied; a documented audit confirms durable keys
      (sessions, index sets, bloom) carry no silent-eviction risk.
- [ ] Popoto client survives a Redis restart (reconnect/backoff) and the worker/bridge do NOT crash
      at startup when Redis is down (degraded-start warning logged instead).
- [ ] `work-patterns.md:177` corrected — no longer encodes the false durability claim.
- [ ] `docs/features/redis-durability.md` created and indexed.
- [ ] Doctor durability check asserts `aof_enabled:1` + `noeviction`.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools.

### Team Members

- **Builder (redis-config)**
  - Name: redis-config-builder
  - Role: `scripts/update/redis_persistence.py` + `run.py` wiring + CONFIG REWRITE persistence
  - Agent Type: builder
  - Resume: true

- **Builder (resilient-client)**
  - Name: client-builder
  - Role: `config/redis_bootstrap.py` + worker/bridge startup wiring + doctor check
  - Agent Type: async-specialist
  - Resume: true

- **Validator (durability)**
  - Name: durability-validator
  - Role: verify AOF/eviction applied, degrade-don't-die, test isolation intact
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: durability-doc
  - Role: `docs/features/redis-durability.md`, README index, work-patterns.md correction
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Redis server config + /update propagation
- **Task ID**: build-redis-config
- **Depends On**: none
- **Validates**: tests/unit/test_update_redis_persistence.py (create)
- **Assigned To**: redis-config-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `scripts/update/redis_persistence.py` (dataclass-result, mirror `kokoro.py` shape).
- Apply `CONFIG SET appendonly yes` / `appendfsync everysec` / `maxmemory-policy noeviction`.
- Run `CONFIG REWRITE`; assert post-condition `aof_enabled:1` + policy.
- Wire a new step into `scripts/update/run.py`; make it non-fatal on missing redis-cli/down Redis.

### 2. Resilient Popoto client + startup wiring
- **Task ID**: build-resilient-client
- **Depends On**: none
- **Validates**: tests/unit/test_redis_bootstrap.py (create)
- **Assigned To**: client-builder
- **Agent Type**: async-specialist
- **Parallel**: true
- Create `config/redis_bootstrap.py::configure_resilient_redis()` using `set_REDIS_DB_settings`
  with `retry_on_timeout`, `Retry(ExponentialBackoff(cap=10, base=1), 3)`, `health_check_interval=30`.
- Degrade-don't-die guard (log warning, no raise when Redis down); run-once + pytest no-op guard.
- Call it at worker (`worker/__main__.py`) and bridge (`bridge/telegram_bridge.py`) startup.
- Extend `tools/doctor.py` with the durability check (`aof_enabled` + `noeviction`).

### 3. Validation
- **Task ID**: validate-durability
- **Depends On**: build-redis-config, build-resilient-client
- **Assigned To**: durability-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `redis-cli INFO persistence` → `aof_enabled:1`; `CONFIG GET maxmemory-policy` → `noeviction`.
- Verify degrade-don't-die: simulate Redis-down at startup → warning logged, no crash.
- Re-run `tests/unit/test_redis_flush_guard.py` + queue async tests; confirm test isolation intact.

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-durability
- **Assigned To**: durability-doc
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/redis-durability.md`; add README index entry.
- Correct `config/personas/segments/work-patterns.md:177`.

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: durability-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all Verification checks; confirm every Success Criterion; generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_redis_bootstrap.py tests/unit/test_update_redis_persistence.py tests/unit/test_doctor.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| AOF enabled | `redis-cli INFO persistence \| grep aof_enabled` | output contains `aof_enabled:1` |
| Eviction pinned | `redis-cli CONFIG GET maxmemory-policy` | output contains `noeviction` |
| Bootstrap module exists | `test -f config/redis_bootstrap.py && echo ok` | output contains `ok` |
| Update step wired | `grep -c redis_persistence scripts/update/run.py` | output > 0 |
| False claim removed | `grep -c "operational state only, not durable records" config/personas/segments/work-patterns.md` | match count == 0 |
| Feature doc exists | `test -f docs/features/redis-durability.md && echo ok` | output contains `ok` |
| No popoto vendoring | `git ls-files \| grep -c "^popoto/redis_db.py$"` | match count == 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Deferred-fix issue filing.** The plan tags Fix #2/#4/#5 as `[SEPARATE-SLUG #1814]`
   placeholders and asks the supervisor to split each into its own new issue (per the scope
   decision). Confirm the supervisor will do so and update the tags to the real numbers —
   the plan deliberately does not create them itself.
2. **`redis.conf` persistence when Redis was started without a conf file.** The persistence
   step uses `CONFIG REWRITE`, which errors if Redis was launched with no config file. Plan
   currently falls back to runtime `CONFIG SET` + a loud warning in that case. Confirm that is
   the desired behavior vs. hard-failing the step.
3. **Doctor check severity.** Should `aof_enabled:0` on a machine be a doctor *warning* or a
   *failure* (non-zero `/update` verify)? Plan currently treats it as an actionable warning.
