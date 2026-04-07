---
status: Planning
type: bug
appetite: Medium
owner: Tom Counsell
created: 2026-04-07
tracking: https://github.com/tomcounsell/ai/issues/810
last_comment_id: ""
---

# Fix Worker Concurrency: Per-Chat Serialization and Global Session Cap

## Problem

When three PM sessions were enqueued with `chat_id="0"` (via `python -m tools.valor_session create --role pm`), multiple sessions ran concurrently instead of serializing. The dashboard showed 5+ sessions running simultaneously, requiring `kill --all` to recover.

**Current behavior:**
- Sessions with the same `chat_id` can run in parallel due to a TOCTOU race in `_pop_agent_session()`
- Two concurrent pops can both see a session as `pending` before either marks it `running`
- No global concurrency ceiling — each unique `chat_id` spawns its own unconstrained `_worker_loop`
- `create_local()` generates collision-prone `chat_id` values (`local{timestamp % 10000}`) instead of using the Claude Code session UUID

**Desired outcome:**
- Sessions with the same `chat_id` execute strictly one at a time
- A configurable global ceiling (`MAX_CONCURRENT_SESSIONS`, default 3) prevents resource exhaustion
- CLI sessions use the Claude Code session UUID as `chat_id` for proper isolation

## Prior Art

- **PR #801** (merged 2026-04-07): Added `_starting_workers` guard and `_starting_workers: set[str]` to prevent duplicate `_worker_loop` task spawns when `_ensure_worker()` is called rapidly. This fixed the worker-spawn race but **not** the session-pop TOCTOU or the global concurrency ceiling. The pop race can still produce two simultaneously running sessions for the same `chat_id`.

## Data Flow

1. **Entry:** `enqueue_agent_session()` calls `_push_agent_session()` then `_ensure_worker(chat_id)`
2. **Worker spawn:** `_ensure_worker()` creates one `asyncio.Task(_worker_loop(chat_id))` per chat — `_starting_workers` guard (PR #801) prevents duplicate tasks
3. **Session pop:** `_worker_loop()` calls `_pop_agent_session(chat_id)` — does `async_filter(status="pending")` then `transition_status(chosen, "running")` — **no lock between query and write**
4. **Race window:** If two worker loops somehow share a `chat_id` (recovery scenario), or if the same loop is re-entered, both can see the same pending session before either commits `running`
5. **Global ceiling:** None exists — each `chat_id` gets its own loop with no coordination

## Architectural Impact

- **New module-level semaphore:** `_global_session_semaphore: asyncio.Semaphore` in `agent_session_queue.py` — acquired before executing each session, released after
- **Redis lock for pop atomicity:** A short-lived Redis lock (`SETNX`-based, ~5s TTL) wrapping the query+transition in `_pop_agent_session()` prevents two concurrent pops from claiming the same session
- **`create_local()` change:** `models/agent_session.py` — use `hook_input["session_id"]` (the Claude Code UUID) as `chat_id` instead of modular timestamp
- **Reversibility:** Medium — semaphore and lock are additive; `chat_id` change requires verifying all callers of `create_local()` pass `session_id` correctly

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (confirm MAX_CONCURRENT_SESSIONS default)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis reachable | `python -c "import redis; redis.Redis().ping()"` | Lock and semaphore storage |

## Solution

### Key Elements

- **Redis pop lock:** `_pop_agent_session()` acquires a short-lived Redis lock (`setnx worker:pop_lock:{chat_id}`) before querying pending sessions; released immediately after `transition_status`. This makes the query→transition atomic across processes/workers.
- **Global asyncio semaphore:** `_global_session_semaphore = asyncio.Semaphore(MAX_CONCURRENT_SESSIONS)` (configurable via env var, default 3). Every `_worker_loop` acquires this before calling `_execute_agent_session()` and releases after.
- **`create_local()` UUID fix:** Replace `f"local{int(now.timestamp()) % 10000}"` with the `session_id` parameter (which is the Claude Code UUID), giving each CLI session an isolated queue automatically.
- **`MAX_CONCURRENT_SESSIONS` config:** Read from env var at module import, default 3. Logged at worker startup.
- **Fallback path coverage:** The Redis pop lock must be applied in BOTH code paths:
  1. In `_pop_agent_session()` — wraps the async `async_filter` + `transition_status` block.
  2. In the **sync fallback branch only** of `_pop_agent_session_with_fallback()` (lines ~629+) — the branch that runs ONLY when `_pop_agent_session()` returned `None`. This branch does its own independent `AgentSession.query.filter()` + `transition_status()` call without going through `_pop_agent_session()` at all, so it is NOT covered by the lock in step 1.
  - This is **not re-entrant**: `_pop_agent_session()` acquires the lock, does its work, RELEASES the lock, and returns `None`. Only then does the sync fallback branch start — it acquires a fresh lock on the same key. No lock is held when the second acquisition happens.

### Flow

**Hot path:** Enqueue session → `_ensure_worker(chat_id)` → `_worker_loop` waits for global semaphore → acquires Redis pop lock → `_pop_agent_session()` (atomic) → releases pop lock → `_execute_agent_session()` → releases semaphore

**Drain/fallback path:** `_pop_agent_session_with_fallback()` → calls `_pop_agent_session()` (lock acquired + released inside) → if None: acquires Redis pop lock → sync fallback query + `transition_status` → releases pop lock → returns session

### Technical Approach

- Implement Redis pop lock using `Popoto`'s underlying Redis client (avoid new dependencies). TTL=5s — long enough to cover the transition write, short enough to self-heal on crash.
- Use `asyncio.Semaphore` (not threading.Semaphore) — already in an async context.
- `MAX_CONCURRENT_SESSIONS` read via `int(os.environ.get("MAX_CONCURRENT_SESSIONS", "3"))` at module level in `agent_session_queue.py`.
- `_global_session_semaphore` is initialized eagerly in `_run_worker()` before the first `asyncio.Task(_worker_loop(...))` is created.
- `create_local()` receives `session_id` as a required kwarg — it already does. Change the `chat_id` default from timestamp to `session_id`.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Redis lock acquisition failure must be handled: if `setnx` fails (lock held), the pop returns `None` and the worker waits for the event — not a crash. Add test asserting this path.
- [ ] Semaphore acquisition is not cancellable silently — `CancelledError` during semaphore wait must still release correctly. Existing `CancelledError` handler in `_worker_loop` already covers this.

### Empty/Invalid Input Handling
- [ ] `MAX_CONCURRENT_SESSIONS=0` must not create a semaphore with value 0 (deadlock). Clamp minimum to 1 with a warning log.
- [ ] `create_local()` called without `session_id` — existing TypeError from required kwarg is correct behavior.

### Error State Rendering
- [ ] Dashboard query (`/dashboard.json`) reflects the semaphore ceiling — never shows more running sessions than `MAX_CONCURRENT_SESSIONS`.

## Test Impact

- [ ] `tests/unit/test_agent_session_queue.py` — UPDATE: add tests for pop lock contention and global semaphore ceiling
- [ ] `tests/integration/test_worker_concurrency.py` — CREATE: integration test that enqueues 3 sessions with `chat_id="0"` and asserts max 1 running at any point
- [ ] `tests/unit/test_agent_session.py` — CREATE: new test file, add test asserting `create_local()` uses `session_id` as `chat_id` when no explicit `chat_id` provided

## Rabbit Holes

- Distributed locking with Lua scripts or Redlock — overkill. Single-worker `setnx` with TTL is sufficient.
- Replacing asyncio with threading for session execution — out of scope.
- Per-project concurrency limits (e.g., project A gets 2 slots, project B gets 1) — separate issue.
- Backpressure signaling to Telegram (rate limiting inbound messages) — separate concern.

## Risks

### Risk 1: Redis lock TTL too short
**Impact:** Lock expires during a slow `transition_status` write, allowing a concurrent pop to grab the same session. Two sessions run in parallel.
**Mitigation:** Set TTL to 5s (well above any realistic Redis write latency). Log a warning if lock acquisition takes > 1s.

### Risk 2: Semaphore deadlock on `CancelledError`
**Impact:** Worker cancelled while holding semaphore slot — slot never released, eventual deadlock after `MAX_CONCURRENT_SESSIONS` cancellations.
**Mitigation:** Use `async with semaphore` (context manager) so the slot is always released on exception.

### Risk 3: `create_local()` chat_id change breaks existing sessions
**Impact:** CLI sessions that were using timestamp-based `chat_id` values will get a new `chat_id` format on restart.
**Mitigation:** This is intentional — old sessions are terminal state and won't be affected. New sessions get clean isolation.

## Race Conditions

### Race 1: Two concurrent `_pop_agent_session()` calls for same `chat_id`
**Location:** `agent/agent_session_queue.py` line 507–583
**Trigger:** Dual worker instances during startup recovery (watchdog + manual start) or health-loop race
**Data prerequisite:** Session must be in `pending` state when one worker reads it, and must be atomically transitioned to `running` before any other reader sees it
**State prerequisite:** Only one pop can succeed for a given session
**Mitigation:** Redis `SETNX worker:pop_lock:{chat_id}` with TTL=5s wraps the query+transition block. If lock is held, return `None` (caller will retry via event loop).

### Race 2: Global semaphore not initialized before workers start
**Location:** `agent/agent_session_queue.py` module level
**Trigger:** Worker import before `asyncio` event loop is running
**Data prerequisite:** `asyncio.Semaphore` must be initialized inside or after the event loop starts
**State prerequisite:** Event loop must exist when semaphore is first awaited
**Mitigation:** Initialize `_global_session_semaphore` eagerly in `_run_worker()` before the first `asyncio.Task(_worker_loop(...))` is created, ensuring the semaphore exists before any worker loop can access it.

## No-Gos (Out of Scope)

- Per-project or per-user concurrency quotas
- Queueing theory / fair scheduling between `chat_id` groups
- Distributed locking across multiple worker hosts
- Changing the `enqueue_agent_session()` API signature

## Update System

No update system changes required — this is an internal worker fix. `MAX_CONCURRENT_SESSIONS` env var can be added to `.env.example` for documentation purposes, but no migration is needed.

## Agent Integration

No agent integration required — this is a worker-internal change. The agent's behavior is unchanged; it simply won't observe concurrent sessions for the same `chat_id`.

## Documentation

- [ ] Update `docs/features/bridge-worker-architecture.md` to document per-chat serialization guarantee, global semaphore, and `MAX_CONCURRENT_SESSIONS` configuration
- [ ] Add entry to `docs/features/README.md` index if a new feature doc is created

## Success Criteria

- [ ] Three PM sessions enqueued with `chat_id="0"` execute strictly one at a time (verified by test)
- [ ] At most `MAX_CONCURRENT_SESSIONS` sessions run simultaneously across all `chat_ids`
- [ ] Dashboard never shows more running sessions than the configured limit
- [ ] Integration test: enqueue 3 sessions, assert max 1 is `running` at any point in time
- [ ] `create_local()` uses `session_id` (Claude UUID) as `chat_id` when no explicit `chat_id` provided
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (concurrency-fix)**
  - Name: concurrency-builder
  - Role: Implement Redis pop lock, global semaphore, and `create_local()` UUID fix
  - Agent Type: async-specialist
  - Resume: true

- **Test Engineer (concurrency)**
  - Name: concurrency-tester
  - Role: Write unit and integration tests for serialization guarantees
  - Agent Type: test-engineer
  - Resume: true

- **Validator (concurrency)**
  - Name: concurrency-validator
  - Role: Verify implementation, run tests, check dashboard shows correct counts
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Update bridge-worker-architecture.md with new concurrency semantics
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Implement Redis pop lock in `_pop_agent_session()`
- **Task ID**: build-pop-lock
- **Depends On**: none
- **Validates**: `tests/unit/test_agent_session_queue.py`
- **Assigned To**: concurrency-builder
- **Agent Type**: async-specialist
- **Parallel**: true
- Add `_acquire_pop_lock(chat_id)` / `_release_pop_lock(chat_id)` helpers using Redis `SETNX` with TTL=5s
- Wrap the `async_filter` + `transition_status` block in `_pop_agent_session()` with the lock
- If lock is held, return `None` immediately (worker will retry naturally)
- ALSO add the same `setnx worker:pop_lock:{chat_id}` guard at the top of the **sync fallback branch only** in `_pop_agent_session_with_fallback()` (the branch that executes when `_pop_agent_session()` returns `None`). That branch does its own independent `AgentSession.query.filter()` + `transition_status()` and is NOT covered by the lock in `_pop_agent_session()`.
- This is NOT re-entrant: `_pop_agent_session()` acquires the lock, does its work, RELEASES it, and returns `None`. The sync fallback branch then acquires a fresh lock — no lock is held at that point.
- `_run_worker()` lives in `worker/__main__.py`. The semaphore is initialized there, not in `agent_session_queue.py`.

### 2. Add global `asyncio.Semaphore` in `_run_worker()`
- **Task ID**: build-semaphore
- **Depends On**: none
- **Validates**: `tests/unit/test_agent_session_queue.py`
- **Assigned To**: concurrency-builder
- **Agent Type**: async-specialist
- **Parallel**: true
- Add `_global_session_semaphore: asyncio.Semaphore | None = None` at module level
- Initialize eagerly in `_run_worker()` before the first `asyncio.Task(_worker_loop(...))` is created, with `int(os.environ.get("MAX_CONCURRENT_SESSIONS", "3"))`, minimum 1
- Wrap `_execute_agent_session(session)` in `async with _global_session_semaphore`
- Log semaphore value at worker startup

### 3. Fix `create_local()` to use session UUID as `chat_id`
- **Task ID**: build-local-chat-id
- **Depends On**: none
- **Validates**: `tests/unit/test_agent_session.py`
- **Assigned To**: concurrency-builder
- **Agent Type**: builder
- **Parallel**: true
- Change default `chat_id` in `create_local()` from `f"local{int(now.timestamp()) % 10000}"` to use `chat_id = kwargs.pop("chat_id", None) or session_id` as the fallback expression — this means callers that do not pass an explicit `chat_id` automatically get `session_id` as the isolation key, with no change required at call sites
- Verify all callers pass `session_id` correctly (already a required kwarg)

### 4. Write unit tests for pop lock contention
- **Task ID**: test-pop-lock
- **Depends On**: build-pop-lock
- **Assigned To**: concurrency-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Add tests in `tests/unit/test_agent_session_queue.py` for lock acquisition, lock contention (returns None), lock release on success
- Add test for `create_local()` using `session_id` as `chat_id`

### 5. Write integration test: enqueue 3 sessions, assert max 1 running
- **Task ID**: test-integration-concurrency
- **Depends On**: build-pop-lock, build-semaphore
- **Validates**: `tests/integration/test_worker_concurrency.py` (create)
- **Assigned To**: concurrency-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Create `tests/integration/test_worker_concurrency.py`
- Enqueue 3 sessions with `chat_id="0"`, monitor running status at 100ms intervals
- Assert: at no point are more than 1 session in `running` state for `chat_id="0"`
- Assert: at no point are more than `MAX_CONCURRENT_SESSIONS` sessions running globally
- Patch `_execute_agent_session` to be a no-op with controlled delay (`asyncio.sleep`) to make tests fast and deterministic without needing real Claude API calls
- Use the `redis_test_db` fixture pattern from `tests/integration/test_agent_session_queue_race.py` for Redis isolation

### 6. Validate and run full test suite
- **Task ID**: validate-all-tests
- **Depends On**: test-pop-lock, test-integration-concurrency
- **Assigned To**: concurrency-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/ -x -q`
- Run `pytest tests/integration/test_worker_concurrency.py -v`
- Verify dashboard shows correct counts after fix

### 7. Update documentation
- **Task ID**: document-concurrency
- **Depends On**: validate-all-tests
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/bridge-worker-architecture.md` with concurrency semantics
- Document `MAX_CONCURRENT_SESSIONS` env var in the doc and `.env.example`

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `pytest tests/unit/ -x -q` | exit code 0 |
| Concurrency integration test | `pytest tests/integration/test_worker_concurrency.py -v` | exit code 0 |
| Format clean | `python -m black --check agent/agent_session_queue.py models/agent_session.py` | exit code 0 |
| No timestamp chat_id | `grep -n "timestamp.*10000\|10000.*timestamp" models/agent_session.py` | exit code 1 |
| Semaphore present | `grep -n "MAX_CONCURRENT_SESSIONS\|_global_session_semaphore" agent/agent_session_queue.py` | output > 0 |
| Pop lock in async pop | `grep -n "pop_lock" agent/agent_session_queue.py` | output ≥ 2 (once in `_pop_agent_session`, once in sync fallback of `_pop_agent_session_with_fallback`) |
| Semaphore in _run_worker | `grep -n "_global_session_semaphore\|MAX_CONCURRENT_SESSIONS" worker/__main__.py` | output > 0 |

## Critique Results

### Fourth Pass — NEEDS REVISION (2026-04-07)

**Findings**: 3 total (1 blocker, 1 concern, 1 nit)

#### Blocker

**Semaphore placement inconsistency: Flow shows semaphore-before-pop, Task 2 says semaphore-wraps-execution-only**

The Hot Path flow (Solution section) states: "_worker_loop **waits for global semaphore** → acquires Redis pop lock → `_pop_agent_session()` → ... → `_execute_agent_session()` → releases semaphore." This means the semaphore should be acquired BEFORE the pop. However, Task 2 bullet says "Wrap `_execute_agent_session(session)` in `async with _global_session_semaphore`" — i.e., acquired AFTER the pop.

If acquired after the pop, `_pop_agent_session()` marks the session `running` in Redis BEFORE the semaphore slot is obtained. Multiple workers can pop sessions simultaneously, put them all into `running` status, and then queue for the semaphore. The dashboard will show N sessions as `running` even though only `MAX_CONCURRENT_SESSIONS` are actually executing. This directly violates the success criterion "Dashboard never shows more running sessions than the configured limit."

Fix: Revise Task 2's bullet to wrap the entire pop+execute block: acquire the semaphore before calling `_pop_agent_session()`, and release it after `_execute_agent_session()` completes. This keeps the Hot Path flow accurate and ensures the dashboard ceiling holds.

#### Concern

**Cross-module semaphore injection mechanism is unspecified**

Task 2 says `_global_session_semaphore: asyncio.Semaphore | None = None` at module level in `agent_session_queue.py`, and initialization in `_run_worker()` in `worker/__main__.py`. But Task 2 does NOT specify how `_run_worker()` injects the initialized semaphore into `agent_session_queue._global_session_semaphore`. Without this step, the `None` sentinel remains and `async with None` crashes `_worker_loop` with `TypeError`. The builder needs explicit guidance: `import agent.agent_session_queue as _queue; _queue._global_session_semaphore = asyncio.Semaphore(max_sessions)`.

#### Nit

**Task 4 unnecessarily duplicates `create_local()` test in the wrong file**

Task 4 says "Add test for `create_local()` using `session_id` as `chat_id`" in `tests/unit/test_agent_session_queue.py`. But `create_local()` is a classmethod on `AgentSession` (`models/agent_session.py`), not part of the queue module. Test Impact already says to create `tests/unit/test_agent_session.py` for this test. Remove the `create_local()` bullet from Task 4 to avoid test duplication in the wrong file.

---

### Third Pass — NEEDS REVISION (2026-04-07)

**Findings**: 4 total (1 blocker, 2 concerns, 1 nit)

#### Blocker

**`_pop_agent_session_with_fallback()` sync path is NOT covered by the lock in `_pop_agent_session()`**

The plan states the fallback path is "automatically covered" because `_pop_agent_session_with_fallback()` calls `_pop_agent_session()`. This is true for the hot path (line 625). However, the sync fallback branch (lines 629–731) performs an independent `AgentSession.query.filter()` + `transition_status()` entirely outside the lock. Two concurrent sync fallback executions for the same `chat_id` can race identically to the original bug.

Fix: Add the same `setnx worker:pop_lock:{chat_id}` acquisition at the top of the sync fallback branch in `_pop_agent_session_with_fallback()`. Since the two branches are mutually exclusive within a single call, re-entrancy is not an issue.

#### Concerns

1. **`_run_worker()` is in `worker/__main__.py`, not `agent_session_queue.py`** — Task 2 says initialize the semaphore in `_run_worker()` but doesn't clarify the file. Builder needs explicit guidance to initialize in `worker/__main__.py` and set into `agent_session_queue` module before calling `_ensure_worker()`.

2. **Task 5 missing `redis_test_db` fixture reference** — `tests/integration/test_agent_session_queue_race.py` contains the required test infrastructure. Task 5 should reference this file to prevent incompatible test setup.

#### Nit

`MAX_CONCURRENT_SESSIONS=0` minimum-clamp is in Failure Path section but not in Task 2 bullet list.

---

## Open Questions

1. ~~Should `MAX_CONCURRENT_SESSIONS` apply only to PM/Dev sessions or also to child sessions spawned by the agent tool?~~ **Resolved:** Yes — all sessions go through `_execute_agent_session`, so the semaphore applies equally to PM, Dev, Teammate, and child sessions. No special-casing needed.
2. ~~Should the Redis pop lock use a project-scoped key (`worker:pop_lock:{project_key}:{chat_id}`) to avoid cross-project interference?~~ **Resolved:** Use `worker:pop_lock:{chat_id}` (global, not project-scoped). `chat_id` values are already distinct between projects (each project uses a different Telegram chat ID or CLI session UUID), so project-scoped keys would add complexity without benefit.
