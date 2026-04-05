---
status: Ready
type: bug
appetite: Medium
owner: Valor
created: 2026-04-05
tracking: https://github.com/tomcounsell/ai/issues/723
last_comment_id:
---

# Session Recovery Audit — Terminal Status Respawn Safety

## Problem

After the zombie loop fix in PR #703, 7 session recovery mechanisms exist (5 active + 2 confirmed safe), each written at a different time with different assumptions about session lifecycle. No systematic audit has verified that ALL active mechanisms properly respect terminal session states (`completed`, `failed`, `killed`, `abandoned`, `cancelled`).

**Current behavior:**
- `determine_delivery_action()` (L80) checks only `session_status == "completed"` before returning `deliver_already_completed`. Sessions in `failed`, `killed`, `abandoned`, or `cancelled` states fall through to nudge logic, meaning terminal non-completed sessions can be nudged.
- `_enqueue_nudge()` has no explicit terminal status guard — it relies on `determine_delivery_action()` upstream. Worse, the fallback path (L1778-1804) bypasses `transition_status()` entirely, setting `fields["status"] = "pending"` directly via `AgentSession.async_create()`. This path has zero terminal status protection.
- `transition_status()` in `session_lifecycle.py` has NO source-status check. The docstring claims "completed->pending is allowed for session revival/auto-continue" (L140) but the code does not actually inspect the current status before allowing the transition. Any status can transition to any non-terminal status. This means `_mark_superseded()` in `enqueue_agent_session()` uses `transition_status(old, "superseded")` on completed sessions — this works because `superseded` is classified as NON_TERMINAL, but the lack of source-status validation is a latent risk.
- The revival system (`check_revival()`) queries `pending`/`running` sessions from Redis by `project_key`+`status` (not by `session_id`), then matches by `chat_id`. It can match stale entries whose logical work is completed (Redis state not cleaned up, git branch still exists), creating duplicate sessions for already-completed work.

**Desired outcome:**
- Every recovery mechanism is verified safe against terminal status respawn, with fixes where gaps exist
- The revival system has an explicit guard against sessions whose work has a terminal-status sibling
- A regression test per mechanism proves completed/failed sessions cannot be respawned
- A single reference doc at `docs/features/session-recovery-mechanisms.md` catalogues all mechanisms

## Prior Art

- **PR #703**: Fix session completion zombie loop — added `status` to `_AGENT_SESSION_FIELDS` and nudge guard in worker finally block. Emergency fix, targeted at hierarchy health check only.
- **PR #719**: Added integration test for session zombie health check (#717) — regression test for the hierarchy path specifically.
- **PR #721**: Consolidated session lifecycle mutations into `models/session_lifecycle.py` — created `finalize_session()` and `transition_status()` as single entry points.
- **Issue #700**: Original zombie loop bug report — completed sessions reverted to pending via hierarchy health check.
- **Issue #701**: Consolidate AgentSession lifecycle mutations into single-entrypoint functions — shipped as PR #721.
- **Issue #471**: Test coverage gaps for nudge loop, cross-project routing, revival path — closed but revival path tests were not fully addressed.

## Data Flow

Recovery mechanisms interact with session state at three layers:

1. **Entry point**: Recovery trigger fires (startup, timer, per-output, per-message)
2. **Redis query**: Mechanism queries `AgentSession.query.filter(status=X)` to find candidates
3. **Status mutation**: Mechanism calls `transition_status(session, "pending")` or `finalize_session()` from `models/session_lifecycle.py`
4. **Worker dispatch**: `_ensure_worker(chat_id)` starts a worker loop that pops the next pending session
5. **Execution**: Worker calls `_execute_agent_session()` which runs the SDK agent
6. **Output routing**: `send_to_chat()` → `determine_delivery_action()` → possibly `_enqueue_nudge()` which calls `transition_status(session, "pending")` again

The race window is between steps 2 and 3: a session could transition to terminal between the query and the mutation. The `transition_status()` function does not inspect the source status at all — it accepts any current status transitioning to any non-terminal status. Guards must live in callers, and additionally the fallback path in `_enqueue_nudge()` bypasses `transition_status()` entirely (uses raw `async_create()`), so it needs its own independent guard.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #703 | Added nudge guard in worker finally block to re-read status before completing | Fixed the specific hierarchy health check vector but did not audit the other 6 mechanisms |
| PR #721 | Consolidated lifecycle mutations into `session_lifecycle.py` | Improved code structure but `transition_status()` deliberately allows `completed→pending` with no caller-side guard enforcement |

**Root cause pattern:** Each fix addressed the symptom it observed (hierarchy check, nudge overwrite) without auditing the full surface area of recovery mechanisms that could trigger the same class of bug.

## Architectural Impact

- **No new dependencies**: Pure audit and guard additions
- **Interface changes**: `determine_delivery_action()` checks all terminal statuses (not just `completed`); `_enqueue_nudge()` gains terminal guards on both main and fallback paths; `check_revival()` gains a terminal-session filter; `transition_status()` gains a `reject_from_terminal` parameter (default `True`, backward-compatible signature change)
- **Coupling**: Minimal — `determine_delivery_action()` gains an import of `TERMINAL_STATUSES` from `models.session_lifecycle`; guards in `_enqueue_nudge()` use the same constant
- **Reversibility**: High — guards are additive `if` checks that can be removed without structural changes. The `reject_from_terminal` parameter defaults to `True` so removing it restores the old permissive behavior

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1 (scope confirmation before build)
- Review rounds: 1 (code review of guard additions + tests)

## Prerequisites

No prerequisites — this work operates on existing code with no external dependencies.

## Solution

### Key Elements

- **Audit matrix**: Systematic checklist of all 5 active mechanisms against terminal-status safety criteria (2 confirmed-safe mechanisms documented but not modified)
- **`determine_delivery_action()` fix**: Check all `TERMINAL_STATUSES`, not just `completed`
- **Nudge guard (main path)**: Add explicit terminal status check at the top of `_enqueue_nudge()` before mutating status
- **Nudge guard (fallback path)**: Add terminal status check in the fallback `async_create()` path (L1778-1804) that currently bypasses `transition_status()`
- **Revival guard**: Add terminal-session check to `check_revival()` that filters out branches belonging to sessions with terminal status
- **`transition_status()` source-status guard**: Add an optional `reject_from_terminal=True` parameter that rejects transitions when the current status is terminal. Default `True` to prevent accidental respawns. Callers that legitimately need terminal→non-terminal (revival, `_mark_superseded`) pass `reject_from_terminal=False`.
- **Regression test suite**: One test per mechanism proving completed/failed sessions are not respawned
- **Reference doc**: Single document cataloguing all recovery mechanisms

### Flow

**Audit phase** → Identify gaps → **Fix phase** (delivery action + nudge guards + revival guard + transition_status guard) → **Test phase** (regression tests per mechanism) → **Doc phase** (reference doc)

### Technical Approach

- Each mechanism is checked against three criteria: (1) queries only non-terminal statuses, (2) re-reads status atomically before acting, (3) no race window between read and mutation
- For `determine_delivery_action()`: change L80 from `if session_status == "completed"` to `if session_status in TERMINAL_STATUSES` (import from `models.session_lifecycle`). This ensures `failed`, `killed`, `abandoned`, and `cancelled` sessions are not nudged.
- For `_enqueue_nudge()` main path: add a guard at function entry that re-reads the session from Redis and returns early if status is in `TERMINAL_STATUSES`. This makes the function self-defending rather than relying on caller discipline.
- For `_enqueue_nudge()` fallback path (L1778-1804): before setting `fields["status"] = "pending"` and calling `async_create()`, check the extracted session status. If the original session's status is in `TERMINAL_STATUSES`, log a warning and return early. This path bypasses `transition_status()` so it needs its own independent guard.
- For `check_revival()`: after finding pending/running sessions for a chat by `project_key`+`status`, also query terminal sessions for the same `chat_id`. If a terminal session exists with a matching branch name (derived from `session_id`), skip the revival for that branch. This handles the stale-Redis-entry scenario where a session is logically done but its pending/running record hasn't been cleaned up.
- For `transition_status()`: add a `reject_from_terminal` parameter (default `True`). When `True` and the current session status is in `TERMINAL_STATUSES`, raise `ValueError`. Callers that legitimately need this transition:
  - `_mark_superseded()` in `enqueue_agent_session()` → pass `reject_from_terminal=False` (transitioning `completed→superseded` is intentional)
  - `queue_revival_agent_session()` → pass `reject_from_terminal=False` (intentional revival)
  - All other callers use the default, making accidental terminal→non-terminal a hard error

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_enqueue_nudge()` fallback path (L1778-1804) now has terminal guard BEFORE `async_create()` — verify a terminal session triggers early return and warning log, not session recreation
- [ ] `_enqueue_nudge()` main path re-reads session from Redis — verify a session that became terminal between caller check and nudge call triggers early return
- [ ] `check_revival()` catches Redis query failures (L2441) — verify it returns `None` (no revival) rather than silently proceeding
- [ ] Worker finally block catches guard check failures — verify it still completes the session rather than leaving it in limbo
- [ ] `determine_delivery_action()` with each terminal status (`completed`, `failed`, `killed`, `abandoned`, `cancelled`) returns `deliver_already_completed`

### Empty/Invalid Input Handling
- [ ] `_enqueue_nudge()` called with a session that has `session_id=None` — verify it does not create an orphan pending session
- [ ] `check_revival()` called with empty `chat_id` — verify no false positive matches
- [ ] `transition_status()` called on terminal session without `reject_from_terminal=False` — verify `ValueError` raised

### Error State Rendering
- [ ] When a terminal guard blocks a respawn, a warning log is emitted (observable in tests via caplog)
- [ ] When `transition_status()` rejects a terminal→non-terminal transition, the error message includes both current and target status

## Test Impact

- [ ] `tests/integration/test_session_zombie_health_check.py` — UPDATE: extend with tests for nudge path and revival path (currently only tests hierarchy health check)
- [ ] `tests/unit/test_delivery_execution.py` — UPDATE: add test cases for each terminal status (`failed`, `killed`, `abandoned`, `cancelled`) as input to `determine_delivery_action()` returning `deliver_already_completed`
- [ ] `tests/unit/test_session_lifecycle_consolidation.py` — UPDATE: add tests for `transition_status()` rejecting terminal→non-terminal by default, and allowing it when `reject_from_terminal=False`

## Rabbit Holes

- **Restructuring all 7 mechanisms into a unified recovery framework**: Tempting but out of scope. The issue asks for an audit and guards, not an architecture rewrite.
- **Adding distributed locks or Redis transactions**: The race windows are narrow and the consequence is a duplicate session (annoying, not data-destroying). Simple re-read guards are sufficient.
- **Cleaning up stale Redis entries as part of this audit**: That is a separate concern (data hygiene). This audit focuses on ensuring stale entries do not cause respawns.

## Risks

### Risk 1: Tightening `transition_status()` breaks `_mark_superseded` and intentional revival
**Impact:** `_mark_superseded()` in `enqueue_agent_session()` calls `transition_status(old, "superseded")` on completed sessions — this is a legitimate `completed→superseded` transition. The revival system and auto-continue rely on `completed→pending` transitions. Adding `reject_from_terminal=True` default would break both paths.
**Mitigation:** Grep all `transition_status()` call sites. Three callers need `reject_from_terminal=False`: (1) `_mark_superseded()` — completed→superseded is intentional bookkeeping, (2) `queue_revival_agent_session()` (via `enqueue_agent_session()`) — intentional revival, (3) `_enqueue_nudge()` main path if it ever processes a session that was legitimately terminal. All other callers use the default. Update all callers atomically in a single commit.

### Risk 2: Revival guard false negatives from Redis TTL
**Impact:** If terminal session records expire from Redis before the revival check, the guard won't find them and revival proceeds.
**Mitigation:** Accept this as a design limitation. If the session record is gone from Redis, there is no reliable way to detect prior completion. Document this edge case in the reference doc.

## Race Conditions

### Race 1: Status changes between `determine_delivery_action()` and `_enqueue_nudge()`
**Location:** `agent_session_queue.py` L2001-2069
**Trigger:** External process (health check, watchdog) finalizes the session between the delivery action decision and the nudge enqueue call
**Data prerequisite:** Session must still exist in Redis with non-terminal status when `_enqueue_nudge` reads it
**State prerequisite:** Session status must not have transitioned to terminal between the two calls
**Mitigation:** `_enqueue_nudge()` will re-read session status from Redis at entry and return early if terminal. The re-read is the atomic guard.

### Race 2: Revival check finds pending session that is about to complete
**Location:** `agent_session_queue.py` L2430-2460
**Trigger:** `check_revival()` queries pending/running sessions, finds one. Between the query and the revival notification, the session completes normally.
**Data prerequisite:** Session must be in pending/running when queried
**State prerequisite:** Session worker must still be executing
**Mitigation:** Revival only sends a notification; it does not immediately respawn. The actual respawn (`queue_revival_agent_session()`) happens later when the user responds. By that time the session will be terminal and the guard will catch it.

## No-Gos (Out of Scope)

- Restructuring recovery mechanisms into a unified framework
- Adding Redis transactions or distributed locks
- Cleaning up stale Redis session entries
- Changing the session TTL or expiry policy
- Modifying the bridge watchdog or session watchdog behavior (confirmed safe by recon)

## Update System

No update system changes required — this is a bridge-internal change with no new dependencies, config files, or migration steps.

## Agent Integration

No agent integration required — this is a bridge-internal change. All modifications are to `agent/agent_session_queue.py` and `models/session_lifecycle.py`, which are already part of the bridge runtime. No MCP server changes needed.

## Documentation

- [ ] Create `docs/features/session-recovery-mechanisms.md` — reference doc listing all 7 mechanisms, their triggers, guards, and intended behavior
- [ ] Update `docs/features/session-lifecycle.md` — add section on terminal status respawn protection
- [ ] Add entry to `docs/features/README.md` index table for the new recovery mechanisms doc

## Success Criteria

- [ ] All 5 active + 2 confirmed-safe recovery mechanisms audited with findings documented in the reference doc
- [ ] `determine_delivery_action()` checks all `TERMINAL_STATUSES`, not just `completed`
- [ ] `_enqueue_nudge()` main path has explicit terminal status guard (re-read from Redis, return early if terminal)
- [ ] `_enqueue_nudge()` fallback path (L1778-1804) has terminal status guard before `async_create()`
- [ ] `check_revival()` filters out branches whose sessions have terminal status siblings
- [ ] `transition_status()` rejects terminal→non-terminal by default (requires `reject_from_terminal=False`)
- [ ] `_mark_superseded()` still works (passes `reject_from_terminal=False` for completed→superseded)
- [ ] At least one regression test per mechanism proving completed/failed sessions are not respawned
- [ ] Reference doc created at `docs/features/session-recovery-mechanisms.md`
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (recovery-guards)**
  - Name: guard-builder
  - Role: Implement terminal status guards in `_enqueue_nudge()`, `check_revival()`, and `transition_status()`
  - Agent Type: builder
  - Resume: true

- **Builder (regression-tests)**
  - Name: test-builder
  - Role: Write regression tests for each recovery mechanism
  - Agent Type: test-engineer
  - Resume: true

- **Builder (docs)**
  - Name: docs-builder
  - Role: Create recovery mechanisms reference doc and update session lifecycle docs
  - Agent Type: documentarian
  - Resume: true

- **Validator (all)**
  - Name: audit-validator
  - Role: Verify all guards work, all tests pass, all mechanisms audited
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Audit All Mechanisms & Implement Guards
- **Task ID**: build-guards
- **Depends On**: none
- **Validates**: `tests/unit/test_session_lifecycle_consolidation.py`, `tests/unit/test_delivery_execution.py`
- **Assigned To**: guard-builder
- **Agent Type**: builder
- **Parallel**: true
- Audit all 5 active mechanisms against the terminal-status safety checklist (query scope, atomic re-read, race window). Document the 2 confirmed-safe mechanisms (session watchdog, bridge watchdog) without code changes.
- **Fix `determine_delivery_action()`** (BLOCKER 1): Change L80 from `if session_status == "completed"` to `if session_status in TERMINAL_STATUSES`. Import `TERMINAL_STATUSES` from `models.session_lifecycle`.
- **Fix `_enqueue_nudge()` main path**: Add terminal status guard at function entry — re-read session from Redis, return early with warning log if status in `TERMINAL_STATUSES`
- **Fix `_enqueue_nudge()` fallback path** (BLOCKER 2): Before `fields["status"] = "pending"` at L1784, check extracted session status. If in `TERMINAL_STATUSES`, log warning and return early. This path bypasses `transition_status()` so needs its own guard.
- **Fix `transition_status()`**: Add `reject_from_terminal: bool = True` parameter. When `True` and `session.status in TERMINAL_STATUSES`, raise `ValueError` with both current and target status in the message. Update callers:
  - `_mark_superseded()` in `enqueue_agent_session()` → pass `reject_from_terminal=False` (completed→superseded is intentional)
  - `queue_revival_agent_session()` path → pass `reject_from_terminal=False`
  - All other callers keep default `True`
- **Fix `check_revival()`**: After finding pending/running sessions by `project_key`+`status`+`chat_id`, also query terminal sessions for the same `chat_id`. For each candidate branch, check if a terminal session exists with a matching `session_id` (derived from branch name). If so, skip that branch.

### 2. Write Regression Tests
- **Task ID**: build-tests
- **Depends On**: build-guards
- **Validates**: `tests/unit/test_recovery_respawn_safety.py` (create)
- **Assigned To**: test-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- Create `tests/unit/test_recovery_respawn_safety.py` with one test per mechanism:
  - `test_startup_recovery_skips_completed` — create completed session, run `_recover_interrupted_agent_sessions_startup()`, verify not respawned
  - `test_health_check_skips_completed` — create completed session, run `_agent_session_health_check()`, verify not respawned
  - `test_hierarchy_check_skips_completed` — create completed parent, run `_agent_session_hierarchy_health_check()`, verify not respawned (existing test in `test_session_zombie_health_check.py` covers this, but add to unified suite)
  - `test_nudge_main_path_skips_terminal` — create completed session, call `_enqueue_nudge()`, verify returns early without mutating status
  - `test_nudge_fallback_path_skips_terminal` — simulate missing session (fallback path), verify terminal status prevents `async_create()` with `status="pending"`
  - `test_determine_delivery_action_all_terminal_statuses` — verify each of `completed`, `failed`, `killed`, `abandoned`, `cancelled` returns `deliver_already_completed`
  - `test_revival_skips_completed` — create completed session with matching branch, call `check_revival()`, verify returns None
  - `test_session_watchdog_safe` — verify watchdog only abandons, never respawns (document-only test asserting code path)
  - `test_bridge_watchdog_safe` — verify bridge watchdog has no `AgentSession` imports (document-only test asserting code path)
- Add test for `transition_status()` rejecting terminal→non-terminal by default, and allowing with `reject_from_terminal=False`
- Add test for `transition_status()` allowing `completed→superseded` with `reject_from_terminal=False` (protecting `_mark_superseded` path)

### 3. Create Reference Documentation
- **Task ID**: build-docs
- **Depends On**: build-guards
- **Validates**: `docs/features/session-recovery-mechanisms.md` exists
- **Assigned To**: docs-builder
- **Agent Type**: documentarian
- **Parallel**: true (parallel with build-tests)
- Create `docs/features/session-recovery-mechanisms.md` with:
  - Table of all 7 mechanisms: name, location, trigger, what it does, terminal status safety status
  - Audit findings summary
  - Race condition analysis
  - Edge cases and known limitations (e.g., Redis TTL expiry)
- Update `docs/features/session-lifecycle.md` with terminal status respawn protection section
- Add entry to `docs/features/README.md`

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-guards, build-tests, build-docs
- **Assigned To**: audit-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite: `pytest tests/ -x -q`
- Verify all 7 mechanisms are documented in the reference doc
- Verify `_enqueue_nudge()` has terminal guard (grep for `TERMINAL_STATUSES` in function body)
- Verify `check_revival()` has terminal-session filter
- Verify `transition_status()` has `allow_revival` parameter
- Verify all success criteria met

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Delivery action checks all terminal | `grep -c 'TERMINAL_STATUSES' agent/agent_session_queue.py` | output >= 3 |
| Nudge fallback guard exists | `grep -A5 'Fallback: recreate' agent/agent_session_queue.py \| grep -c 'TERMINAL'` | output >= 1 |
| Revival guard exists | `grep -c 'terminal' agent/agent_session_queue.py` | output > 0 |
| transition_status has reject_from_terminal | `grep -c 'reject_from_terminal' models/session_lifecycle.py` | output >= 1 |
| Reference doc exists | `test -f docs/features/session-recovery-mechanisms.md` | exit code 0 |
| Recovery tests exist | `pytest tests/unit/test_recovery_respawn_safety.py -q` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room) on 2026-04-05. Verdict: NEEDS REVISION (2 blockers). -->
<!-- Revised 2026-04-05: All findings addressed. -->
| Severity | Critic(s) | Finding | Resolution |
|----------|-----------|---------|------------|
| BLOCKER | Adversary, Skeptic | `determine_delivery_action()` L80 only checks `completed`, not all terminal statuses — `failed`/`killed`/`abandoned`/`cancelled` sessions fall through to nudge logic | FIXED: Plan now includes explicit fix to check `session_status in TERMINAL_STATUSES` instead of `== "completed"`. Added to Problem, Solution, Step 1, and Success Criteria. |
| BLOCKER | Adversary, Skeptic | `_enqueue_nudge()` fallback path (L1778-1803) bypasses `transition_status()` entirely — sets `fields["status"] = "pending"` directly via `async_create()`, needs its own terminal guard | FIXED: Plan now includes independent terminal guard on fallback path before `async_create()`. Added to Problem, Solution (separate bullet), Step 1, Failure Path Tests, and Success Criteria. |
| CONCERN | Archaeologist, Skeptic | `transition_status()` has NO source-status check at all (not a deliberate escape hatch as plan states); `_mark_superseded` at L256 transitions `completed->superseded` and would break under proposed `allow_revival` change | FIXED: Plan now correctly describes `transition_status()` as having no source-status check. Changed parameter from `allow_revival` to `reject_from_terminal` (default `True`). Explicitly lists `_mark_superseded` as a caller needing `reject_from_terminal=False`. Added regression test for completed→superseded path. |
| CONCERN | Operator, Adversary | `check_revival()` terminal-session filter needs clarification: should match on `session_id` or `branch_name`? Current code queries by `project_key`+`status`, not `session_id` | FIXED: Plan now clarifies: query terminal sessions for same `chat_id`, then match by branch name (derived from `session_id` via `_session_branch_name()`). This aligns with how the existing code identifies branches. |
| CONCERN | Operator | Test Impact references `tests/unit/test_determine_delivery_action.py` and `tests/unit/test_session_lifecycle.py` — neither exists; actual file is `tests/unit/test_session_lifecycle_consolidation.py` | FIXED: Test Impact now references correct files: `tests/unit/test_delivery_execution.py` and `tests/unit/test_session_lifecycle_consolidation.py`. |
| NIT | Simplifier | Plan frames "7 mechanisms" but 2 (session watchdog, bridge watchdog) are confirmed no-ops by recon; consider framing as "5 active + 2 confirmed safe" | FIXED: Reframed throughout as "5 active + 2 confirmed safe". |

---

## Open Questions

No open questions — the issue recon confirmed which mechanisms are safe and which need guards. The scope is well-defined.
