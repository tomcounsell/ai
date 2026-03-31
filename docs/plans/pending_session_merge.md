---
status: Ready
type: bug
appetite: Small
owner: Valor
created: 2026-03-20
tracking: https://github.com/tomcounsell/ai/issues/619
last_comment_id:
---

# Message Merge: Attach Follow-up Messages to Pending Sessions

## Problem

When a user sends multiple messages in quick succession (e.g., a forwarded message + a comment), each creates a separate session. The bridge has steering infrastructure for injecting messages into **running/active** sessions, but no mechanism for attaching to **pending** sessions (enqueued but not yet started).

**Current behavior:**
Two messages sent ~300ms apart create two competing sessions. The steering fast path (line 1001) only checks `running`/`active`. The intake classifier (line 1070) checks `running`/`active`/`dormant`. Both skip `pending`. When a pending session is found (line 1039), it logs a warning but falls through.

**Desired outcome:**
Follow-up messages within 7 seconds of the first attach to the existing pending/running session via steering, producing a single combined session.

## Prior Art

No prior issues found related to pending session merge. The steering infrastructure was built by #320 (intake classifier) and the mid-session steering system. This is the first attempt to extend steering to cover the pending→running race window.

## Data Flow

1. **Entry point**: Telegram message arrives → `_handle_message()` in `telegram_bridge.py`
2. **Reply fast path** (line 992): If reply-to a Valor message, check for running/active sessions → steer or fall through
3. **Pending detection** (line 1036): Currently logs but doesn't act on pending sessions
4. **Intake classifier** (line 1064): Non-reply messages classified by Haiku → checks running/active/dormant
5. **Enqueue**: If no steering target found, new job created → `AgentSession` with status=`pending`
6. **Worker pop** (job_queue.py:360): `_pop_job()` finds pending job → deletes and recreates with status=`running`
7. **Execution** (job_queue.py:1497): `_execute_job()` starts agent work with `job.message_text`

**Gap**: Between steps 5 and 6, a follow-up message arriving has no path to attach to the pending session. Between steps 6 and 7, the session is `running` but the agent hasn't started consuming steering messages yet.

## Architectural Impact

- **New dependencies**: None
- **Interface changes**: None — uses existing `push_steering_message()` and `pop_all_steering_messages()` APIs
- **Coupling**: No change — same components already handle steering for running sessions
- **Data ownership**: No change
- **Reversibility**: Trivially revertible — remove the pending status check and the drain-on-start logic

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

- **Pending steering** (telegram_bridge.py): Extend the fast path and intake classifier to steer into pending sessions within a 7s recency window
- **Drain-on-start** (job_queue.py): When `_pop_job` transitions pending→running, drain any queued steering messages and prepend them to the job's message text
- **Acknowledgment**: Send "Adding to current task" ack when steering into a pending session (same as running)

### Flow

**Message 1 arrives** → enqueue as pending → **Message 2 arrives (< 7s)** → find pending session → push to steering queue → ack → **Worker pops job** → drain steering queue → prepend to message_text → **Agent processes combined message**

### Technical Approach

1. **Steering fast path** (telegram_bridge.py ~line 1034-1044): When pending sessions are found with `created_at` < 7s ago, push to steering queue and return (same pattern as running session steering at line 1015-1033)

2. **Intake classifier** (telegram_bridge.py ~line 1070): Add `"pending"` to the status check loop, with a 7s recency guard — if the pending session is older than 7s, skip it

3. **Drain-on-start** (job_queue.py ~line 400, after recreating as running): Call `pop_all_steering_messages(session_id)` and if any exist, prepend their text to `message_text` on the new job object

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The steering fast path has a broad `except Exception` at line 1052 — existing behavior, not modified by this change. Tests should verify pending steering degrades gracefully on Redis errors.
- [ ] `_pop_job` has no try/except around the drain call — add test that drain failure doesn't crash job start

### Empty/Invalid Input Handling
- [ ] If `pop_all_steering_messages` returns empty list, no change to message_text
- [ ] If steering message text is empty/whitespace, skip prepend

### Error State Rendering
- [ ] Ack message "Adding to current task" sent to user for pending steering (same as running)
- [ ] If pending session not found (race: already popped), fall through to normal enqueue

## Test Impact

- [ ] `tests/unit/test_intake_classifier.py` — UPDATE: add test cases for pending status in classifier routing
- [ ] `tests/integration/test_steering.py` — UPDATE: add test for steering into pending session
- [ ] `tests/integration/test_unthreaded_routing.py` — UPDATE: may need to account for pending in status checks

## Rabbit Holes

- **Telegram `grouped_id` detection**: The 7s window implicitly handles media groups without needing Telegram-specific grouped_id parsing
- **Accumulator/delay pattern**: Tempting to buffer messages and batch-send after a delay — adds latency and complexity. First-message-immediate is better.
- **Semantic classification of pending messages**: The intake classifier shouldn't need to call Haiku for pending sessions < 7s — temporal proximity is sufficient signal

## Risks

### Risk 1: Race between pending detection and worker pop
**Impact:** Message steered into a session that's already been popped and is now running — steering queue is keyed by session_id, so this actually works fine. The message lands in the steering queue and gets consumed by PostToolUse hook.
**Mitigation:** Session_id is stable across the delete-and-recreate in `_pop_job`. Steering queue key `steering:{session_id}` works regardless of session status.

### Risk 2: Drain-on-start misses messages arriving during execution setup
**Impact:** A steering message pushed between drain and agent start could be missed until the first PostToolUse hook fires.
**Mitigation:** Acceptable — PostToolUse hook already handles this for running sessions. The drain-on-start is for messages that arrived during the pending window before any hook exists.

## Race Conditions

### Race 1: Pending session popped between status check and steering push
**Location:** telegram_bridge.py ~line 1036-1044
**Trigger:** Worker pops the pending job at the exact moment the second message is checking for pending sessions
**Data prerequisite:** Session must be in pending status
**State prerequisite:** Worker must not have popped yet
**Mitigation:** Not harmful — if session transitions to running, the steering message still arrives via the same Redis queue key. If it transitions and completes before consumption, the steering message is orphaned (same as existing behavior for running sessions that complete during steering).

### Race 2: Multiple follow-up messages steering simultaneously
**Location:** telegram_bridge.py steering fast path
**Trigger:** Three messages arrive within 1s — first enqueues, second and third both find pending
**Data prerequisite:** Pending session exists
**State prerequisite:** Both second and third messages find the session in pending state
**Mitigation:** Both push to the steering queue — `pop_all_steering_messages` drains all of them. Order is preserved by Redis list RPUSH/LPOP.

## No-Gos (Out of Scope)

- Lowering confidence thresholds for intake classifier (separate tuning issue)
- Medium-confidence semantic routing (deferred to Phase 3 per `session_router.py:26`)
- Telegram `grouped_id` detection (covered implicitly by 7s window)
- Changes to the PostToolUse health check hook (already works for running sessions)

## Update System

No update system changes required — this is a bridge-internal change with no new dependencies or configuration.

## Agent Integration

No agent integration required — this is a bridge-internal change. The agent's tools and MCP servers are unchanged. The bridge modifies how messages are routed before the agent ever sees them.

## Documentation

- [ ] Update `docs/features/steering-queue.md` — add section on pending session steering
- [ ] Update `docs/features/intake-classifier.md` — document pending status handling
- [ ] Add inline code comments on the 7s threshold constant

## Success Criteria

- [ ] Two messages sent < 7s apart to the same chat produce a single session (not two)
- [ ] Two messages sent > 7s apart still produce two separate sessions
- [ ] Steering into pending session sends "Adding to current task" ack
- [ ] Drain-on-start prepends steering messages to job message_text
- [ ] No regression in existing steering for running/active sessions
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (pending-merge)**
  - Name: bridge-builder
  - Role: Implement pending session steering and drain-on-start
  - Agent Type: builder
  - Resume: true

- **Validator (pending-merge)**
  - Name: bridge-validator
  - Role: Verify steering behavior for pending sessions
  - Agent Type: validator
  - Resume: true

### Available Agent Types

Using core tier only — this is a small, focused change.

## Step by Step Tasks

### 1. Implement Pending Session Steering
- **Task ID**: build-pending-steering
- **Depends On**: none
- **Validates**: tests/unit/test_intake_classifier.py, tests/integration/test_steering.py
- **Assigned To**: bridge-builder
- **Agent Type**: builder
- **Parallel**: true
- Add 7s recency constant (e.g., `PENDING_MERGE_WINDOW_SECONDS = 7`) to `bridge/telegram_bridge.py`
- Modify steering fast path (line 1034-1044): when pending session found with `created_at` within 7s, call `push_steering_message()`, send ack, and return
- Add `"pending"` to intake classifier status loop (line 1070) with 7s recency guard
- Add drain-on-start logic in `_pop_job()` (job_queue.py ~line 400): call `pop_all_steering_messages()` and prepend to `message_text` if any
- Write unit tests for pending detection and 7s window logic
- Write integration test for two messages < 7s apart producing one session

### 2. Validation
- **Task ID**: validate-pending-steering
- **Depends On**: build-pending-steering
- **Assigned To**: bridge-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify existing steering tests still pass
- Verify new pending steering tests pass
- Verify 7s boundary correctly separates merge vs. new session

### 3. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-pending-steering
- **Assigned To**: bridge-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/steering-queue.md` with pending session steering
- Update `docs/features/intake-classifier.md` with pending status handling

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: bridge-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify all success criteria met

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Steering tests | `pytest tests/integration/test_steering.py -x -q` | exit code 0 |
| Intake tests | `pytest tests/unit/test_intake_classifier.py -x -q` | exit code 0 |
| Pending constant exists | `grep -n 'PENDING_MERGE_WINDOW' bridge/telegram_bridge.py` | exit code 0 |

---

## Open Questions

None — the issue description is well-specified with clear approach, thresholds, and code locations. Ready for implementation.
