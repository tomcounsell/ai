---
status: Planning
type: bug
appetite: Medium
owner: Valor
created: 2026-04-05
tracking: https://github.com/tomcounsell/ai/issues/723
last_comment_id:
---

# Session Recovery Audit — Terminal Status Respawn Safety

## Problem

After the zombie loop fix in PR #703, 7 independent session recovery mechanisms exist, each written at a different time with different assumptions about session lifecycle. No systematic audit has verified that ALL of them properly respect terminal session states (`completed`, `failed`, `killed`, `abandoned`, `cancelled`).

**Current behavior:**
- The revival system (`check_revival()`) queries `pending`/`running` sessions from Redis but can match stale entries whose logical work is completed (Redis state not cleaned up, git branch still exists). This creates duplicate sessions for already-completed work.
- `_enqueue_nudge()` has no explicit terminal status guard — it relies on `determine_delivery_action()` returning `deliver_already_completed` upstream. If the caller bypasses that check or the session status changes between the check and the nudge call, the nudge can overwrite a terminal status back to `pending`.
- `transition_status()` in `session_lifecycle.py` allows `completed->pending` transitions as a deliberate escape hatch (line 140: "completed->pending is allowed for session revival/auto-continue") but has no audit trail for who invoked it, making it impossible to distinguish intentional revival from accidental respawn.

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

The race window is between steps 2 and 3: a session could transition to terminal between the query and the mutation. The `transition_status()` function does not reject terminal→pending transitions (by design, for revival), so the guard must live in the caller.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #703 | Added nudge guard in worker finally block to re-read status before completing | Fixed the specific hierarchy health check vector but did not audit the other 6 mechanisms |
| PR #721 | Consolidated lifecycle mutations into `session_lifecycle.py` | Improved code structure but `transition_status()` deliberately allows `completed→pending` with no caller-side guard enforcement |

**Root cause pattern:** Each fix addressed the symptom it observed (hierarchy check, nudge overwrite) without auditing the full surface area of recovery mechanisms that could trigger the same class of bug.

## Architectural Impact

- **No new dependencies**: Pure audit and guard additions
- **Interface changes**: `_enqueue_nudge()` gains a terminal status guard; `check_revival()` gains a completed-session filter
- **Coupling**: No change — guards are added at the caller level, not the lifecycle module
- **Reversibility**: High — guards are additive `if` checks that can be removed without structural changes

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

- **Audit matrix**: Systematic checklist of all 7 mechanisms against terminal-status safety criteria
- **Revival guard**: Add terminal-session check to `check_revival()` that filters out branches belonging to sessions with terminal status
- **Nudge guard**: Add explicit terminal status check at the top of `_enqueue_nudge()` before mutating status
- **Regression test suite**: One test per mechanism proving completed/failed sessions are not respawned
- **Reference doc**: Single document cataloguing all recovery mechanisms

### Flow

**Audit phase** → Identify gaps → **Fix phase** (revival guard + nudge guard) → **Test phase** (regression tests per mechanism) → **Doc phase** (reference doc)

### Technical Approach

- Each mechanism is checked against three criteria: (1) queries only non-terminal statuses, (2) re-reads status atomically before acting, (3) no race window between read and mutation
- For `check_revival()`: after finding pending/running sessions for a chat, also query for terminal sessions with the same `session_id` to detect stale Redis entries. If a terminal session exists for the same work, skip the revival.
- For `_enqueue_nudge()`: add a guard at function entry that re-reads the session from Redis and returns early if status is in `TERMINAL_STATUSES`. This makes the function self-defending rather than relying on caller discipline.
- For `transition_status()`: tighten the `completed→pending` escape hatch by requiring an explicit `allow_revival=True` parameter. Default behavior rejects terminal→non-terminal transitions. This makes accidental respawns a hard error.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_enqueue_nudge()` fallback path (line 1778-1803) catches all exceptions and recreates session — verify the recreated session respects terminal status
- [ ] `check_revival()` catches Redis query failures (line 2441) — verify it returns `None` (no revival) rather than silently proceeding
- [ ] Worker finally block (line 1594-1624) catches guard check failures — verify it still completes the session rather than leaving it in limbo

### Empty/Invalid Input Handling
- [ ] `_enqueue_nudge()` called with a session that has `session_id=None` — verify it does not create an orphan pending session
- [ ] `check_revival()` called with empty `chat_id` — verify no false positive matches

### Error State Rendering
- [ ] When a terminal guard blocks a respawn, a warning log is emitted (observable in tests via caplog)

## Test Impact

- [ ] `tests/integration/test_session_zombie_health_check.py` — UPDATE: extend with tests for nudge path and revival path (currently only tests hierarchy health check)
- [ ] `tests/unit/test_determine_delivery_action.py` — UPDATE: add test cases for terminal session input to `determine_delivery_action()`
- [ ] `tests/unit/test_session_lifecycle.py` — UPDATE: add tests for `transition_status()` rejecting terminal→pending without `allow_revival=True`

## Rabbit Holes

- **Restructuring all 7 mechanisms into a unified recovery framework**: Tempting but out of scope. The issue asks for an audit and guards, not an architecture rewrite.
- **Adding distributed locks or Redis transactions**: The race windows are narrow and the consequence is a duplicate session (annoying, not data-destroying). Simple re-read guards are sufficient.
- **Cleaning up stale Redis entries as part of this audit**: That is a separate concern (data hygiene). This audit focuses on ensuring stale entries do not cause respawns.

## Risks

### Risk 1: Tightening `transition_status()` breaks intentional revival
**Impact:** The revival system and auto-continue both rely on `completed→pending` transitions. Adding `allow_revival=True` could break callers that don't pass it.
**Mitigation:** Grep all `transition_status()` call sites (found 10 in the codebase). Only `_enqueue_nudge()` and `queue_revival_agent_session()` (via `enqueue_agent_session()`) need the flag. Update all callers atomically.

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

- [ ] All 7 recovery mechanisms audited with findings documented in the reference doc
- [ ] `_enqueue_nudge()` has explicit terminal status guard (re-read from Redis, return early if terminal)
- [ ] `check_revival()` filters out branches whose sessions have terminal status siblings
- [ ] `transition_status()` rejects terminal→non-terminal by default (requires `allow_revival=True`)
- [ ] At least one regression test per mechanism proving completed sessions are not respawned
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
- **Validates**: `tests/unit/test_session_lifecycle.py`, `tests/unit/test_enqueue_nudge_terminal_guard.py` (create)
- **Assigned To**: guard-builder
- **Agent Type**: builder
- **Parallel**: true
- Audit all 7 mechanisms against the terminal-status safety checklist (query scope, atomic re-read, race window)
- Add terminal status guard to `_enqueue_nudge()`: re-read session from Redis at entry, return early if status in `TERMINAL_STATUSES`
- Add terminal-session filter to `check_revival()`: after finding pending/running sessions, check if any terminal session exists with the same session_id; if so, skip revival
- Tighten `transition_status()` to reject terminal→non-terminal transitions unless `allow_revival=True` is passed
- Update all `transition_status()` callers that legitimately need terminal→pending to pass `allow_revival=True`

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
  - `test_nudge_skips_completed` — create completed session, call `_enqueue_nudge()`, verify returns early without mutating status
  - `test_revival_skips_completed` — create completed session with matching branch, call `check_revival()`, verify returns None
  - `test_session_watchdog_safe` — verify watchdog only abandons, never respawns (document-only test asserting code path)
  - `test_bridge_watchdog_safe` — verify bridge watchdog has no `AgentSession` imports (document-only test asserting code path)
- Add test for `transition_status()` rejecting `completed→pending` without `allow_revival=True`

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
| Nudge guard exists | `grep -c 'TERMINAL_STATUSES' agent/agent_session_queue.py` | output > 1 |
| Revival guard exists | `grep -c 'terminal' agent/agent_session_queue.py` | output > 0 |
| Reference doc exists | `test -f docs/features/session-recovery-mechanisms.md` | exit code 0 |
| Recovery tests exist | `pytest tests/unit/test_recovery_respawn_safety.py -q` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| CONCERN | [agent-type] | [The concern raised] | [How/whether it was addressed] |

---

## Open Questions

No open questions — the issue recon confirmed which mechanisms are safe and which need guards. The scope is well-defined.
