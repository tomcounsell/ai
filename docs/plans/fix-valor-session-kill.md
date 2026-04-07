---
status: Planning
type: bug
appetite: Small
owner: Tom Counsell
created: 2026-04-07
tracking: https://github.com/tomcounsell/ai/issues/804
last_comment_id:
---

# Fix valor-session kill — Wrong Lifecycle Function for Terminal Status

## Problem

Running `python -m tools.valor_session kill --all` or `kill --id <ID>` throws an error and fails to kill sessions:

```
transition_status() is for non-terminal statuses. Got terminal status 'killed'.
Use finalize_session() for terminal transitions.
```

**Current behavior:** `cmd_kill()` in `tools/valor_session.py` calls `transition_status(s, "killed", ...)` at lines 408 and 431. `transition_status()` explicitly raises `ValueError` when called with a terminal status. Sessions remain stuck in `running` or `pending` state — kill commands are completely broken.

**Desired outcome:** `kill --id <ID>` and `kill --all` successfully transition sessions to `killed` status without errors.

## Prior Art

No prior issues found specifically for this bug.

Related issues mentioned in the tracker:
- **Issue #701**: Consolidate lifecycle mutations — established the two-function contract (`transition_status` vs `finalize_session`)
- **Issue #783**: Ghost session status corruption — adjacent lifecycle bug, different root cause

## Solution

### Key Elements

- **`tools/valor_session.py` import fix**: Add `finalize_session` to the import from `models.session_lifecycle` (line 396)
- **`cmd_kill()` call-site fix**: Replace both `transition_status(s, "killed", ...)` calls with `finalize_session(s, "killed", ...)` at lines 408 and 431
- **New unit test**: Cover `cmd_kill()` with both `--id` and `--all` flags, verifying sessions reach `killed` status without raising `ValueError`

### Flow

`valor-session kill --id <ID>` → `cmd_kill()` → `finalize_session(session, "killed", reason)` → session.status = "killed" → session.save() → success output

`valor-session kill --all` → `cmd_kill()` → iterate non-terminal sessions → `finalize_session(s, "killed", reason)` for each → success output

### Technical Approach

- Import change: `from models.session_lifecycle import TERMINAL_STATUSES, finalize_session` (replace `transition_status` import — not used in `cmd_kill`)
- Two call-site substitutions at lines 408 and 431, no logic changes to surrounding control flow
- The `--id` path already has a guard check against terminal statuses (lines 422-429), which is correct and stays as-is
- `finalize_session` already accepts `reason` as a positional/keyword arg — confirmed in `models/session_lifecycle.py`

## Failure Path Test Strategy

### Exception Handling Coverage
- The `--all` path wraps each session kill in `try/except Exception as e` and appends to `errors` list — correct behavior. Test should assert that an unexpected error from `finalize_session` is captured in `errors`, not propagated.

### Empty/Invalid Input Handling
- `--id` with nonexistent session: already handled (returns 1 with error message)
- `--all` with no non-terminal sessions: already handled (returns empty `killed` list)

### Error State Rendering
- `--json` flag output for both success and partial-failure cases should be tested in unit tests

## Test Impact

- `tests/unit/test_steering_mechanism.py` — no change needed, tests `--help` and `steer` subcommands only
- `tests/unit/test_valor_session_project_key.py` — no change needed, tests `resolve_project_key` only
- `tests/unit/test_session_lifecycle_consolidation.py` — no change needed, tests `finalize_session` directly

No existing tests cover `cmd_kill()` — this is additive coverage only.

## Rabbit Holes

- Refactoring `cmd_kill()` beyond the two call-site fixes — not needed
- Adding process-level kill signals (SIGTERM to running worker processes) — separate concern
- Consolidating all lifecycle imports across the codebase — out of scope for this bug fix

## Risks

### Risk 1: `finalize_session` has different keyword arguments than `transition_status`
**Impact:** Fix would fail with a different error if `finalize_session` does not accept `reason`.
**Mitigation:** Already verified — `finalize_session(session, status, reason)` accepts `reason` as a positional/keyword arg (line 34 of `models/session_lifecycle.py`).

## Race Conditions

No race conditions identified — the kill command is synchronous and single-threaded. Each session is fetched, transitioned, and saved independently.

## No-Gos (Out of Scope)

- Sending SIGTERM/SIGKILL to the OS process running a session (separate feature)
- Bulk-kill by role or status filter beyond current `--all` behavior
- Retry logic on failed session kills

## Update System

No update system changes required — this is a purely internal bug fix to a CLI tool with no new dependencies or config files.

## Agent Integration

No agent integration required — `tools/valor_session.py` is already accessible as a CLI tool. The fix does not change the CLI interface, only its internal implementation.

## Documentation

No documentation changes needed — the `valor_session` CLI docs and help text are unchanged. The fix corrects a broken behavior to match already-documented behavior. No new docs path required.

## Success Criteria

- [ ] `python -m tools.valor_session kill --id <ID>` transitions the session to `killed` without error
- [ ] `python -m tools.valor_session kill --all` kills all non-terminal sessions without error
- [ ] No `ValueError` from `transition_status()` during kill operations
- [ ] Unit test `tests/unit/test_valor_session_kill.py` covers `cmd_kill()` with `--id` and `--all` flags
- [ ] Tests pass (`/do-test`)

## Team Orchestration

### Team Members

- **Builder (kill-fix)**
  - Name: kill-fix-builder
  - Role: Fix the two call sites in `cmd_kill()` and add unit tests
  - Agent Type: builder
  - Resume: true

## Step by Step Tasks

### 1. Fix Import and Call Sites
- **Task ID**: build-fix
- **Depends On**: none
- **Validates**: `tests/unit/test_valor_session_kill.py` (create)
- **Assigned To**: kill-fix-builder
- **Agent Type**: builder
- **Parallel**: false
- In `tools/valor_session.py` line 396: replace `transition_status` with `finalize_session` in the import
- At line 408: replace `transition_status(s, "killed", reason="valor-session kill --all")` with `finalize_session(s, "killed", reason="valor-session kill --all")`
- At line 431: replace `transition_status(session, "killed", reason="valor-session kill")` with `finalize_session(session, "killed", reason="valor-session kill")`
- Create `tests/unit/test_valor_session_kill.py` with unit tests for `cmd_kill()` covering `--id` and `--all` flags using mocked `AgentSession` and `finalize_session`

### 2. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-fix
- **Assigned To**: kill-fix-builder
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_valor_session_kill.py -v`
- Verify no `ValueError` from `transition_status` during kill operations
- Confirm all success criteria met

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `pytest tests/unit/test_valor_session_kill.py -v` | exit code 0 |
| finalize_session imported | `grep "finalize_session" tools/valor_session.py` | output contains "finalize_session" |
| transition_status removed from cmd_kill | `grep -A5 "def cmd_kill" tools/valor_session.py \| grep transition_status` | exit code 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

None — root cause and fix are fully understood.
