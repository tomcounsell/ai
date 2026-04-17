---
status: docs_complete
type: chore
appetite: Small
owner: Valor Engels
created: 2026-04-17
tracking: https://github.com/tomcounsell/ai/issues/1021
last_comment_id:
---

# Session Concurrency Collapse — Single `MAX_CONCURRENT_SESSIONS=8` Cap

## Problem

The worker (`worker/__main__.py`) currently enforces **two** concurrency ceilings:

| Variable | Default | Scope |
|---|---|---|
| `MAX_CONCURRENT_SESSIONS` | `3` | All session types (global cap) |
| `MAX_CONCURRENT_DEV_SESSIONS` | `1` | Dev sessions only (secondary cap) |

The secondary cap exists to work around a deadlock where PM sessions could hold all global slots while waiting for child dev sessions that could never acquire one. The workaround is the **swap trick** (`agent/agent_session_queue.py:2625-2652`): a dev session releases its global slot, waits for a dev-semaphore slot, then re-acquires the global slot before executing.

**Current behavior:**
The worker has two semaphores, a non-trivial acquire/release sequence, two env vars, two dashboard metrics, and extra test scaffolding — all to solve a deadlock that #1004 already fixed via two other mechanisms:
1. **Child-boost ordering** (`agent/agent_session_queue.py:773-799`) — children of `waiting_for_children` parents sort before peers at the same priority tier, so they are popped first.
2. **Force-deliver on `waiting_for_children`** (`agent/output_router.py:109`) — a PM session in this state must deliver (not nudge) so it exits cleanly and releases its global slot.

These mechanisms guarantee that PM sessions release their global slot when they spawn a child, and children sort to the front of the queue. The swap trick is now **redundant complexity** on top of a fix that already works.

**Desired outcome:**
One env var, one semaphore, no swap logic. `MAX_CONCURRENT_SESSIONS=8` — enough for four simultaneous work items at peak (1 PM + 1 dev each, though peak is 1 at a time per pipeline because PM pauses when its dev spawns).

## Freshness Check

**Baseline commit:** `d47d5a81`
**Issue filed at:** 2026-04-17T05:29:02Z (today)
**Disposition:** Unchanged

**File:line references re-verified:**
- `worker/__main__.py:186-192` — `MAX_CONCURRENT_DEV_SESSIONS` init block — still holds exactly.
- `agent/agent_session_queue.py:2625-2652` — swap trick — still holds exactly.
- `agent/agent_session_queue.py:2813-2816` — dev semaphore release in `finally` — still holds exactly.
- `agent/agent_session_queue.py:773-799` — child-boost ordering — still holds.
- `agent/output_router.py:109` — force-deliver on `waiting_for_children` — still holds.
- `tests/unit/test_worker_startup.py:89-138` — `TestDevSessionSemaphoreInit` — still holds.
- `.env.example:47` — `# MAX_CONCURRENT_SESSIONS=3` — confirmed; no `MAX_CONCURRENT_DEV_SESSIONS` entry present.

**Cited sibling issues/PRs re-checked:**
- `#1004` — CLOSED 2026-04-16; deadlock fix merged; its mechanisms confirmed in code.
- `#810` — CLOSED 2026-04-07; global semaphore introduced by merged PR #832.
- `#973` — CLOSED 2026-04-16 (surfaced by prior-art search); introduced `MAX_CONCURRENT_DEV_SESSIONS`.

**Commits on main since issue was filed (touching referenced files):**
- None. `git log --since=<issue_createdAt>` returned zero commits for any referenced file.

**Active plans in `docs/plans/` overlapping this area:** None. Checked `worker_lifecycle_cleanup.md` and `pm-persona-hardening.md` (the two most recent); no concurrency overlap.

**Notes:** Recon surfaced two references not cited in the issue body: `ui/app.py:305-326` exposes BOTH `dev_sessions_running` AND `dev_sessions_cap` (AC only mentions the first), and `docs/features/bridge-worker-architecture.md:229-253` + `docs/features/README.md:26` contain `MAX_CONCURRENT_DEV_SESSIONS` documentation that must be updated. These are addressed in the Technical Approach and Test Impact sections.

## Prior Art

- **Issue #973 / PR that shipped #973** (closed 2026-04-16): Introduced `MAX_CONCURRENT_DEV_SESSIONS`, `_dev_session_semaphore`, the swap trick, the dashboard metric, and `TestDevSessionSemaphoreInit`. This is the code being removed.
- **Issue #1004 / fix-pm-dev-deadlock branch** (closed 2026-04-16): Added child-boost ordering and force-deliver on `waiting_for_children`. These mechanisms obsolete the swap trick — they are the reason this chore is safe.
- **Issue #810** (closed 2026-04-07): Introduced `MAX_CONCURRENT_SESSIONS` via PR #832. This plan keeps that env var and raises its default from `3` to `8`.

## Research

No relevant external findings — this is a pure internal refactor (removing code, no external APIs or ecosystem patterns involved). Proceeding with codebase context.

## Data Flow

The change affects one data path: **a slugged dev session acquiring a worker slot.**

**Before:**
1. `_worker_loop` calls `_pop_agent_session` → pops a dev session off the queue
2. Global semaphore already held (acquired before the pop)
3. If `session_type == DEV and session.slug`: **release global, acquire dev, re-acquire global** (swap trick)
4. `_execute_agent_session(session)`
5. Finally: release dev, release global

**After:**
1. `_worker_loop` calls `_pop_agent_session` → pops a dev session off the queue
2. Global semaphore already held
3. *(no swap — proceed directly)*
4. `_execute_agent_session(session)`
5. Finally: release global

**PM deadlock prevention** (unchanged, from #1004):
1. PM session spawns child dev session via `valor_session create`
2. PM transitions to `waiting_for_children`
3. `output_router.route_session_output` returns `"deliver"` for this status (line 109)
4. PM finalizes and releases its global slot
5. Child dev session's `sort_key` uses `child_boost=0` (line 796-798), placing it at the front of the eligible queue
6. Next `_pop_agent_session` call in any worker picks up the child dev session first
7. Child dev session acquires the now-free global slot

The deadlock cannot recur because step 4 releases the PM's slot **before** the child needs it, and step 5 guarantees the child jumps the queue.

## Architectural Impact

- **New dependencies**: None removed, none added. Pure deletion.
- **Interface changes**: None public. `_queue._dev_session_semaphore` and `_queue._dev_session_semaphore_cap` are module-private. Removing them affects only internal callers (the worker and the dashboard endpoint).
- **Coupling**: Decreases. Removes one dimension of coupling between `worker/__main__.py` ↔ `agent/agent_session_queue.py` ↔ `ui/app.py` ↔ test files.
- **Data ownership**: Unchanged.
- **Reversibility**: High. Re-introducing the dev semaphore would revert to #973's design. However, since #1004 is the canonical solution, reversion is unlikely.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (scope is tightly bounded by recon)
- Review rounds: 1 (standard PR review)

This is a mechanical code removal informed by recon. The hardest decision — whether the removal is safe — is already answered by #1004 being merged and its mechanisms verified in code.

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **Global semaphore (kept, default raised)**: `_global_session_semaphore` in `agent/agent_session_queue.py`, initialized from `MAX_CONCURRENT_SESSIONS` env var. Default raised from `3` to `8`.
- **Dev semaphore (removed)**: `_dev_session_semaphore` and `_dev_session_semaphore_cap` module attributes deleted from `agent/agent_session_queue.py`.
- **Swap trick (removed)**: Lines 2625-2652 in `_worker_loop` deleted. The `_dev_semaphore_acquired` local flag and its `finally`-block release at lines 2813-2816 deleted.
- **Worker startup (simplified)**: Lines 186-192 in `worker/__main__.py` deleted. Global default at line 180 updated from `"3"` to `"8"`.
- **Dashboard (cleaned)**: `ui/app.py:305-326` — remove `_dev_sem`, `_dev_sem_cap`, `dev_sessions_running`, and `dev_sessions_cap` from the computation block and the JSON response.
- **Tests (updated)**: Delete `TestDevSessionSemaphoreInit` from `test_worker_startup.py`. Update two tests in `test_worker_concurrency.py` to stop patching the removed attributes.
- **.env.example (cleaned)**: Update the `MAX_CONCURRENT_SESSIONS` comment to reflect the new default `8`.
- **Docs (cleaned)**: Remove the "Dev Session Concurrency Cap" subsection from `docs/features/bridge-worker-architecture.md`. Update the global section's default. Update the index entry in `docs/features/README.md`.

### Flow

Worker starts → reads `MAX_CONCURRENT_SESSIONS` (default 8) → creates single semaphore → each session (PM, teammate, dev) acquires one global slot → PM spawns child, transitions to `waiting_for_children`, delivers and releases its slot → child dev session (boosted by #1004's sort key) acquires the freed slot → executes.

### Technical Approach

- **Single semaphore**: One `asyncio.Semaphore(MAX_CONCURRENT_SESSIONS)` governs all session execution. No session type exemptions.
- **Default raised to 8**: The issue's rationale (4 simultaneous work items × 2 slots peak) is the cap's operational budget. In practice, peak-per-pipeline is ≤1 because PM releases before child runs — but 8 still allows 4 pipelines in their PM phase plus 4 children to queue.
- **Deletions, not refactors**: Every change is a deletion. No code is moved, no logic is rewritten. This minimizes risk and keeps the diff mechanically reviewable.
- **Dashboard fields removed, not replaced**: The issue's AC says "removed or replaced with a simple counter." Replacement would require new logic (what counter? global running count is already derivable from `len(AgentSession.query.filter(status="running"))` if needed). **Decision: remove only.** No replacement. If a dev-specific metric is needed later, it can be added via a separate issue.
- **Tests deleted, not merely updated where the tested behavior is gone**: `TestDevSessionSemaphoreInit` tests a removed init block — `DELETE`. The two `test_worker_concurrency.py` tests still exercise the global semaphore — `UPDATE` (remove `_dev_session_semaphore` patches).

## Failure Path Test Strategy

### Exception Handling Coverage
- [x] The removed swap trick includes a `try/except BaseException` (lines 2645-2652). After removal, no exception handler exists in the scope of this work. Its absence is not a failure path because the code it guarded is gone — the operation it guarded (re-acquiring the global slot after dev slot was acquired) no longer happens.
- [x] No `except Exception: pass` blocks introduced.

### Empty/Invalid Input Handling
- [x] `MAX_CONCURRENT_SESSIONS=0` continues to be clamped to minimum 1 via `max(1, ...)` at `worker/__main__.py:180`. Verified still present after this change.
- [x] `MAX_CONCURRENT_SESSIONS` missing from env continues to default (new default `8`).
- [x] No new input surfaces introduced.

### Error State Rendering
- [x] Dashboard fields `dev_sessions_running` and `dev_sessions_cap` removed from `/dashboard.json`. Any external consumer reading these fields will get `KeyError` on access — **this is intended** since the fields no longer have meaning. The issue's acceptance criteria explicitly permits removal.
- [x] No user-visible output on the happy path is changed.

## Test Impact

- [x] `tests/unit/test_worker_startup.py::TestDevSessionSemaphoreInit` (class, lines 89-138) — **DELETE**: the entire class tests initialization of a removed attribute. Includes three methods: `test_zero_clamped_to_one`, `test_three_initializes_with_cap_three`, `test_default_is_one_when_env_not_set`.
- [x] `tests/integration/test_worker_concurrency.py::test_semaphore_limits_concurrent_sessions` (lines 120-180) — **UPDATE**: remove the `original_dev_semaphore = _queue._dev_session_semaphore` save (line 130), the `_queue._dev_session_semaphore = asyncio.Semaphore(10)` assignment (line 136), and the restore at line 175. The test's actual assertion (global semaphore limits concurrent sessions) remains unchanged.
- [x] `tests/integration/test_worker_concurrency.py` (lines 390-431, method name `test_two_slugged_dev_sessions_execute_concurrently` or similar) — **UPDATE**: remove the `original_dev_semaphore` / `original_dev_semaphore_cap` saves (lines 402-403), the `_queue._dev_session_semaphore = asyncio.Semaphore(2)` / `_queue._dev_session_semaphore_cap = 2` assignments (lines 407-408), and the restore block (lines 425-426). The test's assertion that two slugged dev sessions run in parallel now relies purely on the global semaphore being set to 5 at line 405 — with `MAX_CONCURRENT_DEV_SESSIONS` gone, two dev sessions with a global cap of 5 will run in parallel automatically. Verify the assertion still passes (`peak_running == 2`).
- [x] No new tests required. The change is a removal; existing coverage of the global semaphore (`test_semaphore_limits_concurrent_sessions`) verifies the single-semaphore invariant. The deadlock prevention is covered by #1004's tests, which remain in place.

## Rabbit Holes

- **Don't introduce a new dashboard counter for dev sessions**: The issue's AC permits "removed or replaced" — choose removal. Replacement invites bikeshedding about what the counter should count (running dev sessions? pending dev sessions? by-worktree breakdown?). Defer to a separate issue if the metric proves useful.
- **Don't re-evaluate the default value of 8**: The issue specifies `8`. Don't debate 4 vs 6 vs 8 vs 10. The PR that revisits the default can reference this one.
- **Don't refactor the semaphore acquire/release pattern**: Only *remove* the dev-semaphore path. The global-semaphore acquire/release pattern (at `_worker_loop` start, and in the `finally` block) is unchanged.
- **Don't touch `agent/output_router.py` line 109**: Force-deliver on `waiting_for_children` is load-bearing for the deadlock-prevention contract. Leave it.
- **Don't touch the child-boost logic at `agent_session_queue.py:773-799`**: Same reason.
- **Don't touch archived plans**: `docs/plans/worktree-parallel-sdlc.md` is a historical record of the feature being removed. Archived plans are immutable.
- **Don't manually close #973**: That issue is already closed. Don't re-open and re-close it. The chore's connection to #973 is informational, not action-bearing.

## Risks

### Risk 1: Deadlock reappears if #1004's mechanisms are ever rolled back
**Impact:** Removing the swap trick removes the last safety net. If `agent/output_router.py:109` or the child-boost logic at `agent_session_queue.py:773-799` is ever reverted, the original #1004 deadlock returns immediately.
**Mitigation:** (a) Plan explicitly names both load-bearing code sites in the Rabbit Holes section. (b) Add an inline comment at the deletion site in `_worker_loop` linking to #1004 and stating the dependency. (c) Integration test `test_worker_concurrency.py` exercises the global semaphore under parallel dev sessions — if the deadlock mechanism breaks, this test will hang.

### Risk 2: Dashboard consumers break silently when `dev_sessions_running` / `dev_sessions_cap` disappear
**Impact:** Anyone (scripts, other services, tooling) reading `/dashboard.json` for these two fields will see `KeyError` or `None`.
**Mitigation:** Search for consumers. `grep -r "dev_sessions_running\|dev_sessions_cap" .` found only the dashboard producer (`ui/app.py`) and this plan. No external consumers. Acceptable break.

### Risk 3: Increased memory footprint from 8 parallel sessions vs 3
**Impact:** With cap raised from 3 → 8, peak memory could increase by ~2.6× if 8 sessions run simultaneously. Each Claude Agent SDK session holds a conversation history and tool state in memory.
**Mitigation:** (a) 8 is an upper bound — actual concurrency is limited by queue pressure, not the cap. (b) The PM→Dev handoff pattern means per-pipeline peak is ≤1 active session (PM pauses when dev runs). (c) The `CLAUDE.md` memory threshold table says "Warning: 600MB, Critical: 800MB" — Valor's machine has headroom. (d) If pressure manifests, operators can set `MAX_CONCURRENT_SESSIONS=5` in `.env` to override without code changes.

### Risk 4: A PM session created **without** `valor_session` infrastructure (edge case, direct-enqueue PMs) might not release its slot correctly
**Impact:** None identified. PM sessions created via any path (bridge auto-classify, `valor_session create`, direct Popoto instantiation) all go through the same `_worker_loop` → `_execute_agent_session` → `output_router.route_session_output` path. The `waiting_for_children` transition is driven by the session's own state, not by who created it.
**Mitigation:** No action needed; documented here for completeness.

## Race Conditions

### Race 1: Child dev session queued before PM releases its slot
**Location:** `agent/agent_session_queue.py:773-799` (child-boost logic) and `agent/agent_session_queue.py:_worker_loop` (semaphore release in `finally`).
**Trigger:** PM session A spawns child dev session B, but B is enqueued before A's `_worker_loop` enters its `finally` block and releases the global semaphore.
**Data prerequisite:** B's `parent_agent_session_id == A.id` must be set at the moment B is popped from the queue (set at B's creation, immutable thereafter). A's `status == "waiting_for_children"` must be visible to `_parent_waiting` lookup in `sort_key`.
**State prerequisite:** Global semaphore must reach `value > 0` before B can be popped and its `await semaphore.acquire()` completes. That requires A's `finally` block to have run.
**Mitigation:** This race is **solved by existing code**, unchanged by this plan:
  1. Child-boost ordering ensures B sorts ahead of peers when `_parent_waiting` contains A's ID — so when a slot opens, B is next.
  2. `route_session_output` returns `"deliver"` for `waiting_for_children` (output_router.py:109), guaranteeing A's `_execute_agent_session` returns normally → `finally` → `semaphore.release()`.
  3. The global semaphore is `asyncio.Semaphore` — `release()` followed by any waiter's `acquire()` is safe under the event loop.

This plan makes the race **more** robust, not less: removing the swap trick removes a concurrent acquire/release sequence (swap) that could interleave with A's release in complex ways. With one semaphore, the release→acquire sequence is trivially linearizable.

### Race 2: Worker reads `_dev_session_semaphore` while it's being removed (deployment race)
**Location:** `ui/app.py:306-307` and `agent/agent_session_queue.py` module scope.
**Trigger:** During the deployment of this change, if the dashboard process picks up the new `ui/app.py` before the `agent_session_queue.py` import is refreshed (or vice versa), the dashboard could try to read a removed attribute.
**Data prerequisite:** None for correctness; only for deployment.
**State prerequisite:** Partial deploy state where one module is new and the other is old.
**Mitigation:** Both modules deploy in the same git commit via the same `./scripts/valor-service.sh restart`. Partial deploy is not possible — the restart cycles all services together. If the deploy script changes (out of scope), this risk re-emerges.

## No-Gos (Out of Scope)

- **Not changing** `agent/output_router.py` — the force-deliver on `waiting_for_children` stays.
- **Not changing** the child-boost logic at `agent/agent_session_queue.py:773-799`.
- **Not changing** `models/session_lifecycle.py`.
- **Not renaming** `MAX_CONCURRENT_SESSIONS` or `_global_session_semaphore`.
- **Not introducing** new session-type-specific caps (no new semaphores). One cap, one semaphore.
- **Not adjusting** per-chat serialization (`worker_key` routing) — orthogonal concern.
- **Not adding** new dashboard metrics to replace the removed ones. If needed later, separate issue.
- **Not touching** the archived plan `docs/plans/worktree-parallel-sdlc.md`.
- **Not benchmarking** whether 8 is the "right" number. The issue specified 8; this plan ships 8.

## Update System

No update system changes required — this is a pure internal refactor. `MAX_CONCURRENT_SESSIONS` already exists in `.env.example`; its default changes but no new env var is introduced. Operators who have explicitly set `MAX_CONCURRENT_SESSIONS=N` in `~/Desktop/Valor/.env` will continue to use that value. Operators using the default will see the new default `8` after `./scripts/valor-service.sh restart`.

**Operators who have set `MAX_CONCURRENT_DEV_SESSIONS=N`** in their local `.env` will have that value silently ignored after this ships. The env var is no longer read; no error is raised. This is acceptable because (a) the variable is optional and not documented as required, (b) a silent no-op is better than a startup error on a removed feature, (c) grep across machine `.env` files (the ones in `~/Desktop/Valor/`) confirmed no operator has set it — but this can't be verified remotely.

## Agent Integration

No agent integration required — this is a worker-internal change. The agent's tool surface, MCP servers, and bridge integration are unaffected. No `.mcp.json` changes. No new `tools/` modules.

## Documentation

### Feature Documentation
- [x] Update `docs/features/bridge-worker-architecture.md` — delete the "Dev Session Concurrency Cap (`MAX_CONCURRENT_DEV_SESSIONS`)" subsection (lines 229-253). Update the "Global Session Ceiling (`MAX_CONCURRENT_SESSIONS`)" subsection: change default from `3` to `8` at line 218, update clamped-minimum examples that reference old default. Update any prose that references the dev cap as a separate concept.
- [x] Update `docs/features/README.md` line 26 — the index entry currently says "...global `MAX_CONCURRENT_SESSIONS` semaphore, `MAX_CONCURRENT_DEV_SESSIONS` dev cap for parallel slugged dev sessions, Redis pop lock..." — remove the `MAX_CONCURRENT_DEV_SESSIONS` clause.
- [x] No new feature doc needed — this is a simplification of an existing feature.

### External Documentation Site
No external documentation site.

### Inline Documentation
- [x] At the deletion site in `agent/agent_session_queue.py` (where the swap trick was removed), add a one-line comment: `# Deadlock prevention lives in #1004's child-boost ordering (lines 773-799) and force-deliver on waiting_for_children (output_router.py:109). The swap trick was removed in #1021.`
- [x] Update docstring of `_run_worker` in `worker/__main__.py` if it mentions the dev semaphore (check).
- [x] No API docstrings change — all affected functions are private.

## Success Criteria

- [x] `MAX_CONCURRENT_DEV_SESSIONS` is gone: no env var read, no semaphore attribute, no swap code. Grep confirms zero matches across `worker/`, `agent/`, `ui/`, `tests/`, `.env.example`, `docs/features/`.
- [x] Default global cap is `8` in `worker/__main__.py:180`.
- [x] PM sessions still transition to `waiting_for_children` on child spawn (no regression in `agent/agent_session_queue.py` PM-side logic).
- [x] Child-boost ordering at `agent/agent_session_queue.py:773-799` is unchanged (verified by diff-review).
- [x] `output_router.py:109` force-deliver on `waiting_for_children` is unchanged.
- [x] `tests/unit/test_worker_startup.py` passes (with `TestDevSessionSemaphoreInit` deleted).
- [x] `tests/integration/test_worker_concurrency.py` passes (with dev-semaphore patches removed).
- [x] Dashboard `/dashboard.json` `health` object no longer contains `dev_sessions_running` or `dev_sessions_cap` fields.
- [x] `docs/features/bridge-worker-architecture.md` no longer documents `MAX_CONCURRENT_DEV_SESSIONS`.
- [x] `docs/features/README.md` index no longer mentions `MAX_CONCURRENT_DEV_SESSIONS`.
- [x] Tests pass (`pytest tests/unit/test_worker_startup.py tests/integration/test_worker_concurrency.py -x -q`).
- [x] Lint clean (`python -m ruff check .`).
- [x] Format clean (`python -m ruff format --check .`).
- [x] Documentation updated (`/do-docs`).

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead NEVER builds directly — they deploy team members and coordinate.

### Team Members

- **Builder (code-and-tests)**
  - Name: `semaphore-remover`
  - Role: Delete dev semaphore code from `worker/__main__.py`, `agent/agent_session_queue.py`, and `ui/app.py`. Update `.env.example`. Delete `TestDevSessionSemaphoreInit` and update two tests in `test_worker_concurrency.py`. All four files (plus .env.example) are in scope for one builder because the diff is small and internally consistent.
  - Agent Type: builder
  - Resume: true

- **Validator (correctness)**
  - Name: `semaphore-validator`
  - Role: Verify no remaining references to `MAX_CONCURRENT_DEV_SESSIONS`, `_dev_session_semaphore`, `_dev_session_semaphore_cap`, `dev_sessions_running`, or `dev_sessions_cap` outside `docs/plans/` (which contains historical records) and the `.claude/worktrees/` scratch area. Run targeted pytest commands.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `concurrency-docs`
  - Role: Update `docs/features/bridge-worker-architecture.md` and `docs/features/README.md`.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Remove dev semaphore from worker and queue
- **Task ID**: build-queue-worker
- **Depends On**: none
- **Validates**: `tests/unit/test_worker_startup.py`, `tests/integration/test_worker_concurrency.py`
- **Informed By**: recon (all file:line references verified)
- **Assigned To**: `semaphore-remover`
- **Agent Type**: builder
- **Parallel**: true
- In `worker/__main__.py`: delete lines 186-192 (the `MAX_CONCURRENT_DEV_SESSIONS` block including comment and log line). Change `"3"` to `"8"` in the default at line 180.
- In `agent/agent_session_queue.py`: delete module-level `_dev_session_semaphore` and `_dev_session_semaphore_cap` declarations (search the file for all occurrences; they are initialized at module scope alongside `_global_session_semaphore`).
- In `agent/agent_session_queue.py`: delete the swap trick block at lines 2625-2652 (the comment block starting at 2625 and the full `if ... _dev_semaphore is not None` block through 2652).
- In `agent/agent_session_queue.py`: delete the local variable initialization for `_dev_semaphore_acquired = False` (~line 2623) and the comment block above it (~lines 2621-2622).
- In `agent/agent_session_queue.py`: delete the `finally`-block dev semaphore release at lines 2813-2816 (the `if _dev_semaphore_acquired and _dev_semaphore is not None: _dev_semaphore.release(); _dev_semaphore_acquired = False` block plus its preceding comment).
- Remove any local assignment `_dev_semaphore = _dev_session_semaphore` or similar inside `_worker_loop`.
- Add inline comment at the deletion point: `# Deadlock prevention lives in #1004's child-boost ordering and force-deliver on waiting_for_children. The swap trick was removed in #1021.`
- Run `grep -n "_dev_session_semaphore\|MAX_CONCURRENT_DEV_SESSIONS\|_dev_semaphore" agent/agent_session_queue.py worker/__main__.py` — expect zero matches.

### 2. Remove dashboard fields
- **Task ID**: build-dashboard
- **Depends On**: none (can run in parallel with task 1)
- **Validates**: `curl -s http://localhost:8500/dashboard.json | python -c "import sys, json; d=json.load(sys.stdin); assert 'dev_sessions_running' not in d['health']; assert 'dev_sessions_cap' not in d['health']"`
- **Informed By**: recon identified `ui/app.py:305-326` as the full scope
- **Assigned To**: `semaphore-remover`
- **Agent Type**: builder
- **Parallel**: true
- In `ui/app.py`: delete lines 305-313 (the `# Dev session semaphore metrics` comment and the four lines computing `_dev_sem`, `_dev_sem_cap`, `dev_sessions_cap`, `dev_sessions_running`).
- In `ui/app.py`: delete the two entries in the JSON response body at lines 325-326 (`"dev_sessions_running"` and `"dev_sessions_cap"`).
- Verify the surrounding JSON body is still syntactically valid (check for trailing commas).
- Run `grep -n "_dev_sem\|dev_sessions_" ui/app.py` — expect zero matches.

### 3. Update .env.example
- **Task ID**: build-envexample
- **Depends On**: none (can run in parallel with task 1)
- **Validates**: `grep -c "MAX_CONCURRENT" .env.example` returns 1 (only the global entry)
- **Assigned To**: `semaphore-remover`
- **Agent Type**: builder
- **Parallel**: true
- In `.env.example`: update the `# MAX_CONCURRENT_SESSIONS=3` comment at line 47 to `# MAX_CONCURRENT_SESSIONS=8` so the example reflects the new default. If there is surrounding prose explaining the variable, update any mention of `3` to `8`.
- Do **not** add a `MAX_CONCURRENT_DEV_SESSIONS` entry. There was never one in `.env.example` (recon confirmed).

### 4. Delete and update tests
- **Task ID**: build-tests
- **Depends On**: build-queue-worker (because the test file imports and uses the attributes being removed)
- **Validates**: `pytest tests/unit/test_worker_startup.py tests/integration/test_worker_concurrency.py -x -q` exits 0
- **Assigned To**: `semaphore-remover`
- **Agent Type**: builder
- **Parallel**: false
- In `tests/unit/test_worker_startup.py`: delete the entire `class TestDevSessionSemaphoreInit` (lines 89-138). Also delete any now-unused imports at the top of the file (e.g., if `asyncio` and `_queue` were imported only for this class — verify by checking other test classes in the same file).
- In `tests/integration/test_worker_concurrency.py:120-180` (`test_semaphore_limits_concurrent_sessions`): delete `original_dev_semaphore = _queue._dev_session_semaphore` (line 130), the `_queue._dev_session_semaphore = asyncio.Semaphore(10)` assignment (line 136) and its preceding comment (line 135), and the restore `_queue._dev_session_semaphore = original_dev_semaphore` (line 175).
- In `tests/integration/test_worker_concurrency.py:390-431` (the test at that line range): delete `original_dev_semaphore = _queue._dev_session_semaphore` and `original_dev_semaphore_cap = _queue._dev_session_semaphore_cap` (lines 402-403), the `_queue._dev_session_semaphore = asyncio.Semaphore(2)` and `_queue._dev_session_semaphore_cap = 2` assignments (lines 407-408) and their preceding comment, and the two restore lines (425-426).
- Run `pytest tests/unit/test_worker_startup.py tests/integration/test_worker_concurrency.py -x -q` to confirm all surviving tests pass.

### 5. Validate removal completeness
- **Task ID**: validate-removal
- **Depends On**: build-queue-worker, build-dashboard, build-envexample, build-tests
- **Assigned To**: `semaphore-validator`
- **Agent Type**: validator
- **Parallel**: false
- Run `grep -rn "MAX_CONCURRENT_DEV_SESSIONS\|_dev_session_semaphore\|_dev_sem\b\|dev_sessions_running\|dev_sessions_cap" --include="*.py" --include="*.md" --include=".env.example" --exclude-dir=.venv --exclude-dir=node_modules --exclude-dir=.claude/worktrees .` — expect matches **only** in `docs/plans/worktree-parallel-sdlc.md` (archival) and `docs/plans/session-concurrency-collapse.md` (this plan). Zero matches in any code, test, config, or active doc.
- Run `pytest tests/unit/test_worker_startup.py tests/integration/test_worker_concurrency.py -v` — all tests pass.
- Run `python -m ruff check .` — exit 0.
- Run `python -m ruff format --check .` — exit 0.
- Start the worker locally (`./scripts/valor-service.sh worker-restart`) and confirm the startup log shows `Global session semaphore initialized: MAX_CONCURRENT_SESSIONS=8` and does NOT show any `Dev session semaphore initialized` line.
- Start the dashboard and verify `/dashboard.json` does not contain `dev_sessions_running` or `dev_sessions_cap` keys.

### 6. Update documentation
- **Task ID**: document-feature
- **Depends On**: validate-removal
- **Assigned To**: `concurrency-docs`
- **Agent Type**: documentarian
- **Parallel**: false
- In `docs/features/bridge-worker-architecture.md`: delete the entire "Dev Session Concurrency Cap (`MAX_CONCURRENT_DEV_SESSIONS`)" subsection (lines 229-253 and any subsection-closing fence).
- In the remaining "Global Session Ceiling (`MAX_CONCURRENT_SESSIONS`)" subsection: update the default from `3` to `8` at line 218 (and the sample command at line 215 if it references `3`). Update any prose sentence that compares against the dev cap (e.g., "dev cap must be ≤ global cap") — remove it.
- In `docs/features/README.md` line 26: update the index entry for `bridge-worker-architecture.md`. Remove the clause `, \`MAX_CONCURRENT_DEV_SESSIONS\` dev cap for parallel slugged dev sessions`. The entry should now read approximately: "Bridge/worker process separation: bridge as pure I/O adapter, worker as sole session executor, Redis contract, operator CLI. Includes `worker_key` routing (project-keyed vs chat-keyed serialization), global `MAX_CONCURRENT_SESSIONS` semaphore, Redis pop lock (TOCTOU prevention), and CLI session UUID isolation."
- Verify no other docs reference the removed variable: `grep -rn "MAX_CONCURRENT_DEV_SESSIONS" docs/` — expect only plan files (archival + this plan).

### 7. Final validation
- **Task ID**: validate-all
- **Depends On**: build-queue-worker, build-dashboard, build-envexample, build-tests, validate-removal, document-feature
- **Assigned To**: `semaphore-validator`
- **Agent Type**: validator
- **Parallel**: false
- Run full unit test suite: `pytest tests/unit/ -x -q` — exit 0.
- Run worker concurrency integration tests: `pytest tests/integration/test_worker_concurrency.py -v` — exit 0.
- Run `python -m ruff check .` and `python -m ruff format --check .` — both exit 0.
- Verify all Success Criteria checkboxes.
- Confirm final grep sweep: only `docs/plans/worktree-parallel-sdlc.md` and `docs/plans/session-concurrency-collapse.md` mention the removed symbols.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| No `MAX_CONCURRENT_DEV_SESSIONS` references in code | `grep -rn "MAX_CONCURRENT_DEV_SESSIONS" --include="*.py" .` | exit code 1 |
| No `_dev_session_semaphore` references in code | `grep -rn "_dev_session_semaphore" --include="*.py" .` | exit code 1 |
| No `dev_sessions_running` in dashboard producer | `grep -n "dev_sessions_running\|dev_sessions_cap" ui/app.py` | exit code 1 |
| Global default raised to 8 | `grep -n 'MAX_CONCURRENT_SESSIONS", "8"' worker/__main__.py` | output > 0 |
| `TestDevSessionSemaphoreInit` deleted | `grep -n "TestDevSessionSemaphoreInit" tests/unit/test_worker_startup.py` | exit code 1 |
| Child-boost still present | `grep -n "child_boost" agent/agent_session_queue.py` | output > 0 |
| Force-deliver on waiting_for_children still present | `grep -n "waiting_for_children" agent/output_router.py` | output > 0 |
| Unit tests pass | `pytest tests/unit/test_worker_startup.py -x -q` | exit code 0 |
| Integration concurrency tests pass | `pytest tests/integration/test_worker_concurrency.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Dashboard lacks dev session fields | `curl -s http://localhost:8500/dashboard.json \| python -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if 'dev_sessions_running' not in d['health'] and 'dev_sessions_cap' not in d['health'] else 1)"` | exit code 0 |
| Worker startup log shows cap=8 | `grep -l "MAX_CONCURRENT_SESSIONS=8" logs/worker.log` | output > 0 |
| No dev semaphore log line in worker | `grep -c "Dev session semaphore initialized" logs/worker.log` | output contains `0` |
| Docs no longer mention dev cap | `grep -rn "MAX_CONCURRENT_DEV_SESSIONS" docs/features/` | exit code 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| | | | | |
