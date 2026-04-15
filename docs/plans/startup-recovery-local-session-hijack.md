---
status: docs_complete
type: bug
appetite: Small
owner: Valor
created: 2026-04-15
tracking: https://github.com/tomcounsell/ai/issues/986
last_comment_id:
revision_applied: true
---

# Startup Recovery Must Not Hijack Local CLI Sessions

## Problem

On worker restart, `_recover_interrupted_agent_sessions_startup()` finds all sessions with `status="running"`, marks them "interrupted", and resets them to `pending` for re-execution — including sessions belonging to live, interactive local Claude Code CLI sessions.

**Current behavior:**
When a worker dies while a local Claude Code session is running, the session row remains in `status="running"` in Redis. On worker restart, startup recovery resets it to `pending` and re-enqueues it. The worker then spawns `claude --resume <UUID>` against the same Claude session UUID that the interactive CLI is already using. Two harnesses are now driving the same Claude session UUID: garbled transcripts, duplicate tool calls, file system conflicts, and at worst a corrupted Claude session.

**Desired outcome:**
Startup recovery skips local CLI sessions — sessions whose `session_id` starts with `"local"`. These are abandoned (marked `"abandoned"`) instead of reset to `"pending"`, which is exactly what the periodic health check already does.

## Freshness Check

**Baseline commit:** `99bf051d9b31ec454a85f17a5befb59293de3830`
**Issue filed at:** 2026-04-15T06:46:11Z
**Disposition:** Minor drift (line numbers shifted; all claims still hold)

**File:line references re-verified:**
- `agent/agent_session_queue.py:1319-1398` — `_recover_interrupted_agent_sessions_startup()` — confirmed; no local-session guard present (issue claim holds)
- `agent/agent_session_queue.py:1597` — health check's `is_local = worker_key.startswith("local")` guard — confirmed at line 1597; note: this uses `worker_key` as discriminator, which is also flawed (see BLOCKER in Critique Results). The fix in this plan uses `session_id` as the correct discriminator; the health check fix is a separate follow-up.
- `agent/agent_session_queue.py:1673` — health check pending-session local guard — confirmed at line 1673; same flawed pattern as above
- `.claude/hooks/user_prompt_submit.py:89` — local session creation with `session_id=f"local-{session_id}"` — confirmed; `session_id` always starts with `"local"` for all sessions created via `create_local()`

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
2. **Recovery loop** — For each stale session, checks `entry.session_id.startswith("local")`
3. **Local session path** — Calls `finalize_session(entry, "abandoned", ...)` — session is terminated, never re-queued
4. **Bridge session path** — Existing behavior: reset to `pending` and re-execute

## Architectural Impact

- **No new dependencies** — Uses existing `finalize_session` import and `session_id` field (already present on every AgentSession)
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

- **Local-session guard in startup recovery** — Check `entry.session_id.startswith("local")` (NOT `worker_key`) and call `finalize_session(entry, "abandoned", ...)` instead of resetting to `pending`. Note: the health check at line 1597 uses `worker_key.startswith("local")` — that is a pre-existing flawed pattern; this plan fixes startup recovery with the correct discriminator (`session_id`). A follow-up ticket should fix the health check.
- **Log improvement** — Log a summary count of stale sessions found before the loop executes, so a human can see what happened at startup

### Flow

Worker restarts → startup recovery queries running sessions → for each stale session: `session_id.startswith("local")`? → YES: abandon with log → NO: reset to pending (existing behavior)

### Technical Approach

In `_recover_interrupted_agent_sessions_startup()` (agent/agent_session_queue.py:1319-1398), add a branch inside the stale session loop:

```python
wk = entry.worker_key
is_local = entry.session_id.startswith("local")  # session_id is the reliable discriminator

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
        "[startup-recovery] Abandoned local session %s (session_id=%s, worker_key=%s)",
        entry.agent_session_id,
        entry.session_id,
        wk,
    )
else:
    # Existing bridge session recovery path
    update_session(...)
    count += 1
```

The `finalize_session` import already lives in the health check section of the same file; the startup recovery function can use the same lazy import pattern.

**Why `session_id` not `worker_key`:** `worker_key` for DEV sessions without a slug returns `project_key` (e.g., `"ai"`), not `chat_id`. `create_local()` always sets `session_id=f"local-{uuid}"`, making `session_id` the only reliable discriminator for local sessions.

**Log improvement** — Before the loop, log a single summary line: `logger.warning("[startup-recovery] Found %d stale session(s) to process", len(stale_sessions))`. This is sufficient — individual per-session disposition logs follow inside the loop.

## Failure Path Test Strategy

### Exception Handling Coverage
- The existing `except Exception` block at line 1323 (handles `update_session` failure) will be extended to also catch `finalize_session` failures for local sessions. The fallback deletes the corrupted session, which is safe for local sessions too.
- Test: assert that a `finalize_session` failure for a local session triggers deletion fallback (not re-queue)

### Empty/Invalid Input Handling
- `entry.session_id` is always set at session creation — it never returns None. The `startswith("local")` check is safe.
- `entry.worker_key` is still fetched (for log context) — it is a computed property that falls back to `project_key` and also never returns None.
- No empty input edge cases in scope

### Error State Rendering
- No user-visible output — this is a background recovery path; output goes to logs only

## Test Impact

- [x] `tests/unit/test_recovery_respawn_safety.py::TestStartupRecoverySkipsTerminal::test_startup_recovery_only_queries_running` — UPDATE: currently only asserts the function queries `status="running"`; extend to assert local sessions are abandoned and bridge sessions are recovered
- [x] `tests/unit/test_agent_session_scheduler_kill.py::test_recover_interrupted_agent_sessions_startup_filters_running` — UPDATE: extend mock to include a local-keyed session; assert it is abandoned not re-queued to pending

New tests to add to `tests/unit/test_recovery_respawn_safety.py` (greenfield, not listed as UPDATE/DELETE/REPLACE):
- `test_startup_recovery_abandons_local_sessions` — mock a stale local session; assert `finalize_session("abandoned")` called, count not incremented
- `test_startup_recovery_recovers_bridge_sessions` — mock a stale bridge session; assert `update_session("pending")` called, count incremented
- `test_startup_recovery_mixed_local_and_bridge` — mock both types; assert correct disposition for each

## Rabbit Holes

- **Lockfile / heartbeat detection** — The issue body suggests checking whether a `claude` process is actively writing to the session UUID. This is complex (process enumeration, file locking) and unnecessary: we don't care if the local CLI is still alive — we just don't want the worker to compete with it. Abandoning is always the right call for local sessions.
- **Fixing the health check discriminator in this plan** — The health check at line 1597 also uses `worker_key.startswith("local")`, which is flawed for the same reason. Fixing that is a separate follow-up ticket; do not expand scope here.
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
**Location:** `agent/agent_session_queue.py:1319-1398` (startup recovery) vs `.claude/hooks/user_prompt_submit.py:61-84` (hook reactivation)
**Trigger:** Human types a new prompt in Claude Code exactly as the worker restarts; hook calls `transition_status(agent_session, "running")` while startup recovery calls `finalize_session(entry, "abandoned")`
**Data prerequisite:** Session must exist in Redis with `status="running"` for startup recovery to see it
**State prerequisite:** Hook fires before startup recovery's loop iteration for this session
**Mitigation:** The actual guard is **temporal**: the `AGENT_SESSION_HEALTH_MIN_RUNNING` timing guard (300s) means sessions started within 300s are skipped entirely. In practice this covers the hook-reactivation window — a user actively typing at the exact moment of a worker restart would have started the session recently, and startup recovery skips it. Sessions old enough to be considered stale (>300s) by definition predate the current typing activity.

Note: CAS via `finalize_session(expected_status="running")` does NOT protect against this race. Hook reactivation transitions `"running" → "running"` (same status — the hook sets `running` again). So the CAS condition (in-memory `"running"` vs on-disk `"running"`) still matches, and `finalize_session` would proceed to abandon the live session. The CAS catch for `StatusConflictError` should still be included for other unexpected concurrent modifications, but it is NOT the defense for this specific race. The timing guard is.

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

- [x] Update `docs/features/session-recovery-mechanisms.md` — Mechanism 1 table entry for Startup Recovery: add "Local session guard" row describing the `session_id.startswith("local")` check and `"abandoned"` disposition for local sessions
- [x] Add inline docstring note to `_recover_interrupted_agent_sessions_startup()` in `agent/agent_session_queue.py` explaining the local-session guard and why local sessions are abandoned rather than re-queued

## Success Criteria

- [x] `_recover_interrupted_agent_sessions_startup()` never calls `update_session(..., "pending")` for sessions with `session_id.startswith("local")`
- [x] Local stale sessions are finalized as `"abandoned"` with `skip_auto_tag=True`
- [x] Bridge sessions are recovered exactly as before (no regression)
- [x] Startup log shows stale session count before loop executes
- [x] Worker restart log shows `[startup-recovery] Abandoned local session <id>` when a local session was stale at startup (human-observable acceptance signal)
- [x] New unit tests pass: `test_startup_recovery_abandons_local_sessions`, `test_startup_recovery_recovers_bridge_sessions`, `test_startup_recovery_mixed_local_and_bridge`
- [x] Updated tests pass: `test_startup_recovery_only_queries_running`, `test_recover_interrupted_agent_sessions_startup_filters_running`
- [x] `docs/features/session-recovery-mechanisms.md` updated
- [x] Tests pass (`/do-test`)

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
- In `_recover_interrupted_agent_sessions_startup()` at agent/agent_session_queue.py:1319, add `is_local = entry.session_id.startswith("local")` check before the existing try block (use `session_id`, NOT `worker_key` — see Technical Approach for why)
- If `is_local`: call `finalize_session(entry, "abandoned", reason="startup recovery: local session cannot be resumed by worker", skip_auto_tag=True)` and log at INFO; do NOT increment `count`; track in separate `abandoned` counter
- Add a pre-loop summary log at WARNING level: `logger.warning("[startup-recovery] Found %d stale session(s) to process", len(stale_sessions))`; do NOT dump full ID list (redundant with per-session loop logs)
- Log summary at end: "Recovered {count} bridge session(s), abandoned {abandoned} local session(s)"
- Wrap `finalize_session` call in try/except; on failure, fall through to the existing deletion fallback
- Catch `StatusConflictError` at INFO level (other concurrent modifications — note: this does NOT protect against hook reactivation; timing guard is the actual defense for that race)

### 2. Write new unit tests
- **Task ID**: build-tests
- **Depends On**: build-guard
- **Validates**: tests/unit/test_recovery_respawn_safety.py
- **Assigned To**: startup-recovery-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `TestStartupRecoveryLocalSessionGuard` class to `tests/unit/test_recovery_respawn_safety.py`
- `test_startup_recovery_abandons_local_sessions`: mock stale local session (session_id="local-abc123", worker_key="ai"), assert `finalize_session` called with "abandoned", count returns 0
- `test_startup_recovery_recovers_bridge_sessions`: mock stale bridge session (session_id="tg-xyz789", worker_key="ai"), assert `update_session` called with "pending", count returns 1
- `test_startup_recovery_mixed_local_and_bridge`: mock one of each (local session_id="local-abc", bridge session_id="tg-xyz"); assert local→abandoned, bridge→pending, count=1
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
- Update `docs/features/session-recovery-mechanisms.md` Mechanism 1 table: add "Local session guard" row with: check `session_id.startswith("local")`, disposition "abandoned", reason "no bridge worker can deliver output for local sessions"

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
| Guard present | `grep -n "session_id.*startswith.*local" agent/agent_session_queue.py` | output contains "startup" context |
| Docs updated | `grep "Local session guard" docs/features/session-recovery-mechanisms.md` | exit code 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER ✅ | Skeptic, Adversary | `worker_key.startswith("local")` fails for default DEV sessions (worker_key = project_key) | Changed discriminator to `entry.session_id.startswith("local")` throughout plan | `create_local()` always sets `session_id=f"local-{uuid}"` — session_id is the reliable discriminator; `worker_key` for session_type=DEV without slug returns `project_key`, not `chat_id` |
| CONCERN ✅ | Adversary | Race 1 mitigation description was incorrect: hook reactivation transitions "running" → "running" (same status), CAS in finalize_session would NOT conflict | Race Conditions section rewritten: timing guard (300s) is the actual defense; CAS catch kept for other concurrent modifications only | The timing guard means sessions old enough to be stale (>300s) predate any active typing; hook reactivation is implausible for truly stale sessions |
| NIT ✅ | Simplifier | Pre-loop WARNING log of all stale session IDs is redundant | Replaced with single summary count log: `logger.warning("Found %d stale session(s) to process", len(stale_sessions))` | N/A |
| NIT ✅ | User | All success criteria were purely technical; no human-observable acceptance criterion | Added: "Worker restart log shows `[startup-recovery] Abandoned local session <id>`" to Success Criteria | N/A |

---

## Open Questions

None — the fix is well-defined by the existing health check pattern. No supervisor input needed before building.
