---
status: Planning
type: chore
appetite: Medium
owner: Valor
created: 2026-04-05
tracking: https://github.com/tomcounsell/ai/issues/701
last_comment_id:
---

# Session Lifecycle Consolidation

## Problem

Session lifecycle mutations are scattered across 6 files with 15+ direct `session.status = X; session.save()` call sites. Three parallel completion paths each perform different subsets of completion work (lifecycle logging, auto-tagging, branch checkpointing, parent finalization). This fragmentation caused the zombie loop in #700 and creates ongoing risk of similar bugs.

**Current behavior:**

Three parallel completion paths exist, each doing different things:

| Path | Location | Logs lifecycle | Auto-tags | Checkpoints branch | Finalizes parent |
|------|----------|:-:|:-:|:-:|:-:|
| A | `bridge/session_transcript.py:252` `complete_transcript()` | Yes | Yes | No | No |
| B | `agent/agent_session_queue.py:887` `_complete_agent_session()` | No | No | Yes | Yes |
| C | `.claude/hooks/stop.py:155` | No | No | No | No |
| D | `agent/hooks/subagent_stop.py:86` | No | No | No | No |

Non-terminal transitions (pending->running, running->pending, etc.) are also scattered with inconsistent lifecycle logging — some sites log, some don't. The model docstring documents 7 status values but 11 are in use.

**Desired outcome:**

- One canonical function per lifecycle operation (complete, fail, kill, abandon, revive, cancel)
- All completion side effects happen in one place, regardless of caller
- Non-terminal transitions go through a wrapper with consistent lifecycle logging
- Model docstring documents all status values and valid transitions

## Prior Art

- **Issue #700 / PR #703**: Fix session completion zombie loop — patched `_extract_agent_session_fields()` to include `status`. Addressed the immediate symptom; this issue addresses the structural cause.
- **Issue #592**: Audit AgentSession model: fix status KeyField duplicates, prune dead fields — cleaned up field types and naming. Established that `status` is an IndexedField (safe for direct mutation).
- **Issue #342 / PR #344**: Fix session stuck in pending after BUILD COMPLETED — another symptom of fragmented lifecycle management.
- **Issue #626 / PR #636**: Fix silent session death — added crash diagnostics and snapshot saving. Added the `save_session_snapshot()` call in the worker's finally block.
- **Issue #473**: AgentSession field naming cleanup and deprecation sweep — cleaned up field names but didn't consolidate lifecycle operations.
- **PR #217**: Add session lifecycle diagnostics and stall detection — introduced `log_lifecycle_transition()` but didn't mandate its use at all mutation sites.

## Data Flow

Session status mutations originate from multiple entry points and flow through different code paths:

1. **Entry: Worker picks up session** — `agent_session_queue.py:564` sets `pending->running`, logs lifecycle
2. **Entry: Agent execution completes** — Worker's finally block (line 1654) calls `_complete_agent_session()` which does branch checkpoint + parent finalization + status save. Separately, `complete_transcript()` is called by `sdk_client.py` which does transcript marker + auto-tag + lifecycle log + status save.
3. **Entry: Claude Code hook stop** — `.claude/hooks/stop.py:155` sets status directly, no lifecycle log, no auto-tag, no checkpoint
4. **Entry: Subagent stop** — `agent/hooks/subagent_stop.py:86` sets status directly, no lifecycle log
5. **Entry: Nudge loop re-enqueue** — `agent_session_queue.py:1914` sets `completed->pending` for auto-continue, no lifecycle log
6. **Entry: Health check recovery** — `agent_session_queue.py:1200` sets `running->pending`, no lifecycle log
7. **Entry: Watchdog abandon** — `monitoring/session_watchdog.py:583` logs lifecycle, sets `abandoned`
8. **Entry: Watchdog fail (ModelException)** — `monitoring/session_watchdog.py:186` sets `failed`, no lifecycle log
9. **Entry: Bridge acknowledgment** — `bridge/telegram_bridge.py:1354` sets `dormant->completed`, logs lifecycle
10. **Entry: PM cancel** — `agent_session_queue.py:779` sets `cancelled`, no lifecycle log
11. **Entry: Startup recovery** — `agent_session_queue.py:1065` sets `running->pending`, no lifecycle log
12. **Entry: Supersede** — `agent_session_queue.py:253` sets `completed->superseded`, no lifecycle log
13. **Entry: CLI recover** — `agent_session_queue.py:2852` sets `running->pending`, no lifecycle log

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #703 | Added `status` to `_extract_agent_session_fields()` | Fixed the immediate zombie bug but left 3 parallel completion paths intact — any future change to completion logic must be replicated in 3 places |
| PR #344 | Fixed session stuck in pending after BUILD COMPLETED | Fixed one specific transition edge case but didn't address the systemic issue of scattered mutations |
| PR #636 | Added crash snapshot before session deletion | Added a new side effect (snapshot) to the completion path, but only in path B — paths A/C/D don't snapshot |
| PR #217 | Introduced `log_lifecycle_transition()` | Created the tool for consistent logging but didn't mandate its use — 8 of 13 mutation sites don't use it |

**Root cause pattern:** Each fix adds a new side effect to one completion path without replicating it to the others. The fix is structural: consolidate into a single function that all paths call.

## Architectural Impact

- **New dependencies**: None — uses existing `log_lifecycle_transition()`, `auto_tag_session()`, `checkpoint_branch_state()`, `_finalize_parent()`
- **Interface changes**: New `finalize_session()` and `transition_status()` functions exposed from a new `models/session_lifecycle.py` module. Existing callers updated to use these instead of direct mutation.
- **Coupling**: Decreases coupling — instead of 6 files each knowing completion side effects, they delegate to one module
- **Data ownership**: No change — AgentSession model still owns status
- **Reversibility**: High — the new functions are wrappers; reverting means restoring direct mutations at each call site

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1 (scope alignment on which side effects apply to which transitions)
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies. All code is internal to the bridge/agent subsystem.

## Solution

### Key Elements

- **`models/session_lifecycle.py`**: New module containing `finalize_session()` for terminal transitions and `transition_status()` for non-terminal transitions
- **Side effect consolidation**: All completion side effects (lifecycle log, auto-tag, branch checkpoint, parent finalization, timestamp update) execute in `finalize_session()` regardless of which code path triggers completion
- **Import-safe design**: The module must be importable from `.claude/hooks/stop.py` (subprocess with limited imports) — use lazy imports for heavy dependencies

### Flow

**Terminal transition:**
Caller → `finalize_session(session, status, reason)` → lifecycle log → auto-tag → branch checkpoint → parent finalization → set status + completed_at → save

**Non-terminal transition:**
Caller → `transition_status(session, new_status, reason)` → lifecycle log → set status → save

### Technical Approach

1. Create `models/session_lifecycle.py` with two public functions:
   - `finalize_session(session, status, reason, *, skip_auto_tag=False, skip_checkpoint=False)` — for terminal statuses (completed, failed, killed, abandoned, cancelled)
   - `transition_status(session, new_status, reason)` — for non-terminal statuses (pending, running, active, dormant, waiting_for_children, superseded)

2. `finalize_session()` executes side effects in order:
   - `log_lifecycle_transition(new_status, reason)` — always
   - `auto_tag_session(session_id)` — skip if `skip_auto_tag=True` (e.g., hooks subprocess)
   - `checkpoint_branch_state(session)` — skip if `skip_checkpoint=True` (e.g., hooks subprocess)
   - `_finalize_parent(parent_id, ...)` — only if session has a parent
   - Set `session.status = status`, `session.completed_at = now`, `session.save()`

3. `transition_status()` is simpler:
   - `log_lifecycle_transition(new_status, reason)` — always
   - Set `session.status = new_status`, `session.save()`

4. Replace all 13 direct mutation sites with calls to these two functions. The hooks subprocess paths (stop.py, subagent_stop.py) use `finalize_session()` with `skip_auto_tag=True, skip_checkpoint=True` to avoid importing heavy dependencies.

5. Update `complete_transcript()` to call `finalize_session()` internally, keeping its transcript-writing logic but delegating the status mutation.

6. Update `_complete_agent_session()` to call `finalize_session()` internally, keeping its branch checkpoint call but delegating the rest.

7. Update model docstring to document all 11 statuses:
   - `pending` — Queued, waiting for worker
   - `running` — Worker picked up, agent executing
   - `active` — Session in progress (transcript tracking)
   - `dormant` — Paused on open question
   - `waiting_for_children` — Parent waiting for child sessions
   - `completed` — Work finished successfully
   - `failed` — Work failed
   - `killed` — Terminated by user/scheduler
   - `abandoned` — Unfinished, auto-detected by watchdog
   - `cancelled` — Cancelled before execution (pending->cancelled)
   - `superseded` — Replaced by a newer session for the same session_id

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `finalize_session()` must catch and log failures in each side effect (auto-tag, checkpoint, parent finalization) without blocking the status save — matching existing behavior in `complete_transcript()`
- [ ] `transition_status()` must catch `ModelException` from save and log a warning (matching existing watchdog behavior)

### Empty/Invalid Input Handling
- [ ] `finalize_session()` with `session=None` raises `ValueError`
- [ ] `finalize_session()` with a non-terminal status raises `ValueError`
- [ ] `transition_status()` with a terminal status raises `ValueError` (directing callers to `finalize_session()`)

### Error State Rendering
- [ ] No user-visible output — this is a bridge-internal change

## Test Impact

- [ ] `tests/unit/test_stop_hook.py::test_stop_hook_has_complete_agent_session` — UPDATE: verify the hook calls `finalize_session` instead of direct mutation
- [ ] `tests/unit/test_session_completion_zombie.py` — UPDATE: assertions should verify `finalize_session` is used; zombie prevention logic is preserved
- [ ] `tests/unit/test_crash_snapshot.py` — UPDATE: verify snapshot still happens via `finalize_session`
- [ ] `tests/integration/test_connectivity_gaps.py::test_complete_transcript_*` — UPDATE: `complete_transcript()` now delegates to `finalize_session()`, verify same observable behavior
- [ ] `tests/integration/test_lifecycle_transition.py::test_complete_transcript_logs_lifecycle` — UPDATE: verify lifecycle logging still occurs through the new path
- [ ] `tests/unit/test_subagent_stop_hook.py` — UPDATE: verify subagent stop uses `finalize_session` instead of direct mutation
- [ ] `tests/unit/test_agent_session_hierarchy.py` — UPDATE: parent finalization tests should verify `finalize_session` handles the parent path

## Rabbit Holes

- **Enforcing valid transitions at the model layer**: Tempting to add a state machine validator that rejects invalid transitions (e.g., `completed->running`). This would break session revival and auto-continue flows that intentionally do `completed->pending`. Defer to a future issue.
- **Async vs sync variants**: The codebase mixes `save()` and `async_save()`. Don't try to unify — just provide sync `finalize_session()` and `transition_status()` and let callers wrap in `asyncio.to_thread()` where needed (matching existing patterns).
- **Refactoring `complete_transcript()` transcript-writing logic**: The transcript file writing is orthogonal to lifecycle management. Don't try to absorb it into `finalize_session()` — keep it in `complete_transcript()` which calls `finalize_session()` for the status part.

## Risks

### Risk 1: Import failures in hooks subprocess
**Impact:** `.claude/hooks/stop.py` runs in a subprocess with limited Python path. If `finalize_session()` imports something unavailable, sessions won't be marked completed/failed.
**Mitigation:** Use lazy imports inside `finalize_session()`. The `skip_auto_tag` and `skip_checkpoint` flags prevent importing `tools.session_tags` and `agent.agent_session_queue` in the hooks context. Test the import path explicitly.

### Risk 2: Breaking auto-continue flow
**Impact:** The nudge loop sets `completed->pending` which is technically a "terminal->non-terminal" transition. If `transition_status()` rejects this, auto-continue breaks.
**Mitigation:** `transition_status()` handles revive/re-enqueue as a special case. The `completed->pending` transition is explicitly allowed with reason logging. Alternatively, create a `revive_session()` convenience function.

## Race Conditions

### Race 1: Worker finally block vs complete_transcript
**Location:** `agent/agent_session_queue.py:1654` and `bridge/session_transcript.py:252`
**Trigger:** Both paths can run close together when a session ends — the SDK client calls `complete_transcript()` and the worker's finally block calls `_complete_agent_session()`.
**Data prerequisite:** The session must exist in Redis.
**State prerequisite:** The session should only be completed once.
**Mitigation:** `finalize_session()` is idempotent — if the session is already in a terminal state, it logs and returns without re-executing side effects. This matches the existing guard in `_complete_agent_session()` where `_session_is_completed_guard()` prevents double completion.

## No-Gos (Out of Scope)

- State machine validator that rejects invalid transitions — defer to future issue
- Async variants of `finalize_session()` / `transition_status()` — callers use `asyncio.to_thread()` as needed
- Refactoring transcript file I/O — stays in `complete_transcript()`
- Changing status field names or values — all 11 values are preserved as-is
- Adding new status values — this is a consolidation, not a redesign

## Update System

No update system changes required — this is a bridge-internal refactoring. No new dependencies, no new config files, no migration steps. The existing `/update` skill pulls the latest code, which includes the refactored module.

## Agent Integration

No agent integration required — this is a bridge-internal refactoring. The agent does not directly call lifecycle functions. The bridge, worker, and hooks call them. No MCP server changes, no `.mcp.json` changes, no new tools.

## Documentation

- [ ] Create `docs/features/session-lifecycle.md` describing the consolidated lifecycle functions, the status state diagram, and which side effects occur at each transition
- [ ] Update model docstring in `models/agent_session.py` to document all 11 statuses (this is part of the implementation, not a separate doc task)
- [ ] Add entry to `docs/features/README.md` index table for session-lifecycle

## Success Criteria

- [ ] `finalize_session()` exists in `models/session_lifecycle.py` and handles all terminal transitions
- [ ] `transition_status()` exists in `models/session_lifecycle.py` and handles all non-terminal transitions
- [ ] All 13 direct mutation sites replaced with calls to `finalize_session()` or `transition_status()`
- [ ] All completion side effects (lifecycle log, auto-tag, branch checkpoint, parent finalization) execute through `finalize_session()` regardless of caller
- [ ] Model docstring lists all 11 statuses with definitions
- [ ] `finalize_session()` is importable from `.claude/hooks/stop.py` subprocess context
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (lifecycle-module)**
  - Name: lifecycle-builder
  - Role: Create `models/session_lifecycle.py` and replace all direct mutation sites
  - Agent Type: builder
  - Resume: true

- **Validator (lifecycle-module)**
  - Name: lifecycle-validator
  - Role: Verify all mutation sites use the new functions, no direct `.status =` mutations for lifecycle changes outside the new module
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Create lifecycle module
- **Task ID**: build-lifecycle-module
- **Depends On**: none
- **Validates**: `tests/unit/test_session_lifecycle_consolidation.py` (create)
- **Assigned To**: lifecycle-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `models/session_lifecycle.py` with `finalize_session()` and `transition_status()`
- `finalize_session(session, status, reason, *, skip_auto_tag=False, skip_checkpoint=False)` — validates status is terminal, executes all side effects in order, sets status + completed_at, saves
- `transition_status(session, new_status, reason)` — validates status is non-terminal (with revive exception), logs lifecycle, sets status, saves
- Both functions are idempotent: if session is already in the target state, log and return
- Use lazy imports for `tools.session_tags`, `agent.agent_session_queue.checkpoint_branch_state`, `agent.agent_session_queue._finalize_parent`
- Write unit tests covering: terminal transitions, non-terminal transitions, idempotency, invalid status rejection, skip flags, import safety

### 2. Replace completion paths
- **Task ID**: build-replace-completions
- **Depends On**: build-lifecycle-module
- **Validates**: `tests/unit/test_stop_hook.py`, `tests/unit/test_subagent_stop_hook.py`, `tests/integration/test_connectivity_gaps.py`, `tests/unit/test_crash_snapshot.py`
- **Assigned To**: lifecycle-builder
- **Agent Type**: builder
- **Parallel**: false
- Replace Path A (`complete_transcript`): keep transcript writing, delegate status mutation to `finalize_session()`
- Replace Path B (`_complete_agent_session`): delegate to `finalize_session()`, remove redundant status/save
- Replace Path C (`stop.py`): call `finalize_session()` with `skip_auto_tag=True, skip_checkpoint=True`
- Replace Path D (`subagent_stop.py`): call `finalize_session()` with appropriate skip flags
- Update existing tests to verify the new delegation

### 3. Replace non-terminal mutations
- **Task ID**: build-replace-transitions
- **Depends On**: build-lifecycle-module
- **Validates**: `tests/unit/test_session_completion_zombie.py`, `tests/unit/test_agent_session_hierarchy.py`
- **Assigned To**: lifecycle-builder
- **Agent Type**: builder
- **Parallel**: false
- Replace pending->running (worker pickup, lines 564/668)
- Replace running->pending (health check recovery, startup recovery, CLI recovery, lines 1065/1200/2852)
- Replace completed->pending (nudge re-enqueue, line 1914)
- Replace dormant->completed (bridge acknowledgment, line 1354)
- Replace completed->superseded (push session, line 253)
- Replace pending->cancelled (PM cancel, line 779)
- Replace running->abandoned/failed (watchdog, lines 186/583/1191/1245)

### 4. Update model docstring
- **Task ID**: build-update-docstring
- **Depends On**: build-replace-transitions
- **Validates**: none (documentation change)
- **Assigned To**: lifecycle-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `models/agent_session.py` class docstring to list all 11 statuses with definitions
- Add valid transitions documentation

### 5. Validate consolidation
- **Task ID**: validate-consolidation
- **Depends On**: build-replace-transitions
- **Assigned To**: lifecycle-validator
- **Agent Type**: validator
- **Parallel**: false
- `grep -rn '\.status\s*=' models/ agent/ bridge/ monitoring/ .claude/hooks/ --include="*.py"` — verify no direct status mutations outside `models/session_lifecycle.py` (except test files)
- Run full test suite
- Verify `finalize_session` is importable from a clean Python subprocess (simulating hooks context)

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-consolidation
- **Assigned To**: lifecycle-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/session-lifecycle.md` with status state diagram and function reference
- Add entry to `docs/features/README.md`

### 7. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: lifecycle-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| No direct status mutations outside lifecycle module | `grep -rn '\.status\s*=\s*"' agent/ bridge/ monitoring/ .claude/hooks/ --include="*.py" \| grep -v test \| grep -v session_lifecycle` | exit code 1 |
| Lifecycle module importable from subprocess | `python -c "from models.session_lifecycle import finalize_session, transition_status"` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. **Revive semantics**: The nudge loop currently does `completed->pending` to re-enqueue sessions for auto-continue. Should this go through `transition_status()` (treating it as a non-terminal transition) or should there be a dedicated `revive_session()` function that explicitly handles the terminal->non-terminal exception? The plan currently proposes allowing it as a special case in `transition_status()`.

2. **Side effect granularity for watchdog paths**: The watchdog marks sessions as `failed` (ModelException path, line 186) and `abandoned` (line 583). The `abandoned` path already calls `log_lifecycle_transition()` but the `failed` path doesn't. With `finalize_session()`, both would get lifecycle logging + auto-tag + checkpoint. Is it correct to add auto-tagging and branch checkpointing to the watchdog's `failed` path, or should those be skipped (the session may be corrupted)?
