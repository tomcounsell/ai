---
status: Planning
type: chore
appetite: Small
owner: Valor Engels
created: 2026-04-17
tracking: https://github.com/tomcounsell/ai/issues/1017
last_comment_id:
---

# Worker Lifecycle Cleanup: Kill Command Gaps, Heartbeat Constant, State Machine Docs

## Problem

Four operational gaps degrade reliability and developer confidence in the session lifecycle subsystem. Operators cannot kill sessions stuck in non-terminal states like `dormant`, `paused`, or `paused_circuit` through the standard CLI. The worker heartbeat staleness threshold is duplicated as bare literals in two files. The state machine documentation claims 11 states but the code has 13. And `docs/features/agent-session-queue.md` references removed functions and fields.

**Current behavior:**
- `python -m tools.agent_session_scheduler kill --agent-session-id <ID>` returns "Session not found" for sessions in `dormant`, `paused`, `paused_circuit`, `superseded`, or `active` states — operators have no CLI path to terminate these without direct Redis surgery.
- The 360-second worker-healthy threshold is a bare integer literal in `ui/app.py:188,207` and `tools/agent_session_scheduler.py:489`; only `tools/valor_session.py:63` names it `_WORKER_HEALTHY_THRESHOLD_S`.
- `docs/features/session-lifecycle.md` header says "Session States (11 total)" but `models/session_lifecycle.py:64-73` defines 13 non-terminal states plus 5 terminal states; `paused` and `paused_circuit` are absent from all doc surfaces.
- `docs/features/agent-session-queue.md` references `_reset_running_jobs()`, `_job_hierarchy_health_check()`, and `_finalize_parent()` (all removed/renamed), plus `stable_agent_session_id` and `depends_on` fields, and describes the delete-and-recreate pattern as still active for status transitions when it was superseded by in-place `IndexedField` mutation.

**Desired outcome:**
- Kill command locates and terminates sessions in all non-terminal states.
- Single named constant for the 360s threshold; two call sites import it.
- All 13 states documented with descriptions everywhere states are listed.
- `docs/features/agent-session-queue.md` references only functions and fields that exist; no internal contradictions.

## Freshness Check

**Baseline commit:** `a0f861a6ccaae2844a26092c11a5a1847095a40f`
**Issue filed at:** 2026-04-17T02:59:49Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `tools/agent_session_scheduler.py:874,885,903` — kill command scans only 5 states — confirmed (lines 874, 885, 903 all show the same 5-element tuple)
- `ui/app.py:188,207` — bare literal `360` — confirmed at those exact lines
- `tools/agent_session_scheduler.py:489` — bare literal `360` — confirmed
- `tools/valor_session.py:63` — `_WORKER_HEALTHY_THRESHOLD_S = 360` — confirmed
- `agent/agent_session_queue.py:125` — `AGENT_SESSION_HEALTH_CHECK_INTERVAL = 300` — confirmed
- `models/session_lifecycle.py:64-73` — `NON_TERMINAL_STATUSES` includes `paused_circuit` and `paused` — confirmed at lines 72-73
- `docs/features/session-lifecycle.md:5` — "Session States (11 total)" — confirmed, `paused` and `paused_circuit` absent from the table
- `models/agent_session.py:117` — "Status values (11 total)" in docstring — confirmed
- `docs/features/agent-session-queue.md:23` — references `_reset_running_jobs()` — confirmed present in the doc
- `docs/features/agent-session-queue.md:139` — references `_finalize_parent()` — confirmed (now named `_finalize_parent_sync` in the code)
- `docs/features/agent-session-queue.md:126` — references `stable_agent_session_id` as active field — field still exists in `models/agent_session.py:477` (in the `_EXTRACT_FIELDS` list), so this reference is technically still accurate
- `docs/features/agent-session-queue.md:127` — references `~~depends_on~~` as already removed — correct, strikethrough notation present
- `docs/features/agent-session-queue.md:145` — references `_job_hierarchy_health_check()` — the function does NOT exist in `agent/agent_session_queue.py` (grep confirms zero hits)

**Active plans in `docs/plans/` overlapping this area:** none found for session lifecycle or agent-session-queue docs.

**Notes:** `_reset_running_jobs()` is absent from `agent/agent_session_queue.py` (grep: zero hits) — the doc at line 23 lists it as one of four delete-and-recreate callers, but the function was removed. The actual shutdown recovery path is `_recover_interrupted_agent_sessions_startup()`. The "delete-and-recreate" section itself is partially stale — `_pop_agent_session()` and status transitions now use in-place `IndexedField` mutation via `transition_status()`, not delete-and-recreate (per `session_lifecycle.py:1602` comment). The doc's section on this pattern needs a clear "prior design" callout.

## Prior Art

- **Issue #804** (closed 2026-04-07): `valor-session kill` used `transition_status()` for terminal `killed` status — bug fix applied the kill transition via the correct `finalize_session()` path. Relevant: shows the kill pathway has been patched before at the model level; this issue patches the CLI-level state scan.
- **Issue #783** (closed 2026-04-07): AgentSession status index corruption from delete-and-recreate bugs — root cause analysis led to switching `IndexedField` status mutations to in-place `transition_status()`. Relevant: context for why `agent-session-queue.md`'s delete-and-recreate section is now partially stale.
- **Issue #701** (not listed in search but cited in issue): Consolidate AgentSession lifecycle mutations — the single-entrypoint refactor that created `models/session_lifecycle.py`. Direct ancestor of this cleanup.

## Research

No relevant external findings — this is purely an internal refactor with no external library or API surface changes.

## Data Flow

**Kill command path (W3 fix):**

1. **Entry point**: `python -m tools.agent_session_scheduler kill --agent-session-id <ID>`
2. **`cmd_kill()` in `tools/agent_session_scheduler.py`**: iterates a hardcoded tuple of statuses, queries `AgentSession.query.filter(status=s)` for each, matches on `agent_session_id`
3. **Gap**: `dormant`, `paused`, `paused_circuit`, `superseded`, `active` not in the tuple → "Session not found"
4. **Fix**: replace the hardcoded tuple with `NON_TERMINAL_STATUSES` (imported from `models/session_lifecycle.py`) plus the existing terminal states for completeness
5. **`_kill_agent_session()`**: calls `finalize_session(session, "killed", ...)` — already correct; kill logic doesn't change
6. **Output**: structured JSON with `status: "killed"` and session details

**Heartbeat constant path (W4 fix):**

1. `tools/valor_session.py:63` — `_WORKER_HEALTHY_THRESHOLD_S = 360` (named constant, correct location)
2. `ui/app.py:188,207` — bare `360` (two sites needing import)
3. `tools/agent_session_scheduler.py:489` — bare `360` (one site needing import)
4. **Fix**: move the constant to `agent/constants.py` (the shared constants module) and add a comment explaining the 60s buffer above `AGENT_SESSION_HEALTH_CHECK_INTERVAL`; update the three consumer files to import from `agent/constants.py`

## Architectural Impact

- **New dependencies**: `agent/constants.py` becomes a dependency for `ui/app.py` and `tools/agent_session_scheduler.py` (both already import from `agent/` elsewhere)
- **Interface changes**: `_WORKER_HEALTHY_THRESHOLD_S` moves from `tools/valor_session.py` to `agent/constants.py`; `tools/valor_session.py` will re-export or import from the new location
- **Coupling**: Slight reduction — three files previously had divergent literal values, now share one constant
- **Data ownership**: No change
- **Reversibility**: Trivially reversible — no data or schema changes

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

- **Kill command fix**: `cmd_kill()` uses `NON_TERMINAL_STATUSES` from `models/session_lifecycle.py` for the state scan instead of a hardcoded tuple; the retry path also updated
- **Heartbeat constant**: `WORKER_HEALTHY_THRESHOLD_S` (drop the leading underscore for a shared public constant) moved to `agent/constants.py` with a comment linking it to `AGENT_SESSION_HEALTH_CHECK_INTERVAL`; three consumer files updated
- **State machine docs**: `docs/features/session-lifecycle.md`, `docs/features/agent-session-model.md`, `CLAUDE.md`, and `models/agent_session.py` docstring all updated to reflect all 13 states
- **Queue doc rewrite**: `docs/features/agent-session-queue.md` sections referencing removed functions (`_reset_running_jobs`, `_job_hierarchy_health_check`, `_finalize_parent`) and the stale delete-and-recreate narrative updated to reflect current code

### Technical Approach

**W3 — Kill command:**
- In `cmd_kill()`, import `NON_TERMINAL_STATUSES` from `models.session_lifecycle`
- Replace the hardcoded 5-state tuple at lines 874, 885, 903 with `tuple(NON_TERMINAL_STATUSES | {"completed", "failed"})` — keeping terminal states in the scan for the use case where operators kill a "completed" session that shouldn't be (idempotent per `finalize_session` guard)
- The `--all` path at line 862-864 only scans `running` and `pending` — this is intentional behavior (kill-all targets active work only) and is NOT changed
- `skip_process_kill` logic at line 926 already checks `entry.status != "running"` — this remains correct for new states since none of them have live subprocesses needing SIGTERM (paused/dormant sessions have no running subprocess)

**W4 — Heartbeat constant:**
- Add to `agent/constants.py`:
  ```python
  # Worker heartbeat staleness threshold.
  # The worker writes its heartbeat every AGENT_SESSION_HEALTH_CHECK_INTERVAL seconds (300s).
  # A threshold of 360s gives one full check-cycle grace period before declaring unhealthy.
  WORKER_HEALTHY_THRESHOLD_S: int = 360
  ```
- `tools/valor_session.py`: replace `_WORKER_HEALTHY_THRESHOLD_S = 360` with `from agent.constants import WORKER_HEALTHY_THRESHOLD_S`; update the two usages in the same file
- `ui/app.py`: import `WORKER_HEALTHY_THRESHOLD_S` from `agent.constants`; replace bare `360` literals at lines 188 and 207
- `tools/agent_session_scheduler.py`: import `WORKER_HEALTHY_THRESHOLD_S` from `agent.constants`; replace bare `360` at line 489

**W6+W8 — State machine docs:**
- `docs/features/session-lifecycle.md`: change header to "Session States (13 total)"; add `paused` and `paused_circuit` rows to the non-terminal table with descriptions matching `models/session_lifecycle.py:72-73`
- `docs/features/agent-session-model.md`: update lifecycle diagram/table to include all 13 states
- `CLAUDE.md`: expand Session Management table from 4 rows to include all 13 operational states
- `models/agent_session.py`: update docstring "Status values (11 total)" to "(13 total)" and add `paused` and `paused_circuit` entries

**W7 — Queue doc:**
- Remove or clearly mark-historical the "Delete-and-Recreate Pattern" section (it describes the old approach; in-place `transition_status()` is now the standard path per `session_lifecycle.py:1602`)
- Replace `_reset_running_jobs()` reference with `_recover_interrupted_agent_sessions_startup()` (the actual function)
- Replace `_finalize_parent()` references with `_finalize_parent_sync()` (the actual function)
- Remove `_job_hierarchy_health_check()` reference (function deleted); replace with accurate description of the health-check task in `_periodic_health_check()` / `_perform_health_checks()`
- Retain `stable_agent_session_id` documentation (field still exists); retain `~~depends_on~~` strikethrough (accurate as historical note)

## Failure Path Test Strategy

### Exception Handling Coverage
- No exception handlers are added by this work; the `try/except` in `cmd_kill()` already exists and is unchanged.

### Empty/Invalid Input Handling
- The kill command already returns structured errors for empty/missing `--agent-session-id`. No new input paths are added.

### Error State Rendering
- Kill command returns structured JSON with `status: "error"` for not-found — this path is unchanged and already tested.

## Test Impact

- [ ] `tests/unit/test_agent_session_scheduler_kill.py` — UPDATE: add test asserting that a session in `dormant` status is found and killed by `cmd_kill --agent-session-id`; add test for `paused` and `paused_circuit` states; verify "Session not found" is NOT returned for these states
- [ ] `tests/unit/test_agent_session_scheduler_kill.py::test_nonexistent_session_returns_error` (or equivalent) — UPDATE: verify the test still exercises a session in a truly missing state (not just an unscanned state)

No other existing tests are affected — the heartbeat constant rename is a search-and-replace with no behavioral change, and the doc updates add no new code paths.

## Rabbit Holes

- **Unifying `_WORKER_HEALTHY_THRESHOLD_S` and `AGENT_SESSION_HEALTH_CHECK_INTERVAL` into a single constant**: these two values serve different purposes (staleness threshold vs. check frequency); combining them would obscure the intentional 60s buffer. Don't merge them.
- **Refactoring `cmd_kill` to use a different lookup strategy**: the current per-status filter loop is correct and already handles the Redis Popoto query pattern; switching to a scan-all approach would change semantics for large deployments. Avoid.
- **Rewriting `docs/features/agent-session-queue.md` from scratch**: the document has substantial correct content (priority model, deferred execution, parent-child hierarchy). Only the stale function references and the delete-and-recreate section need updating. A full rewrite risks losing accurate content.
- **Adding `paused`/`paused_circuit` to `CLAUDE.md`'s quick-reference commands table**: the table shows states relevant to operators, not all internal states. Adding all 13 states would bloat the table beyond utility. Session Management table can remain operator-focused; the definitive reference is `docs/features/session-lifecycle.md`.

## Risks

### Risk 1: Kill semantics for paused/dormant sessions
**Impact:** A `paused` session has active state in Redis (e.g., `queued_steering_messages`, `session_events`). Killing it via `finalize_session()` correctly terminates the session, but the session-resume-drip in `bridge/watchdog.py` might still attempt revival before it sees the `killed` status.
**Mitigation:** `finalize_session()` is already idempotent and sets `completed_at`. The resume-drip checks `NON_TERMINAL_STATUSES` and skips sessions not in the set — a `killed` session won't be revived. No additional guard needed.

### Risk 2: Import cycle — `agent/constants.py` imported by `ui/app.py`
**Impact:** `ui/app.py` importing from `agent/` could introduce an import cycle if `agent/constants.py` ever imports from `ui/`.
**Mitigation:** `agent/constants.py` only defines primitive constants — no imports from other project modules. The cycle risk is zero.

## Race Conditions

No race conditions identified. All changes are: (a) importing a constant (no state), (b) expanding a state scan list (no new mutation paths), or (c) documentation. The kill execution path itself (`_kill_agent_session` → `finalize_session`) is unchanged.

## No-Gos (Out of Scope)

- Refactoring `agent/agent_session_queue.py` (5031 LOC) — separate issue per the issue spec
- Naming drift cleanup (`session_type` vs `role`, `parent_session_id` vs `parent_agent_session_id`) — separate issue
- Investigating `agent/hooks/subagent_stop.py` orphan status post-CLI migration — separate investigation
- Adding `paused`/`paused_circuit` revival integration tests — valuable but separate from this cleanup scope

## Update System

No update system changes required — all changes are internal code and documentation; no new dependencies, config files, or migration steps needed.

## Agent Integration

No agent integration required — the kill command is a CLI tool for operators, not an MCP-exposed agent capability. The heartbeat constant change is internal to the worker/UI. No `.mcp.json` or MCP server changes needed.

## Documentation

- [ ] Update `docs/features/session-lifecycle.md`: change "11 total" to "13 total" in the header; add `paused` and `paused_circuit` rows to the non-terminal table
- [ ] Update `docs/features/agent-session-model.md`: add `paused` and `paused_circuit` to the lifecycle diagram/state table
- [ ] Update `CLAUDE.md`: expand Session Management table to include all 13 states (or add a "See also: `docs/features/session-lifecycle.md` for full state reference" note)
- [ ] Update `docs/features/agent-session-queue.md`: remove/historicize stale function references; fix `_finalize_parent` → `_finalize_parent_sync`; remove `_reset_running_jobs` and `_job_hierarchy_health_check` references

## Success Criteria

- [ ] `python -m tools.agent_session_scheduler kill --agent-session-id <ID>` successfully kills sessions in all non-terminal states (`dormant`, `paused`, `paused_circuit`, `superseded`, `active`, plus existing states)
- [ ] The 360s worker-healthy threshold is defined as `WORKER_HEALTHY_THRESHOLD_S` in `agent/constants.py`; `ui/app.py` and `tools/agent_session_scheduler.py` import and use it; `tools/valor_session.py` also imports from `agent/constants.py`
- [ ] `docs/features/session-lifecycle.md` header says "13 total" and includes `paused` and `paused_circuit` rows
- [ ] `docs/features/agent-session-model.md` includes `paused` and `paused_circuit` in its state listing
- [ ] `models/agent_session.py` docstring count says "13 total" and lists `paused` and `paused_circuit`
- [ ] `docs/features/agent-session-queue.md` contains zero references to `_reset_running_jobs`, `_job_hierarchy_health_check`, or `_finalize_parent()` (as opposed to `_finalize_parent_sync`); no internal contradictions
- [ ] Tests pass (`pytest tests/unit/test_agent_session_scheduler_kill.py -v`)
- [ ] Lint clean (`python -m ruff check .`)

## Team Orchestration

### Team Members

- **Builder (lifecycle-cleanup)**
  - Name: lifecycle-builder
  - Role: Implement all four fixes: kill command state scan, heartbeat constant extraction, state machine doc updates, queue doc correction
  - Agent Type: builder
  - Resume: true

- **Validator (lifecycle-cleanup)**
  - Name: lifecycle-validator
  - Role: Verify kill command works for all non-terminal states, constant is in the right place, docs are accurate
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Fix kill command state scan (W3)
- **Task ID**: build-kill-states
- **Depends On**: none
- **Validates**: `tests/unit/test_agent_session_scheduler_kill.py`
- **Assigned To**: lifecycle-builder
- **Agent Type**: builder
- **Parallel**: true
- In `tools/agent_session_scheduler.py`, import `NON_TERMINAL_STATUSES` from `models.session_lifecycle`
- Replace the hardcoded 5-state tuple at lines 874, 885, and 903 with `tuple(NON_TERMINAL_STATUSES | {"completed", "failed"})` (or an equivalent sorted tuple for determinism)
- Add unit tests in `tests/unit/test_agent_session_scheduler_kill.py` for `dormant`, `paused`, and `paused_circuit` states being found and killed
- Verify `skip_process_kill` logic remains correct (only `running` sessions get SIGTERM)

### 2. Extract heartbeat threshold constant (W4)
- **Task ID**: build-heartbeat-constant
- **Depends On**: none
- **Validates**: `python -m ruff check . && python -c "from agent.constants import WORKER_HEALTHY_THRESHOLD_S; assert WORKER_HEALTHY_THRESHOLD_S == 360"`
- **Assigned To**: lifecycle-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `WORKER_HEALTHY_THRESHOLD_S: int = 360` to `agent/constants.py` with the comment explaining the 60s buffer above `AGENT_SESSION_HEALTH_CHECK_INTERVAL`
- Update `tools/valor_session.py` to import from `agent.constants` instead of defining locally
- Update `ui/app.py` lines 188 and 207 to use the imported constant
- Update `tools/agent_session_scheduler.py` line 489 to use the imported constant

### 3. Update state machine documentation (W6+W8)
- **Task ID**: build-state-docs
- **Depends On**: none
- **Validates**: `grep -c "paused" docs/features/session-lifecycle.md` (output > 0)
- **Assigned To**: lifecycle-builder
- **Agent Type**: documentarian
- **Parallel**: true
- `docs/features/session-lifecycle.md`: update header to "13 total"; add `paused` and `paused_circuit` rows with descriptions
- `docs/features/agent-session-model.md`: add `paused` and `paused_circuit` to any state listing
- `CLAUDE.md`: update Session Management table to add `paused`, `paused_circuit`, `pending`, `running`, `waiting_for_children`, `superseded`, `cancelled`, `failed`, `killed` — or add a pointer to `docs/features/session-lifecycle.md` for the full reference
- `models/agent_session.py`: update docstring from "(11 total)" to "(13 total)"; add `paused_circuit` and `paused` entries in the non-terminal section

### 4. Correct agent-session-queue.md (W7)
- **Task ID**: build-queue-doc
- **Depends On**: none
- **Validates**: `grep -c "_reset_running_jobs\|_job_hierarchy_health_check" docs/features/agent-session-queue.md` (exit code 0, output = 0)
- **Assigned To**: lifecycle-builder
- **Agent Type**: documentarian
- **Parallel**: true
- Remove `_reset_running_jobs()` from the delete-and-recreate callers list; replace with `_recover_interrupted_agent_sessions_startup()` (the actual startup recovery function)
- Add a callout that the "Delete-and-Recreate Pattern" section describes the historical design; note that status transitions now use in-place `IndexedField` mutation via `transition_status()` for KeyField-free paths
- Replace `_finalize_parent()` references with `_finalize_parent_sync()`
- Remove `_job_hierarchy_health_check()` reference; describe the health check logic as part of the periodic health monitor in `_perform_health_checks()` / `_periodic_health_check()`
- Verify `stable_agent_session_id` reference is accurate (field still exists — retain)

### 5. Final validation
- **Task ID**: validate-all
- **Depends On**: build-kill-states, build-heartbeat-constant, build-state-docs, build-queue-doc
- **Assigned To**: lifecycle-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_agent_session_scheduler_kill.py -v` and verify all tests pass
- Run `python -m ruff check . && python -m ruff format --check .`
- Verify `grep "WORKER_HEALTHY_THRESHOLD_S" ui/app.py tools/agent_session_scheduler.py` shows imports, not literals
- Verify `grep "13 total" docs/features/session-lifecycle.md models/agent_session.py` confirms both updated
- Verify `grep "_reset_running_jobs\|_job_hierarchy_health_check" docs/features/agent-session-queue.md` returns no matches

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_agent_session_scheduler_kill.py -v` | exit code 0 |
| Full unit suite | `pytest tests/unit/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Heartbeat constant importable | `python -c "from agent.constants import WORKER_HEALTHY_THRESHOLD_S; assert WORKER_HEALTHY_THRESHOLD_S == 360"` | exit code 0 |
| No bare 360 in ui/app.py | `grep -n "< 360\|> 360\|== 360" ui/app.py` | exit code 1 |
| No stale queue doc functions | `grep -c "_reset_running_jobs\|_job_hierarchy_health_check" docs/features/agent-session-queue.md` | output contains 0 |
| State count updated | `grep "13 total" docs/features/session-lifecycle.md` | exit code 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

No open questions — all four items are well-scoped and confirmed by freshness check. Plan is ready for critique.
