---
status: Planning
type: bug
appetite: Medium
owner: Valor
created: 2026-03-11
tracking: https://github.com/tomcounsell/ai/issues/360
last_comment_id:
---

# Smart Stall Detection via Transcript Mtime

## Problem

The session watchdog detects stalled sessions but can't distinguish **productive silence** (deep sub-agent work) from **real stalls** (stuck, crashed, looping). This causes two failure modes:

1. **False positives**: A builder spawning test agents 3 levels deep shows no `last_activity` updates for 20+ minutes. The watchdog sees "silent for 20 min" and kills productive work.
2. **20-minute dead zone**: Silence is detected at 10 min (`SILENCE_THRESHOLD`) but kill+re-enqueue doesn't happen until 30 min (`ABANDON_THRESHOLD`). Stalled jobs sit idle for 20 minutes before recovery.
3. **Re-enqueue into blocked workers**: After killing, the re-enqueued job goes `pending` behind the same single-project worker. After 3 retries it's abandoned.

**Current behavior:**
- `last_activity` only updates at session start and end — blind to sub-agent progress
- 10-min detection but 30-min action threshold creates a dead zone
- 5 work items stalled in Dev: Valor chat (PRs #370, #367; issues #360, #363, #364) because the watchdog couldn't tell productive sessions from stuck ones

**Desired outcome:**
- Observer checks every 5 min and **accurately** identifies stalls using transcript file mtime
- Truly stalled sessions are killed and re-enqueued immediately on detection
- Productive sessions with active sub-agents are left alone
- Timeout stays as a last-resort safety net that should almost never fire

## Prior Art

- **PR #217** (merged 2026-02-27): Added session lifecycle diagnostics and stall detection. Introduced `retry_count`/`last_stall_reason` fields and `log_lifecycle_transition()`. Established the diagnostic data model this feature builds on.
- **PR #316** (merged 2026-03-09): Stall detection and automatic retry for agent sessions. Implemented `_kill_stalled_worker()`, `_enqueue_stall_retry()`, and exponential backoff. This is the mechanical recovery layer — works correctly but uses the wrong signal (`last_activity` instead of transcript mtime).
- **PR #344** (merged 2026-03-10): Fix session stuck in pending after BUILD COMPLETED. Addressed a specific race condition but didn't fix the fundamental detection problem.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #316 | Added stall retry with `_kill_stalled_worker()` | Uses `last_activity` which only updates at session start/end — can't see sub-agent progress |
| PR #344 | Fixed pending→running race | Addressed one specific race but the general "is this session making progress?" question remained unanswered |

**Root cause pattern:** All prior fixes relied on `last_activity` timestamps in Redis, which require explicit updates from the top-level session. Sub-agents (builder → test-runner → baseline-verifier) don't update this field. The system needs a **process-level signal** that reflects actual I/O, not application-level bookkeeping.

## Data Flow

1. **Entry point**: `_session_health_loop()` runs every 5 min in the bridge event loop
2. **Detection**: For each `status="active"` session, check transcript file mtime at `logs/sessions/{session_id}/transcript.txt`
3. **Classification**: Compare `now - mtime` against threshold. Fresh mtime = healthy (sub-agents writing). Stale mtime = truly stalled.
4. **Recovery**: If stale >15 min → `_kill_stalled_worker()` → `_enqueue_stall_retry()` → `_ensure_worker()`
5. **Fallback**: If transcript file doesn't exist, fall back to current `last_activity` heuristic

## Architectural Impact

- **New dependencies**: None. Uses `os.path.getmtime()` and `pathlib.Path` (stdlib).
- **Interface changes**: `_handle_unhealthy_session()` gains transcript mtime check before acting. New `_check_transcript_liveness()` function.
- **Coupling**: No new coupling. Reads transcript files that `bridge/session_transcript.py` already writes.
- **Data ownership**: No change. Transcript files are owned by the SDK subprocess; watchdog only reads mtime.
- **Reversibility**: High. The transcript check is additive — remove it and behavior reverts to current `last_activity` logic.

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (scope is clear from this conversation)
- Review rounds: 1

## Prerequisites

No prerequisites — uses only stdlib (`os.path.getmtime`, `pathlib.Path`) and existing transcript files.

## Solution

### Key Elements

- **Transcript liveness check**: `os.path.getmtime()` on `logs/sessions/{session_id}/transcript.txt` — the cheapest reliable signal for sub-agent activity
- **Single threshold**: If transcript stale >15 min, session is truly stalled. No more dual-threshold dead zone.
- **Smart retry reset**: If transcript grew between retries, reset `retry_count` (session made progress before re-stalling, not a persistent failure)

### Flow

**Watchdog tick (every 5 min)** → Check each active session → Read transcript mtime → *If fresh (<15 min):* healthy, skip → *If stale (>15 min):* kill worker + re-enqueue → *If no transcript:* fall back to `last_activity` check

### Technical Approach

**New function `_check_transcript_liveness(session) -> tuple[bool, float]`:**
- Builds path: `logs/sessions/{session.session_id}/transcript.txt`
- Returns `(is_alive, stale_seconds)` where `is_alive = stale_seconds < TRANSCRIPT_STALE_THRESHOLD`
- Falls back to `(None, 0)` if file doesn't exist (caller uses legacy check)
- Configurable via `TRANSCRIPT_STALE_THRESHOLD` env var (default: 900s / 15 min)

**Modified `_handle_unhealthy_session()`:**
- Before checking silence duration, call `_check_transcript_liveness()`
- If transcript is alive → return False (session is healthy, don't act)
- If transcript is stale → proceed with kill + re-enqueue (existing `_kill_stalled_worker()` + `_enqueue_stall_retry()`)
- If no transcript → fall back to current `last_activity` logic unchanged

**Modified `_enqueue_stall_retry()`:**
- Before incrementing `retry_count`, check if transcript file size grew since last retry
- If transcript grew → reset `retry_count` to 0 (session made progress, this is a new stall, not a persistent failure)
- Store `last_transcript_size` on AgentSession for comparison

**Threshold consolidation:**
- `TRANSCRIPT_STALE_THRESHOLD = 900` (15 min) replaces both `SILENCE_THRESHOLD` (10 min) and `ABANDON_THRESHOLD` (30 min)
- `DURATION_THRESHOLD = 7200` (2 hours) stays as safety net — should almost never fire

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_check_transcript_liveness()` wraps `os.path.getmtime()` in try/except for missing files, permission errors — returns `(None, 0)` on failure
- [ ] Existing `_handle_unhealthy_session()` try/except covers the new transcript check path

### Empty/Invalid Input Handling
- [ ] Session with no `session_id` — `_check_transcript_liveness()` returns `(None, 0)`, falls back to legacy
- [ ] Transcript file exists but is empty (0 bytes) — mtime still valid, treated normally
- [ ] Transcript path with special characters — `Path` handles this safely

### Error State Rendering
- [ ] When transcript-based stall is detected, the stall reason includes mtime age and transcript size for diagnostics
- [ ] Stall retry notification to Telegram includes "transcript stale for Xm" in the message

## Rabbit Holes

- **Reading transcript content for diagnosis**: Tempting to parse the transcript to classify why a session stalled (loops, errors, etc). Save for Phase 2. Mtime alone is sufficient for detection.
- **Process tree monitoring via psutil**: More complex, platform-dependent, and the transcript mtime gives the same signal with less code.
- **Updating `last_activity` from sub-agents**: Would require instrumentation in the SDK subprocess. The whole point is to avoid instrumenting the process — just check the file it already writes.
- **Multiple workers per project**: Architectural change that would help with re-enqueue-into-blocked-worker, but orthogonal to detection accuracy.

## Risks

### Risk 1: Transcript file not written frequently enough
**Impact:** Sub-agents might batch writes, making mtime less granular than expected. A session could look stale despite being active.
**Mitigation:** Claude Code writes to transcript on every tool call and result. Even a 5-minute gap between tool calls is normal (thinking time). The 15-min threshold provides generous headroom.

### Risk 2: Transcript file from a previous session
**Impact:** If session IDs are reused or a stale transcript exists from a crashed session, mtime could incorrectly indicate liveness.
**Mitigation:** `start_transcript()` creates a fresh file at session start. The mtime reflects writes from the current session. Stale files from previous sessions will have old mtimes and correctly indicate staleness.

## Race Conditions

### Race 1: Watchdog checks mtime while transcript is being written
**Location:** `monitoring/session_watchdog.py` `_check_transcript_liveness()`, concurrent with SDK subprocess writes
**Trigger:** Watchdog reads mtime at the exact moment the SDK subprocess is writing
**Data prerequisite:** File must exist
**State prerequisite:** None — `os.path.getmtime()` is atomic on POSIX
**Mitigation:** No mitigation needed. `getmtime()` returns the last completed write's timestamp. Partial writes don't affect mtime reads.

### Race 2: Session killed between transcript check and re-enqueue
**Location:** `_handle_unhealthy_session()` → `_kill_stalled_worker()` → `_enqueue_stall_retry()`
**Trigger:** Session completes naturally between the mtime check and the kill call
**Data prerequisite:** AgentSession must still exist in Redis
**State prerequisite:** Session must still be in `active` status
**Mitigation:** `_kill_stalled_worker()` handles already-dead workers gracefully (returns False). `_enqueue_stall_retry()` handles deleted sessions via try/except. Both are already robust to this race.

## No-Gos (Out of Scope)

- **LLM-powered failure diagnosis**: Classifying stalls as transient vs. code bug vs. infrastructure is valuable but separate. This plan only improves detection accuracy. Diagnosis is Phase 2 (future issue).
- **SDLC-powered self-healing**: Auto-filing issues and spawning SDLC fix jobs. Depends on accurate detection landing first. Future scope.
- **Multi-project health check**: This targets the `valor` project only.
- **Replacing the bridge watchdog**: Bridge-level crash recovery and session-level stall detection are complementary.
- **Multiple workers per project**: Would help with re-enqueue quality but is an architectural change, not a detection fix.

## Update System

No update system changes required. The transcript mtime check uses stdlib only (`os.path.getmtime`). No new dependencies, no new config files. The `TRANSCRIPT_STALE_THRESHOLD` env var defaults to 900s and doesn't need to be set on existing installations.

## Agent Integration

No agent integration required — this is a bridge-internal change. The watchdog runs inside the bridge's event loop and reads local transcript files. No MCP server, no tool exposure, no bridge import changes needed.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/stall-retry.md` to describe transcript mtime detection replacing `last_activity` heuristic
- [ ] Update `docs/features/session-watchdog.md` to document new detection mechanism and threshold consolidation
- [ ] Add entry to `docs/features/README.md` index table if not already present

### Inline Documentation
- [ ] Docstring on `_check_transcript_liveness()`
- [ ] Update module docstring in `monitoring/session_watchdog.py` to mention transcript mtime

## Success Criteria

- [ ] `_check_transcript_liveness()` returns `(True, _)` for sessions with recently-modified transcripts
- [ ] `_check_transcript_liveness()` returns `(False, stale_seconds)` for sessions with stale transcripts
- [ ] `_check_transcript_liveness()` returns `(None, 0)` when transcript file doesn't exist
- [ ] Active sessions with fresh transcripts are NOT killed (no false positives on sub-agent work)
- [ ] Active sessions with stale transcripts (>15 min) ARE killed and re-enqueued
- [ ] `retry_count` resets when transcript grew between retries
- [ ] Fallback to `last_activity` works when no transcript file exists
- [ ] `TRANSCRIPT_STALE_THRESHOLD` env var overrides the default
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (transcript-detection)**
  - Name: detection-builder
  - Role: Implement `_check_transcript_liveness()` and wire into watchdog
  - Agent Type: builder
  - Resume: true

- **Validator (stall-detection)**
  - Name: detection-validator
  - Role: Verify transcript mtime detection accuracy and no false positives
  - Agent Type: validator
  - Resume: true

- **Test Engineer**
  - Name: detection-tester
  - Role: Write tests for transcript liveness, threshold behavior, retry reset
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Update stall-retry and session-watchdog docs
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Implement transcript liveness check
- **Task ID**: build-liveness
- **Depends On**: none
- **Assigned To**: detection-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `_check_transcript_liveness(session) -> tuple[bool | None, float]` to `monitoring/session_watchdog.py`
- Add `TRANSCRIPT_STALE_THRESHOLD` constant (default 900s, env var override)
- Wire into `_handle_unhealthy_session()`: check transcript before acting, skip if alive
- Consolidate thresholds: transcript mtime replaces `SILENCE_THRESHOLD`/`ABANDON_THRESHOLD` dual check
- Add `last_transcript_size` field to AgentSession for retry reset logic

### 2. Implement smart retry reset
- **Task ID**: build-retry-reset
- **Depends On**: none
- **Assigned To**: detection-builder
- **Agent Type**: builder
- **Parallel**: true
- In `_enqueue_stall_retry()`, check transcript file size vs `last_transcript_size`
- If transcript grew since last retry, reset `retry_count` to 0
- Update `last_transcript_size` on re-enqueue
- Log transcript growth in stall reason for diagnostics

### 3. Validate detection accuracy
- **Task ID**: validate-detection
- **Depends On**: build-liveness, build-retry-reset
- **Assigned To**: detection-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify fresh transcript → session not killed
- Verify stale transcript → session killed and re-enqueued
- Verify missing transcript → falls back to legacy `last_activity`
- Verify retry reset when transcript grew

### 4. Write tests
- **Task ID**: test-detection
- **Depends On**: validate-detection
- **Assigned To**: detection-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Unit tests for `_check_transcript_liveness()` with mock transcript files (fresh, stale, missing)
- Unit tests for retry reset logic (transcript grew vs. didn't grow)
- Integration test: create a session with a transcript, verify watchdog classifies correctly
- Edge cases: empty transcript, permission errors, session without session_id

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: test-detection
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/stall-retry.md`
- Update `docs/features/session-watchdog.md`
- Update `docs/features/README.md` index

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: detection-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met (including documentation)
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Liveness function importable | `python -c "from monitoring.session_watchdog import _check_transcript_liveness"` | exit code 0 |
| Stall-retry docs updated | `grep -q 'transcript' docs/features/stall-retry.md` | exit code 0 |
