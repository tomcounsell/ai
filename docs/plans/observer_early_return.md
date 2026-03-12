---
status: Planning
type: bug
appetite: Medium
owner: Valor
created: 2026-03-12
tracking:
last_comment_id:
---

# Fix Observer Early Return on Continuation Sessions

## Problem

The Observer agent prematurely delivers output on continuation sessions instead of continuing the SDLC pipeline. This requires Tom to manually type "continue" to resume work that should have been automatic.

**Current behavior:**
When Tom replies to an SDLC session (e.g., "finish every SDLC step and merge"), three bugs compound:
1. Claude Code resumes the **wrong session** (an unrelated previous transcript)
2. The watchdog evaluates the **wrong transcript** with inflated tool counts, killing the session after only 4 turns
3. The Observer re-reads the **wrong AgentSession record** (from duplicates), sees `is_sdlc=False`, and delivers instead of steering

**Desired outcome:**
Continuation sessions resume the correct context, the watchdog evaluates the correct transcript, and the Observer correctly identifies SDLC sessions — enabling uninterrupted pipeline progression.

## Prior Art

- **PR #321**: "Observer Agent: replace auto-continue/summarizer with stage-aware SDLC steerer" — Established the current Observer architecture. The three bugs are in code paths that existed before or were introduced alongside this refactor.
- **Issue #232**: Original session cross-wire bug — introduced the `_has_prior_session()` guard in `sdk_client.py`. That guard prevents fresh sessions from reusing stale transcripts, but doesn't handle the case where a Telegram session ID never matches a Claude Code UUID-named transcript file.
- **Issue #246**: SDLC classification preservation — addressed classification_type loss during auto-continue. The `_enqueue_continuation()` function now preserves metadata, but the duplicate-record problem predates this fix.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| Issue #232 guard | Added `_has_prior_session()` check before setting `continue_conversation=True` | Guard checks Redis for AgentSession records with the Telegram session ID, but Claude Code stores transcripts as UUID-named files. The Telegram ID passes the Redis check (prior records exist) but doesn't match any `.jsonl` file — so Claude Code falls back to the most recent session on disk. |
| Issue #246 fix | Made `_enqueue_continuation()` preserve metadata via delete-and-recreate | Solved the auto-continue case but didn't address the duplicate-record case where `_push_job()` creates a **new** AgentSession for a reply-to-resume message alongside the old completed one. |

**Root cause pattern:** Each fix addresses one layer of the session identity problem but the fundamental mismatch between Telegram session IDs and Claude Code session UUIDs was never resolved end-to-end.

## Data Flow

Tracing the continuation session flow that fails:

1. **Entry point**: Tom sends a reply-to message in Telegram ("finish every SDLC step")
2. **Bridge (`telegram_bridge.py`)**: Resolves session_id from reply-to message → `tg_valor_-5051653062_7290`. Classifier runs async, correctly identifies as SDLC.
3. **`_push_job()` (`job_queue.py:284`)**: Creates a **new** AgentSession record with `session_id=tg_valor_..._7290`, `classification_type="sdlc"`, `status="pending"`. Old completed record with same session_id still exists in Redis.
4. **`_create_options()` (`sdk_client.py:576`)**: `_has_prior_session()` finds old completed record → returns True → sets `resume="tg_valor_..._7290"`, `continue_conversation=True`.
5. **Claude Code**: Looks for `tg_valor_-5051653062_7290.jsonl` — doesn't exist. Falls back to most recent session file on disk → `87ddb6eb-*.jsonl` (unrelated prior session).
6. **Watchdog hook (`health_check.py:26`)**: `_tool_counts["87ddb6eb-..."]` already at 120+ from prior run. Fires at #140, reads OLD transcript, judges UNHEALTHY, kills session after 4 turns.
7. **Completion handler (`job_queue.py:1206`)**: Re-reads AgentSession records, `filter(session_id=...)` returns multiple records. `fresh[0]` is indeterminate — may pick the old completed record with `classification_type=None`.
8. **Observer (`observer.py`)**: Sees `is_sdlc=False` from wrong record → delivers to Telegram instead of steering pipeline.

## Architectural Impact

- **No new dependencies**: All fixes are internal to existing modules
- **Interface changes**: None — all changes are implementation-level within existing functions
- **Coupling**: Slightly tighter coupling between `sdk_client.py` and `AgentSession` (storing Claude Code UUID), but this is appropriate — the session model should be the single source of truth for identity mapping
- **Data ownership**: `AgentSession` becomes the canonical mapping between Telegram session IDs and Claude Code transcript UUIDs
- **Reversibility**: Each fix is independently reversible

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 0 (requirements are clear from the issue)
- Review rounds: 1 (code review for correctness)

Three independent but related bugs. Each fix is Small on its own, but they need to be tested together to verify the compound failure is resolved.

## Prerequisites

No prerequisites — all work is internal to existing codebase with no new external dependencies.

## Solution

### Key Elements

- **Bug 1 fix (session cross-wire)**: Store the Claude Code UUID in AgentSession when the SDK returns it; use the UUID (not Telegram session ID) for `resume` on continuations
- **Bug 2 fix (stale tool counts)**: Reset watchdog tool counts at the start of each new SDK query; scope counts to bridge session ID via `VALOR_SESSION_ID` env var
- **Bug 3 fix (duplicate records)**: Filter by `status` when re-reading AgentSession in completion handler and Observer; clean up old completed records when creating continuation sessions

### Flow

**Reply-to message arrives** → Bridge resolves session_id → `_push_job()` creates new AgentSession (cleaning up old completed record) → `_create_options()` looks up stored Claude Code UUID for `resume` → Claude Code resumes correct transcript → Watchdog starts fresh tool count → Worker completes stage → Observer re-reads correct AgentSession (filtered by status=running) → `is_sdlc=True` → Pipeline continues

### Technical Approach

**Bug 1: Session identity mapping**
- Add `claude_session_uuid` field to `AgentSession` model
- In `sdk_client.py`, after SDK query completes, extract the Claude Code session UUID from the response/transcript and store it on the AgentSession
- In `_create_options()`, look up the `claude_session_uuid` from the AgentSession for the `resume` parameter instead of using the Telegram session ID
- If no UUID is stored (first message in session), don't set `resume` — let Claude Code create a fresh session

**Bug 2: Watchdog count scoping**
- In `health_check.py`, use the `VALOR_SESSION_ID` env var (already set by `sdk_client.py` at line 563) instead of the Claude Code UUID for count tracking
- Add a `reset_session_count()` function and call it from `sdk_client.py` at query start
- This ensures counts are scoped to the bridge session lifecycle, not the Claude Code session lifecycle

**Bug 3: Deterministic record selection**
- In `job_queue.py:1206`, filter by `status="running"` when re-reading the session
- In `observer.py:308`, apply the same filter
- In `_push_job()`, when creating a new record for a continuation (reply-to-resume), mark old completed records with the same `session_id` as `superseded` to prevent ambiguity
- Alternatively: use `sorted()` by `created_at` descending and take the first, ensuring we always get the newest record

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_has_prior_session()` already has try/except — verify it logs and returns False on Redis errors
- [ ] `_enqueue_continuation()` delete-and-recreate — verify rollback if recreate fails
- [ ] Observer `_handle_update_session()` re-read — verify graceful fallback if filter returns empty

### Empty/Invalid Input Handling
- [ ] `claude_session_uuid=None` on first message — verify `resume` is not set
- [ ] `VALOR_SESSION_ID` missing from env — verify watchdog falls back to Claude Code UUID
- [ ] Empty `filter()` result when filtering by `status="running"` — verify fallback to unfiltered query

### Error State Rendering
- [ ] If watchdog misjudges, verify error message in logs includes both session IDs (Telegram and Claude Code) for debugging
- [ ] If Observer picks wrong record, verify logging shows which record was selected and why

## Rabbit Holes

- **Migrating all session identity to UUIDs**: The Telegram session ID scheme works fine for routing; only the `resume` parameter needs the Claude Code UUID. Don't refactor the entire session identity model.
- **Making the watchdog judge smarter**: The judge itself is fine — it correctly identified the old transcript as unhealthy. The bug is that it was reading the wrong transcript. Don't tune the judge prompt.
- **Deduplicating all historical AgentSession records**: Old duplicates in Redis are harmless. Only fix the selection logic going forward; don't run a migration to clean up past records.

## Risks

### Risk 1: Claude Code UUID extraction is fragile
**Impact:** If the SDK doesn't expose the session UUID in a stable way, Bug 1 fix breaks on SDK upgrades.
**Mitigation:** Check the claude-agent-sdk API surface for official session ID access. If not available, parse the transcript directory for the latest `.jsonl` file created during the query. Fall back to no-resume behavior if UUID extraction fails.

### Risk 2: Status filter too strict — misses valid records
**Impact:** If the record's status transitions between creation and re-read (e.g., a race between worker completion and observer read), the filter returns nothing.
**Mitigation:** Use a fallback chain: filter by `status="running"` first; if empty, filter by `status` in `("running", "active", "pending")`; if still empty, fall back to the current unfiltered behavior with a warning log.

## Race Conditions

### Race 1: AgentSession status transitions during Observer re-read
**Location:** `job_queue.py:1204-1210`
**Trigger:** Worker marks session `completed` right as the completion handler tries to re-read with `status="running"` filter.
**Data prerequisite:** AgentSession must still be in `running` status when the completion handler reads it.
**State prerequisite:** The worker and completion handler run in the same async context, so this is sequential — but if the SDK `query()` method updates status before returning, the handler sees `completed`.
**Mitigation:** Use a broader status filter: `status in ("running", "active", "completed")` with `sort by created_at desc, take first`. The key invariant is picking the RIGHT record (newest), not filtering by exact status.

### Race 2: Concurrent reply-to messages for the same session
**Location:** `_push_job()` in `job_queue.py`
**Trigger:** Tom sends two rapid reply-to messages to the same session before the first is processed.
**Data prerequisite:** First message's AgentSession must exist before second message's cleanup runs.
**State prerequisite:** Job queue is single-consumer per project, so this is safe — second message queues behind the first.
**Mitigation:** No additional mitigation needed; the single-consumer queue serializes access.

## No-Gos (Out of Scope)

- Refactoring the Telegram session ID scheme — it works; only the SDK resume path needs the UUID
- Watchdog judge prompt tuning — the judge is correct; only the input it receives is wrong
- Historical duplicate record cleanup — only affects forward selection logic
- Changing the Observer's decision framework — it correctly delivers when `is_sdlc=False`; the bug is upstream

## Update System

No update system changes required — this is a bridge-internal change. Dependencies are unchanged. The fix deploys via normal `git pull` + service restart through `/update`.

## Agent Integration

No agent integration required — all three bugs are in the bridge/agent infrastructure layer. No new MCP servers, no changes to `.mcp.json`, no new tools exposed. The bridge code changes are internal to `agent/sdk_client.py`, `agent/health_check.py`, `agent/job_queue.py`, and `bridge/observer.py`.

## Documentation

- [ ] Update `docs/features/session-isolation.md` with the Claude Code UUID mapping (new `claude_session_uuid` field)
- [ ] Add entry to `docs/features/README.md` index table for session identity mapping if not already covered
- [ ] Update inline docstrings on `_has_prior_session()`, `_create_options()`, `_enqueue_continuation()`, and `health_check._tool_counts`

## Success Criteria

- [ ] Continuation sessions resume the correct Claude Code transcript (verified by log showing UUID match)
- [ ] Watchdog tool counts reset to 0 at the start of each new query (verified by log showing count=1 on first check)
- [ ] Observer reads `is_sdlc=True` for SDLC continuation sessions (verified by log showing correct classification)
- [ ] Full SDLC pipeline (PLAN→BUILD→TEST→REVIEW→DOCS→MERGE) completes without manual "continue" on a continuation session
- [ ] No regression: fresh sessions (non-continuation) still work correctly
- [ ] No regression: non-SDLC sessions still deliver after max auto-continues
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (session-identity)**
  - Name: identity-builder
  - Role: Implement Bug 1 fix (Claude Code UUID mapping) and Bug 3 fix (deterministic record selection)
  - Agent Type: builder
  - Resume: true

- **Builder (watchdog-fix)**
  - Name: watchdog-builder
  - Role: Implement Bug 2 fix (tool count scoping and reset)
  - Agent Type: builder
  - Resume: true

- **Validator (integration)**
  - Name: integration-validator
  - Role: Verify all three fixes work together on a simulated continuation flow
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Fix session identity mapping (Bug 1 + Bug 3)
- **Task ID**: build-session-identity
- **Depends On**: none
- **Assigned To**: identity-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `claude_session_uuid` field to `AgentSession` model
- Modify `sdk_client.py` to store Claude Code UUID after query completes
- Modify `_create_options()` to use stored UUID for `resume` parameter
- Modify `job_queue.py:1206` to filter by status and sort by `created_at` desc
- Modify `observer.py:308` with same deterministic selection logic
- Add cleanup of old completed records in `_push_job()` for reply-to-resume cases

### 2. Fix watchdog tool count scoping (Bug 2)
- **Task ID**: build-watchdog-fix
- **Depends On**: none
- **Assigned To**: watchdog-builder
- **Agent Type**: builder
- **Parallel**: true
- Modify `health_check.py` to use `VALOR_SESSION_ID` env var for count tracking
- Add `reset_session_count()` function
- Call reset from `sdk_client.py` at query start
- Add fallback to Claude Code UUID if `VALOR_SESSION_ID` not set

### 3. Write tests for all three fixes
- **Task ID**: build-tests
- **Depends On**: build-session-identity, build-watchdog-fix
- **Assigned To**: identity-builder
- **Agent Type**: builder
- **Parallel**: false
- Test: `_create_options()` uses stored UUID for resume, not Telegram ID
- Test: `_has_prior_session()` correctly handles UUID-based lookups
- Test: watchdog counts reset on new query, scope to VALOR_SESSION_ID
- Test: completion handler selects newest running record from duplicates
- Test: Observer sees `is_sdlc=True` when correct record is selected

### 4. Integration validation
- **Task ID**: validate-integration
- **Depends On**: build-tests
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all three fixes compile and pass lint
- Verify no regressions in existing test suite
- Trace the data flow end-to-end through logs
- Verify success criteria are met

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-integration
- **Assigned To**: identity-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/session-isolation.md`
- Update inline docstrings
- Add entry to docs index if needed

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify all success criteria
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| UUID field exists | `grep -n 'claude_session_uuid' models/agent_session.py` | output contains claude_session_uuid |
| Reset function exists | `grep -n 'def reset_session_count' agent/health_check.py` | output contains reset_session_count |
| Status filter in job_queue | `grep -n 'status.*running' agent/job_queue.py` | output contains status filter |
| Status filter in observer | `grep -n 'status.*running' bridge/observer.py` | output contains status filter |
