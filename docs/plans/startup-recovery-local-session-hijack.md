---
status: Planning
type: bug
appetite: Small
owner: Valor
created: 2026-04-15
tracking: https://github.com/tomcounsell/ai/issues/986
last_comment_id:
---

# Startup Recovery Must Not Hijack Local CLI Sessions

## Problem

On worker restart, `_recover_interrupted_agent_sessions_startup()` finds all sessions with `status="running"`, marks them "interrupted", and resets them to `pending` for re-execution — including sessions belonging to live, interactive local Claude Code CLI sessions.

**Current behavior:**
When a worker dies while a local Claude Code session is running, the session row remains in `status="running"` in Redis. On worker restart, startup recovery resets it to `pending` and re-enqueues it. The worker then spawns `claude --resume <UUID>` against the same Claude session UUID that the interactive CLI is already using. Two harnesses are now driving the same Claude session UUID: garbled transcripts, duplicate tool calls, file system conflicts, and at worst a corrupted Claude session.

**Desired outcome:**
Startup recovery skips local CLI sessions — sessions whose `worker_key` starts with `"local"`. These are abandoned (marked `"abandoned"`) instead of reset to `"pending"`, which is exactly what the periodic health check already does.

## Freshness Check

**Baseline commit:** `99bf051d9b31ec454a85f17a5befb59293de3830`
**Issue filed at:** 2026-04-15T06:46:11Z
**Disposition:** Minor drift (line numbers shifted; all claims still hold)

**File:line references re-verified:**
- `agent/agent_session_queue.py:1319-1398` — `_recover_interrupted_agent_sessions_startup()` — confirmed; no local-session guard present (issue claim holds)
- `agent/agent_session_queue.py:1597` — health check's `is_local = worker_key.startswith("local")` guard — confirmed at line 1597; logic is correct and is the model for the fix
- `agent/agent_session_queue.py:1673` — health check pending-session local guard — confirmed at line 1673; same pattern
- `.claude/hooks/user_prompt_submit.py:89` — local session creation with `session_id=f"local-{session_id}"` — not re-read (creation convention unchanged)

**Cited sibling issues/PRs re-checked:**
- PR #745 (merged 2026-04-06) — Added 300s timing guard to startup recovery; did NOT address local-session identity distinction; confirmed by reading the PR body

**Commits on main since issue was filed (touching `agent/agent_session_queue.py`):**
- `e7baf24e` refactor: extract `_handle_harness_not_found` helper — irrelevant (harness path, not startup recovery)
- `f3b8db7b` refactor: extract `_HARNESS_EXHAUSTION_MSG` constant — irrelevant (harness path)
- `23bf0090` fix: guard transition_status conflict, move `_harness_requeued` guard — irrelevant (harness path)

**Active plans in `docs/plans/` overlapping this area:** none found

**Notes:** Issue cited line 1256-1335 for the function and line 1534 for the health check guard; actual current lines are 1319-1398 and 1597 respectively. Claims remain accurate.

## Prior Art

- **PR #745** (merged 2026-04-06): "fix: startup recovery timing guard to prevent worker race" — Added 300s recency guard to startup recovery. This was the last significant change to `_recover_interrupted_agent_sessions_startup()`. It solved a timing race between worker startup and session pickup, but the author did not address the local-session identity distinction.
- **Issue #944** (closed 2026-04-14): "health check skips recovery for stuck dev sessions when a shared project-keyed worker is alive" — Health check fix, not startup recovery. The health check already has the local-session guard; this issue fixed a different health check gap.

## Research

No relevant external findings — this is a purely internal fix mirroring an existing pattern in the same file.

## Data Flow

Failure data flow (the bug):

1. **Worker process dies** — An interactive local Claude Code session (`session_id=local-8b21a3a0-...`) is `status="running"` in Redis
2. **Worker restarts** — `_recover_interrupted_agent_sessions_startup()` queries all `status="running"` sessions
3. **Recovery loop** — For each stale session (older than 300s), calls `update_session(..., new_status="pending")` — no local-session check
4. **Worker picks it up** — `_worker_loop()` pops the session from pending; routes to CLI harness
5. **Second harness spawned** — `claude --resume <uuid>` is invoked against the same UUID the interactive CLI holds; two harnesses now drive one Claude session

Fixed data flow:

1. **Worker restarts** — Same as above
2. **Recovery loop** — For each stale session, checks `entry.worker_key.startswith("local")`
3. **Local session path** — Calls `finalize_session(entry, "abandoned", ...)` — session is terminated, never re-queued
4. **Bridge session path** — Existing behavior: reset to `pending` and re-execute

## Architectural Impact

- **No new dependencies** — Uses existing `finalize_session` import and `worker_key` property
- **No interface changes** — `_recover_interrupted_agent_sessions_startup()` signature unchanged; return type (count of recovered sessions) counts only bridge sessions (local sessions abandoned, not "recovered")
- **Coupling unchanged** — Same modules involved
- **Reversibility** — One guard condition; trivially reverted

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **Local-session guard in startup recovery** — Mirror the exact pattern from `_agent_session_health_check` at line 1534: check `entry.worker_key.startswith("local")` and call `finalize_session(entry, "abandoned", ...)` instead of resetting to `pending`
- **Log improvement** — Log the list of session IDs and their dispositions (recovered vs. abandoned) before the loop executes, so a human can see what happened at startup

### Flow

Worker restarts → startup recovery queries running sessions → for each stale session: is local? → YES: abandon with log → NO: reset to pending (existing behavior)

### Technical Approach

In `_recover_interrupted_agent_sessions_startup()` (agent/agent_session_queue.py:1300-1332), add a branch inside the stale session loop:

```python
wk = entry.worker_key
is_local = wk.startswith("local")

if is_local:
    # Local CLI sessions cannot be resumed by the bridge worker.
    # Mark abandoned so the originating CLI can reclaim on next turn.
    from models.session_lifecycle import finalize_session
    finalize_session(
        entry,
        "abandoned",
        reason="startup recovery: local session cannot be resumed by worker",
        skip_auto_tag=True,
    )
    logger.info(
        "[startup-recovery] Abandoned local session %s (worker_key=%s)",
        entry.agent_session_id,
        wk,
    )
else:
    # Existing bridge session recovery path
    update_session(...)
    count += 1
```

The `finalize_session` import already lives in the health check section of the same file; the startup recovery function can use the same lazy import pattern.

**Log improvement** — Before the loop, log the full list of stale session IDs with their worker_key values at WARNING level, so a human restarting the worker can audit what will be recovered vs. abandoned without needing to wait for individual log lines.

## Failure Path Test Strategy

### Exception Handling Coverage
- The existing `except Exception` block at line 1323 (handles `update_session` failure) will be extended to also catch `finalize_session` failures for local sessions. The fallback deletes the corrupted session, which is safe for local sessions too.
- Test: assert that a `finalize_session` failure for a local session triggers deletion fallback (not re-queue)

### Empty/Invalid Input Handling
- `entry.worker_key` is a computed property that falls back to `project_key` — it never returns None. The `startswith("local")` check is safe.
- No empty input edge cases in scope

### Error State Rendering
- No user-visible output — this is a background recovery path; output goes to logs only

## Test Impact

- [ ] `tests/unit/test_recovery_respawn_safety.py::TestStartupRecoverySkipsTerminal::test_startup_recovery_only_queries_running` — UPDATE: currently only asserts the function queries `status="running"`; extend to assert local sessions are abandoned and bridge sessions are recovered
- [ ] `tests/unit/test_agent_session_scheduler_kill.py::test_recover_interrupted_agent_sessions_startup_filters_running` — UPDATE: extend mock to include a local-keyed session; assert it is abandoned not re-queued to pending

New tests to add to `tests/unit/test_recovery_respawn_safety.py` (greenfield, not listed as UPDATE/DELETE/REPLACE):
- `test_startup_recovery_abandons_local_sessions` — mock a stale local session; assert `finalize_session("abandoned")` called, count not incremented
- `test_startup_recovery_recovers_bridge_sessions` — mock a stale bridge session; assert `update_session("pending")` called, count incremented
- `test_startup_recovery_mixed_local_and_bridge` — mock both types; assert correct disposition for each

## Rabbit Holes

- **Lockfile / heartbeat detection** — The issue body suggests checking whether a `claude` process is actively writing to the session UUID. This is complex (process enumeration, file locking) and unnecessary: we don't care if the local CLI is still alive — we just don't want the worker to compete with it. Abandoning is always the right call for local sessions.
- **Modifying `worker_key` logic** — The `startswith("local")` check is already established convention (used in health check). Do not introduce a new field or enum; use the same discriminator.
- **Auditing all 8 recovery mechanisms** — `session-recovery-coverage.md` (issue #871) is already handling this documentation gap. Stay focused on the startup recovery fix.

## Risks

### Risk 1: `finalize_session` import at function body level
**Impact:** If `models.session_lifecycle` is unavailable at startup (import error), the local session fallback fails and falls through to the exception handler (which deletes the session) — acceptable behavior.
**Mitigation:** The same lazy import pattern is used throughout the file. Use `from models.session_lifecycle import finalize_session` inside the branch, matching the health check's pattern.

### Risk 2: Counting semantics
**Impact:** The function currently returns the count of "recovered" sessions (i.e., re-queued to pending). If we abandon local sessions, they should not increment this counter — the caller logs "Recovered N interrupted session(s)" which would be misleading if it included abandonments.
**Mitigation:** Only increment `count` for successfully re-queued bridge sessions. Add a separate `abandoned` counter and log it independently.

## Race Conditions

### Race 1: Local session hook re-activates session while startup recovery is abandoning it
**Location:** `agent/agent_session_queue.py:1300-1332` (startup recovery) vs `.claude/hooks/user_prompt_submit.py:61-84` (hook reactivation)
**Trigger:** Human types a new prompt in Claude Code exactly as the worker restarts; hook calls `transition_status(agent_session, "running")` while startup recovery calls `finalize_session(entry, "abandoned")`
**Data prerequisite:** Session must exist in Redis with `status="running"` for startup recovery to see it
**State prerequisite:** Hook fires before startup recovery's loop iteration for this session
**Mitigation:** `finalize_session` calls `transition_status` which uses CAS (compare-and-swap) semantics via `expected_status="running"`. If the hook has already transitioned the session to "running" from "abandoned" (or if the session was already re-activated), the CAS will conflict and `finalize_session` will raise or no-op. The session remains live. This is the correct outcome. Add a catch for `StatusConflictError` at INFO level — same pattern used elsewhere.

No other race conditions — startup recovery runs once, synchronously, before any worker loops are started.

## No-Gos (Out of Scope)

- Detecting whether the originating CLI process is still alive (lockfile, PID check) — unnecessary complexity
- Changing what happens when a local session subsequently has a new prompt (hook already handles reactivation correctly)
- Adding a UI to list "abandoned at startup" sessions — logs are sufficient
- Modifying the `worker_key` computation or introducing a new session field

## Update System

No update system changes required — this feature is purely internal to `agent/agent_session_queue.py`. No new dependencies, no config files, no migration needed.

## Agent Integration

No agent integration required — this is a worker-internal change. The bridge, MCP servers, and `.mcp.json` are unaffected.

## Documentation

- [ ] Update `docs/features/session-recovery-mechanisms.md` — Mechanism 1 table entry for Startup Recovery: add "Local session guard" row describing the `worker_key.startswith("local")` check and `"abandoned"` disposition for local sessions
- [ ] Add inline docstring note to `_recover_interrupted_agent_sessions_startup()` in `agent/agent_session_queue.py` explaining the local-session guard and why local sessions are abandoned rather than re-queued

## Success Criteria

- [ ] `_recover_interrupted_agent_sessions_startup()` never calls `update_session(..., "pending")` for sessions with `worker_key.startswith("local")`
- [ ] Local stale sessions are finalized as `"abandoned"` with `skip_auto_tag=True`
- [ ] Bridge sessions are recovered exactly as before (no regression)
- [ ] Startup log shows per-session disposition before loop executes
- [ ] New unit tests pass: `test_startup_recovery_abandons_local_sessions`, `test_startup_recovery_recovers_bridge_sessions`, `test_startup_recovery_mixed_local_and_bridge`
- [ ] Updated tests pass: `test_startup_recovery_only_queries_running`, `test_recover_interrupted_agent_sessions_startup_filters_running`
- [ ] `docs/features/session-recovery-mechanisms.md` updated
- [ ] Tests pass (`/do-test`)

## Team Orchestration

### Team Members

- **Builder (startup-recovery)**
  - Name: startup-recovery-builder
  - Role: Add local-session guard to `_recover_interrupted_agent_sessions_startup()` and write new unit tests
  - Agent Type: builder
  - Resume: true

- **Validator (startup-recovery)**
  - Name: startup-recovery-validator
  - Role: Verify guard is correct, tests pass, no regressions
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Update `docs/features/session-recovery-mechanisms.md`
  - Agent Type: documentarian
  - Resume: false

### Available Agent Types

Builder, validator, documentarian (see template for full list).

## Step by Step Tasks

### 1. Add local-session guard to startup recovery
- **Task ID**: build-guard
- **Depends On**: none
- **Validates**: tests/unit/test_recovery_respawn_safety.py, tests/unit/test_agent_session_scheduler_kill.py
- **Assigned To**: startup-recovery-builder
- **Agent Type**: builder
- **Parallel**: true
- In `_recover_interrupted_agent_sessions_startup()` at agent/agent_session_queue.py:1300, add `is_local = entry.worker_key.startswith("local")` check before the existing try block
- If `is_local`: call `finalize_session(entry, "abandoned", reason="startup recovery: local session cannot be resumed by worker", skip_auto_tag=True)` and log at INFO; do NOT increment `count`; track in separate `abandoned` counter
- Add a pre-loop log at WARNING level listing all stale session IDs and their worker_key values
- Log summary at end: "Recovered {count} bridge session(s), abandoned {abandoned} local session(s)"
- Wrap `finalize_session` call in try/except; on failure, fall through to the existing deletion fallback
- Catch `StatusConflictError` at INFO level (CAS conflict = session was concurrently reactivated = correct outcome)

### 2. Write new unit tests
- **Task ID**: build-tests
- **Depends On**: build-guard
- **Validates**: tests/unit/test_recovery_respawn_safety.py
- **Assigned To**: startup-recovery-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `TestStartupRecoveryLocalSessionGuard` class to `tests/unit/test_recovery_respawn_safety.py`
- `test_startup_recovery_abandons_local_sessions`: mock stale local session (worker_key="local-abc"), assert `finalize_session` called with "abandoned", count returns 0
- `test_startup_recovery_recovers_bridge_sessions`: mock stale bridge session (worker_key="myproject"), assert `update_session` called with "pending", count returns 1
- `test_startup_recovery_mixed_local_and_bridge`: mock one of each; assert local→abandoned, bridge→pending, count=1
- Update `TestStartupRecoverySkipsTerminal::test_startup_recovery_only_queries_running`: extend to also assert local sessions are not re-queued
- Update `test_recover_interrupted_agent_sessions_startup_filters_running` in test_agent_session_scheduler_kill.py: add a local-session mock and assert abandoned disposition

### 3. Validate
- **Task ID**: validate-guard
- **Depends On**: build-tests
- **Assigned To**: startup-recovery-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_recovery_respawn_safety.py tests/unit/test_agent_session_scheduler_kill.py -v`
- Verify all new and updated tests pass
- Run `python -m ruff check agent/agent_session_queue.py && python -m ruff format --check agent/agent_session_queue.py`
- Spot-check that `finalize_session` import pattern matches the health check's existing pattern

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-guard
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/session-recovery-mechanisms.md` Mechanism 1 table: add "Local session guard" row with: check `worker_key.startswith("local")`, disposition "abandoned", reason "no bridge worker can deliver output for local sessions"

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: startup-recovery-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/ -x -q`
- Verify `docs/features/session-recovery-mechanisms.md` updated
- Confirm all success criteria met

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_recovery_respawn_safety.py tests/unit/test_agent_session_scheduler_kill.py -v` | exit code 0 |
| All unit tests | `pytest tests/unit/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check agent/agent_session_queue.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/agent_session_queue.py` | exit code 0 |
| Guard present | `grep -n "startswith.*local" agent/agent_session_queue.py` | output contains "startup" context |
| Docs updated | `grep "Local session guard" docs/features/session-recovery-mechanisms.md` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Skeptic, Adversary | `worker_key.startswith("local")` fails for default DEV sessions (worker_key = project_key) | Replace discriminator with `entry.session_id.startswith("local")` | `create_local()` always sets `session_id=f"local-{uuid}"` — session_id is the reliable discriminator; `worker_key` for session_type=DEV without slug returns `project_key`, not `chat_id` |
| CONCERN | Adversary | Race 1 mitigation description is incorrect: hook reactivation transitions "running" → "running" (same status), CAS in finalize_session would NOT conflict and would proceed to abandon a live session | Add guard: after `finalize_session` is called, check `StatusConflictError` AND add a pre-call re-read to confirm status is still "running" before abandoning | `finalize_session` CAS compares in-memory status to on-disk status at call time; if hook has already re-activated (status still "running" on disk), CAS passes and the live session gets abandoned. Guard: `from models.session_lifecycle import StatusConflictError` then catch it, but also add idempotency check against hook reactivation by checking `entry.session_id` in running sessions post-finalize. |
| NIT | Simplifier | Pre-loop WARNING log of all stale session IDs is redundant — each session is already logged at WARNING inside the loop | Optionally keep a single "N stale sessions found" count log instead of full ID list; remove per-session duplication | N/A |
| NIT | User | All success criteria are purely technical; no human-observable acceptance criterion | Add: "Worker restart log shows `[startup-recovery] Abandoned local session <id>`" as acceptance signal | N/A |

---

## Open Questions

None — the fix is well-defined by the existing health check pattern. No supervisor input needed before building.
