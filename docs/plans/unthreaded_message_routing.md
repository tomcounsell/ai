---
status: Ready
type: feature
appetite: Small
owner: Valor Engels
created: 2026-03-11
tracking: https://github.com/tomcounsell/ai/issues/318
---

# Route Unthreaded Messages into Active Sessions

## Problem

When a human sends a follow-up message during an active SDLC pipeline run without using Telegram's reply-to threading, the message creates a brand new session instead of being incorporated into the running work.

**Current behavior:**
`session_router.py` uses `find_matching_session()` to semantically match unthreaded messages against sessions with `expectations`. When a match is found, the matched `session_id` is used directly — the bridge then treats it as a continuation and creates a new job for that session. But for **active** sessions (currently being worked by a worker agent), this is wrong: it creates a competing job on the same session instead of queuing the message for the Observer to pick up at the next checkpoint.

**Desired outcome:**
When an unthreaded message matches an **active** session (status = "running") with >= 0.80 confidence, the message is inserted into `queued_steering_messages` on that session. The Observer incorporates it on its next stop hook. The user gets an acknowledgment ("Noted — I'll incorporate this on my next checkpoint.") so they know the message wasn't lost. Dormant session matching continues unchanged.

## Prior Art

- **PR #275**: Semantic session routing with structured summarizer — landed the `session_router.py` module and `find_matching_session()` logic. Handles dormant session matching. This plan extends it.
- **Issue #309 / Observer Agent**: Introduced `queued_steering_messages` ListField on AgentSession, `push_steering_message()`, and `pop_steering_messages()`. The Observer already reads and clears queued messages. Reply-to messages are already queued via the bridge intake path.

## Data Flow

1. **Entry point**: Unthreaded Telegram message arrives at `handle_new_message()` in `telegram_bridge.py` (line ~686)
2. **Session router**: `find_matching_session()` returns `(session_id, confidence)` — currently does not distinguish active vs. dormant
3. **Gap**: Bridge uses matched `session_id` as if it's a dormant session to resume, creating a new job
4. **Fix**: After match, check session status. If `running`/`active`, call `push_steering_message()` and send acknowledgment instead of creating a job
5. **Observer**: On next worker stop, Observer calls `read_session` → sees `queued_steering_messages` → incorporates into coaching decision

## Architectural Impact

- **New dependencies**: None — all building blocks exist (`push_steering_message`, `find_matching_session`, `AgentSession.status`)
- **Interface changes**: `find_matching_session()` return value unchanged. New logic is in the bridge's routing branch after the match.
- **Coupling**: Minimal increase — bridge already imports `session_router` and `AgentSession`
- **Data ownership**: No change — `queued_steering_messages` is already owned by AgentSession
- **Reversibility**: Trivially reversible — remove one routing branch in the bridge handler

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (scope is well-defined in issue #318)
- Review rounds: 1 (code review)

## Prerequisites

No prerequisites — all dependencies (#309 Observer, #274 semantic routing) are already merged.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| SEMANTIC_ROUTING enabled | `python -c "import os; assert os.environ.get('SEMANTIC_ROUTING') in ('true', '1', 'yes')"` | Feature flag must be on |
| Observer merged | `python -c "from bridge.observer import Observer; print('OK')"` | Observer must be available |

## Solution

### Key Elements

- **Active session detection**: After `find_matching_session()` returns a match, look up the session and check its `status` field
- **Message queuing**: For active/running sessions, call `push_steering_message()` instead of creating a new job
- **User acknowledgment**: Send a brief Telegram reply so the user knows their message was received and queued
- **Dormant passthrough**: Dormant session matches continue to work as before (resume the session)

### Flow

**Unthreaded message arrives** → `find_matching_session()` → Match found (>= 0.80) → Check session status →
- If `running`/`active` → `push_steering_message()` → Send acknowledgment → Done (no new job)
- If `dormant` → Resume session (existing behavior)
- If no match → Create new session (existing behavior)

### Technical Approach

1. In `telegram_bridge.py` around line 702, after `matched_id` is obtained from `find_matching_session()`:
   - Load the matched `AgentSession` from Redis
   - Check `session.status`
   - If status is `running` or `active`: call `session.push_steering_message(clean_text)`, send acknowledgment reply, set reaction, and `return` early (skip job creation)
   - Otherwise: fall through to existing behavior

2. The acknowledgment message should be brief and sent as a reply to the user's message: "Noted — I'll incorporate this on my next checkpoint."

3. No changes needed to `session_router.py` or `observer.py` — the existing `push_steering_message` / `pop_steering_messages` / Observer `read_session` pipeline handles everything downstream.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The `push_steering_message()` method already has try/except with `logger.warning` — test that a save failure logs but doesn't crash
- [ ] The semantic routing block in the bridge already has a broad `except Exception` — test that active-session routing failures fall through to new session creation

### Empty/Invalid Input Handling
- [ ] Empty message text matched to active session: should still queue (Observer handles empty gracefully)
- [ ] Session matched but deleted between match and status check: fall through to new session
- [ ] `queued_steering_messages` is None (not initialized): `push_steering_message()` already handles this

### Error State Rendering
- [ ] Acknowledgment message fails to send: non-fatal, message is still queued
- [ ] User sees acknowledgment, confirming their message wasn't lost

## Rabbit Holes

- **Multi-session disambiguation**: If multiple active sessions match, just pick the highest-confidence one. Complex disambiguation UX is out of scope.
- **Medium-confidence routing (0.50-0.80)**: The issue mentions this range. Don't build it — stick with the existing >= 0.80 threshold.
- **Changing `find_matching_session()` return type**: Don't change the function signature to return session status. Just load the session after the match. Keep the router simple.

## Risks

### Risk 1: Message queued but Observer doesn't pick it up
**Impact:** User's message is silently lost despite acknowledgment
**Mitigation:** Observer already reads `queued_steering_messages` on every `read_session` call. The field persists in Redis. If the Observer skips it, it's a pre-existing bug in #309, not introduced here.

### Risk 2: Race between session completing and message being queued
**Impact:** Message queued to a session that transitions to `completed` before Observer reads it
**Mitigation:** Accept this edge case — the message was acknowledged to the user, and worst case the Observer delivers it on final delivery. For v1 this is acceptable.

## Race Conditions

### Race 1: Session status changes between check and push
**Location:** `telegram_bridge.py` routing block (~line 702)
**Trigger:** Session transitions from `running` to `completed` between the `status` check and `push_steering_message()` call
**Data prerequisite:** Session must exist in Redis with status `running`/`active`
**State prerequisite:** Session must still be active when message is pushed
**Mitigation:** `push_steering_message()` is a simple Redis list append — it succeeds regardless of session status. If the session completes before the Observer reads the queued message, the message is effectively lost. This is acceptable for v1: the window is tiny (milliseconds) and the user got an acknowledgment. A future improvement could check for orphaned queued messages during session completion.

## No-Gos (Out of Scope)

- Medium-confidence routing (0.50-0.80 disambiguation UI)
- Multi-session conflict resolution
- Queuing media messages (photos, files) — text only for v1
- Changing `find_matching_session()` to return session objects
- Modifying Observer logic — it already handles queued messages correctly

## Update System

No update system changes required — this is a bridge-internal change with no new dependencies, config files, or migration steps.

## Agent Integration

No agent integration required — this is a bridge-internal routing change. The bridge already imports `session_router` and `AgentSession`. No new MCP servers or tool wrappers needed. The agent (worker) is unaware of this change; it just sees steering messages appear via the Observer.

## Documentation

- [ ] Create `docs/features/unthreaded-message-routing.md` describing the routing decision matrix and behavior
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Update docstring in `bridge/session_router.py` to reference active session queuing
- [ ] Update `docs/features/semantic-session-routing.md` if it exists (or create as the feature doc above)

## Success Criteria

- [ ] Unthreaded message matching active session → appears in `queued_steering_messages`
- [ ] User receives acknowledgment reply ("Noted — I'll incorporate this on my next checkpoint.")
- [ ] Unthreaded message matching dormant session → resumes session (unchanged behavior)
- [ ] Low confidence match → creates new session (unchanged behavior)
- [ ] No match → creates new session (unchanged behavior)
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (routing)**
  - Name: routing-builder
  - Role: Implement active session detection and message queuing in bridge handler
  - Agent Type: builder
  - Resume: true

- **Validator (routing)**
  - Name: routing-validator
  - Role: Verify routing behavior for all session states
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Implement active session routing in bridge
- **Task ID**: build-routing
- **Depends On**: none
- **Assigned To**: routing-builder
- **Agent Type**: builder
- **Parallel**: true
- In `telegram_bridge.py`, after `find_matching_session()` returns a match (~line 702):
  - Load `AgentSession` by `session_id`
  - Check `session.status in ("running", "active")`
  - If active: call `session.push_steering_message(clean_text)`, send acknowledgment reply, set reaction to REACTION_RECEIVED, and return early
  - If dormant: fall through to existing behavior (use matched session_id)
- Add logging for the active-session routing path

### 2. Write tests
- **Task ID**: build-tests
- **Depends On**: build-routing
- **Assigned To**: routing-builder
- **Agent Type**: builder
- **Parallel**: false
- Unit test: unthreaded message + active session match → `push_steering_message` called
- Unit test: unthreaded message + dormant session match → session_id returned for resumption
- Unit test: low confidence → new session created
- Unit test: session not found after match → falls through to new session

### 3. Validate implementation
- **Task ID**: validate-routing
- **Depends On**: build-tests
- **Assigned To**: routing-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify routing logic handles all decision matrix cases
- Verify acknowledgment message text
- Verify no changes to session_router.py or observer.py
- Run validation commands

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-routing
- **Assigned To**: routing-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/unthreaded-message-routing.md`
- Add entry to `docs/features/README.md` index

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: routing-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all verification commands
- Verify all success criteria met

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| push_steering_message exists | `grep -r "push_steering_message" bridge/telegram_bridge.py` | output contains push_steering_message |
| Acknowledgment text | `grep -r "next checkpoint" bridge/telegram_bridge.py` | output contains next checkpoint |
