---
status: Planning
type: bug
appetite: Small
owner: Valor
created: 2026-03-17
tracking: https://github.com/tomcounsell/ai/issues/434
last_comment_id:
---

# Error Summary Enforcement for Failed Sessions

## Problem

When sessions fail, the `complete_transcript` function is called with `status="failed"` but often without a `summary` argument. This means the AgentSession record has an empty `summary` field. Later, the reflections system (`scripts/reflections.py`) picks up these failed sessions and passes them to Claude Haiku for categorization, but the error pattern dict contains only `"summary": ""` -- an empty string. The LLM then generates a vague, generic reflection like "Failed session with empty error summary, preventing root cause analysis" (which is what issue #434 describes). This gets filed as a GitHub issue that provides no actionable information.

**Current behavior:**

1. `sdk_client.py` line 1264: calls `complete_transcript(session_id, status="failed")` with NO summary and NO error context from the caught exception
2. `session_watchdog.py` line 179: sets `session.status = "failed"` with NO summary
3. `reflections.py` line 258-263: packages the failed session into an error pattern with `"summary": (session.summary or "")[:200]` -- which is empty
4. The LLM reflection produces a vague "empty error summary" bug pattern
5. A useless GitHub issue gets auto-filed

**Desired outcome:**

All code paths that mark a session as "failed" must capture and persist the error context (exception type, message, and brief traceback) into the session's `summary` field, so the reflections system can produce actionable bug reports.

## Prior Art

- **Issue #212**: "Improve root cause analysis: 5 Whys missed integration gaps" -- Addressed root cause analysis methodology but not the data quality problem (empty summaries)
- **Issue #293**: "Test coverage gaps: silent SDLC failures" -- Related to silent failures but focused on test coverage, not error capture

## Data Flow

1. **Entry point**: Exception raised during `agent.query()` in `sdk_client.py:1247`
2. **sdk_client.py catch block**: Logs the error (`logger.error`) but does NOT pass it to `complete_transcript`
3. **complete_transcript()**: Receives `status="failed"` with no summary, writes `SESSION_END: status=failed` to transcript with no error detail
4. **AgentSession**: Gets status set to "failed", summary remains empty/None
5. **reflections.py**: Reads the session, finds empty summary, passes to LLM
6. **LLM reflection**: Gets no actionable data, produces generic "empty error summary" pattern
7. **GitHub issue**: Auto-filed with no useful information (issue #434)

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites -- this work has no external dependencies.

## Solution

### Key Elements

- **Error capture at failure sites**: Pass exception details to `complete_transcript` as summary
- **Watchdog error capture**: Include the ModelException message when marking sessions failed
- **Reflections filtering**: Skip filing issues for failed sessions that still have empty summaries (defensive guard)

### Flow

**Exception occurs** → catch block captures `str(e)` + type → `complete_transcript(session_id, status="failed", summary=error_detail)` → **AgentSession.summary** populated → reflections reads meaningful error → actionable issue filed

### Technical Approach

1. **`agent/sdk_client.py` line 1264**: Pass the caught exception to `complete_transcript` as a summary string: `f"{type(e).__name__}: {e}"`
2. **`monitoring/session_watchdog.py` line 179**: After setting `session.status = "failed"`, also set `session.summary = f"Watchdog: {type(e).__name__}: {e}"` before `session.save()`
3. **`scripts/reflections.py` line 258**: Add a guard -- skip error_patterns entries where summary is empty, logging a warning so we can track if other code paths still produce empty summaries

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `sdk_client.py` crash guard (line 1261-1266): Test that the exception message is passed through to complete_transcript
- [ ] `session_watchdog.py` ModelException handler (line 178-187): Test that summary is set before save

### Empty/Invalid Input Handling
- [ ] Test that `complete_transcript` handles summary=None vs summary="" correctly (existing behavior, just verify)
- [ ] Test that reflections skips empty-summary failed sessions

### Error State Rendering
- [ ] Verify the error summary appears in the reflections LLM prompt data (so the LLM gets actionable context)

## Test Impact

- [ ] `tests/unit/test_summarizer.py` -- multiple tests create sessions with `status="failed"` but these test the summarizer, not the error capture path. No changes needed.
- [ ] `tests/unit/test_session_watchdog.py::test_*` -- UPDATE: tests that verify watchdog marks sessions as failed should also assert summary is populated

No other existing tests affected -- the error capture paths in sdk_client.py and reflections.py are currently untested for this specific behavior.

## Rabbit Holes

- **Full stack trace capture**: Capturing multi-line tracebacks into a single-line summary field adds complexity. A one-line `{ExceptionType}: {message}` is sufficient for the reflections LLM to categorize the bug. Full tracebacks are already in bridge.log.
- **Retry/recovery logic**: The purpose here is diagnostic data quality, not fixing the underlying failures. Each root cause is a separate issue.
- **Refactoring complete_transcript**: The function works fine; we just need callers to pass the summary argument.

## Risks

### Risk 1: Summary field length overflow
**Impact:** Very long exception messages could be truncated unexpectedly
**Mitigation:** The summary field has `max_length=50_000` which is more than sufficient. The `complete_transcript` function already truncates to 200 chars in the transcript file, but the AgentSession field stores the full value.

## Race Conditions

No race conditions identified -- all changes are in synchronous exception handlers that execute in the same thread as the failure.

## No-Gos (Out of Scope)

- Not fixing the underlying causes of session failures (each is a separate issue)
- Not adding structured error fields to AgentSession (overkill for this appetite)
- Not changing the reflections LLM prompt (the data quality fix is sufficient)

## Update System

No update system changes required -- this is a bridge-internal bug fix with no new dependencies or configuration.

## Agent Integration

No agent integration required -- this is a bridge-internal change affecting error capture in the SDK client, watchdog, and reflections script.

## Documentation

- [ ] Update `docs/features/session-lifecycle-diagnostics.md` if it exists, to note that failed sessions now carry error summaries
- [ ] Add inline code comments at each fix site explaining why the summary is important

## Success Criteria

- [ ] `sdk_client.py` crash guard passes exception details to `complete_transcript` summary
- [ ] `session_watchdog.py` sets summary before marking sessions as failed
- [ ] `reflections.py` skips empty-summary failed sessions with a warning log
- [ ] Unit test verifies error summary flows from exception to AgentSession.summary
- [ ] Unit test verifies reflections skips empty-summary sessions
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (error-capture)**
  - Name: error-capture-builder
  - Role: Implement error summary propagation at all failure sites
  - Agent Type: builder
  - Resume: true

- **Validator (error-capture)**
  - Name: error-capture-validator
  - Role: Verify error summaries are populated in all failure paths
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Fix error capture at failure sites
- **Task ID**: build-error-capture
- **Depends On**: none
- **Validates**: tests/unit/test_session_watchdog.py, tests/unit/test_sdk_client_error_capture.py (create)
- **Assigned To**: error-capture-builder
- **Agent Type**: builder
- **Parallel**: true
- In `agent/sdk_client.py` line 1264: change `complete_transcript(session_id, status="failed")` to `complete_transcript(session_id, status="failed", summary=f"{type(e).__name__}: {e}")`
- In `monitoring/session_watchdog.py` line 178-180: add `session.summary = f"Watchdog: {type(e).__name__}: {e}"` before `session.save()`
- In `scripts/reflections.py` around line 257: add guard to skip failed sessions with empty summary, logging `logger.warning(f"Skipping failed session {session_id} with empty summary")`
- Write unit tests for the new behavior

### 2. Validate error capture
- **Task ID**: validate-error-capture
- **Depends On**: build-error-capture
- **Assigned To**: error-capture-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify error summary appears in AgentSession after sdk_client failure
- Verify watchdog failure path includes summary
- Verify reflections skips empty-summary sessions
- Run full test suite

### 3. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-error-capture
- **Assigned To**: error-capture-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update session lifecycle docs
- Add inline comments at fix sites

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: error-capture-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all tests
- Verify all success criteria met

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Error summary in sdk_client | `grep -n "summary=" agent/sdk_client.py \| grep "failed"` | output contains summary= |
| Error summary in watchdog | `grep -n "summary" monitoring/session_watchdog.py \| grep -i "watchdog"` | output contains summary |

---

## Open Questions

1. Should we cap the error summary length explicitly (e.g., 500 chars) before passing to `complete_transcript`, or rely on the existing field max_length? The field allows 50k chars but the transcript file truncates to 200. Recommend capping at 500 chars in the callers for consistency.
