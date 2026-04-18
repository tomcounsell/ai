---
status: Planning
type: chore
appetite: Large
owner: Valor Engels
created: 2026-04-18
tracking: https://github.com/tomcounsell/ai/issues/1023
last_comment_id:
---

# Refactor: Split agent_session_queue.py by Responsibility

## Problem

`agent/agent_session_queue.py` is currently **5545 lines** and holds at least seven distinct
responsibilities in a single file. It is the hottest file in the `agent/` directory — touched
by nearly every worker-path PR — making each change a full-file audit. Prior regressions (#950,
#954) traced to full-save stomps that required auditing every save site across unrelated
concerns; a properly factored module would have localized those changes to a single file.

**Current behavior:**
- Contributing a bug fix to the executor requires understanding (and not breaking) the health
  check, revival detection, CLI tools, and session pickup code that all share the same namespace.
- Unit testing `_execute_agent_session` in isolation is impossible without importing the
  continuation-PM builder, completion handler, and both steering drain paths.
- Near-duplicate Redis drain logic exists at lines 730–809 (`_pop_agent_session`) and
  1052–1071 (`_pop_agent_session_with_fallback`) — the same `pop_all_steering_messages` block
  copy-pasted for the sync fallback path.
- Contributors cannot locate their concern without full-text search.

**Desired outcome:**
- `agent/agent_session_queue.py` shrinks to the queue dispatch surface only (~1500 LOC target).
- Each extracted module has a one-sentence purpose statement at the top of its docstring.
- The duplicate Redis drain paths collapse to one shared helper.
- Zero behavior change — all integration tests pass unchanged.
- `python -m ruff format .` clean.

## Freshness Check

**Baseline commit:** `573f5fde6cf7495ef5a3209ead86f151972c2139`
**Issue filed at:** 2026-04-17T08:00:57Z
**Disposition:** Minor drift — line numbers shifted by ~100–300 lines due to PRs #1029, #1036,
#1039, #1046, #1048 landing after the issue was filed. All seven responsibility anchors
confirmed to still exist; claims remain valid.

**File:line references re-verified:**
- `agent/agent_session_queue.py` — issue cited 5031 LOC, now **5545 LOC** (file grew after filing)
- `_handle_harness_not_found` — issue cited L62–119, confirmed at **L72** (func start)
- `_pop_agent_session` — issue cited L865–895, confirmed at **L730**
- `_pop_agent_session_with_fallback` (sync fallback) — issue cited L1000–1025, confirmed at **L944**
- Steering drain duplicate — confirmed at **L1052** inside `_pop_agent_session_with_fallback`
- Bridge re-push — issue cited L1923, not pinned as a function boundary (inline logic)
- `_create_continuation_pm` — issue cited L3252, confirmed at **L3551**
- `_handle_dev_session_completion` — issue cited L3328, confirmed at **L3679**
- `_execute_agent_session` — issue cited L4067, confirmed at **L3891**

**Cited sibling issues/PRs re-checked:**
- **#1018** (PM→Dev steering on CLI harness) — CLOSED, merged as PR #1020 on 2026-04-17T06:37Z
  (before the issue was filed). Dependency is already satisfied.
- **#1022** (session_type/role/session_mode) — OPEN. Still a downstream beneficiary; no blocking
  relationship in this direction.

**Commits on main since issue was filed (touching agent_session_queue.py):**
- `29f8b450` Collapse session concurrency: single MAX_CONCURRENT_SESSIONS=8 cap (#1029) — additive
- `350df702` feat(health): two-tier no-progress detector (#1036) (#1039) — added `_has_progress` extensions and `_tier2_reprieve_signal`
- `b847ae4a` Fix orphan detection crash — targeted bug fix
- `d76232f4` feat(health-check): promote last_stdout_at to Tier-1 kill signal (#1046) (#1048) — health check additions

None of these PRs change the module-level structure; the seven responsibility groups remain
intact. All line number references in this plan use the **current** (post-drift) positions.

**Active plans in `docs/plans/` overlapping this area:** None. `worker_lifecycle_cleanup.md`
(#1017) is status: Shipped and does not overlap.

## Prior Art

- **PR #737** (Extract standalone worker) — Split bridge monolith into bridge + worker separation.
  Established the pattern that worker execution code belongs in `agent/`, not `bridge/`. Sets
  the precedent this refactor extends.
- **PR #856** (Add TelegramRelayOutputHandler) — Extracted output routing protocol to
  `agent/output_handler.py` and `agent/output_router.py`. This is the most direct prior art:
  161 LOC extracted, zero behavior change, all callers updated. The same `re-export for
  backward compatibility` pattern used there should be repeated here.
- **PRs #950, #954** — Regressions caused by full-save stomps. Both required auditing all save
  sites across unrelated concerns in the same file. Cited in the issue as motivation.

No prior attempts to split `agent_session_queue.py` further were found.

## Research

No relevant external findings — this is a pure internal refactor with no external library or
ecosystem patterns involved.

## Spike Results

### spike-1: Circular import mapping
- **Assumption**: Extracting modules will not create circular imports because candidate modules
  need AgentSession, output_router, and possibly bridge hooks, all of which are already
  non-circular.
- **Method**: code-read
- **Finding**: `agent/agent_session_queue.py` currently defers all bridge imports to inline
  `from bridge.telegram_bridge import ...` calls within functions (e.g., lines 1200+), never
  at module top-level. This pattern MUST be preserved in extracted modules — any module that
  needs bridge symbols must use the same inline import style, not a top-level import. No
  top-level circular dependency exists because bridge imports from `agent_session_queue`, not
  the reverse at module level.
- **Confidence**: high
- **Impact on plan**: All extracted modules that reference bridge symbols must use deferred
  inline imports, matching the existing pattern.

### spike-2: Steering drain duplication
- **Assumption**: The duplicate drain paths at `_pop_agent_session` (L730–809) and
  `_pop_agent_session_with_fallback` (L1052–1071) can be collapsed into one helper without
  behavioral difference.
- **Method**: code-read
- **Finding**: Both paths call `pop_all_steering_messages(chosen.session_id)` and prepend the
  result to `chosen.message_text` with the same logic. The only difference is that the
  fallback path calls `await chosen.async_save(update_fields=["initial_telegram_message",
  "updated_at"])` after prepending. The hot path does not save separately (it relies on the
  downstream startup save). This difference is load-bearing and must be preserved. A shared
  helper with a `save_after: bool` parameter safely collapses both paths.
- **Confidence**: high
- **Impact on plan**: The shared helper takes `(session, save_after: bool = False)` parameter.
  Hot path calls with `save_after=False`, fallback path calls with `save_after=True`.

### spike-3: Test import surface
- **Assumption**: All existing tests that import from `agent.agent_session_queue` will continue
  working if extracted symbols are re-exported from the original module.
- **Method**: code-read
- **Finding**: Confirmed — all test imports use `from agent.agent_session_queue import X`.
  Re-exporting extracted symbols from `agent_session_queue.py` means zero test changes. The
  re-export pattern was already used for `output_router` symbols (see top of file: `from
  agent.output_router import MAX_NUDGE_COUNT, ...`). Test files specifically importing
  private symbols (`_handle_harness_not_found`, `_pop_agent_session`, etc.) will continue
  working via re-exports.
- **Confidence**: high
- **Impact on plan**: Every extracted symbol that has an existing external caller or test
  must be re-exported from `agent_session_queue.py`. Builder must cross-check the full import
  list before removing any symbol from the original module.

## Data Flow

This is a structural refactor — no data flow changes. The execution model remains:

1. **Worker loop** calls `_pop_agent_session()` → returns `AgentSession`
2. **Startup steering drain** runs `pop_all_steering_messages()` before execution
3. **`_execute_agent_session()`** runs the CLI harness subprocess, consuming turn-boundary
   steering messages from `AgentSession.queued_steering_messages`
4. On completion: **`_complete_agent_session()`** finalizes lifecycle, then
   **`_handle_dev_session_completion()`** (for dev sessions) calls **`_create_continuation_pm()`**
   to build the next PM session
5. **`enqueue_agent_session()`** puts the continuation PM back on the Redis queue

Post-refactor: same flow, different files. All public symbols re-exported from
`agent_session_queue.py` for backward compatibility.

## Architectural Impact

- **New modules**: 5 new files in `agent/` (see Solution section)
- **Interface changes**: None — all public symbols re-exported from `agent_session_queue.py`
- **Coupling**: Decreases coupling between unrelated concerns; health check module no longer
  needs to be imported to test the executor
- **Data ownership**: Unchanged — AgentSession remains the authoritative model
- **Reversibility**: Fully reversible — re-exports make the split transparent to all callers;
  can be undone by inlining the modules back

## Appetite

**Size:** Large

**Team:** Solo dev (builder + validator)

**Interactions:**
- PM check-ins: 1 (scope alignment at start)
- Review rounds: 1

The implementation is mechanical but high-surface — every extracted function must be
verified for implicit dependencies on module-level globals in the source file.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Issue #1018 merged | `gh issue view 1018 --json state -q .state \| grep -q CLOSED && echo ok` | Steering fix must land before this refactor reshapes the seam |

Run all checks: `python scripts/check_prerequisites.py docs/plans/refactor-agent-session-queue.md`

## Solution

### Key Elements

- **`agent/session_executor.py`**: The core `_execute_agent_session()` loop plus turn-boundary
  steering consumption. Owns the subprocess harness lifecycle.
- **`agent/session_completion.py`**: `_complete_agent_session()`, `_transition_parent()`,
  `_handle_dev_session_completion()`, `_create_continuation_pm()`. Owns the post-run lifecycle.
- **`agent/session_health.py`**: `_agent_session_health_check()`, `_agent_session_hierarchy_health_check()`,
  `_dependency_health_check()`, `_agent_session_health_loop()`, `_has_progress()`,
  `_tier2_reprieve_signal()`, `_get_agent_session_timeout()`, `SessionHandle`,
  `_recover_interrupted_agent_sessions_startup()`. Owns health monitoring and recovery.
- **`agent/session_revival.py`**: `check_revival()`, `record_revival_cooldown()`,
  `maybe_send_revival_prompt()`, `queue_revival_agent_session()`, `cleanup_stale_branches()`,
  `cleanup_stale_branches_all_projects()`, `_session_branch_name()`, `_load_cooldowns()`,
  `_save_cooldowns()`. Owns revival detection and branch cleanup.
- **`agent/session_pickup.py`**: `_pop_agent_session()`, `_pop_agent_session_with_fallback()`,
  `_acquire_pop_lock()`, `_release_pop_lock()`, `_maybe_inject_resume_hydration()`,
  `_drain_startup_steering()` (new shared helper collapsing the duplicate drain paths),
  `dependency_status()`. Owns session selection and startup preparation.
- **`agent_session_queue.py` (residual)**: Queue dispatch surface: `enqueue_agent_session()`,
  `_push_agent_session()`, `_worker_loop()`, `register_callbacks()`, `_resolve_callbacks()`,
  `_ensure_worker()`, `_session_notify_listener()`, `request_shutdown()`, `_check_restart_flag()`,
  `_trigger_restart()`, `clear_restart_flag()`, CLI tools (`_cli_*`), plus all re-exports for
  backward compatibility.

### Technical Approach

1. **Extract in dependency order** (bottom-up): revival → pickup → health → completion →
   executor → residual queue. Each step is independently testable.
2. **Module-level globals**: Each extracted module must copy any globals it references
   (`PRIORITY_RANK`, `_RESTART_FLAG`, etc.) or import them from the residual queue module.
   Prefer copying constants; import mutable globals from one canonical home.
3. **Re-export pattern**: After each extraction, add to `agent_session_queue.py`:
   `from agent.session_executor import _execute_agent_session  # noqa: F401`
   This ensures zero breakage for existing callers.
4. **Deferred bridge imports**: Any extracted module referencing `bridge.telegram_bridge`
   must use inline `from bridge.telegram_bridge import X` (not top-level). Match existing pattern.
5. **Shared drain helper**: `_drain_startup_steering(session, save_after: bool = False)` in
   `agent/session_pickup.py`. Both `_pop_agent_session` and `_pop_agent_session_with_fallback`
   call it; `save_after=True` only on the fallback path.
6. **Verification**: After each module extraction, run `pytest tests/unit/ -n auto` and confirm
   green before proceeding. Full integration run at end.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_execute_agent_session` has broad `except Exception` catch around subprocess handling —
  builder must verify the existing tests for this path cover observable behavior (log + status
  transition), not just happy path
- [ ] Extracted modules that call `await session.async_save()` inherit the existing save-failure
  logging; verify no new silent swallows are introduced

### Empty/Invalid Input Handling
- [ ] `_drain_startup_steering(session, save_after)` must handle `session.session_id = None`
  gracefully — `pop_all_steering_messages` already handles this; builder must not break it
- [ ] `_create_continuation_pm()` with `session.parent_agent_session_id = None` — existing
  guard; verify it survives the move

### Error State Rendering
- [ ] No user-visible output changes — this is pure restructuring; all output paths remain in
  the same functions, just in new files

## Test Impact

All test files that import from `agent.agent_session_queue` will continue to work via
re-exports. No test file needs modification. The re-export strategy makes this refactor
transparent to the test suite.

- [ ] `tests/unit/test_harness_retry.py` — no change needed (re-exported `_handle_harness_not_found`)
- [ ] `tests/unit/test_continuation_pm.py` — no change needed (re-exported `_create_continuation_pm`)
- [ ] `tests/unit/test_health_check_recovery_finalization.py` — no change needed (re-exported
  `_has_progress`, `_tier2_reprieve_signal`, `SessionHandle`)
- [ ] `tests/unit/test_agent_session_queue.py` — no change needed (all symbols re-exported)
- [ ] `tests/unit/test_agent_session_queue_async.py` — no change needed
- [ ] `tests/unit/test_steering_mechanism.py` — no change needed (MAX_NUDGE_COUNT etc. re-exported)
- [ ] `tests/unit/test_agent_session_hierarchy.py` — no change needed (`_transition_parent` re-exported)

After extraction, run full unit suite to confirm zero regressions before integration run.

## Rabbit Holes

- **Renaming the residual module**: `agent_session_queue.py` is imported by 30+ call sites.
  Renaming it is a separate migration with high breakage risk. Out of scope.
- **Moving tests**: Tests importing from `agent.agent_session_queue` do not need to be
  relocated — re-exports handle it. Do not create new test files as part of this refactor.
- **Fixing naming drift** (`session_type` / `role` / `session_mode`): tracked in #1022.
  Do not touch field names during this refactor.
- **Refactoring the CLI tools**: `_cli_show_status()` and friends are only ~180 LOC and could
  move to `tools/agent_session_scheduler.py`. Save for a follow-up.
- **Restructuring the health check module**: `_agent_session_health_check()` at 762 LOC is a
  candidate for further internal decomposition. Out of scope for this pass.
- **Changing call signatures**: This refactor must be zero-behavior-change. If a function
  signature change seems needed to make the extraction clean, defer it.

## Risks

### Risk 1: Module-level global references
**Impact:** Extracted functions that reference globals defined in `agent_session_queue.py`
(e.g., `_active_sessions`, `_worker_futures`, `_RESTART_FLAG`) will break at runtime if the
globals are not reachable in the extracted module.
**Mitigation:** Inventory every module-level global referenced by each candidate function
before extraction. Globals shared across modules must live in one canonical module and be
imported everywhere else. Use `grep -n "^[A-Z_]" agent/agent_session_queue.py` to enumerate.

### Risk 2: Async context propagation
**Impact:** `_execute_agent_session` and health check functions use `asyncio.create_task()`,
`asyncio.gather()`, and `asyncio.Event`. If these are extracted to a module that's imported
before the event loop is running, startup behavior may change.
**Mitigation:** All asyncio calls are inside `async def` functions — no top-level async code.
Extraction is safe as long as module-level code remains synchronous (no `asyncio.run()` at
module scope).

### Risk 3: Re-export completeness
**Impact:** Missing a re-export causes an `ImportError` at runtime in any caller of the
moved symbol. Bridge/worker startup fails silently.
**Mitigation:** After each extraction step, run `python -c "from agent.agent_session_queue
import *"` and `pytest tests/unit/ -n auto`. If either fails, a re-export is missing.

### Risk 4: deferred bridge imports
**Impact:** If any extracted module accidentally adds a top-level `from bridge.telegram_bridge
import ...`, it will create a circular import at worker startup.
**Mitigation:** Ruff rule `PLC0415` (import-outside-toplevel) is suppressed for existing
inline imports with `# noqa` — extracted modules must NOT add top-level bridge imports.
Post-extraction: `python -c "import agent.session_executor"` must not raise ImportError.

## Race Conditions

No new race conditions introduced — this is a structural refactor with zero behavior change.
All existing concurrency mechanisms (pop locks, asyncio.Event, asyncio.Lock) remain intact
and are moved with their owning functions.

## No-Gos (Out of Scope)

- Renaming `agent_session_queue.py` — high breakage risk, separate migration
- Modifying any function signatures or behavior
- Fixing `session_type` / `role` / `session_mode` naming drift (see #1022)
- Moving tests to new locations
- Refactoring the health check module's internal structure
- Any CLI tool relocation
- Adding new features or fixing bugs as part of this PR

## Update System

No update system changes required — this is a purely internal module reorganization.
No new config, dependencies, or environment variables are introduced.
The residual `agent_session_queue.py` retains all its public symbols via re-exports,
so the update process (git pull + dep sync) is sufficient.

## Agent Integration

No agent integration required — this refactor reorganizes internal worker code. No MCP
server changes, no `.mcp.json` changes. The bridge continues to import
`enqueue_agent_session` and `register_callbacks` from `agent.agent_session_queue` exactly
as before (those functions stay in the residual module).

## Documentation

- [ ] Update `docs/features/agent-session-queue.md` to list the new module structure and
  the purpose of each extracted file
- [ ] Add inline docstrings to each new module file with a one-sentence purpose statement
  (this is also an acceptance criterion)
- [ ] Add entry to `docs/features/README.md` if the feature doc is new

## Success Criteria

- [ ] `agent/agent_session_queue.py` is under ~1500 LOC (`wc -l agent/agent_session_queue.py`)
- [ ] Each extracted module (`session_executor.py`, `session_completion.py`, `session_health.py`,
  `session_revival.py`, `session_pickup.py`) has a one-sentence purpose statement in its top docstring
- [ ] No behavior change — `pytest tests/unit/ -n auto` passes unchanged
- [ ] No behavior change — `pytest tests/integration/ -x` passes unchanged (or existing failures
  are pre-existing and documented)
- [ ] Duplicate Redis drain paths collapsed into `_drain_startup_steering()` helper
- [ ] `python -m ruff format .` clean
- [ ] `python -m ruff check .` clean
- [ ] All existing callers still import successfully (`python -c "from agent.agent_session_queue
  import enqueue_agent_session, register_callbacks, _execute_agent_session"`)

## Team Orchestration

### Team Members

- **Builder (extraction)**
  - Name: extractor
  - Role: Extract the five modules from agent_session_queue.py in dependency order
  - Agent Type: builder
  - Resume: true

- **Validator (regression)**
  - Name: regressor
  - Role: Confirm zero regressions after each extraction step
  - Agent Type: validator
  - Resume: true

### Available Agent Types

**Tier 1 — Core:**
- `builder` - General implementation
- `validator` - Read-only verification

## Step by Step Tasks

### 1. Inventory module-level globals
- **Task ID**: build-globals-inventory
- **Depends On**: none
- **Assigned To**: extractor
- **Agent Type**: builder
- **Parallel**: false
- List every module-level global, constant, and `asyncio.Event`/`asyncio.Lock` in
  `agent/agent_session_queue.py` with a note on which candidate functions reference them
- Produce a mapping: `{global_name: [functions that reference it]}` as a comment block
  at the top of the plan's Technical Approach (inline in the PR description is fine)
- This inventory drives all subsequent extraction steps

### 2. Extract agent/session_revival.py
- **Task ID**: build-revival
- **Depends On**: build-globals-inventory
- **Assigns To**: extractor
- **Agent Type**: builder
- **Parallel**: false
- Create `agent/session_revival.py` with one-sentence docstring:
  "Revival detection, cooldown tracking, and stale branch cleanup for AgentSession."
- Move: `check_revival`, `record_revival_cooldown`, `maybe_send_revival_prompt`,
  `queue_revival_agent_session`, `cleanup_stale_branches`, `cleanup_stale_branches_all_projects`,
  `_session_branch_name`, `_load_cooldowns`, `_save_cooldowns`, `REVIVAL_COOLDOWN_SECONDS`,
  `_COOLDOWN_FILE`
- Re-export all moved symbols from `agent_session_queue.py`
- Run `pytest tests/unit/ -n auto` — must be green before proceeding

### 3. Extract agent/session_pickup.py
- **Task ID**: build-pickup
- **Depends On**: build-revival
- **Assigned To**: extractor
- **Agent Type**: builder
- **Parallel**: false
- Create `agent/session_pickup.py` with one-sentence docstring:
  "Session selection, pop locking, startup steering drain, and dependency readiness checks."
- Move: `_pop_agent_session`, `_pop_agent_session_with_fallback`, `_acquire_pop_lock`,
  `_release_pop_lock`, `_maybe_inject_resume_hydration`, `dependency_status`
- **Add** `_drain_startup_steering(session, save_after: bool = False)` helper that
  consolidates the duplicate drain logic from both `_pop_agent_session` and
  `_pop_agent_session_with_fallback`. Update both functions to call the helper.
- Re-export all moved symbols from `agent_session_queue.py`
- Run `pytest tests/unit/ -n auto` — must be green before proceeding

### 4. Extract agent/session_health.py
- **Task ID**: build-health
- **Depends On**: build-pickup
- **Assigned To**: extractor
- **Agent Type**: builder
- **Parallel**: false
- Create `agent/session_health.py` with one-sentence docstring:
  "Periodic health monitoring, no-progress detection, orphan cleanup, and startup recovery."
- Move: `_agent_session_health_check`, `_agent_session_hierarchy_health_check`,
  `_dependency_health_check`, `_agent_session_health_loop`, `_has_progress`,
  `_tier2_reprieve_signal`, `_get_agent_session_timeout`, `SessionHandle`,
  `_recover_interrupted_agent_sessions_startup`, `_write_worker_heartbeat`,
  `AGENT_SESSION_HEALTH_MIN_RUNNING`, `DRAIN_TIMEOUT` (if defined here)
- Re-export all moved symbols from `agent_session_queue.py`
- Run `pytest tests/unit/ -n auto` — must be green before proceeding

### 5. Extract agent/session_completion.py
- **Task ID**: build-completion
- **Depends On**: build-health
- **Assigned To**: extractor
- **Agent Type**: builder
- **Parallel**: false
- Create `agent/session_completion.py` with one-sentence docstring:
  "Post-execution lifecycle: session finalization, parent transitions, dev completion
  handling, and continuation-PM creation."
- Move: `_complete_agent_session`, `_transition_parent`, `_handle_dev_session_completion`,
  `_create_continuation_pm`, `_extract_issue_number`, `_diagnose_missing_session`
- Re-export all moved symbols from `agent_session_queue.py`
- Run `pytest tests/unit/ -n auto` — must be green before proceeding

### 6. Extract agent/session_executor.py
- **Task ID**: build-executor
- **Depends On**: build-completion
- **Assigned To**: extractor
- **Agent Type**: builder
- **Parallel**: false
- Create `agent/session_executor.py` with one-sentence docstring:
  "Core session execution: CLI harness subprocess lifecycle and turn-boundary steering."
- Move: `_execute_agent_session`, `_handle_harness_not_found`,
  `_HARNESS_NOT_FOUND_PREFIX`, `_HARNESS_NOT_FOUND_MAX_RETRIES`, `_HARNESS_EXHAUSTION_MSG`
- Re-export all moved symbols from `agent_session_queue.py`
- Run `pytest tests/unit/ -n auto` — must be green before proceeding

### 7. Validate residual queue
- **Task ID**: validate-residual
- **Depends On**: build-executor
- **Assigned To**: regressor
- **Agent Type**: validator
- **Parallel**: false
- Confirm `wc -l agent/agent_session_queue.py` is under 1500 LOC
- Confirm all five new modules exist with one-sentence docstrings
- Run `python -c "from agent.agent_session_queue import enqueue_agent_session,
  register_callbacks, _execute_agent_session, _create_continuation_pm, _has_progress,
  check_revival"` — must not raise ImportError
- Run `python -m ruff format --check .` and `python -m ruff check .`
- Run `pytest tests/unit/ -n auto`
- Report pass/fail for each criterion

### 8. Integration regression
- **Task ID**: validate-integration
- **Depends On**: validate-residual
- **Assigned To**: regressor
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/integration/ -x -q` — document any pre-existing failures (do not
  introduce new ones)
- Run `pytest tests/ -x -q --ignore=tests/integration` as a broader sweep
- Confirm `_drain_startup_steering` helper is used in both `_pop_agent_session` and
  `_pop_agent_session_with_fallback` (grep for the duplicate `pop_all_steering_messages`
  call — should appear only once in `session_pickup.py`)

### 9. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-integration
- **Assigned To**: extractor
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/agent-session-queue.md` to list the five extracted modules with
  their one-sentence purpose statements and the symbols each owns
- Add or update the module diagram showing the five new files and their relationships
- Add entry to `docs/features/README.md` if not already present for agent-session-queue

### 10. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: regressor
- **Agent Type**: validator
- **Parallel**: false
- Verify all success criteria are met
- Confirm `docs/features/agent-session-queue.md` is updated
- Generate final report confirming zero regressions

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Residual LOC under target | `wc -l agent/agent_session_queue.py` | output < 1500 |
| Unit tests pass | `pytest tests/unit/ -n auto -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Re-exports intact | `python -c "from agent.agent_session_queue import enqueue_agent_session, _execute_agent_session, _create_continuation_pm, _has_progress, check_revival"` | exit code 0 |
| Drain deduplication | `grep -c "pop_all_steering_messages" agent/agent_session_queue.py agent/session_pickup.py` | output contains "session_pickup.py:1" |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| — | — | (populated by /do-plan-critique) | — | — |

---

## Open Questions

1. **LOC target**: The ~1500 LOC target for the residual `agent_session_queue.py` assumes
   the CLI tools (~180 LOC), worker loop (~913 LOC), and dispatch surface (~400 LOC)
   stay together. If the worker loop is also extracted, the residual could reach ~600 LOC.
   Should the worker loop (`_worker_loop`, `enqueue_agent_session`, `_push_agent_session`)
   be extracted to `agent/worker_dispatch.py`, or is the ~1500 LOC residual acceptable?
   *(Recommendation: leave worker loop in residual for now; a follow-up can extract it.)*
