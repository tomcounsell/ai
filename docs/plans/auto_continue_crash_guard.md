---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-02-17
tracking: https://github.com/tomcounsell/ai/issues/129
---

# Auto-Continue Crash Guard

## Problem

When the SDK agent crashes with a fatal error, the auto-continue system treats the canned error response as a status update and immediately retries — causing cascading failures that persist for 10+ hours.

**Current behavior:**
1. SDK crashes (e.g., JSON buffer overflow after 7.5 min run)
2. Catch block in `agent/sdk_client.py:667-672` returns canned error message
3. Output classifier correctly tags it as `error` (confidence=0.85)
4. Auto-continue ignores the classification — it only checks `OutputType.STATUS_UPDATE`, and the canned message has no question mark, so it doesn't match QUESTION either
5. Continuation job spawns, hits `UniqueKeyField` constraint on AgentSession (session still "active" in Redis)
6. Watchdog retries the stale session every 5 minutes indefinitely

**Desired outcome:**
- Error-classified responses are never auto-continued
- SDK crash handler cleans up the AgentSession so no stale "active" session remains
- Watchdog handles `Unique constraint violated` gracefully instead of logging the same error forever

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 0

Three surgical fixes in three files. No architectural changes.

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **Error bypass in auto-continue**: Check classification before deciding to auto-continue
- **Session cleanup on SDK crash**: Mark AgentSession as "failed" in the catch block
- **Watchdog resilience**: Handle unique constraint errors gracefully

### Flow

**SDK crash** → catch block marks session "failed" → error message sent to classifier → classified as ERROR → auto-continue skipped → error message delivered to chat → reaction set to ERROR emoji

### Technical Approach

**Fix 1 — Auto-continue respects error classification** (`agent/job_queue.py`, `send_to_chat` closure, ~line 1014-1017):

The current guard only checks for `STATUS_UPDATE`. Add an early return before that check: if classification is `ERROR`, skip auto-continue entirely and fall through to send the message to chat. This is a 3-line change.

```python
# Before the STATUS_UPDATE auto-continue block:
if classification.output_type == OutputType.ERROR:
    logger.info(
        f"[{job.project_key}] Error classified — skipping auto-continue"
    )
    # Fall through to send error to chat
```

**Fix 2 — SDK crash handler cleans up session** (`agent/sdk_client.py`, catch block at line 667-672):

Pass the `session_id` into the error path and mark the AgentSession as "failed". This prevents the watchdog from finding a stale "active" session and prevents the continuation job from hitting the unique constraint.

```python
except Exception as e:
    elapsed = time.time() - start_time
    logger.error(f"[{request_id}] SDK error after {elapsed:.1f}s: {e}")
    # Clean up session so watchdog doesn't loop on it
    try:
        from models.sessions import AgentSession
        sessions = AgentSession.query.filter(session_id=session_id)
        for s in sessions:
            s.status = "failed"
            s.save()
    except Exception:
        pass  # Best-effort cleanup
    return (
        "Sorry, I ran into an issue and couldn't recover. "
        "The error has been logged for investigation."
    )
```

**Fix 3 — Watchdog handles unique constraint gracefully** (`monitoring/session_watchdog.py`, `check_all_sessions` at line 92-98):

The existing `except Exception` block already catches the error, but the session stays "active" so it's re-caught every cycle. Add specific handling: if the error is a unique constraint violation, mark the session as "failed" and move on.

```python
except Exception as e:
    if "Unique constraint violated" in str(e):
        # Stale session from a crash — mark as failed to stop the loop
        try:
            session.status = "failed"
            session.save()
            logger.warning(
                "[watchdog] Marked stale session %s as failed (unique constraint)",
                session.session_id,
            )
        except Exception:
            pass
    else:
        logger.error(...)
```

## Rabbit Holes

- Don't redesign the output classification system — it already correctly identifies errors, the problem is that the auto-continue logic ignores the classification
- Don't add retry logic or exponential backoff to the SDK client — the crash is a hard failure, not a transient one
- Don't try to increase the 1MB JSON buffer limit — that's an SDK-level concern, not ours

## Risks

### Risk 1: False error classification suppresses legitimate auto-continues
**Impact:** Agent pauses unnecessarily on a status update misclassified as error
**Mitigation:** The classifier already has 0.80 confidence threshold; errors below that default to QUESTION (which also pauses). This is the conservative/safe direction.

### Risk 2: Session cleanup in catch block races with job_queue session creation
**Impact:** Could mark a session as "failed" before the job_queue has finished creating it
**Mitigation:** The catch block runs after `agent.query()` returns, which is after the job_queue's `AgentSession.async_create()`. The session already exists by the time we'd clean it up.

## No-Gos (Out of Scope)

- Not addressing the 1MB JSON buffer limit itself (SDK-level concern)
- Not adding a session TTL/expiry system (separate feature)
- Not changing the output classification model or thresholds
- Not adding alerting for SDK crashes (watchdog already creates GitHub issues for critical sessions)

## Update System

No update system changes required — this is purely internal bridge/agent logic. No new dependencies or config files.

## Agent Integration

No agent integration required — these are bridge-internal fixes to the job queue, SDK client error handler, and session watchdog. No MCP server or tool changes needed.

## Documentation

- [ ] Update `docs/features/coaching-loop.md` to document error-bypass behavior
- [ ] Add entry to `docs/features/README.md` if a new feature doc is created

### Inline Documentation
- [ ] Add comments explaining the error-bypass guard in `job_queue.py`

## Success Criteria

- [ ] Error-classified SDK responses do NOT trigger auto-continue
- [ ] After an SDK crash, the AgentSession is marked "failed" (not left "active")
- [ ] Watchdog handles unique constraint errors without infinite retry loop
- [ ] Existing auto-continue behavior unchanged for STATUS_UPDATE outputs
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (crash-guard)**
  - Name: crash-guard-builder
  - Role: Implement all three fixes
  - Agent Type: builder
  - Resume: true

- **Validator (crash-guard)**
  - Name: crash-guard-validator
  - Role: Verify fixes are correct and don't break existing behavior
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add error bypass to auto-continue logic
- **Task ID**: build-error-bypass
- **Depends On**: none
- **Assigned To**: crash-guard-builder
- **Agent Type**: builder
- **Parallel**: true
- In `agent/job_queue.py`, in the `send_to_chat` closure (~line 1014), add a guard before the `STATUS_UPDATE` check: if `classification.output_type == OutputType.ERROR`, log it and fall through to send to chat
- Import `OutputType` is already available in scope via the existing import

### 2. Add session cleanup to SDK crash handler
- **Task ID**: build-session-cleanup
- **Depends On**: none
- **Assigned To**: crash-guard-builder
- **Agent Type**: builder
- **Parallel**: true
- In `agent/sdk_client.py`, in the catch block at line 667-672, add best-effort AgentSession cleanup: query by session_id and mark as "failed"
- Must handle the case where no session exists yet (query returns empty)

### 3. Add unique constraint handling to watchdog
- **Task ID**: build-watchdog-resilience
- **Depends On**: none
- **Assigned To**: crash-guard-builder
- **Agent Type**: builder
- **Parallel**: true
- In `monitoring/session_watchdog.py`, in `check_all_sessions` at line 92-98, add specific handling for "Unique constraint violated" errors
- Mark the offending session as "failed" to break the infinite retry loop

### 4. Validate all fixes
- **Task ID**: validate-crash-guard
- **Depends On**: build-error-bypass, build-session-cleanup, build-watchdog-resilience
- **Assigned To**: crash-guard-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify the error bypass is correctly placed before the STATUS_UPDATE check
- Verify session cleanup uses best-effort pattern (try/except)
- Verify watchdog handles both the specific error and general errors
- Run `ruff check agent/job_queue.py agent/sdk_client.py monitoring/session_watchdog.py`
- Run `black --check agent/job_queue.py agent/sdk_client.py monitoring/session_watchdog.py`

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-crash-guard
- **Assigned To**: crash-guard-builder
- **Agent Type**: builder
- **Parallel**: false
- Update coaching loop docs to mention error bypass
- Add inline comments on the new guards

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: crash-guard-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify all success criteria met
- Generate final report

## Validation Commands

- `ruff check agent/job_queue.py agent/sdk_client.py monitoring/session_watchdog.py` - Lint check
- `black --check agent/job_queue.py agent/sdk_client.py monitoring/session_watchdog.py` - Format check
- `python -c "from agent.job_queue import _execute_job; print('job_queue imports OK')"` - Import check
- `python -c "from monitoring.session_watchdog import check_all_sessions; print('watchdog imports OK')"` - Import check
- `pytest tests/ -x` - Run tests
