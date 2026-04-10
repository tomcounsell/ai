---
status: Planning
type: bug
appetite: Small
owner: Valor
created: 2026-04-10
tracking: https://github.com/tomcounsell/ai/issues/871
---

# Session Recovery Coverage Split

## Problem

The session recovery system is split between two processes -- the worker and the bridge-hosted watchdog -- but this split is undocumented and actively misleading. A developer reading the worker's `_agent_session_health_check` docstring sees "Unified health check for all sessions -- the single recovery mechanism" and reasonably concludes that all non-terminal statuses are covered. In reality, the function only scans `running` and `pending`. Sessions in `active`, `waiting_for_children`, `paused`, `paused_circuit`, and `dormant` are handled by the bridge-hosted watchdog or specialized drip mechanisms.

**Current behavior:**
- The `_agent_session_health_check` docstring at `agent/agent_session_queue.py:1266` claims to be "the single recovery mechanism" while only covering 2 of 8 non-terminal statuses.
- No authoritative source documents which process owns recovery for which status.
- Adding a new status will silently fall into an undocumented gap.

**Desired outcome:**
- A single registry constant maps every non-terminal status to its recovery owner.
- A unit test asserts coverage completeness -- adding a new status without registering it breaks the test.
- The misleading docstring is corrected.
- `docs/features/session-recovery-mechanisms.md` documents the split.

## Freshness Check

**Baseline commit:** `e5e1569a9e395b4b71da487926c0d7ec72e0d04c`
**Issue filed at:** 2026-04-10T06:20:35Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `agent/agent_session_queue.py:1172` -- `_recover_interrupted_agent_sessions_startup()` -- still holds, scans `status="running"` only
- `agent/agent_session_queue.py:1265` -- `_agent_session_health_check()` -- still holds, misleading docstring present
- `agent/agent_session_queue.py:1266` -- docstring "Unified health check for all sessions -- the single recovery mechanism" -- confirmed present
- `monitoring/session_watchdog.py:153` -- `check_all_sessions()` filters `status="active"` -- confirmed
- `monitoring/session_watchdog.py:236` -- `check_stalled_sessions()` covers pending, running, active -- confirmed

**Cited sibling issues/PRs re-checked:**
- #867 -- still open (nudge-stomp race, adjacent area)
- #727 -- closed, merged as PR #745 (startup recovery timing guard)
- #402 -- closed (pending stall recovery)

**Commits on main since issue was filed (touching referenced files):**
- None touching the referenced files

**Active plans in `docs/plans/` overlapping this area:** None

**Notes:** All line numbers and claims verified against baseline commit.

## Prior Art

- **Issue #723 / PR #724**: "Audit all session recovery mechanisms for completed-session respawn safety" -- Catalogued all 8 recovery mechanisms and verified terminal-status safety. Created `docs/features/session-recovery-mechanisms.md`. Did not address the worker-vs-bridge coverage split.
- **Issue #727 / PR #745**: "Startup recovery timing guard" -- Added `AGENT_SESSION_HEALTH_MIN_RUNNING` guard to prevent startup recovery from orphaning recently-started sessions. Relevant because any extension of startup recovery must preserve this guard.
- **Issue #750 / PR #826**: "Decouple bridge from session execution" -- Separated bridge (I/O) from worker (execution). Established the current process boundary that created the implicit recovery split.
- **Issue #773 / PR #842**: "Sustainable self-healing: circuit-gated queue governance" -- Added `paused_circuit` status and circuit breaker integration. Did not update recovery ownership documentation.

## Data Flow

Not applicable -- this change is documentation + a constant definition + docstring fix + unit test. No data flows through the system differently.

## Architectural Impact

- **New dependencies**: None
- **Interface changes**: None -- the `RECOVERY_OWNERSHIP` constant is informational, not functional
- **Coupling**: No change -- documents existing coupling without altering it
- **Data ownership**: No change
- **Reversibility**: Trivially reversible (delete constant, revert docstring, delete test)

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites -- this work has no external dependencies.

## Solution

### Tradeoff Analysis: Option A vs B vs C

| Criterion | A (Expand worker) | B (Formalize split) | C (Hybrid) |
|-----------|-------------------|---------------------|-------------|
| Closes bridge-down gap | Yes | No | Partially (startup only) |
| Risk of race conditions | High (two processes recovering same statuses) | None | Low (startup is one-shot) |
| Code complexity | High (duplicate logic + coordination) | Low (constant + test + docs) | Medium |
| Matches appetite (Small) | No | Yes | Borderline |
| Future-proofs new statuses | Yes | Yes | Yes |

**Decision: Option B -- Formalize the split and document it.**

Rationale:
1. The bridge-down gap is empirically tiny (launchd restarts bridge within seconds) and the issue explicitly says "the real bug is the coverage split is undocumented and the worker's docstring misleads."
2. Option A introduces real race condition risk (two processes competing to recover the same sessions) for marginal benefit.
3. Option C's startup extension for `active` sessions is tempting but `active` is a transcript-tracking status set by `session_transcript.py` -- resetting it to `pending` at worker startup could interfere with transcript state. This needs more investigation than a Small appetite allows.
4. Option B directly addresses the root cause: undocumented contract + misleading docstring + no coverage assertion.

### Key Elements

- **Recovery ownership registry**: A `RECOVERY_OWNERSHIP` dict in `models/session_lifecycle.py` mapping every non-terminal status to its recovery owner process
- **Coverage completeness test**: A unit test asserting `set(RECOVERY_OWNERSHIP.keys()) == NON_TERMINAL_STATUSES`
- **Docstring correction**: Fix `_agent_session_health_check` to accurately describe its scope
- **Documentation update**: Add recovery split section to `docs/features/session-recovery-mechanisms.md`

### Flow

**Developer adds new status** -> adds to `NON_TERMINAL_STATUSES` -> test fails because `RECOVERY_OWNERSHIP` is missing entry -> developer adds ownership entry -> test passes -> coverage maintained

### Technical Approach

1. Add `RECOVERY_OWNERSHIP` constant to `models/session_lifecycle.py` alongside `NON_TERMINAL_STATUSES`:
   ```python
   RECOVERY_OWNERSHIP = {
       "pending": "worker",          # _agent_session_health_check
       "running": "worker",          # _agent_session_health_check + _recover_interrupted_agent_sessions_startup
       "active": "bridge-watchdog",  # monitoring/session_watchdog.py check_all_sessions + check_stalled_sessions
       "dormant": "bridge-watchdog", # monitoring/session_watchdog.py (via check_stalled_sessions activity check)
       "waiting_for_children": "worker",  # _agent_session_hierarchy_health_check
       "superseded": "none",         # transitional; superseded sessions are finalized immediately
       "paused_circuit": "bridge-watchdog",  # agent/sustainability.py circuit breaker drip
       "paused": "bridge-watchdog",  # agent/hibernation.py session-resume-drip
   }
   ```

2. Fix the `_agent_session_health_check` docstring at `agent/agent_session_queue.py:1266` to replace the misleading first line. New docstring: "Health check for worker-managed sessions (running and pending). Other non-terminal statuses (active, dormant, paused, paused_circuit) are monitored by the bridge-hosted watchdog in monitoring/session_watchdog.py. See RECOVERY_OWNERSHIP in models/session_lifecycle.py for the full coverage map."

3. Add a unit test in `tests/unit/test_recovery_ownership.py` that asserts `set(RECOVERY_OWNERSHIP.keys()) == NON_TERMINAL_STATUSES`.

4. Update `docs/features/session-recovery-mechanisms.md` with a new "Recovery Ownership" section documenting the split.

## Failure Path Test Strategy

### Exception Handling Coverage
No exception handlers in scope -- this change adds a constant, a test, and documentation.

### Empty/Invalid Input Handling
Not applicable -- no functions are added or modified (only a docstring is changed).

### Error State Rendering
Not applicable -- no user-visible output.

## Test Impact

- [ ] `tests/unit/test_recovery_respawn_safety.py` -- No changes needed. Existing tests cover terminal-status safety. The new `RECOVERY_OWNERSHIP` constant is orthogonal.
- [ ] `tests/unit/test_session_watchdog.py` -- No changes needed. Tests cover watchdog health assessment logic, not ownership mapping.
- [ ] `tests/unit/test_stall_detection.py` -- No changes needed. Tests cover stall threshold logic, not ownership mapping.
- [ ] `tests/integration/test_agent_session_lifecycle.py` -- No changes needed. Tests cover lifecycle transitions, not recovery ownership.
- [ ] `tests/integration/test_lifecycle_transition.py` -- No changes needed. Tests cover `transition_status()` behavior, not recovery ownership.

No existing tests affected -- this is a purely additive change (new constant, new test file, docstring fix, documentation update) that does not modify any existing behavior or interfaces.

## Rabbit Holes

- Extending worker startup recovery to cover `active` sessions (Option C) -- tempting but requires understanding transcript state interactions. Separate issue if needed.
- Building a runtime enforcement mechanism that routes recovery based on the registry -- over-engineering for a documentation bug.
- Auditing whether `dormant` sessions actually have a recovery path in the watchdog -- the watchdog queries `status="active"` specifically, so `dormant` may have a gap. Worth noting but out of scope for this Small-appetite fix. File a follow-up issue if confirmed.

## Risks

### Risk 1: RECOVERY_OWNERSHIP becomes stale
**Impact:** The constant drifts from reality, same problem as the docstring.
**Mitigation:** The unit test asserts key coverage against `NON_TERMINAL_STATUSES`. Adding a status without updating ownership breaks CI. Removing a status without updating ownership also breaks CI. The only drift risk is changing which process handles a status without updating the constant -- but that is far less likely than forgetting to add a new status entirely.

## Race Conditions

No race conditions identified -- this change adds a constant and a test. No concurrent access patterns are introduced or modified.

## No-Gos (Out of Scope)

- Extending worker recovery to cover `active` or other bridge-owned statuses (Option A/C)
- Runtime enforcement of the ownership registry
- Changing which process handles which statuses
- Investigating whether `dormant` has a recovery gap (file as separate issue if confirmed)

## Update System

No update system changes required -- this is a documentation + constant + test change with no runtime behavior change.

## Agent Integration

No agent integration required -- this is an internal code quality and documentation change.

## Documentation

- [ ] Update `docs/features/session-recovery-mechanisms.md` with a "Recovery Ownership" section documenting the worker-vs-bridge split, including the `RECOVERY_OWNERSHIP` constant reference and per-status rationale
- [ ] Update inline docstring for `_agent_session_health_check` in `agent/agent_session_queue.py`

## Success Criteria

- [ ] `RECOVERY_OWNERSHIP` constant exists in `models/session_lifecycle.py` with an entry for every non-terminal status
- [ ] Unit test in `tests/unit/test_recovery_ownership.py` asserts `set(RECOVERY_OWNERSHIP.keys()) == NON_TERMINAL_STATUSES`
- [ ] `_agent_session_health_check` docstring no longer claims to be "the single recovery mechanism"
- [ ] `docs/features/session-recovery-mechanisms.md` includes recovery ownership documentation
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (recovery-ownership)**
  - Name: ownership-builder
  - Role: Add RECOVERY_OWNERSHIP constant, fix docstring, create unit test
  - Agent Type: builder
  - Resume: true

- **Documentarian (recovery-docs)**
  - Name: recovery-docs
  - Role: Update session-recovery-mechanisms.md
  - Agent Type: documentarian
  - Resume: true

- **Validator (final-check)**
  - Name: final-validator
  - Role: Verify all success criteria
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add RECOVERY_OWNERSHIP constant and fix docstring
- **Task ID**: build-ownership
- **Depends On**: none
- **Validates**: tests/unit/test_recovery_ownership.py (create)
- **Assigned To**: ownership-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `RECOVERY_OWNERSHIP` dict to `models/session_lifecycle.py` after `NON_TERMINAL_STATUSES`
- Create `tests/unit/test_recovery_ownership.py` with coverage completeness assertion
- Fix `_agent_session_health_check` docstring in `agent/agent_session_queue.py`

### 2. Update documentation
- **Task ID**: document-feature
- **Depends On**: build-ownership
- **Assigned To**: recovery-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Add "Recovery Ownership" section to `docs/features/session-recovery-mechanisms.md`
- Include the ownership table, cross-reference to the constant, and rationale for the split

### 3. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-ownership, document-feature
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_recovery_ownership.py -v`
- Verify docstring no longer contains "single recovery mechanism"
- Verify `docs/features/session-recovery-mechanisms.md` contains ownership section
- Run `python -m ruff check . && python -m ruff format --check .`

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Ownership test | `pytest tests/unit/test_recovery_ownership.py -v` | exit code 0 |
| Docstring fixed | `grep -c "single recovery mechanism" agent/agent_session_queue.py` | output contains 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

No open questions -- the issue provides clear direction and Option B is well-scoped for a Small appetite.
