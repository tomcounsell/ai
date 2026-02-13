---
status: Ready
type: bug
appetite: Medium
owner: Valor
created: 2026-02-13
tracking: https://github.com/tomcounsell/ai/issues/92
---

# Fix Reply Delivery: Reactions Without Text Replies

## Problem

Tom sends messages to the Valor Telegram bridge and sees the emoji reaction sequence (👀 → processing → 👍) indicating work completed, but **no text reply is ever delivered**. The 👍 reaction misleads the user into thinking everything is fine when in fact their response was silently swallowed.

**Current behavior:**
1. Message received → 👀 reaction set
2. Intent classified → processing emoji (🤔, 👨‍💻, etc.) set
3. Agent runs to completion, returns a response
4. `send_to_chat()` classifies the response via Haiku/heuristics
5. If classified as `STATUS_UPDATE`: response is suppressed, "continue" pushed to Redis steering queue
6. Agent is **already done** — steering message is never consumed
7. Unconsumed steering messages are drained and logged as warnings
8. 👍 reaction set based solely on `task.error == None`, regardless of whether text was sent
9. User sees 👍, no text → confused

**Root causes identified:**
1. **Auto-continue uses wrong mechanism** — it classifies the response AFTER the SDK agent has completed and pushes "continue" to the steering Redis queue. But steering only works for live injection during agent execution (PostToolUse hook). Since the agent is done, the message is never consumed and the response is silently dropped. Fix: re-enqueue a continuation job with the same session_id (same pattern as user reply-to messages).
2. **👍 reaction is unconditional** — `job_queue.py:821-822` checks only `task.error`, not `messenger.has_communicated()`
3. **Error reaction uses invalid emoji** — `job_queue.py:822` uses `\u274c` (❌) but ❌ is in the `INVALID_REACTIONS` list. `REACTION_ERROR` is `😱` but isn't used here.
4. **Three paths to silent text loss** — empty SDK response, `filter_tool_logs()` stripping everything, and auto-continue suppression

**Evidence:** 18 cases found in Feb 11-12 logs with "unconsumed steering message(s) dropped" warnings, all preceded by STATUS_UPDATE classifications.

**Desired outcome:**
- 👍 reaction = intentional "got it" / "yes" / "done" — simple acknowledgment where no text reply is needed (agent explicitly chose this)
- A different reaction (e.g., 🏆) = "work complete, reply attached" — MUST always accompany a text reply
- No path where a response is silently dropped
- Steering race condition eliminated

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1 (to confirm reaction emoji choice)
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **Reaction semantics redesign**: Split the single "success" reaction into two distinct signals
- **Auto-continue fix**: Replace broken steering-queue approach with job re-enqueue (same mechanism as user reply-to)
- **Delivery guarantee**: Never set a "complete" reaction without confirming text was delivered (when text is expected)
- **Steering race condition test**: Cover the old race with a test to prevent regression

### Flow

**Message arrives** → 👀 reaction → classify intent → processing emoji → agent runs → response returned →

**Branch A (response classified as COMPLETION/QUESTION/BLOCKER/ERROR):**
→ Send text to chat → Confirm delivery → Set 🏆 reaction

**Branch B (response classified as STATUS_UPDATE, auto-continue count < 3):**
→ Don't send text → Re-enqueue job with same session_id + "continue" message → Agent resumes conversation → Loop

**Branch C (response is empty / simple ack):**
→ Set 👍 reaction (no text needed)

**Branch D (error):**
→ Send error message → Set 😱 reaction (use REACTION_ERROR constant, not ❌)

### Technical Approach

#### 1. Redesign reaction constants (`response.py`)

Add a new reaction for "work complete with reply":

```python
REACTION_RECEIVED = "👀"     # Message acknowledged
REACTION_PROCESSING = "🤔"   # Default thinking
REACTION_SUCCESS = "👍"      # Simple ack, no text needed
REACTION_COMPLETE = "🏆"     # Work done, reply attached
REACTION_ERROR = "😱"        # Error occurred
```

`🏆` is in the validated reactions list (line 106). Alternative candidates: `🎉`, `💯`, `🫡`.

#### 2. Fix reaction logic in `job_queue.py` (lines 821-826)

Replace the unconditional 👍 with a check:

```python
if react_cb:
    if task.error:
        emoji = REACTION_ERROR  # 😱, not ❌
    elif messenger.has_communicated():
        emoji = REACTION_COMPLETE  # 🏆 — text was sent
    else:
        emoji = REACTION_SUCCESS  # 👍 — simple ack, no text
    await react_cb(job.chat_id, job.message_id, emoji)
```

#### 3. Fix auto-continue via job re-enqueue (`job_queue.py:649-712`)

The current auto-continue pushes "continue" to the steering Redis queue, but the agent is already done so the message is never consumed. Fix by using the same mechanism as user reply-to messages: **re-enqueue a new job with the same `session_id`**.

When `send_to_chat()` classifies a response as `STATUS_UPDATE` and `auto_continue_count < MAX_AUTO_CONTINUES`:

1. Don't send the response to chat (same as now)
2. Instead of `push_steering_message()`, call `enqueue_job()` with:
   - Same `session_id` → SDK creates session with `continue_conversation=True, resume=session_id`
   - `message_text="continue"` → agent resumes with conversation context intact
   - Same `project_key`, `chat_id`, `message_id` → reactions still target original message
   - `priority="high"` → processes next in the sequential worker queue
3. The current job completes (no reaction set yet — defer to the continuation job)
4. The worker picks up the "continue" job, agent resumes, and eventually produces a COMPLETION response
5. The final job sets the reaction based on delivery state

This works because:
- `enqueue_job` + `_worker_loop` already handles sequential job processing
- The SDK's `resume=session_id` reloads the full conversation context
- No race condition — the continuation job runs AFTER the current one fully completes
- `MAX_AUTO_CONTINUES` cap (3) prevents infinite loops
- Each continuation is ~2-3s SDK init overhead (acceptable)

Key change: the `auto_continue_count` must be passed through the job or tracked in Redis (not just a local variable) since it spans multiple job executions. Add a `auto_continue_count` field to `RedisJob` or track via the session.

#### 4. Ensure `filter_tool_logs` doesn't silently swallow responses (`response.py:345-350`)

When `filter_tool_logs()` returns empty but the original response was non-empty, log a warning and send a fallback message like "Done." rather than silently dropping.

#### 5. Fix error reaction emoji (`job_queue.py:822`)

Replace `"\u274c"` (❌, invalid) with `REACTION_ERROR` (😱, validated).

#### 6. Test for steering race condition

Write a test that:
1. Pushes a "continue" steering message to a session's Redis queue
2. Verifies it's consumed when an agent is running (PostToolUse hook fires)
3. Verifies it's properly drained and logged when NO agent is running
4. Verifies no response is silently dropped in the drain case

### Note on steering mechanism

The Redis steering queue + PostToolUse hook remains for its original purpose: human-initiated mid-session messages (user replies while agent is running). That mechanism works correctly for live injection.

The auto-continue use case is different — it happens AFTER the agent finishes — so it uses the job re-enqueue pattern instead, mirroring how user reply-to messages resume sessions via `continue_conversation=True` + `resume=session_id` in the SDK.

Reference: https://platform.claude.com/docs/en/build-with-claude/working-with-messages (multi-turn conversation pattern)

## Rabbit Holes

- **Persistent auto-continue counter across restarts** — use a simple Redis key or RedisJob field. Don't build a complex state machine.
- **Per-message reaction customization** — don't try to support custom reactions per intent for completion. Keep it simple: 👍 or 🏆.
- **Telegram Premium custom reactions** — stick to the 73 validated free-tier reactions.
- **Optimizing SDK init time for continuations** — ~2-3s per re-enqueue is acceptable. Don't try to keep SDK sessions alive across jobs.

## Risks

### Risk 1: SDK init overhead per auto-continue
**Impact:** Each "continue" re-enqueue costs ~2-3s of SDK initialization
**Mitigation:** Capped at 3 continuations max. Total overhead: ~6-9s across a full auto-continue chain. Acceptable vs silently dropping responses.

### Risk 2: 🏆 may not be intuitive
**Impact:** Users may not understand what 🏆 means
**Mitigation:** It's a positive signal ("won/accomplished"), and it always accompanies a text reply which is the real signal. Can be changed later.

### Risk 3: Auto-continue counter must persist across jobs
**Impact:** Local variable resets on each job. Without tracking, auto-continue could loop indefinitely.
**Mitigation:** Track `auto_continue_count` on the RedisJob or via a Redis key keyed by session_id. Clear on session completion.

## No-Gos (Out of Scope)

- Persistent job queue across bridge restarts (separate issue)
- Changing the steering mechanism for human-initiated messages (it works fine for that)
- Revamping the summarizer/classifier (it works, just needs to route correctly)

## Update System

No update system changes required — this is a bridge-internal behavioral change. No new dependencies or config files.

## Agent Integration

No agent integration required — this changes bridge-side response routing and reaction logic only. The agent itself is unaffected. MCP servers and tools are unchanged.

## Documentation

- [ ] Update `docs/features/session-isolation.md` to reflect removal of auto-continue from the delivery path
- [ ] Add entry to `docs/features/README.md` index table for reaction semantics
- [ ] Create `docs/features/reaction-semantics.md` documenting the reaction protocol: 👀 → processing → 👍/🏆/😱

## Success Criteria

- [ ] No code path where a non-empty agent response is silently dropped without sending text to chat
- [ ] 👍 reaction only set when no text reply is intended
- [ ] 🏆 (or chosen emoji) set when text reply was delivered
- [ ] 😱 set on errors (not ❌)
- [ ] Auto-continue uses job re-enqueue (not steering queue) — no more "unconsumed steering messages dropped" from auto-continue
- [ ] Test covers the steering race condition (message pushed after agent done → properly drained, response not lost)
- [ ] Documentation updated and indexed

## Team Orchestration

### Team Members

- **Builder (reply-delivery)**
  - Name: reply-builder
  - Role: Implement reaction redesign, remove broken auto-continue, fix error emoji, add delivery guarantee
  - Agent Type: builder
  - Resume: true

- **Validator (reply-delivery)**
  - Name: reply-validator
  - Role: Verify all code paths, run tests, confirm no silent drops
  - Agent Type: validator
  - Resume: true

- **Test Writer (steering-race)**
  - Name: steering-test-writer
  - Role: Write test covering the steering race condition
  - Agent Type: test-writer
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Create reaction-semantics.md, update README index
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Redesign reaction constants and fix auto-continue
- **Task ID**: build-reactions
- **Depends On**: none
- **Assigned To**: reply-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `REACTION_COMPLETE = "🏆"` to `bridge/response.py`
- Update `job_queue.py:821-826` to check `messenger.has_communicated()` and use correct emoji
- Replace `"\u274c"` with `REACTION_ERROR` constant
- In `send_to_chat()` (`job_queue.py:664-699`): replace `push_steering_message()` with `enqueue_job()` using same `session_id` + `message_text="continue"`
- Add `auto_continue_count` field to `RedisJob` (or track via Redis key per session_id) so the counter persists across re-enqueued jobs
- Defer reaction-setting to the final job in the auto-continue chain (don't set 👍 on intermediate jobs)
- Add fallback message in `response.py` when `filter_tool_logs()` empties a non-empty response

### 2. Write steering race condition test
- **Task ID**: build-steering-test
- **Depends On**: none
- **Assigned To**: steering-test-writer
- **Agent Type**: test-writer
- **Parallel**: true
- Test that pushing "continue" to steering queue after agent finishes results in proper drain (not silent response loss)
- Test that `messenger.has_communicated()` correctly tracks whether text was sent
- Test that reaction emoji matches delivery state (👍 when no text, 🏆 when text sent, 😱 on error)

### 3. Validate implementation
- **Task ID**: validate-reactions
- **Depends On**: build-reactions, build-steering-test
- **Assigned To**: reply-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify no `"\u274c"` or `❌` remains in reaction-setting code
- Verify `send_to_chat()` STATUS_UPDATE path calls `enqueue_job()` instead of `push_steering_message()`
- Verify `messenger.has_communicated()` is checked before setting reaction
- Run tests: `pytest tests/`
- Run linting: `ruff check . && black --check .`

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-reactions
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/reaction-semantics.md`
- Add entry to `docs/features/README.md` index table
- Update session-isolation.md re: auto-continue fix (job re-enqueue pattern)

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: reply-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met
- Generate final report

## Validation Commands

- `pytest tests/` - Run all tests
- `ruff check .` - Lint check
- `black --check .` - Format check
- `grep -r "\\\\u274c" bridge/ agent/` - Confirm no ❌ in reaction code
- `grep -r "unconsumed steering" logs/bridge.log | tail -5` - Check for regression after restart

## Open Questions

None — all resolved. Reaction emoji: 🏆 (trophy) confirmed for "complete with reply".
