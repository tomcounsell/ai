---
status: Planning
type: bug
appetite: Large
owner: Valor Engels
created: 2026-06-30
tracking: https://github.com/tomcounsell/ai/issues/1814
last_comment_id:
---

# Redis Durability Hardening

## Problem

Every durable record in the system — `AgentSession`s, memories, Telegram/email
history, the bloom filter — lives in Popoto, and **Popoto is Redis**. There is no
write-ahead log persisted across restarts, no second store, no retry on the
client, and an unbounded `noeviction` memory policy. A hard crash, power loss, or
OOM-kill of `redis-server` loses every write since the last RDB snapshot (worst
case ~1 hour), and a total data-dir loss (FLUSHALL / disk failure) loses
everything with no fallback.

**Current behavior:**
- AOF is enabled on the *current* host (`aof_enabled:1`) but this is not pinned in
  `redis.conf` and not propagated by `/update`, so it is not guaranteed on every
  machine or across a Redis reinstall.
- The Popoto redis client (`popoto/redis_db.py`, a **pip-installed package**) is
  built at import with `socket_timeout=5` and **no** `retry_on_timeout`,
  `health_check_interval`, or backoff; it `raise`s on connection failure, so if
  Redis is down at boot, `import popoto` fails and the worker/bridge cannot start.
- Hot-path `AgentSession.query.*` calls run the *sync* redis-py client on the
  asyncio loop (`agent/agent_session_queue.py` drain loop, ~line 1367); a slow
  Redis wedges the whole loop up to 5s per call.
- `maxmemory-policy` is `noeviction`. If anyone later switches to `allkeys-lru`,
  Redis will silently evict index sets, class sets, and bloom-filter keys
  (`agent/memory_hook.py` ~line 180) — none carry TTLs marking them transient.
- A teaching example in `config/personas/segments/work-patterns.md:177` asserts
  "Redis is used for operational state only, not durable records" — factually
  wrong (Popoto *is* Redis).

**Desired outcome:** Bounded-loss durability guaranteed on every machine, a
Redis client that survives restarts and degrades rather than crashing at import, a
true second store so Redis loss is survivable, a pinned eviction policy that can
never silently drop durable keys, and docs/persona copy that match reality.

## Freshness Check

**Baseline commit:** aa6b9968
**Issue filed at:** 2026-06-29T09:20:43Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `popoto/redis_db.py` (~line 112-130) — client built at import with
  `socket_timeout=5`/`socket_connect_timeout=5`, no retry/health-check/backoff,
  `raise` on failure — **still holds verbatim**. Drift: this file lives in
  `.venv/lib/python3.14/site-packages/popoto/`, i.e. it is a **third-party
  pip-installed package, not vendored repo source**. Fix #3 therefore cannot be a
  plain in-repo edit (see Technical Approach).
- `agent/agent_session_queue.py` (~line 1367) — sync `AgentSession.query.filter(...)`
  on the drain loop — **still holds**.
- `agent/memory_hook.py` (~line 180) — bloom field via `Memory._meta.fields["bloom"]`
  — **still holds**.
- `config/personas/segments/work-patterns.md:177` — the asserted string is present,
  but it is an **example `memory_search save` command** inside a "When to Save"
  memory-teaching section, not a standalone architectural assertion. Still worth
  correcting; it is doc copy, not config-driving.
- `config/settings.py` (~line 141-142) — `REDIS_URL` env-driven default — **still holds**.

**Live-host re-verification (plan time):**
- `redis-cli CONFIG GET appendonly` → **`yes`**, `aof_enabled:1` (issue reported
  `no`). Fix #1 reframes from "turn AOF on" to "pin AOF in `redis.conf` + propagate
  via `/update` so it survives restart and is guaranteed on every machine."
- `redis-cli CONFIG GET maxmemory-policy` → `noeviction` (unchanged, as reported).

**Cited sibling issues/PRs re-checked:** None cited (this is workstream 1 of 4; only
#1814 is filed).

**Commits on main since issue was filed (touching referenced files):** None.

**Active plans in `docs/plans/` overlapping this area:** `redis-popoto-migration.md`
(historical Redis/Popoto migration) — predates this work, no live overlap.

**Notes:** No drift changes the plan premise. The two material drifts (popoto is a
pip package; AOF already on this host) are folded into the Technical Approach below.

## Prior Art

No prior issues or merged PRs found for `redis durability`, `AOF`, or `persistence`
hardening (`gh issue list --state closed` and `gh pr list --state merged` both
empty for those keywords). This is greenfield durability work.

- `docs/plans/redis-popoto-migration.md` — historical migration onto Popoto/Redis;
  established Popoto as the persistence layer but did not address AOF, a secondary
  store, or client resilience. It is the reason "Popoto handles persistence" became
  the mental model the issue flags as incomplete.

## Research

**Queries used:**
- Redis AOF appendfsync everysec durability vs RDB best practices 2025
- redis-py retry_on_timeout health_check_interval reconnect backoff best practice
- atomic SQLite write temp file rename durability WAL mode best practice python

**Key findings:**
- **AOF + RDB hybrid is the recommended production config.** Since Redis 4.0 the
  AOF can carry an RDB preamble — fast restart from the RDB snapshot plus ≤1s loss
  from `appendfsync everysec`. We keep RDB and pin AOF on. Source:
  https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/
- **redis-py resilient client recipe.** Pass `retry=Retry(ExponentialBackoff(cap=10,
  base=1), N)` + `retry_on_error=[ConnectionError, TimeoutError, ConnectionResetError]`
  + `health_check_interval`. redis-py ≥6.0 already retries 3× by default; the gap is
  the *no-raise-at-import* and *health-check* behavior, not retry count. Source:
  https://redis.io/docs/latest/develop/clients/redis-py/produsage/
- **SQLite secondary store config.** `PRAGMA journal_mode=WAL` + `synchronous=NORMAL`
  + `busy_timeout=5000` is the production sweet spot; durable atomic snapshot via
  write-temp-then-`os.replace()`. Source: https://sqlite.org/wal.html

## Data Flow

For the secondary-store path (fix #2), trace from terminal transition to durable disk:

1. **Entry point**: a session reaches a terminal status →
   `models/session_lifecycle.py::finalize_session()`.
2. **Archive hook**: `finalize_session` calls a new `agent/session_archive.py`
   exporter, which serializes the `AgentSession` fields to a row in SQLite.
3. **Periodic sweep**: a cadence-driven sweep (reflection or worker timer) exports
   *all* live sessions, not just terminal ones, so non-terminal state is also
   covered.
4. **Restore**: on worker startup, if Redis is empty (`DBSIZE == 0` or no
   `AgentSession` index), the restore path rehydrates sessions from SQLite back into
   Popoto before the drain loop starts.
5. **Output**: a single-file SQLite DB on disk (WAL mode) that survives a full Redis
   data-dir loss.

## Why Previous Fixes Failed

No prior fix attempts exist for this problem — greenfield. The closest prior work
(`redis-popoto-migration.md`) is not a failed fix; it simply never scoped
durability, which is the gap this plan closes.

## Architectural Impact

- **New dependencies**: SQLite (stdlib `sqlite3`, no new package). No new external
  service for fixes #1–#4/#6. Fix #5 (Sentinel) would add a second Redis host +
  ops, hence deferred (see No-Gos).
- **Interface changes**: `finalize_session()` gains an archive side-effect (best-effort,
  never blocks the transition). A new `agent/session_archive.py` module. Redis client
  construction moves behind a resilient builder at the app boundary (`config/settings.py`),
  not inside the pip package.
- **Coupling**: adds a one-way dependency from `session_lifecycle` → `session_archive`.
  The archive must be fire-and-forget so a SQLite failure never blocks a session
  transition.
- **Data ownership**: Redis remains the source of truth; SQLite is a derived,
  restore-only mirror. No dual-write consistency contract beyond "SQLite is
  eventually ≤ cadence behind Redis."
- **Reversibility**: every element is independently revertible. AOF/policy via
  `CONFIG SET` + redis.conf; client wrapper behind a settings flag; archive module
  deletable; doc edit trivial.

## Appetite

**Size:** Large

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 2-3 (scope alignment — especially which fixes land now vs defer)
- Review rounds: 2+ (the resilient-client wrapper and the restore path both warrant
  careful review; restore is a data-integrity surface)

This spans Redis config propagation, a new persistence module with a restore path,
a client-resilience wrapper around a third-party package, and an event-loop offload —
four distinct surfaces with real review overhead.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `redis-cli` available | `redis-cli PING` | Apply/verify AOF + maxmemory-policy |
| Redis ≥ 4.0 (AOF+RDB hybrid) | `redis-cli INFO server` | RDB-preamble AOF support |
| Writable Redis config dir | `redis-cli CONFIG GET dir` | Persist `CONFIG REWRITE` to redis.conf |
| SQLite (stdlib) | `python -c "import sqlite3"` | Secondary store |

Run via `python scripts/check_prerequisites.py docs/plans/redis-durability-hardening.md`.

## Solution

### Key Elements

- **AOF pinned + propagated (#1)**: ensure `appendonly yes` + `appendfsync everysec`
  is written into `redis.conf` (`CONFIG SET` then `CONFIG REWRITE`) and that `/update`
  asserts/applies it on every machine, with a post-condition check on `aof_enabled:1`.
- **SQLite secondary store (#2)**: `agent/session_archive.py` — periodic + on-terminal
  `AgentSession` export to a WAL-mode SQLite file (atomic snapshot), plus a startup
  restore path that rehydrates Popoto when Redis is empty.
- **Resilient Redis client (#3)**: a resilient builder at the app boundary
  (`config/settings.py`) that constructs the redis-py client with
  `retry`/`retry_on_error`/`health_check_interval` and, critically, **does not crash
  the process when Redis is down at import** — degrade, log, reconnect.
- **Hot-path offload (#4)**: move the drain-loop and startup-scan `AgentSession.query.*`
  calls off the event loop via `run_in_executor` (or an async client) so a slow Redis
  cannot freeze every session in lockstep.
- **Pinned eviction policy (#6)**: pin a `maxmemory-policy` (keep `noeviction` +
  alerting, OR `volatile-*` with TTLs) documented and applied so eviction can never
  silently drop durable/index/bloom keys.
- **Doc correction (—)**: fix the `work-patterns.md:177` example and document the real
  durability model.

### Flow

Redis crash/restart → resilient client reconnects (no process death) → AOF replays
≤1s of writes → if data-dir lost, startup restore rehydrates from SQLite → drain
loop resumes on the executor-offloaded query path.

### Technical Approach

- **#1 (AOF):** Do NOT rely on `CONFIG SET` alone — pair it with `CONFIG REWRITE`
  so it persists to `redis.conf`, and add an `/update` step that asserts
  `aof_enabled:1` + `appendfsync everysec` and applies them if missing. Keep RDB
  (`save`) untouched for the hybrid fast-restart.
- **#3 (client) — the key constraint:** `popoto/redis_db.py` is a **pip package**,
  not repo source, so we must NOT edit site-packages. Two viable paths, to be
  confirmed by spike-1:
  (a) Popoto exposes `set_REDIS_DB_settings()` (seen in the same file) — call it at
  app startup with a pre-built resilient client / connection-pool kwargs; or
  (b) set `REDIS_URL`/connection kwargs from `config/settings.py` and wrap import so
  a down-Redis-at-import degrades instead of raising.
  Whichever path, the resilient client gets `retry=Retry(ExponentialBackoff(cap=10,
  base=1), N)`, `retry_on_error=[ConnectionError, TimeoutError, ConnectionResetError]`,
  and `health_check_interval`.
- **#2 (SQLite):** WAL mode, `synchronous=NORMAL`, `busy_timeout=5000`. Atomic
  full-snapshot via write-to-temp + `os.replace()`; incremental on-terminal upsert via
  the `finalize_session` hook. Restore gated on "Redis has no AgentSession index."
  Archive writes are best-effort and never block a transition.
- **#4 (offload):** wrap the specific hot-path `AgentSession.query.*` calls
  (`agent_session_queue.py` ~1367; startup scans in `worker/__main__.py`) in
  `loop.run_in_executor(None, ...)`. Confirm scope is bounded (do not blanket-async
  all of Popoto — see Rabbit Holes).
- **#6 (policy):** pin the chosen policy via `CONFIG SET maxmemory-policy ...` +
  `CONFIG REWRITE`, propagate via `/update`, document why durable keys cannot be
  evicted under it.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The archive hook in `finalize_session` must catch and **log** (not silently
  swallow) any SQLite error — test asserts a `logger.warning` fires and the session
  transition still completes.
- [ ] The resilient-client builder must log on degraded start (Redis down at import)
  rather than `raise` — test asserts process continues and logs.

### Empty/Invalid Input Handling
- [ ] Restore path with an **empty** SQLite file → no-op, no crash.
- [ ] Restore path when Redis is **non-empty** → must NOT clobber live data (guard on
  empty-Redis precondition).
- [ ] Archive of a session with null/partial fields → row written without error.

### Error State Rendering
- [ ] No user-visible surface in this work (infra/persistence). State: error states
  are operator-visible via logs/metrics, not end-user output. Tests assert log lines.

## Test Impact

- [ ] `tests/` — no existing test directly covers AOF config, the Popoto client
  construction, or `finalize_session` archival. **No existing tests affected** — this
  is additive durability infrastructure with no prior coverage of these surfaces; new
  tests are created for the archive/restore round-trip, the resilient client degraded
  start, and the offload path. (Search for existing `finalize_session` and redis-config
  tests during build to confirm none assert the old no-archive behavior; if any do,
  reclassify to UPDATE.)

## Rabbit Holes

- **Vendoring/forking Popoto** to edit `redis_db.py` directly. Do NOT — wrap at the app
  boundary via the resilient builder + `set_REDIS_DB_settings()`. Forking the package
  is a maintenance tar pit.
- **Blanket-asyncifying all Popoto access (#4).** Only the named hot paths need
  offloading. Converting every `.query.*` call is a multi-week rewrite and out of scope.
- **Building a full bidirectional Redis↔SQLite sync / CDC.** SQLite is a one-way,
  restore-only mirror. Do not chase live consistency.
- **Sentinel/replication topology (#5).** Real ops + a second host; deferred (No-Gos).

## Risks

### Risk 1: Restore path clobbers live Redis data
**Impact:** Rehydrating from a stale SQLite snapshot over a healthy Redis would
overwrite newer state — data loss caused by the durability feature itself.
**Mitigation:** Restore runs ONLY when Redis has no `AgentSession` index (empty-Redis
precondition), gated and tested. Restore is additive (create-if-absent), never an
overwrite of existing keys.

### Risk 2: Resilient-client change breaks worker/bridge startup
**Impact:** Mis-wiring `set_REDIS_DB_settings()` or the degraded-import path could make
the process fail to connect even when Redis is healthy.
**Mitigation:** Behind a settings flag; spike-1 confirms the Popoto seam before build;
integration test asserts normal start (Redis up) AND degraded start (Redis down) both
work. Revertible by flag.

### Risk 3: AOF `CONFIG REWRITE` fails on a read-only/missing redis.conf
**Impact:** AOF set at runtime but lost on next restart on some machines.
**Mitigation:** `/update` step checks `CONFIG GET dir` writability and surfaces a clear
error; post-condition asserts `aof_enabled:1`. If `CONFIG REWRITE` cannot persist, fail
loudly rather than silently leaving a non-durable machine.

## Race Conditions

### Race 1: Concurrent archive write vs. periodic snapshot
**Location:** `agent/session_archive.py`
**Trigger:** A terminal-transition upsert fires while the periodic full-snapshot is
mid-write to the same SQLite file.
**Data prerequisite:** SQLite file in a consistent state before either writer commits.
**State prerequisite:** Single-writer semantics per SQLite connection.
**Mitigation:** WAL mode + `busy_timeout=5000` serializes writers; full-snapshot uses
write-temp + `os.replace()` (atomic) so a concurrent reader/writer never sees a partial
file. On-terminal upserts use a short transaction.

### Race 2: Restore racing the drain loop on startup
**Location:** `worker/__main__.py` startup → `agent_session_queue` drain loop
**Trigger:** Drain loop starts pulling sessions before restore finishes rehydrating.
**Data prerequisite:** All restored sessions present in Redis before the loop reads.
**State prerequisite:** Restore completes (or no-ops) before the loop is signalled.
**Mitigation:** Run restore synchronously in the startup sequence BEFORE the drain
loop is started/signalled; it is gated on empty-Redis so it is a no-op in the common case.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1814] Fix #5 (Redis replication + Sentinel on a second host).
  Requires a second physical/virtual Redis host plus ops runbook; it is the one item
  that cannot be done purely in-repo by the agent. The other five fixes fully close the
  single-host durability gap; #5 is the cross-host failover layer on top.
- [EXTERNAL] Applying AOF/maxmemory-policy on machines the agent cannot reach — those
  apply on their next `/update` run on that machine.

## Update System

**This plan is heavily `/update`-coupled — Redis config must reach every machine.**

- Add a step to `scripts/update/run.py` (Redis-hardening step) that:
  - Asserts/sets `appendonly yes` + `appendfsync everysec`, then `CONFIG REWRITE`.
  - Asserts/sets the chosen `maxmemory-policy`, then `CONFIG REWRITE`.
  - Post-condition: `aof_enabled:1` and the expected policy; fail loudly if not.
  - Idempotent; safe to run on a machine where AOF is already on (current host).
- No new Popoto model is introduced for the SQLite store, so no
  `scripts/update/migrations.py` entry is strictly required. **However**, if the
  resilient-client wiring or archive introduces any persisted Popoto field, add a
  migration to `migrations.py` and register it in `MIGRATIONS` (per repo convention).
- No new pip dependency (SQLite is stdlib; redis-py already present).

## Agent Integration

No new agent/MCP tool surface and no `.mcp.json` change. This is bridge/worker-internal
durability infrastructure:
- The resilient client and offload are transparent to the agent.
- The archive/restore runs inside the worker startup + `finalize_session` hook.
- Integration tests verify the worker starts (and degrades) correctly; no agent-invokable
  capability is added. State: **No agent integration required — bridge/worker-internal change.**

## Documentation

### Feature Documentation
- [ ] Create `docs/features/redis-durability.md` describing the durability model
  (AOF+RDB hybrid, SQLite secondary store + restore path, resilient client,
  eviction policy, and the deferred Sentinel option).
- [ ] Add an entry to `docs/features/README.md` index table.
- [ ] Create `docs/infra/redis-durability-hardening.md` (current state, new
  requirements, rules/constraints — AOF + policy + SQLite location, rollback plan).

### Inline Documentation
- [ ] Correct `config/personas/segments/work-patterns.md:177` so the example no longer
  asserts "Redis is used for operational state only, not durable records." Replace with
  an accurate save example.
- [ ] Docstrings on `agent/session_archive.py` public functions and the resilient-client builder.

## Success Criteria

- [ ] AOF pinned in `redis.conf` and applied by `/update`; `aof_enabled:1` verified on
  every machine via the post-condition check.
- [ ] Periodic + on-terminal `AgentSession` export to WAL-mode SQLite, with a tested
  restore path that rehydrates Popoto when Redis is empty and is a no-op otherwise.
- [ ] Popoto client survives a Redis restart (reconnect/backoff) and does NOT crash the
  process at import when Redis is down (tested degraded start).
- [ ] Hot-path drain-loop `AgentSession.query.*` calls run off the event loop.
- [ ] A pinned `maxmemory-policy` documented and applied, proven not to evict durable keys.
- [ ] `work-patterns.md:177` corrected.
- [ ] `docs/features/redis-durability.md` created and indexed.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

### Team Members

- **Builder (redis-config)**
  - Name: redis-config-builder
  - Role: AOF + maxmemory-policy pinning and `/update` step (#1, #6)
  - Agent Type: builder
  - Resume: true

- **Builder (secondary-store)**
  - Name: archive-builder
  - Role: `agent/session_archive.py` + `finalize_session` hook + restore path (#2)
  - Agent Type: data-architect
  - Resume: true

- **Builder (resilient-client)**
  - Name: client-builder
  - Role: resilient redis-py builder at app boundary + degrade-at-import (#3)
  - Agent Type: async-specialist
  - Resume: true

- **Builder (loop-offload)**
  - Name: offload-builder
  - Role: move hot-path queries off the event loop (#4)
  - Agent Type: async-specialist
  - Resume: true

- **Documentarian**
  - Name: durability-doc
  - Role: feature + infra docs, persona correction
  - Agent Type: documentarian
  - Resume: true

- **Validator**
  - Name: durability-validator
  - Role: verify all success criteria, run restore round-trip + degraded-start tests
  - Agent Type: validator
  - Resume: true

### Available Agent Types

(See template tiers — using builder, data-architect, async-specialist, documentarian, validator.)

## Step by Step Tasks

### 0. Spike: confirm the Popoto client seam
- **Task ID**: spike-1
- **Depends On**: none
- **Method**: code-read
- **Agent Type**: Explore
- **Parallel**: true
- Confirm whether `set_REDIS_DB_settings()` in `popoto/redis_db.py` accepts a
  pre-built client / connection kwargs, and whether setting `REDIS_URL` + import order
  lets us inject retry/health-check params without editing site-packages.
- **Result**: [filled at build time] **Confidence**: [ ] **Impact if false**: fall back to
  wrapping the import in `config/settings.py` and degrading on connection error.

### 1. AOF + maxmemory-policy + /update step
- **Task ID**: build-redis-config
- **Depends On**: none
- **Validates**: `tests/` new test asserting the `/update` step is idempotent and
  post-conditions `aof_enabled:1`
- **Assigned To**: redis-config-builder
- **Agent Type**: builder
- **Parallel**: true
- Pin `appendonly yes` + `appendfsync everysec` and the chosen `maxmemory-policy` via
  `CONFIG SET` + `CONFIG REWRITE`; add the idempotent `/update` step with post-condition check.

### 2. SQLite secondary store + restore
- **Task ID**: build-archive
- **Depends On**: spike-1
- **Validates**: new `tests/` archive→restore round-trip + empty/non-empty restore guards
- **Informed By**: spike-1
- **Assigned To**: archive-builder
- **Agent Type**: data-architect
- **Parallel**: true
- Create `agent/session_archive.py` (WAL, atomic snapshot, on-terminal upsert), wire the
  `finalize_session` hook (best-effort/logged), add the startup restore path gated on empty Redis.

### 3. Resilient Redis client
- **Task ID**: build-client
- **Depends On**: spike-1
- **Validates**: integration test — normal start (Redis up) AND degraded start (Redis down)
- **Informed By**: spike-1
- **Assigned To**: client-builder
- **Agent Type**: async-specialist
- **Parallel**: true
- Build the resilient client at the app boundary with retry/backoff/health-check; ensure
  import does not crash when Redis is down.

### 4. Hot-path loop offload
- **Task ID**: build-offload
- **Depends On**: build-client
- **Validates**: test asserting the drain-loop query runs via executor
- **Assigned To**: offload-builder
- **Agent Type**: async-specialist
- **Parallel**: false
- Wrap the named hot-path `AgentSession.query.*` calls in `run_in_executor`.

### 5. Documentation + persona correction
- **Task ID**: document-feature
- **Depends On**: build-redis-config, build-archive, build-client, build-offload
- **Assigned To**: durability-doc
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/redis-durability.md` + index entry + `docs/infra/redis-durability-hardening.md`;
  correct `work-patterns.md:177`.

### 6. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: durability-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all verification checks; confirm every success criterion; run restore round-trip + degraded-start.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| AOF enabled | `redis-cli INFO persistence \| grep -c 'aof_enabled:1'` | output contains 1 |
| Archive module exists | `test -f agent/session_archive.py` | exit code 0 |
| Persona claim corrected | `grep -c 'operational state only, not durable records' config/personas/segments/work-patterns.md` | match count == 0 |
| Feature doc exists | `test -f docs/features/redis-durability.md` | exit code 0 |
| /update Redis step present | `grep -rc 'appendfsync\|maxmemory-policy' scripts/update/run.py` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Scope of this plan vs. follow-ups.** Recommend landing #1, #2, #3, #4, #6 + the
   doc fix here, and deferring #5 (replication + Sentinel) to a dedicated follow-up
   issue (it needs a second host + ops). Agree, or fold #5 in?
2. **Eviction policy choice (#6).** Keep `noeviction` + add OOM alerting (safest for
   durability), or move to a `volatile-*` policy with explicit TTLs on transient keys?
   The former is lower-risk; the latter requires auditing which keys are safe to evict.
3. **Restore trigger precondition.** Is "Redis has no `AgentSession` index" the right
   empty-Redis signal, or should restore also key off `DBSIZE == 0` to avoid rehydrating
   into a partially-populated Redis?
