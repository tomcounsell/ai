---
status: Planning
type: chore
appetite: Small
owner: Valor Engels
created: 2026-04-23
tracking: https://github.com/tomcounsell/ai/issues/1131
last_comment_id:
---

# Worker Lifecycle Cleanup: Dead Alias, Dead Handler, Starting-Workers Leak Edge Case

## Problem

Three leftover items from the worker-lifecycle audit (#1019) remain in the codebase. Each is small on its own; bundled, they close out the investigation.

**Current behavior:**

1. **W9 — dead alias.** `recover_orphaned_agent_sessions_all_projects` at `agent/session_health.py:1322-1324` is a 2-line wrapper around `cleanup_corrupted_agent_sessions()`. It is also re-exported from `agent/agent_session_queue.py:92`. Grep confirms zero callers in runtime code, scripts, update system, `.claude/`, launchd plists, configs, or tests. The only references are the definition, the re-export, and two historical plan documents (one shipped, one archived).
2. **W10 — dead handler class.** `LoggingOutputHandler` at `agent/output_handler.py:122-153` implements the `OutputHandler` protocol and is fully unit-tested in `tests/unit/test_output_handler.py` (a protocol test and three behaviour tests totalling four test cases). It has **zero production instantiations.** `FileOutputHandler` and `TelegramRelayOutputHandler` are the live implementations used by the worker and bridge. Two doc surfaces (`docs/features/pm-dev-session-architecture.md`, `docs/features/worker-service.md`) describe `LoggingOutputHandler` as a real option, compounding the misleading surface area.
3. **I4 — `_starting_workers` error-path leak.** In `_ensure_worker()` at `agent/agent_session_queue.py:1137-1169`, the happy path at lines 1158-1166 runs `_starting_workers.add()`, then inside the `try` block creates the task, stores it in `_active_workers`, discards from `_starting_workers`, and finally calls `task.add_done_callback()`. If `add_done_callback()` raises after the `discard`, the `except` block at line 1168 rediscards `worker_key` (a no-op since it was already cleared) and re-raises. The real leak is not the set — it is the orphan task sitting in `_active_workers[worker_key]` with no caller-visible ownership. The caller's `except` path rethrows and sees exception semantics, but the task already exists, will run `_worker_loop` to completion, and is untracked by any callback mechanism. Under current asyncio semantics `task.add_done_callback` is essentially infallible on a freshly-created task, so this is a hypothetical hazard rather than an observed failure; the defence is a defence-in-depth exercise.

**Desired outcome:**
- W9: delete the wrapper and its re-export. Zero regressions — grep-verified no callers.
- W10: delete the class, its tests, and its doc mentions. Keep only the two live output handlers.
- I4: restructure `_ensure_worker()` so `_starting_workers.discard(worker_key)` runs on every exit path, the task is not silently orphaned in `_active_workers` if callback registration fails, and an integration test demonstrates that a simulated `add_done_callback` failure leaves both sets empty and the task cancelled.

## Freshness Check

**Baseline commit:** `b6eebc15ae07cea5c040d66f21de4533bb0f8560`
**Issue filed at:** 2026-04-22T17:00:35Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `agent/session_health.py:1322-1324` — `recover_orphaned_agent_sessions_all_projects` wrapper — still present at that exact line range.
- `agent/agent_session_queue.py:92` — re-export of the wrapper — still present.
- `agent/output_handler.py:122-153` — `LoggingOutputHandler` class definition — still present at that exact line range (header on line 122, `react()` body ends on line 153).
- `agent/agent_session_queue.py:1137-1169` — `_ensure_worker()` function — still present at that exact line range. Body structure matches the issue's description.
- `agent/session_state.py:69` — `_starting_workers: set[str]` module-level declaration — still present.

**Cited sibling issues/PRs re-checked:**
- #1019 — closed 2026-04-22T17:01:37Z as the umbrella audit. Its final state acknowledged W9/W10/I4 as separate follow-ups, which is exactly this issue.
- PR #1086 — merged 2026-04-20, shipped W5. That PR is unrelated to W9/W10/I4 (it edited `.env.example` only).
- PR #801 — merged 2026-04-07, introduced `_starting_workers`. Its body explicitly claims "The guard is cleared in the `except` block so it never leaks on `create_task()` failure." That claim covers the `create_task()` failure window — which is correct — but not the `add_done_callback()` failure window, which is what #1131 calls out. This is a genuinely newer observation, not a re-litigation.

**Commits on main since issue was filed (touching referenced files):**
- `git log main --since="2026-04-22T17:00:00Z" -- agent/session_health.py agent/output_handler.py agent/agent_session_queue.py agent/session_state.py` returns no commits. The affected files have not moved since filing.

**Active plans in `docs/plans/` overlapping this area:**
- `docs/plans/worker_lifecycle_cleanup.md` (with underscores, status: Shipped) — different plan, already merged for #1017. Shares a keyword collision in the filename but the hyphenated filename used here is unique.
- `docs/plans/worker-session-lifecycle.md` — status: unrelated scope (session lifecycle state machine work).
- No active plans overlap W9, W10, or I4.

**Notes:** No drift. All claims in the issue body are accurate against the baseline commit.

## Prior Art

- **#1019 — Worker lifecycle audit: open investigations** (closed 2026-04-22): umbrella issue enumerating W1-W12 and I1-I6. W5 was closed by PR #1086. W9/W10/I4 were deferred to this issue. No prior closed attempt to delete W9 or W10 or to harden I4.
- **#1017 — Worker lifecycle audit follow-up: kill command gaps, heartbeat constant drift, state machine doc accuracy** (closed 2026-04-19, plan `docs/plans/worker_lifecycle_cleanup.md` status Shipped): ran the parallel track for kill-command and doc-drift issues in the same #1019 investigation. Completely disjoint mechanical scope (`tools/agent_session_scheduler.py`, `ui/app.py`, `docs/features/session-lifecycle.md`), so no code conflict.
- **PR #801 — fix(queue): prevent duplicate worker spawns per chat_id via `_starting_workers` guard (#785)** (merged 2026-04-07): introduced `_starting_workers`. Its body promised `except`-block protection against `create_task()` failure — delivered correctly. The `add_done_callback()` failure window was not considered in that PR. This plan fills that gap.
- **PR #905 — fix: close nudge-stomp append_event save bypass (#898)** (merged 2026-04-11): unrelated scope; mentioned only as recent queue-area work to confirm no refactor landed between PR #801 and this plan that would supersede the `_starting_workers` design.
- **PR #877 — Add RECOVERY_OWNERSHIP registry for session recovery coverage** (merged 2026-04-10): added the recovery-ownership mechanism but did not touch `_ensure_worker` or the `_starting_workers` set.

## Why Previous Fixes Failed

Not applicable — no prior fix attempted the same scope as this plan. PR #801 (the origin of `_starting_workers`) did address an adjacent window (`create_task()` failure) correctly; it simply did not anticipate the `add_done_callback()` edge case. This is not a re-litigation — it is a narrow defence-in-depth extension.

## Architectural Impact

- **New dependencies**: None.
- **Interface changes**: `recover_orphaned_agent_sessions_all_projects` is removed from `agent/session_health.py` and from the `agent/agent_session_queue.py` re-export block. Because grep confirmed zero callers (in the repo, scripts, configs, and launchd plists), this is effectively a no-op delete. `LoggingOutputHandler` is removed from `agent/output_handler.py`; the class was never imported outside its own test file, so no runtime import site breaks.
- **Coupling**: Decreases. Dead code is always coupling overhead because it shows up in grep results and misleads future readers.
- **Data ownership**: No change.
- **Reversibility**: High. Every change in this plan is a local code edit; revert via `git revert` restores prior behaviour. No data migration, no deployed-machine coordination.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (scope is tightly bounded by the issue's three numbered items)
- Review rounds: 1 (standard PR review)

Solo-dev sized. Aggregate diff is expected to be around 200 lines of deletion plus ~40 lines of edited code and ~40 lines of new test. The surface area is small, the mechanical risk is low, and the test coverage is direct.

## Prerequisites

No prerequisites — this work modifies existing files in the `ai` repo with no new deps, services, or API keys.

## Solution

### Key Elements

- **W9 deletion**: Remove the 3-line wrapper function from `agent/session_health.py` and its single-line import/re-export from `agent/agent_session_queue.py`.
- **W10 deletion**: Remove the `LoggingOutputHandler` class from `agent/output_handler.py`, remove its import and four test cases from `tests/unit/test_output_handler.py`, and remove its mentions from `docs/features/pm-dev-session-architecture.md` and `docs/features/worker-service.md`.
- **I4 fix**: Restructure `_ensure_worker()` so callback registration happens before `_active_workers` assignment, and so every exit path (success or any exception) runs `_starting_workers.discard(worker_key)` via `try/finally`. If the task is created but registration fails, cancel the task before re-raising so no orphan runs.
- **I4 test**: Add a new test case in `TestEnsureWorkerDeduplication` that monkeypatches `asyncio.Task.add_done_callback` to raise, invokes `_ensure_worker()`, catches the re-raised exception, and asserts `_starting_workers` is empty and `_active_workers` contains no orphan task for the key.

### Flow

Not applicable — this is internal cleanup with no user-facing flow.

### Technical Approach

**W9 — delete the wrapper.** Two edits:
1. In `agent/session_health.py`, delete lines 1322-1324 (the function body plus the blank line separator, so the `_cleanup_orphaned_claude_processes` definition is cleanly adjacent to the preceding function).
2. In `agent/agent_session_queue.py`, remove line 92 from the `from agent.session_health import (...)` block.

**W10 — delete the handler.** Four edits:
1. In `agent/output_handler.py`, delete the `LoggingOutputHandler` class (lines 122-153 plus the trailing blank lines up to the `class TelegramRelayOutputHandler` header).
2. In `tests/unit/test_output_handler.py`, remove `LoggingOutputHandler` from the import block (line 15), delete `test_logging_output_handler_is_output_handler` (lines 29-32), delete `TestLoggingOutputHandler` (lines 176-192), and update the module docstring (line 3) to drop the mention of `LoggingOutputHandler`.
3. In `docs/features/pm-dev-session-architecture.md` line 465, rewrite the table row to drop `LoggingOutputHandler` — the live set is `FileOutputHandler` and `TelegramRelayOutputHandler`.
4. In `docs/features/worker-service.md` line 47, delete the `LoggingOutputHandler` row from the table.

**I4 — harden `_ensure_worker`.** Rewrite the function body from the current four-statement `try/except/raise` into a `try/finally` that guarantees `_starting_workers.discard(worker_key)` runs on every exit and cancels any orphan task if callback registration fails:

```python
def _ensure_worker(worker_key: str, is_project_keyed: bool = False) -> None:
    """Start a worker for this worker_key if one isn't already running.

    Workers are keyed by worker_key — either project_key (for PM and
    dev-without-slug sessions that share the main working tree) or chat_id
    (for teammate and slugged-dev sessions with isolated worktrees).

    Creates an asyncio.Event for the key if one doesn't exist. The event is
    used by _worker_loop to wait for new work notifications.

    Idempotency guarantee (two-guard mechanism):
    1. _active_workers[worker_key]: task exists and is not done — steady-state guard.
    2. _starting_workers: worker_key was added here before create_task() and removed
       once the task is live — startup-race guard.

    Leak-safety guarantee: _starting_workers.discard() runs in `finally`, so no exit
    path (including a pathological add_done_callback failure) can leave the key in
    the set. If callback registration fails, the newly-created task is cancelled
    and NOT stored in _active_workers, so no orphan runs.
    """
    existing = _active_workers.get(worker_key)
    if existing and not existing.done():
        return
    if worker_key in _starting_workers:
        logger.warning(f"[worker:{worker_key}] Duplicate worker spawn blocked — in-flight")
        return
    _starting_workers.add(worker_key)
    task: asyncio.Task | None = None
    try:
        event = asyncio.Event()
        _active_events[worker_key] = event
        task = asyncio.create_task(_worker_loop(worker_key, event, is_project_keyed))
        # Register the done_callback BEFORE publishing the task to _active_workers,
        # so either (a) registration succeeds and the task is fully wired up, or
        # (b) registration raises and we cancel the orphan without leaking it.
        task.add_done_callback(lambda _: _starting_workers.discard(worker_key))
        _active_workers[worker_key] = task
        logger.info(f"[worker:{worker_key}] Started session queue worker")
    except Exception:
        # If the task was created but not published, cancel it so no orphan runs.
        if task is not None and worker_key not in _active_workers:
            task.cancel()
        raise
    finally:
        _starting_workers.discard(worker_key)
```

Three changes from the current version:
1. `task.add_done_callback` moves **before** `_active_workers[worker_key] = task`. Either registration succeeds and the task is fully wired up, or it raises before the task is visible to other queue code.
2. `_starting_workers.discard(worker_key)` moves into `finally`, so it runs on every exit path — success, `create_task` exception, or `add_done_callback` exception.
3. The `except` block now cancels the task if it was created but not yet published. Combined with the callback-before-publish ordering, this means a post-create registration failure cannot leave an orphan task running.

This addresses the issue's "either success or any exception path always clears the entry" requirement, plus the implicit task-orphan hazard.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_ensure_worker()` has an `except Exception` that re-raises — the new test case below asserts observable behaviour (orphan task cancelled, set cleared) in that exception path.
- [ ] No other `except Exception: pass` blocks are introduced.
- [ ] `LoggingOutputHandler.send`/`react` had no `try/except` blocks — deletion removes no exception handlers.
- [ ] `recover_orphaned_agent_sessions_all_projects` was a pure re-export with no exception handling — deletion removes no exception handlers.

### Empty/Invalid Input Handling
- [ ] `_ensure_worker()` accepts `worker_key: str`; empty-string behaviour is already exercised implicitly by the existing deduplication tests via `"race-test-chat"`-style IDs. No new empty-input path is introduced.
- [ ] The W9 and W10 deletions touch no input-validation code.

### Error State Rendering
- [ ] No user-visible output surfaces are involved.

## Test Impact

- [ ] `tests/unit/test_output_handler.py::TestOutputHandlerProtocol::test_logging_output_handler_is_output_handler` — DELETE: tests the removed class.
- [ ] `tests/unit/test_output_handler.py::TestLoggingOutputHandler::test_send_does_not_raise` — DELETE: tests the removed class.
- [ ] `tests/unit/test_output_handler.py::TestLoggingOutputHandler::test_send_empty_noop` — DELETE: tests the removed class.
- [ ] `tests/unit/test_output_handler.py::TestLoggingOutputHandler::test_react_does_not_raise` — DELETE: tests the removed class.
- [ ] `tests/unit/test_output_handler.py` module imports — UPDATE: drop `LoggingOutputHandler` from the `agent.output_handler` import.
- [ ] `tests/unit/test_output_handler.py` module docstring — UPDATE: remove "`LoggingOutputHandler`," from the list of tested implementations.
- [ ] `tests/integration/test_agent_session_queue_race.py::TestEnsureWorkerDeduplication::test_double_call_creates_only_one_worker` — UPDATE (light): the assertion `chat_id not in _starting_workers` post-return still holds under the new implementation, so no behavioural change. Re-run to confirm no regression.
- [ ] `tests/integration/test_agent_session_queue_race.py::TestEnsureWorkerDeduplication::test_starting_workers_cleared_after_task_creation` — UPDATE (light): same; the `finally` guarantees this test continues to pass.
- [ ] `tests/integration/test_agent_session_queue_race.py::TestEnsureWorkerDeduplication` — REPLACE (additive): add `test_starting_workers_cleared_when_add_done_callback_fails` — monkeypatches `asyncio.Task.add_done_callback` (via `unittest.mock.patch.object` on the task) to raise, calls `_ensure_worker`, catches the exception, asserts `_starting_workers` is empty, asserts `_active_workers` does not contain the key, and asserts the orphan task was cancelled.
- [ ] `tests/integration/test_worker_concurrency.py` (existing usages of `_ensure_worker`, `_starting_workers`) — no change needed; those tests work at the set-level and do not depend on the internal ordering of `discard` vs. `add_done_callback`.

No tests reference `recover_orphaned_agent_sessions_all_projects` — grep-confirmed zero matches in `tests/`.

## Rabbit Holes

- **Do not rename `cleanup_corrupted_agent_sessions`.** The canonical function already has a clear name; touching it would ripple into every caller across `agent/`, `worker/`, `scripts/`, `monitoring/`, and `tests/` for zero behavioural gain. This plan deletes only the deprecated alias.
- **Do not refactor the entire `_ensure_worker` startup race machinery.** The two-guard mechanism introduced in PR #801 is correct. This plan hardens one narrow exit path; it does not re-examine the guard design.
- **Do not consolidate the three output handlers.** `FileOutputHandler` and `TelegramRelayOutputHandler` have distinct destinations; a unified handler would be a larger design change. Scope is strict deletion of the dead class.
- **Do not audit every module-level `set()` in the queue for similar leak windows.** I4 is a specific, filed investigation; broader cleanup is a separate issue if it ever surfaces.
- **Do not migrate the existing deduplication tests to unit tests.** The current integration-style (with real `asyncio.Event` and `_worker_loop`) is appropriate for race validation; splitting into unit tests would lose coverage.

## Risks

### Risk 1: Callback-before-publish reordering changes observable timing
**Impact:** In the current implementation, `_active_workers[worker_key] = task` (line 1163) happens before `task.add_done_callback(...)` (line 1165). Moving the callback registration earlier means there is a brief window where the task is live in the asyncio event loop but not yet published to `_active_workers`. If another coroutine inspected `_active_workers` in that window (impossible in current single-threaded asyncio — no await happens between `create_task` and the assignment), it could miss the task. In reality `_ensure_worker()` is synchronous and runs atomically in one event-loop turn, so there is no observer for this window.
**Mitigation:** Confirmed synchronous path via code read — there is no `await` between `create_task` and the assignment. Existing `TestEnsureWorkerDeduplication` tests exercise the double-call race and will catch any regression. Document the "callback before publish" ordering choice in the docstring.

### Risk 2: Removing `LoggingOutputHandler` breaks an operator debug workflow nobody documented
**Impact:** If an operator had been manually constructing a `LoggingOutputHandler` in a debug session or one-off script, the import breaks after this ships.
**Mitigation:** Grep across the entire repository (including `scripts/`, `tests/`, `.claude/`, and `docs/`) returned only test and doc references — no operator or script use. Documentation mentions are updated in the same PR. If a genuine debug use emerges post-merge, the class can be restored with a 2-line docstring (git revert on the deletion commit). The 30-day observation window between merge and the start of Q3 is generous.

### Risk 3: `add_done_callback` failure is hypothetical and not reproducible in CPython
**Impact:** The new `test_starting_workers_cleared_when_add_done_callback_fails` test uses `unittest.mock.patch.object` to force a failure that does not occur naturally. A future Python upgrade could change mock-patching semantics and make the test flake or hard-crash.
**Mitigation:** Use `patch.object(task, 'add_done_callback', side_effect=RuntimeError("simulated"))` — patching an instance method of a live `Task` object is a standard, well-supported mock pattern. The test asserts state, not mock-call-count, so it is robust to future refactors. If CPython ever removes `add_done_callback` the test fails loudly, which is the correct signal.

## Race Conditions

### Race 1: Another coroutine observes `_active_workers` between `create_task` and the assignment
**Location:** `agent/agent_session_queue.py:_ensure_worker` in the revised form, between `asyncio.create_task(...)` and `_active_workers[worker_key] = task`.
**Trigger:** Any coroutine that reads `_active_workers` in the tiny synchronous window.
**Data prerequisite:** None — `_active_workers` is a simple module-level dict.
**State prerequisite:** Concurrent coroutine running in the same event loop.
**Mitigation:** `_ensure_worker()` is synchronous (no `await` between these statements). Python's single-threaded event-loop semantics guarantee no other coroutine can observe intermediate state. The existing `_starting_workers` guard already protects against the parallel-spawn race within this function. Confirmed by code read: no `await` statement exists between `create_task` and the dict assignment in either the current or revised body.

### Race 2: `add_done_callback` fires between the `discard` in `try` and the `discard` in `finally`
**Location:** `agent/agent_session_queue.py:_ensure_worker` finally block and done_callback lambda.
**Trigger:** Task completes instantaneously after `create_task` (e.g., `_worker_loop` returns immediately due to shutdown).
**Data prerequisite:** None.
**State prerequisite:** The lambda `_starting_workers.discard(worker_key)` and the `finally` block's `_starting_workers.discard(worker_key)` both operate on the same set.
**Mitigation:** `set.discard()` is idempotent — calling it twice for the same key is a no-op on the second call. Both calls land on the same object, the set ends empty, no race.

## No-Gos (Out of Scope)

- Any rename of `cleanup_corrupted_agent_sessions`.
- Any refactor of the `OutputHandler` protocol (stays as-is).
- Any refactor of the two-guard `_ensure_worker` design (only the cleanup path changes).
- Any edits to `docs/plans/completed/sdlc-1019-w5.md` or `docs/plans/worker_lifecycle_cleanup.md` — shipped plans are historical artefacts, not to be edited.
- Any additional unit test for `FileOutputHandler` or `TelegramRelayOutputHandler` — they are unchanged.
- Any work on other #1019 items (W1-W8, W11-W12, I1-I3, I5-I6) — those are either resolved, separate investigations, or out of scope for this cleanup.

## Update System

No update system changes required — this is purely internal code cleanup with no new dependencies, no new config files, no migration steps, and no operator-visible behaviour change. `/update` remains a straight `git pull`, dep sync, and service restart.

## Agent Integration

No agent integration required — neither the wrapper, the handler, nor `_ensure_worker` is exposed to the agent through any MCP server. The bridge (`bridge/telegram_bridge.py`) does not import any of the three affected code paths directly. No `.mcp.json` changes.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/pm-dev-session-architecture.md:465` — rewrite the `agent/output_handler.py` row to drop `LoggingOutputHandler`; leave the description of `FileOutputHandler` and `TelegramRelayOutputHandler` intact.
- [ ] Update `docs/features/worker-service.md:47` — delete the `LoggingOutputHandler` table row entirely.
- [ ] Add a one-line note to `docs/features/bridge-worker-architecture.md` in the "Chat Serialization and Worker Deduplication" section (added by PR #801) describing the `try/finally` leak-safety guarantee. Target a single sentence under the existing dual-guard paragraph.

### External Documentation Site
- [ ] Not applicable — this repo has no Sphinx / Read the Docs / MkDocs site.

### Inline Documentation
- [ ] Update the `_ensure_worker` docstring to document the leak-safety guarantee (shown in the Technical Approach section above).
- [ ] No docstring changes needed in `session_health.py` or `output_handler.py` — deletions remove entire classes/functions, which is self-documenting.

## Success Criteria

- [ ] `grep -rn recover_orphaned_agent_sessions_all_projects .` returns only historical plan files under `docs/plans/` (zero matches in code, scripts, tests, configs).
- [ ] `grep -rn LoggingOutputHandler .` returns only historical plan files under `docs/plans/` (zero matches in code, tests, current feature docs).
- [ ] `pytest tests/unit/test_output_handler.py -v` passes with the reduced test suite (three test classes: `TestOutputHandlerProtocol` with 4 tests after deletion, `TestFileOutputHandler`, `TestTelegramRelayOutputHandler`).
- [ ] `pytest tests/integration/test_agent_session_queue_race.py::TestEnsureWorkerDeduplication -v` passes all three existing tests plus the new `test_starting_workers_cleared_when_add_done_callback_fails`.
- [ ] `pytest tests/integration/test_worker_concurrency.py -v` passes without any changes needed.
- [ ] `python -m ruff format --check .` exits 0.
- [ ] `python -m ruff check .` exits 0.
- [ ] `_ensure_worker` function body uses `try/finally` with `_starting_workers.discard()` in the finally clause (code review spot-check).
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

### Team Members

- **Builder (cleanup)**
  - Name: cleanup-builder
  - Role: Apply all three deletions (W9, W10) and the I4 refactor in the existing worktree on branch `session/worker-lifecycle-cleanup`. Update doc mentions and the deduplication test file.
  - Agent Type: builder
  - Resume: true

- **Validator (cleanup)**
  - Name: cleanup-validator
  - Role: Verify grep queries return only expected residuals, run the reduced test suite, confirm format/lint clean, spot-check the `_ensure_worker` body for `try/finally` shape.
  - Agent Type: validator
  - Resume: true

### Available Agent Types

Standard Tier 1 roster. No Tier 2 specialist needed — this is a straightforward cleanup with well-defined mechanical edits.

## Step by Step Tasks

### 1. Delete W9 wrapper and re-export
- **Task ID**: build-w9-delete
- **Depends On**: none
- **Validates**: `grep -rn recover_orphaned_agent_sessions_all_projects .` returns only `docs/plans/` matches; `python -c "from agent.agent_session_queue import cleanup_corrupted_agent_sessions"` succeeds; `pytest tests/unit/test_session_health.py tests/integration/test_worker_recovery.py -q` passes (or skips if irrelevant).
- **Informed By**: Grep-verified zero callers outside definition + re-export.
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: true
- Edit `agent/session_health.py`: delete lines 1322-1324 (the `recover_orphaned_agent_sessions_all_projects` definition and its preceding blank line).
- Edit `agent/agent_session_queue.py`: remove the line `    recover_orphaned_agent_sessions_all_projects,` from the `from agent.session_health import (...)` block.
- Run `python -m ruff format agent/` afterward.

### 2. Delete W10 handler, tests, and doc mentions
- **Task ID**: build-w10-delete
- **Depends On**: none
- **Validates**: `grep -rn LoggingOutputHandler .` returns only `docs/plans/completed/` historical matches; `pytest tests/unit/test_output_handler.py -v` passes with the reduced suite; imports in `tests/unit/test_output_handler.py` no longer reference `LoggingOutputHandler`.
- **Informed By**: Grep-verified zero production instantiations.
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: true
- Edit `agent/output_handler.py`: delete the `LoggingOutputHandler` class (lines 122-153 plus trailing blank line).
- Edit `tests/unit/test_output_handler.py`: remove `LoggingOutputHandler` from the import (line 15), delete `test_logging_output_handler_is_output_handler` (lines 29-32), delete the entire `TestLoggingOutputHandler` class (lines 176-192), update the module docstring to drop the `LoggingOutputHandler` mention.
- Edit `docs/features/pm-dev-session-architecture.md`: update line 465's table row to list only `FileOutputHandler` and `TelegramRelayOutputHandler`.
- Edit `docs/features/worker-service.md`: delete the `LoggingOutputHandler` row at line 47.
- Run `python -m ruff format agent/ tests/`.

### 3. Fix I4 `_ensure_worker` leak edge case
- **Task ID**: build-i4-fix
- **Depends On**: none
- **Validates**: `pytest tests/integration/test_agent_session_queue_race.py -v` passes all existing tests plus the new `test_starting_workers_cleared_when_add_done_callback_fails`; `pytest tests/integration/test_worker_concurrency.py -v` passes unchanged.
- **Informed By**: Issue body I4 description; PR #801 as the origin of `_starting_workers`.
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: true
- Replace the body of `_ensure_worker` in `agent/agent_session_queue.py` with the `try/finally`-based version shown in the Technical Approach section. Keep the existing guard checks at the top unchanged.
- Update the function's docstring to describe the leak-safety guarantee.
- Add `test_starting_workers_cleared_when_add_done_callback_fails` to `tests/integration/test_agent_session_queue_race.py::TestEnsureWorkerDeduplication`. The test patches `asyncio.Task.add_done_callback` on the instance via `unittest.mock.patch.object`, wraps the call in `pytest.raises(RuntimeError)`, and asserts:
  - `worker_key not in _starting_workers` (the `finally` cleared it)
  - `worker_key not in _active_workers` (the orphan was not published)
  - The orphan task is cancelled (`task.cancelled()` is True after one event-loop turn)
- Run `python -m ruff format agent/ tests/`.

### 4. Validate all three cleanup tasks
- **Task ID**: validate-all
- **Depends On**: build-w9-delete, build-w10-delete, build-i4-fix
- **Assigned To**: cleanup-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `grep -rn recover_orphaned_agent_sessions_all_projects .` — expect only `docs/plans/` residue.
- Run `grep -rn LoggingOutputHandler .` — expect only `docs/plans/completed/sdlc-1019-w5.md` historical mention.
- Run `pytest tests/unit/test_output_handler.py tests/integration/test_agent_session_queue_race.py tests/integration/test_worker_concurrency.py -v` — expect all green.
- Run `python -m ruff format --check .` — expect exit 0.
- Run `python -m ruff check .` — expect exit 0.
- Spot-check `_ensure_worker` body: confirm `_starting_workers.discard(worker_key)` appears inside a `finally:` block.
- Spot-check the feature docs: confirm `LoggingOutputHandler` is no longer listed.

### 5. Update feature documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: cleanup-builder (acting as documentarian for this small scope)
- **Agent Type**: documentarian
- **Parallel**: false
- Confirm the doc edits from build-w10-delete are in place (already applied inline with that task).
- Add the one-sentence leak-safety note to `docs/features/bridge-worker-architecture.md` in the "Chat Serialization and Worker Deduplication" section, immediately under the existing dual-guard paragraph.
- No new feature doc needed — this is a cleanup plan, not a new feature.

### 6. Final validation
- **Task ID**: final-validate
- **Depends On**: document-feature
- **Assigned To**: cleanup-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the full success-criteria checklist.
- Run `pytest tests/unit/ -q` and `pytest tests/integration/test_agent_session_queue_race.py tests/integration/test_worker_concurrency.py tests/unit/test_output_handler.py -q` to confirm no incidental regressions.
- Generate a final PR-ready summary.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| W9 alias gone | `grep -rn "def recover_orphaned_agent_sessions_all_projects" agent/ tests/` | exit code 1 |
| W9 re-export gone | `grep -n "recover_orphaned_agent_sessions_all_projects" agent/agent_session_queue.py` | exit code 1 |
| W10 class gone | `grep -n "class LoggingOutputHandler" agent/output_handler.py` | exit code 1 |
| W10 test class gone | `grep -n "class TestLoggingOutputHandler" tests/unit/test_output_handler.py` | exit code 1 |
| W10 doc mention gone | `grep -rn LoggingOutputHandler docs/features/` | exit code 1 |
| I4 try/finally in place | `grep -A1 "finally:" agent/agent_session_queue.py \| grep "_starting_workers.discard"` | exit code 0 |
| Unit tests pass | `pytest tests/unit/test_output_handler.py -q` | exit code 0 |
| Race tests pass | `pytest tests/integration/test_agent_session_queue_race.py -q` | exit code 0 |
| Concurrency tests pass | `pytest tests/integration/test_worker_concurrency.py -q` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

None — the issue body was explicit about final dispositions (W9 delete, W10 delete-with-tests-and-docs, I4 try/finally fix). The only marginal choice is whether to keep `LoggingOutputHandler` with an explanatory comment; I propose deletion because (a) it has zero production instantiations, (b) it is fully tested which makes it a false-signal maintenance burden, and (c) if a debug-fallback use case emerges post-merge, the two-commit revert restores it cleanly. The feature docs already describe the live handlers accurately after the deletions.
