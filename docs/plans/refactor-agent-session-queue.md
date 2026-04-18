---
status: Ready
type: chore
appetite: Large
owner: Valor Engels
created: 2026-04-18
tracking: https://github.com/tomcounsell/ai/issues/1023
last_comment_id:
revision_applied: true
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
- `agent/agent_session_queue.py` shrinks to the queue dispatch surface only (~2000 LOC target;
  see Solution section for why ~1500 was aspirational).
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
  result to `chosen.message_text` with the same logic. **Contrary to an earlier draft of this
  plan, both paths call `await chosen.async_save(update_fields=["initial_telegram_message",
  "updated_at"])` when steering messages are present** (confirmed at L929 for the hot path and
  L1063 for the fallback path — both calls are identical). The two paths are therefore
  fully equivalent and collapse to a single helper with no conditional save parameter.
- **Confidence**: high (re-verified against current source)
- **Impact on plan**: `_drain_startup_steering(session)` in `agent/session_pickup.py` takes no
  `save_after` parameter. The function body mirrors the current hot path exactly: pop → prepend
  → `async_save` (always, when `extra_texts` is non-empty). Both callers invoke it identically.
  Verify after extraction: `grep -n "async_save" agent/session_pickup.py` must show exactly
  one call site inside `_drain_startup_steering`.

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

- **New modules**: 6 new files in `agent/` (5 responsibility modules + 1 thin state module)
- **Interface changes**: None — all public symbols re-exported from `agent_session_queue.py`
- **Coupling**: Decreases coupling between unrelated concerns; health check module no longer
  needs to be imported to test the executor; `session_state.py` provides a clean shared-state
  boundary that prevents circular imports
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

Seven modules after the refactor (six new + residual):

- **`agent/session_state.py`** *(new thin state module)*: All mutable session-tracking globals
  and the `SessionHandle` dataclass that extracted modules share. No imports from any other
  `agent/` module (only stdlib + models). Prevents circular imports between `session_executor`
  and `session_health` which both need these globals.
  Owns: `SessionHandle`, `_active_sessions`, `_active_workers`, `_active_events`,
  `_starting_workers`, `_global_session_semaphore`, `_shutdown_requested`,
  `_send_callbacks`, `_reaction_callbacks`, `_response_callbacks`.

- **`agent/session_executor.py`**: The core `_execute_agent_session()` loop plus turn-boundary
  steering consumption, nudge/re-enqueue paths, and calendar heartbeat helpers that are called
  exclusively from within the executor. Owns the subprocess harness lifecycle.
  Owns: `_execute_agent_session`, `_handle_harness_not_found`, `_enqueue_nudge`,
  `re_enqueue_session`, `steer_session`, `_calendar_heartbeat`, `_find_valor_calendar`,
  `_HARNESS_NOT_FOUND_PREFIX`, `_HARNESS_NOT_FOUND_MAX_RETRIES`, `_HARNESS_EXHAUSTION_MSG`.

- **`agent/session_completion.py`**: Post-execution lifecycle: session finalization, parent
  transitions, dev completion handling, and continuation-PM creation.
  Owns: `_complete_agent_session`, `_transition_parent`, `_handle_dev_session_completion`,
  `_create_continuation_pm`, `_extract_issue_number`, `_diagnose_missing_session`,
  `_CONTINUATION_PM_MAX_DEPTH`.

- **`agent/session_health.py`**: Periodic health monitoring, no-progress detection, orphan
  cleanup, and startup recovery.
  Owns: `_agent_session_health_check`, `_agent_session_hierarchy_health_check`,
  `_dependency_health_check`, `_agent_session_health_loop`, `_has_progress`,
  `_tier2_reprieve_signal`, `_get_agent_session_timeout`,
  `_recover_interrupted_agent_sessions_startup`, `_write_worker_heartbeat`,
  `cleanup_corrupted_agent_sessions`, `recover_orphaned_agent_sessions_all_projects`,
  `_cleanup_orphaned_claude_processes`, `format_duration`,
  `AGENT_SESSION_HEALTH_MIN_RUNNING`, `AGENT_SESSION_TIMEOUT_BUILD`,
  `HEARTBEAT_WRITE_INTERVAL`.
  **Do NOT move `DRAIN_TIMEOUT`** — its only caller is `_worker_loop` in the residual.

- **`agent/session_revival.py`**: Revival detection, cooldown tracking, and stale branch cleanup.
  Owns: `check_revival`, `record_revival_cooldown`, `maybe_send_revival_prompt`,
  `queue_revival_agent_session`, `cleanup_stale_branches`, `cleanup_stale_branches_all_projects`,
  `_session_branch_name`, `_load_cooldowns`, `_save_cooldowns`, `REVIVAL_COOLDOWN_SECONDS`,
  `_COOLDOWN_FILE`.

- **`agent/session_pickup.py`**: Session selection, pop locking, startup steering drain, and
  dependency readiness checks.
  Owns: `_pop_agent_session`, `_pop_agent_session_with_fallback`, `_acquire_pop_lock`,
  `_release_pop_lock`, `_maybe_inject_resume_hydration`,
  `_drain_startup_steering` (new shared helper — no `save_after` param; always saves when messages present),
  `dependency_status`, `_POP_LOCK_TTL_SECONDS`.

- **`agent_session_queue.py` (residual)**: Queue dispatch surface — the entry points that
  bridge and worker import.
  Owns: `enqueue_agent_session`, `_push_agent_session`, `_worker_loop`, `register_callbacks`,
  `_resolve_callbacks`, `_ensure_worker`, `_session_notify_listener`, `request_shutdown`,
  `_check_restart_flag`, `_trigger_restart`, `clear_restart_flag`, `DRAIN_TIMEOUT`,
  `_extract_agent_session_fields`, `_pending_depth`, `_remove_by_session`,
  `reorder_agent_session`, `cancel_agent_session`, `retry_agent_session`,
  `get_queue_status`, `get_active_session_for_chat`, `_get_pending_agent_sessions_sync`,
  `_ts`, `PRIORITY_RANK`, `MAX_CONCURRENT_SESSIONS`, `_RESTART_FLAG`, `_RESTART_FLAG_TTL`,
  CLI tools (`_cli_*`), plus all re-exports for backward compatibility.

**LOC target revision**: With the above assignment, the residual is estimated at ~2000–2200 LOC
(not ~1500). The ~1500 LOC figure assumed the queue management helpers (`reorder_agent_session`,
`cancel_agent_session`, `retry_agent_session`, `get_queue_status`, etc.) would also be extracted.
Those are left in the residual for this pass. The success criterion is updated accordingly
(see Success Criteria). A follow-up issue can extract them to `tools/agent_session_scheduler.py`.

### Technical Approach

1. **Extract `agent/session_state.py` first** — this thin module holds only mutable globals
   and `SessionHandle`. No other `agent/` imports. All subsequent extracted modules import
   shared state from here, not from the residual `agent_session_queue.py`. This prevents
   circular imports between `session_executor` (needs `_active_sessions`) and `session_health`
   (also needs `_active_sessions`). The residual also imports from `session_state`.

2. **Extract in dependency order** (bottom-up after session_state): revival → pickup → health →
   completion → executor → residual cleanup. Each step is independently testable.

3. **Module-level constants vs. mutable globals**:
   - *Constants* (`PRIORITY_RANK`, `AGENT_SESSION_TIMEOUT_BUILD`, etc.) — copy into the module
     that owns them. Constants are immutable; duplication is safe and avoids import chains.
   - *Mutable globals* (`_active_sessions`, `_active_workers`, `_active_events`, etc.) — live
     exclusively in `agent/session_state.py`. Every module that needs them imports from there.
   - `DRAIN_TIMEOUT` stays in the residual `agent_session_queue.py`; its only caller is
     `_worker_loop`. Do not move it to `session_health.py`.

4. **Re-export pattern**: After each extraction, add to `agent_session_queue.py`:
   `from agent.session_executor import _execute_agent_session  # noqa: F401`
   This ensures zero breakage for existing callers.

5. **Deferred bridge imports**: Any extracted module referencing `bridge.telegram_bridge`
   must use inline `from bridge.telegram_bridge import X` (not top-level). Match existing pattern.

6. **Shared drain helper**: `_drain_startup_steering(session)` in `agent/session_pickup.py`.
   No `save_after` parameter — both drain paths save when steering messages are present.
   The helper body: pop → prepend → `async_save(update_fields=["initial_telegram_message", "updated_at"])`
   (only when `extra_texts` is non-empty). Both `_pop_agent_session` and `_pop_agent_session_with_fallback`
   call it identically.

7. **Globals inventory sign-off** (Task 1 gate): Before Task 2 begins, the extractor must
   confirm the following 13 mutable globals and 2 path constants are all accounted for in
   the module assignment above: `_active_sessions`, `_active_workers`, `_active_events`,
   `_starting_workers`, `_global_session_semaphore`, `_shutdown_requested`,
   `_send_callbacks`, `_reaction_callbacks`, `_response_callbacks`, `_RESTART_FLAG`,
   `_CONTINUATION_PM_MAX_DEPTH`, `_COOLDOWN_FILE`, `_POP_LOCK_TTL_SECONDS`,
   `DRAIN_TIMEOUT` (residual), `_RESTART_FLAG_TTL` (residual).

8. **Verification**: After each module extraction, run `pytest tests/unit/ -n auto` and confirm
   green before proceeding. Full integration run at end.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_execute_agent_session` has broad `except Exception` catch around subprocess handling —
  builder must verify the existing tests for this path cover observable behavior (log + status
  transition), not just happy path
- [ ] Extracted modules that call `await session.async_save()` inherit the existing save-failure
  logging; verify no new silent swallows are introduced

### Empty/Invalid Input Handling
- [ ] `_drain_startup_steering(session)` must handle `session.session_id = None`
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
- [ ] `tests/unit/test_agent_session_queue_async.py` — no change needed (`get_active_session_for_chat` re-exported)
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

### Risk 1: Module-level global references and circular imports
**Impact:** Extracted functions that reference mutable globals (`_active_sessions`,
`_active_workers`, `_active_events`, etc.) will break at runtime if those globals are not
reachable. Worse, if `session_executor.py` and `session_health.py` both import from
`agent_session_queue.py` to get these globals, and the residual re-exports from those modules,
the import graph becomes circular.
**Mitigation:** All mutable globals live exclusively in `agent/session_state.py`. Every
extracted module that needs them — including the residual — imports from `session_state`.
`session_state.py` itself has no imports from any other `agent/` module. The globals inventory
in Task 1 must explicitly confirm each of the 13 mutable globals is assigned to `session_state`
or the residual before any extraction begins.

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

- [ ] `agent/agent_session_queue.py` is under ~2000 LOC (`wc -l agent/agent_session_queue.py`)
- [ ] Each extracted module (`session_state.py`, `session_executor.py`, `session_completion.py`,
  `session_health.py`, `session_revival.py`, `session_pickup.py`) has a one-sentence purpose
  statement in its top docstring
- [ ] No behavior change — `pytest tests/unit/ -n auto` passes unchanged
- [ ] No behavior change — `pytest tests/integration/ -x` passes unchanged (or existing failures
  are pre-existing and documented)
- [ ] Duplicate Redis drain paths collapsed into `_drain_startup_steering(session)` helper
  (no `save_after` param — always saves when messages present)
- [ ] `python -m ruff format .` clean
- [ ] `python -m ruff check .` clean
- [ ] All existing callers still import successfully (`python -c "from agent.agent_session_queue
  import enqueue_agent_session, register_callbacks, _execute_agent_session"`)

## Team Orchestration

### Team Members

- **Builder (extraction)**
  - Name: extractor
  - Role: Extract the six modules from agent_session_queue.py in dependency order
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
- Confirm the following 13 mutable globals and 2 path constants from `agent/agent_session_queue.py`
  are accounted for in the module assignment in the Solution section (all go to `session_state.py`
  or the residual as documented):
  `_active_sessions`, `_active_workers`, `_active_events`, `_starting_workers`,
  `_global_session_semaphore`, `_shutdown_requested`, `_send_callbacks`, `_reaction_callbacks`,
  `_response_callbacks`, `_RESTART_FLAG`, `_CONTINUATION_PM_MAX_DEPTH`, `_COOLDOWN_FILE`,
  `_POP_LOCK_TTL_SECONDS`, `DRAIN_TIMEOUT` (residual), `_RESTART_FLAG_TTL` (residual)
- Validate with: `python -c "import re; src=open('agent/agent_session_queue.py').read(); [print(m) for m in re.findall(r'^(_[a-z][a-z_]+|DRAIN_TIMEOUT|_RESTART_FLAG_TTL)\s*[:=]', src, re.MULTILINE)]"`
- Sign off that all 15 items match the module assignments above before proceeding to Task 2

### 2. Extract agent/session_state.py
- **Task ID**: build-session-state
- **Depends On**: build-globals-inventory
- **Assigned To**: extractor
- **Agent Type**: builder
- **Parallel**: false
- Create `agent/session_state.py` with one-sentence docstring:
  "Shared mutable session-tracking state for the worker — prevents circular imports between executor and health modules."
- Move: `SessionHandle` dataclass, `_active_sessions`, `_active_workers`, `_active_events`,
  `_starting_workers`, `_global_session_semaphore`, `_shutdown_requested`,
  `_send_callbacks`, `_reaction_callbacks`, `_response_callbacks`
- `session_state.py` must import ONLY from stdlib and `models/` — no other `agent/` imports
- Re-export all moved symbols from `agent_session_queue.py`
- Verify: `python -c "import agent.session_state"` must not raise ImportError
- Run `pytest tests/unit/ -n auto` — must be green before proceeding

### 3. Extract agent/session_revival.py
- **Task ID**: build-revival
- **Depends On**: build-session-state
- **Assigned To**: extractor
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

### 4. Extract agent/session_pickup.py
- **Task ID**: build-pickup
- **Depends On**: build-revival
- **Assigned To**: extractor
- **Agent Type**: builder
- **Parallel**: false
- Create `agent/session_pickup.py` with one-sentence docstring:
  "Session selection, pop locking, startup steering drain, and dependency readiness checks."
- Move: `_pop_agent_session`, `_pop_agent_session_with_fallback`, `_acquire_pop_lock`,
  `_release_pop_lock`, `_maybe_inject_resume_hydration`, `dependency_status`, `_POP_LOCK_TTL_SECONDS`
- **Add** `_drain_startup_steering(session)` helper (no `save_after` param) that consolidates
  the duplicate drain logic from both `_pop_agent_session` and `_pop_agent_session_with_fallback`.
  Function body: pop → prepend → `async_save(update_fields=["initial_telegram_message", "updated_at"])`
  when `extra_texts` is non-empty. Update both callers to use the helper.
- Verify deduplication: `grep -c "pop_all_steering_messages" agent/session_pickup.py` must output `1`
- Re-export all moved symbols from `agent_session_queue.py`
- Run `pytest tests/unit/ -n auto` — must be green before proceeding

### 5. Extract agent/session_health.py
- **Task ID**: build-health
- **Depends On**: build-pickup
- **Assigned To**: extractor
- **Agent Type**: builder
- **Parallel**: false
- Create `agent/session_health.py` with one-sentence docstring:
  "Periodic health monitoring, no-progress detection, orphan cleanup, and startup recovery."
- Move: `_agent_session_health_check`, `_agent_session_hierarchy_health_check`,
  `_dependency_health_check`, `_agent_session_health_loop`, `_has_progress`,
  `_tier2_reprieve_signal`, `_get_agent_session_timeout`,
  `_recover_interrupted_agent_sessions_startup`, `_write_worker_heartbeat`,
  `cleanup_corrupted_agent_sessions`, `recover_orphaned_agent_sessions_all_projects`,
  `_cleanup_orphaned_claude_processes`, `format_duration`,
  `AGENT_SESSION_HEALTH_MIN_RUNNING`, `AGENT_SESSION_TIMEOUT_BUILD`, `HEARTBEAT_WRITE_INTERVAL`
- Import `SessionHandle` and shared state from `agent.session_state` (not from residual)
- **Do NOT move `DRAIN_TIMEOUT`** — it stays in the residual (`_worker_loop` is its only caller)
- Re-export all moved symbols from `agent_session_queue.py`
- Run `pytest tests/unit/ -n auto` — must be green before proceeding

### 6. Extract agent/session_completion.py
- **Task ID**: build-completion
- **Depends On**: build-health
- **Assigned To**: extractor
- **Agent Type**: builder
- **Parallel**: false
- Create `agent/session_completion.py` with one-sentence docstring:
  "Post-execution lifecycle: session finalization, parent transitions, dev completion
  handling, and continuation-PM creation."
- Move: `_complete_agent_session`, `_transition_parent`, `_handle_dev_session_completion`,
  `_create_continuation_pm`, `_extract_issue_number`, `_diagnose_missing_session`,
  `_CONTINUATION_PM_MAX_DEPTH`
- Re-export all moved symbols from `agent_session_queue.py`
- Run `pytest tests/unit/ -n auto` — must be green before proceeding

### 7. Extract agent/session_executor.py
- **Task ID**: build-executor
- **Depends On**: build-completion
- **Assigned To**: extractor
- **Agent Type**: builder
- **Parallel**: false
- Create `agent/session_executor.py` with one-sentence docstring:
  "Core session execution: CLI harness subprocess lifecycle, turn-boundary steering,
  nudge/re-enqueue paths, and calendar heartbeat."
- Move: `_execute_agent_session`, `_handle_harness_not_found`, `_enqueue_nudge`,
  `re_enqueue_session`, `steer_session`, `_calendar_heartbeat`, `_find_valor_calendar`,
  `_HARNESS_NOT_FOUND_PREFIX`, `_HARNESS_NOT_FOUND_MAX_RETRIES`, `_HARNESS_EXHAUSTION_MSG`
- Re-export all moved symbols from `agent_session_queue.py`
- Run `pytest tests/unit/ -n auto` — must be green before proceeding

### 8. Validate residual queue
- **Task ID**: validate-residual
- **Depends On**: build-executor
- **Assigned To**: regressor
- **Agent Type**: validator
- **Parallel**: false
- Confirm `wc -l agent/agent_session_queue.py` is under 2000 LOC
- Confirm all six new modules exist (`session_state.py`, `session_executor.py`,
  `session_completion.py`, `session_health.py`, `session_revival.py`, `session_pickup.py`)
  each with a one-sentence docstring
- Run `python -c "from agent.agent_session_queue import enqueue_agent_session,
  register_callbacks, _execute_agent_session, _create_continuation_pm, _has_progress,
  check_revival"` — must not raise ImportError
- Run `python -c "import agent.session_state; import agent.session_executor; import agent.session_health"` — must not raise ImportError (circular import check)
- Run `python -m ruff format --check .` and `python -m ruff check .`
- Run `pytest tests/unit/ -n auto`
- Report pass/fail for each criterion

### 9. Integration regression
- **Task ID**: validate-integration
- **Depends On**: validate-residual
- **Assigned To**: regressor
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/integration/ -x -q` — document any pre-existing failures (do not
  introduce new ones)
- Run `pytest tests/ -x -q --ignore=tests/integration` as a broader sweep
- Confirm `_drain_startup_steering` helper deduplication:
  `grep -c "pop_all_steering_messages" agent/session_pickup.py` must output `1`

### 10. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-integration
- **Assigned To**: extractor
- **Agent Type**: builder
- **Parallel**: false
- Update `docs/features/agent-session-queue.md` to list all six extracted modules with
  their one-sentence purpose statements and the symbols each owns
- Add or update the module diagram showing the six new files and their relationships
- Add entry to `docs/features/README.md` if not already present for agent-session-queue
- Validate: `grep -c "session_executor\|session_health\|session_pickup\|session_completion\|session_revival\|session_state" docs/features/agent-session-queue.md | awk '$1 >= 6 {exit 0} {exit 1}'`

### 11. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: regressor
- **Agent Type**: validator
- **Parallel**: false
- Verify all success criteria are met
- Confirm `docs/features/agent-session-queue.md` is updated with all six modules
- Generate final report confirming zero regressions

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Residual LOC under target | `wc -l agent/agent_session_queue.py` | output < 2000 |
| Unit tests pass | `pytest tests/unit/ -n auto -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Re-exports intact | `python -c "from agent.agent_session_queue import enqueue_agent_session, _execute_agent_session, _create_continuation_pm, _has_progress, check_revival"` | exit code 0 |
| No circular imports | `python -c "import agent.session_state; import agent.session_executor; import agent.session_health"` | exit code 0 |
| Drain deduplication | `grep -c "pop_all_steering_messages" agent/session_pickup.py` | output is `1` |
| Docs updated | `grep -c "session_executor\|session_health\|session_pickup\|session_completion\|session_revival\|session_state" docs/features/agent-session-queue.md` | output >= 6 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Skeptic, Consistency Auditor | Spike-2 save_after claim incorrect — hot path at L929 does save after prepend; both paths are identical | Revised spike-2, removed `save_after` param | `_drain_startup_steering(session)` always saves when messages present; confirmed by re-reading L929 and L1063 |
| CONCERN | Skeptic, Adversary | Mutable globals straddle modules causing circular import risk | Added `agent/session_state.py` as thin shared-state module | `session_state.py` imports only stdlib + models; executor and health both import from it |
| CONCERN | Skeptic, Simplifier | 21 functions unassigned leaving LOC target unachievable | Explicitly assigned all functions; revised residual LOC target to ~2000 | `_enqueue_nudge`, `steer_session`, calendar helpers → executor; cleanup/orphan/format → health |
| CONCERN | Consistency Auditor, Skeptic | DRAIN_TIMEOUT misassigned to session_health.py | Kept DRAIN_TIMEOUT in residual; added explicit "Do NOT move" note | Only caller is `_worker_loop` in residual; Task 5 explicitly prohibits moving it |
| CONCERN | Operator | Task 1 output not machine-verifiable | Added explicit validation command and sign-off checklist | `python -c "import re; ..."` command added; sign-off on 15 named globals required |
| NIT | — | Task 2 "Assigns To" typo | Fixed to "Assigned To" | Task 3 (now 3) corrected |
| NIT | — | Task 9 agent type "documentarian" not in available types | Changed to "builder" | Task 10 (now 10) updated |
| NIT | — | Task 9 has no validation command | Added `grep -c` validation command | Task 10 (now 10) updated |

---

## Open Questions

No unresolved questions — the critique revision pass addressed all concerns raised. The
LOC target is revised to ~2000 LOC (from ~1500) to account for the 21 previously unassigned
functions that stay in the residual. A follow-up issue can extract the queue management helpers
(`reorder_agent_session`, `cancel_agent_session`, `retry_agent_session`, etc.) to
`tools/agent_session_scheduler.py` if further shrinkage is desired.
