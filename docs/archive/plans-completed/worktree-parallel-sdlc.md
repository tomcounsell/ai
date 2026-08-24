---
status: Shipped
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-04-15
tracking: https://github.com/tomcounsell/ai/issues/973
last_comment_id:
revision_applied: true
---

# Worktree-Parallel SDLC: Concurrent Pipelines with Redis Key Namespacing

## Problem

The SDLC pipeline processes one feature at a time despite having worktree isolation infrastructure in place. When multiple GitHub issues are ready to build, the second waits behind the first — even if they touch completely separate code paths. A developer queue of three slugged issues takes 3× as long as it needs to.

**Current behavior:**
- Slugged dev sessions already route to chat-keyed `_worker_loop` instances (parallel-capable), but there is no separate concurrency cap for dev sessions — only a global `MAX_CONCURRENT_SESSIONS=3` that counts PM, teammate, and dev sessions together.
- No Redis key namespacing exists per slug. SDLC pipeline state (`stage_states` via `PipelineStateMachine`), SDLC session tracking (`sdlc-local-{issue}`), and job playlist keys (`job_playlist:{project_key}`) are all project-scoped. Two concurrent pipelines share the same namespace and can observe each other's intermediate state.
- The dashboard shows all sessions by type but has no "concurrent dev session count" or per-slug pipeline visibility widget.

**Desired outcome:**
- Opt-in parallel mode: `MAX_CONCURRENT_DEV_SESSIONS` (default 1, preserving current behavior) allows 2–3 slugged dev sessions to run concurrently.
- SDLC pipeline state for each slug is namespaced so concurrent pipelines cannot contaminate each other.
- Dashboard surfaces the concurrent dev session count so operators can see utilization at a glance.

## Freshness Check

**Baseline commit:** `7fa273991aecc94d45694f955391797a6c42cb65`
**Issue filed at:** 2026-04-15T00:27:26Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `agent/worktree_manager.py` — worktree creation and isolation logic — unchanged since issue filed
- `worker/__main__.py:180` — `MAX_CONCURRENT_SESSIONS` env var with default 3 — still present at line 180
- `agent/agent_session_queue.py:2059` — `_global_session_semaphore: asyncio.Semaphore | None = None` — still present
- `models/agent_session.py:262` — `worker_key` property routing slugged dev sessions by `chat_id` — unchanged

**Cited sibling issues/PRs re-checked:**
- #620 — closed 2026-04-15 (same day as issue). Roadmap decomposed; this issue is one of the sub-issues extracted from it.
- #810 — closed 2026-04-07. Bug: worker parallelism lacking global cap. Fixed by PR #832.
- #832 — merged 2026-04-08. Added `worker_key` routing and global semaphore. Foundation for this work.

**Commits on main since issue was filed (touching referenced files):**
- `86bc145d` — chore(reflections): merges health gates — irrelevant to this feature
- `d4501d0c` — fix: startup recovery not hijacking local CLI sessions — irrelevant
- `8fb755a3` — fix: SDLC pipeline continuation race — irrelevant
- `37d8b9cd` — fix(harness): retry on FileNotFoundError — irrelevant
- `c1b30c8d` — fix(harness): add session continuity via --resume — irrelevant

**Active plans in `docs/plans/` overlapping this area:** `sdlc_job_playlist.md` — overlaps on the job scheduling concept but explicitly deferred parallel SDLC as out of scope. No conflict.

**Notes:** The issue text says "worker only runs one dev session at a time" — recon found this is slightly imprecise. Slugged dev sessions CAN run in parallel today (they get chat-keyed workers). The gap is the missing per-type cap and slug-scoped Redis namespacing, not the worker routing itself.

## Prior Art

- **Issue #810** (closed 2026-04-07): Bug report — worker ran sessions in parallel without a global cap. Identified TOCTOU race in `_pop_agent_session` and lack of a global ceiling. Fixed by PR #832. This work builds directly on that foundation.
- **PR #832** (merged 2026-04-08): Added `AgentSession.worker_key` computed property, project-keyed worker serialization for PM sessions, and `MAX_CONCURRENT_SESSIONS` global semaphore. The routing infrastructure for per-slug parallelism already exists — this feature adds the dev-session-specific cap and Redis namespacing layer.
- **`sdlc_job_playlist.md`**: Explicitly called out "Parallel SDLC runs from playlist" as a future concern, not in scope. This plan delivers that future concern.

## Research

No relevant external findings — this is purely internal. The concurrency and Redis namespacing patterns are already established in the codebase (semaphore in `agent_session_queue.py`, key namespacing convention follows existing `worker:pop_lock:{worker_key}` patterns).

## Spike Results

### spike-1: Does `PipelineStateMachine` store state by project or by slug?

- **Assumption**: Pipeline state is per-session (session owns stage_states), so concurrent pipelines with different sessions are naturally isolated.
- **Method**: code-read
- **Finding**: `stage_states` is a property on `AgentSession` backed by `session_events` (a per-record list field). Each dev session has its own `stage_states` — no shared state between sessions. The cross-contamination risk is at the **SDLC session lookup layer** (`sdlc_session_ensure`, `sdlc_stage_query`), not the storage layer. When two concurrent pipelines for different slugs run under the same `project_key`, the `find_session_by_issue` helper could return the wrong session if both pipelines are triggered from the same issue number.
- **Confidence**: high
- **Impact on plan**: Redis namespace isolation is needed at the SDLC session lookup key (issue-number → session mapping), not at the `stage_states` storage level. The fix is narrower than the issue implied.

### spike-2: What Redis keys are shared across concurrent pipelines?

- **Assumption**: Multiple Redis keys are shared and need namespacing.
- **Method**: code-read
- **Finding**: Shared keys that could contaminate:
  1. `sdlc-local-{issue_number}` — SDLC local session lookup (in `sdlc_session_ensure.py`). If two pipelines for the same issue run concurrently, they'd share this session. In practice, concurrent pipelines for the same issue number shouldn't happen — each issue has one active pipeline. This is not a contamination risk.
  2. `worker:pop_lock:{worker_key}` — Pop lock already namespaced by `worker_key` (which includes chat_id for slugged sessions). Already safe.
  3. `job_playlist:{project_key}` — Playlist keys are project-scoped. Concurrent pipelines pop from the same list, which is intentional sequential behavior. Not a contamination risk.
  4. `AgentSession` records — Unique per session, never shared. Safe.
- **Confidence**: high
- **Impact on plan**: The "Redis key namespacing per slug" requirement from the issue is **largely already satisfied** by the existing `worker_key` routing (slugged dev sessions use `chat_id`-keyed workers, not `project_key`-keyed ones). The remaining work is: (a) add `MAX_CONCURRENT_DEV_SESSIONS` as a separate cap, and (b) add a dashboard counter. No deep Redis namespacing refactor is needed.

## Data Flow

1. **Operator sets** `MAX_CONCURRENT_DEV_SESSIONS=2` in `.env`
2. **Worker startup** (`worker/__main__.py:_run_worker`) reads the new env var and initializes a dedicated `_dev_session_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DEV_SESSIONS)` alongside the existing global semaphore.
3. **Session enqueued** via `valor_session create --role dev --slug feat-A`
4. **`_worker_loop`** pops the session; if it is a slugged dev session, releases the global semaphore slot, acquires the `_dev_session_semaphore` slot (waiting if at cap), then re-acquires the global slot. This ensures PM/teammate sessions are not starved while dev sessions wait for a dev slot.
5. **Session executes** in its worktree (`.worktrees/feat-A/`), isolated by the existing `worktree_manager`
6. **Second pipeline** for `feat-B` can start immediately if `MAX_CONCURRENT_DEV_SESSIONS >= 2`
7. **Dashboard** reads `_dev_session_semaphore._value` vs capacity and renders "Dev sessions: N/M running" in `dashboard.json` under `health`

## Architectural Impact

- **New env var**: `MAX_CONCURRENT_DEV_SESSIONS` (int, default 1). Additive — no breaking change.
- **New module-level semaphore**: `_dev_session_semaphore` in `agent_session_queue.py`, alongside existing `_global_session_semaphore`. Only acquired for slugged dev sessions.
- **Interface changes**: `_worker_loop` gains a second optional semaphore acquire path (conditioned on `session.session_type == DEV and session.slug`). No external API changes.
- **Coupling**: Minimal. The new semaphore is internal to `agent_session_queue.py`. Worker startup writes it; `_worker_loop` reads it.
- **Reversibility**: High. Setting `MAX_CONCURRENT_DEV_SESSIONS=1` restores sequential behavior. The new code path is only exercised when the value exceeds 1.
- **Global slot hold during dev semaphore wait**: The dev semaphore is acquired AFTER the global semaphore and AFTER `_pop_agent_session` (when the session type is known). While a slugged dev session waits for a dev semaphore slot to open, it holds a global semaphore slot. This effectively reduces the global cap by the number of slugged dev sessions blocked on the dev semaphore, potentially starving PM/teammate sessions. **Mitigation**: Release the global semaphore slot before `await _dev_session_semaphore.acquire()`, then immediately re-acquire it after. Follow the existing acquire/release pattern at `agent/agent_session_queue.py:2359-2375`. The re-acquire must be wrapped in its own `try/except BaseException` matching the existing pattern at lines 2366-2369.

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (scope alignment — confirm spike findings, proceed with narrow approach)
- Review rounds: 1 (code review of semaphore addition and dashboard change)

## Prerequisites

No prerequisites — this work has no external dependencies. It builds on the `MAX_CONCURRENT_SESSIONS` infrastructure already in place.

## Solution

### Key Elements

- **`_dev_session_semaphore`**: A new `asyncio.Semaphore` in `agent_session_queue.py`, initialized from `MAX_CONCURRENT_DEV_SESSIONS` env var (default 1). Acquired in `_worker_loop` only for slugged dev sessions, released when session execution ends.
- **`_run_worker` initialization**: Reads `MAX_CONCURRENT_DEV_SESSIONS` and assigns `_dev_session_semaphore` before any workers start (mirrors how `_global_session_semaphore` is initialized).
- **Dashboard counter**: `dashboard.json` `health` object gains `dev_sessions_running` and `dev_sessions_cap` fields, derived from `_dev_session_semaphore`.

### Flow

`.env MAX_CONCURRENT_DEV_SESSIONS=2` → worker startup initializes dev semaphore → slugged dev session enqueued → `_worker_loop` acquires global slot + dev slot → session runs in `.worktrees/slug/` → second slugged dev session acquires its own global + dev slots and runs concurrently → dashboard shows "dev: 2/2 running"

### Technical Approach

1. Add `_dev_session_semaphore: asyncio.Semaphore | None = None` module-level in `agent_session_queue.py`, initialized to `None` (same sentinel pattern as `_global_session_semaphore`).
2. In `worker/__main__.py:_run_worker`, after the global semaphore is initialized, read `MAX_CONCURRENT_DEV_SESSIONS` (default 1) and assign `_queue._dev_session_semaphore = asyncio.Semaphore(max_dev)`.
3. In `_worker_loop`, AFTER `_pop_agent_session` returns and the session is confirmed as a slugged dev session (`session.session_type == DEV and session.slug`), and `_dev_session_semaphore is not None`: release the global semaphore (`semaphore.release(); _semaphore_acquired = False`), then `await _dev_session_semaphore.acquire(); _dev_semaphore_acquired = True`, then re-acquire the global semaphore (`await semaphore.acquire(); _semaphore_acquired = True`) inside a `try/except BaseException` block matching the pattern at lines 2366-2369. Initialize `_dev_semaphore_acquired = False` at the same location as `session_failed` and `finalized_by_execute`. Release in the `finally` block: `if _dev_semaphore_acquired and _dev_session_semaphore is not None: _dev_session_semaphore.release()`.
4. Export `_dev_session_semaphore` from `agent_session_queue` for dashboard access (already exported as module attr).
5. In `ui/app.py:dashboard_json`, import `_dev_session_semaphore` and include `dev_sessions_running` and `dev_sessions_cap` in the `health` response.
6. Update `docs/features/bridge-worker-architecture.md` with the new dev session cap.

## Failure Path Test Strategy

### Exception Handling Coverage
- The new semaphore acquire/release is wrapped in the same `try/finally` block as the existing global semaphore. If the acquire raises (unexpected — Semaphore.acquire only raises CancelledError), the session is not popped to `running` and the semaphore is released correctly.
- `if _dev_session_semaphore is not None` guard prevents errors when the worker hasn't initialized it (test environments).

### Empty/Invalid Input Handling
- `MAX_CONCURRENT_DEV_SESSIONS=0` clamped to minimum 1 (same pattern as `MAX_CONCURRENT_SESSIONS`).
- `MAX_CONCURRENT_DEV_SESSIONS` missing from env → defaults to 1 (sequential, backward-compatible).

### Error State Rendering
- Dashboard endpoint is wrapped in FastAPI's exception handling. If `_dev_session_semaphore` is `None` (worker not started), `dev_sessions_running` is returned as `null` — not an error, just "worker not running".

## Test Impact

- [ ] `tests/integration/test_worker_concurrency.py::TestDevWorktreeParallelism::test_two_slugged_dev_sessions_execute_concurrently` — UPDATE: currently asserts `peak_running == 2` with global semaphore at 5. After this change, the test must also set `_queue._dev_session_semaphore = asyncio.Semaphore(2)` in setup, otherwise the new dev semaphore would block the second session at default cap=1.
- [ ] `tests/integration/test_worker_concurrency.py::TestGlobalSemaphore::test_semaphore_limits_concurrent_sessions` — UPDATE: must set `_queue._dev_session_semaphore = asyncio.Semaphore(10)` in setup to prevent the new dev cap from interfering with the global semaphore test isolation.
- [ ] `tests/unit/test_worker_startup.py` — ADD: test that `MAX_CONCURRENT_DEV_SESSIONS=0` is clamped to minimum 1; test that `MAX_CONCURRENT_DEV_SESSIONS=3` initializes `_queue._dev_session_semaphore` with `._value == 3`. Use monkeypatch to set env var and call the relevant startup initialization path. Mirror the equivalent test pattern for `MAX_CONCURRENT_SESSIONS` if one exists.

## Rabbit Holes

- **Deep Redis key namespacing for all SDLC state**: Spike-2 confirmed the issue's framing is imprecise — the real gap is a per-type cap, not a broad namespacing refactor. Don't refactor `PipelineStateMachine` storage, `stage_states` keys, or playlist keys — they're not shared across pipelines.
- **Per-slug concurrency quotas**: Don't build slug-specific caps. One cap (`MAX_CONCURRENT_DEV_SESSIONS`) applies to all dev sessions uniformly.
- **Dynamic concurrency adjustment**: Don't build a runtime API to adjust the cap. Env var is sufficient; restart is required.
- **Multi-machine coordination**: The semaphore is in-process. Cross-machine parallelism coordination is not in scope.

## Risks

### Risk 1: Dev semaphore deadlock with global semaphore
**Impact:** Worker hangs — no sessions process.
**Mitigation:** The dev semaphore is acquired AFTER the global semaphore, never before. `MAX_CONCURRENT_DEV_SESSIONS` must be ≤ `MAX_CONCURRENT_SESSIONS` for sensible semantics; document this constraint. If inverted (dev cap > global cap), the global cap is the binding constraint — no deadlock, just the dev cap is effectively unused.

### Risk 2: Semaphore leak if session type detection fails
**Impact:** Dev semaphore slot never released; cap exhausted after N sessions.
**Mitigation:** Track `_dev_semaphore_acquired` boolean in `_worker_loop`. Release in `finally` block, same pattern as `_semaphore_acquired` for the global semaphore. Test with an exception mid-execution to verify the finally block releases.

### Risk 3: Dashboard reading None semaphore
**Impact:** `dashboard.json` endpoint 500s if worker hasn't started.
**Mitigation:** Check `if _dev_session_semaphore is not None` before reading `._value`. Return `null` values for `dev_sessions_running` and `dev_sessions_cap` when worker hasn't initialized. No 500.

### Risk 4: Global semaphore slot held while awaiting dev semaphore (session starvation)
**Impact:** A slugged dev session blocked on the dev semaphore holds a global semaphore slot, preventing PM or teammate sessions from starting. With `MAX_CONCURRENT_SESSIONS=3` and two slugged dev sessions waiting on a full dev semaphore (cap=1), all three global slots could be consumed — no PM sessions can run.
**Mitigation:** Release the global semaphore slot before awaiting the dev semaphore, then re-acquire it immediately after. Concretely: `semaphore.release(); _semaphore_acquired = False` before `await _dev_session_semaphore.acquire()`, then `await semaphore.acquire(); _semaphore_acquired = True` after. The re-acquire must be wrapped in `try/except BaseException` matching the existing pattern at `agent/agent_session_queue.py:2366-2369`. This ensures only the dev semaphore slot is held during the wait — global capacity is freed for PM/teammate sessions.

## Race Conditions

### Race 1: Two workers both detect `_dev_session_semaphore` has a slot and acquire it
**Location:** `agent/agent_session_queue.py` — `_worker_loop` semaphore acquire path
**Trigger:** Two concurrent `_worker_loop` tasks for different `chat_id`s both reach the `await _dev_session_semaphore.acquire()` call simultaneously.
**Data prerequisite:** Both sessions must be slugged dev sessions with `session.slug` set.
**State prerequisite:** `_dev_session_semaphore` has ≥ 2 available slots.
**Mitigation:** `asyncio.Semaphore.acquire()` is cooperative and safe for concurrent `await`s — this is the intended use case. Two tasks awaiting the same semaphore are properly serialized by the event loop when only 1 slot is available. No additional locking needed.

### Race 2: Worker reads `_dev_session_semaphore._value` for dashboard while session acquires it
**Location:** `ui/app.py` — `dashboard_json` endpoint vs `_worker_loop`
**Trigger:** Dashboard reads `_value` mid-acquisition.
**Mitigation:** `asyncio.Semaphore._value` is an integer; reads are atomic in CPython (GIL). The dashboard may show a momentarily stale count but will never corrupt or crash.

## No-Gos (Out of Scope)

- Redis key namespacing refactor — spike-2 confirmed it's not needed
- Concurrency support for PM sessions — PM sessions serialize by design (git safety)
- Teammate session concurrency cap — teammate sessions are already parallel-safe with no cap needed
- Cross-machine semaphore coordination (Redis-backed distributed lock) — out of scope
- Runtime cap adjustment without restart
- Per-pipeline port allocation — not relevant to this codebase

## Update System

No update system changes required — `MAX_CONCURRENT_DEV_SESSIONS` is an optional env var with a default of 1. It is additive and backward-compatible. The env var can be added to `~/Desktop/Valor/.env` on each machine independently; the update script does not need changes.

## Agent Integration

No agent integration required — this is a worker-internal change. The agent does not call the dev session semaphore directly; it creates sessions via `valor_session create`, which the worker then executes. No MCP server changes, no `.mcp.json` changes, no bridge changes.

## Documentation

- [ ] Update `docs/features/bridge-worker-architecture.md` — add "Dev Session Concurrency Cap" section documenting `MAX_CONCURRENT_DEV_SESSIONS`, the dev semaphore, and how it interacts with the global semaphore.
- [ ] Add entry to `docs/features/README.md` index table for the updated bridge-worker-architecture doc (if the feature deserves its own row; otherwise update the existing row).

## Success Criteria

- [ ] `MAX_CONCURRENT_DEV_SESSIONS=2` in `.env` allows two slugged dev sessions to run concurrently; a third waits until one finishes.
- [ ] `MAX_CONCURRENT_DEV_SESSIONS=1` (default) preserves strictly sequential dev session execution — zero behavioral change from current.
- [ ] PM sessions are unaffected by the dev semaphore — they serialize by project_key as before.
- [ ] `dashboard.json` `health` object includes `dev_sessions_running` and `dev_sessions_cap` fields.
- [ ] `tests/integration/test_worker_concurrency.py` all pass, including the updated `TestDevWorktreeParallelism` test.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (worker-concurrency)**
  - Name: concurrency-builder
  - Role: Add `_dev_session_semaphore` to `agent_session_queue.py`, initialize in `worker/__main__.py`, and add dashboard fields to `ui/app.py`
  - Agent Type: async-specialist
  - Resume: true

- **Validator (concurrency)**
  - Name: concurrency-validator
  - Role: Verify semaphore behavior, test correctness, and dashboard field accuracy
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: feature-documentarian
  - Role: Update `docs/features/bridge-worker-architecture.md` and README index
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

See PLAN_TEMPLATE.md for full list.

## Step by Step Tasks

### 1. Add dev session semaphore to worker queue

- **Task ID**: build-dev-semaphore
- **Depends On**: none
- **Validates**: `tests/integration/test_worker_concurrency.py::TestDevWorktreeParallelism`
- **Assigned To**: concurrency-builder
- **Agent Type**: async-specialist
- **Parallel**: true
- Add `_dev_session_semaphore: asyncio.Semaphore | None = None` module-level to `agent/agent_session_queue.py` (beside `_global_session_semaphore`)
- In `worker/__main__.py:_run_worker`, after global semaphore initialization, read `MAX_CONCURRENT_DEV_SESSIONS` (default 1, clamp to min 1) and assign `_queue._dev_session_semaphore = asyncio.Semaphore(max_dev)`. Log the cap value.
- In `_worker_loop`, AFTER `_pop_agent_session` returns a confirmed slugged dev session (i.e., after the `if session is None: ... continue` guard, where `session.session_type == SessionType.DEV and session.slug` is known to be true): initialize `_dev_semaphore_acquired = False` alongside `session_failed` and `finalized_by_execute` flags; release the global semaphore (`semaphore.release(); _semaphore_acquired = False`); then `await _dev_session_semaphore.acquire(); _dev_semaphore_acquired = True`; then immediately re-acquire the global semaphore (`await semaphore.acquire(); _semaphore_acquired = True`) using a `try/except BaseException` block matching the pattern at `agent/agent_session_queue.py:2366-2369`. This prevents PM/teammate session starvation while a dev session waits for a dev slot.
- In the `_worker_loop` `finally` block, add: `if _dev_semaphore_acquired and _dev_session_semaphore is not None: _dev_session_semaphore.release()`.
- Update `TestDevWorktreeParallelism` in `tests/integration/test_worker_concurrency.py` to set `_queue._dev_session_semaphore = asyncio.Semaphore(2)` in setup and restore `None` in teardown.
- Update `TestGlobalSemaphore` tests to set `_queue._dev_session_semaphore = asyncio.Semaphore(10)` (high value) so global semaphore tests are not affected by the new dev cap.

### 2. Add dev session metrics to dashboard

- **Task ID**: build-dashboard
- **Depends On**: build-dev-semaphore
- **Validates**: `curl -s localhost:8500/dashboard.json | python -m json.tool` (manual verification during build)
- **Assigned To**: concurrency-builder
- **Agent Type**: builder
- **Parallel**: false
- In `ui/app.py:dashboard_json`, import `_dev_session_semaphore` and `_global_session_semaphore` from `agent.agent_session_queue`.
- Add to the `health` dict: `"dev_sessions_running"` (cap - semaphore._value if not None, else null) and `"dev_sessions_cap"` (cap if not None, else null).
- Guard both reads with `if _dev_session_semaphore is not None` to avoid AttributeError when worker isn't started.

### 3. Validate concurrency behavior

- **Task ID**: validate-concurrency
- **Depends On**: build-dev-semaphore, build-dashboard
- **Assigned To**: concurrency-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/integration/test_worker_concurrency.py -v` — all tests must pass
- Run `pytest tests/unit/test_worker_startup.py -v` — all tests must pass
- Verify `dashboard.json` health object includes `dev_sessions_running` and `dev_sessions_cap` fields
- Confirm `MAX_CONCURRENT_DEV_SESSIONS=1` (default) results in sequential dev session execution via existing `test_three_sessions_same_chat_id_execute_serially`

### 4. Documentation

- **Task ID**: document-feature
- **Depends On**: validate-concurrency
- **Assigned To**: feature-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/bridge-worker-architecture.md` — add "Dev Session Concurrency Cap" section
- Verify `docs/features/README.md` index has the updated feature listed

### 5. Final Validation

- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: concurrency-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/ -x -q` — all tests pass
- Run `python -m ruff check .` — lint clean
- Run `python -m ruff format --check .` — format clean
- Verify all Success Criteria checked

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Dev semaphore initialized | `grep -n "_dev_session_semaphore" agent/agent_session_queue.py` | output > 0 |
| Dashboard fields present | `grep -n "dev_sessions_running" ui/app.py` | output > 0 |
| Max dev sessions clamped | `grep -n "MAX_CONCURRENT_DEV_SESSIONS" worker/__main__.py` | output > 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Operator, Adversary | Global semaphore held while awaiting dev semaphore causes PM/teammate session starvation | Risk 4 (Risks section), Task 1 (updated procedure), Architectural Impact bullet | Release global slot before `await _dev_session_semaphore.acquire()`, re-acquire after; pattern at `agent_session_queue.py:2366-2369` |
| CONCERN | Skeptic | Task 1 says "before `_pop_agent_session`" but session type is only known after pop — contradictory | Task 1 description corrected to "after `_pop_agent_session` returns a confirmed slugged dev session" | `_dev_semaphore_acquired = False` initialized alongside `session_failed` and `finalized_by_execute` |
| CONCERN | Skeptic | `test_worker_startup.py` listed in Task 1 Validates field but has no dev semaphore tests | Task 1 Validates field updated (file removed); Test Impact ADD entry added for `test_worker_startup.py` | New test: `MAX_CONCURRENT_DEV_SESSIONS=0` clamped to 1; semaphore initialized with correct cap |
| NIT | Simplifier | Team Orchestration section duplicates task info for a solo/medium plan | Acknowledged; left in place for template compliance | No action required |

---

## Open Questions

None — spike findings narrowed the scope significantly. The issue's "Redis key namespacing" requirement is satisfied by existing `worker_key` routing. The plan is ready for critique.
