---
status: Ready
type: bug
appetite: Small
owner: Valor
created: 2026-03-13
tracking: https://github.com/tomcounsell/ai/issues/401
last_comment_id:
---

# Fix Observer Reason Leak to Telegram

## Problem

When the observer decides to deliver output to Telegram, the user sometimes receives the observer's internal reasoning/justification text instead of the agent's actual response. The observer's `reason` parameter (meant for internal logging) leaks into the message sent to the user.

**Current behavior:**
The user sees internal system logic like "Auto-continue limit exceeded (4 > 3) and session is not classified as SDLC" instead of a useful response from the agent.

**Desired outcome:**
The observer's `reason` is logged internally only. The user always receives the agent's last meaningful response. If the agent's output is empty or purely internal (tool_use with no text), a meaningful fallback message is sent instead.

## Prior Art

- **Issue #374**: Observer returns early on continuation sessions due to session cross-wire -- Fixed a different observer routing bug, not directly related to reason leaking.
- **PR #321**: Observer Agent: replace auto-continue/summarizer with stage-aware SDLC steerer -- Introduced the Observer architecture including the `deliver_to_telegram` tool with the `reason` parameter.
- **PR #340**: Add operational logging to routing, observer, enrichment -- Added the logging that now reveals the leak.

## Data Flow

1. **Entry point**: Worker agent completes, `send_to_chat(msg)` is called in `agent/job_queue.py` with the worker's text output as `msg`
2. **Observer creation**: `Observer(worker_output=msg, ...)` receives the worker output at line 1534
3. **Observer decision**: Observer's Claude API call decides to use `deliver_to_telegram` tool, providing a `reason` string
4. **Decision returned**: `observer.run()` returns `{"action": "deliver", "reason": "..."}`
5. **Delivery**: At line 1640, `send_cb(job.chat_id, msg, ...)` sends `msg` (worker output) to Telegram
6. **Logging**: At line 1642-1644, the observer's `reason` is logged

The code at line 1640 correctly sends `msg` (worker output), not the reason. However, there are two failure modes:

**Failure mode A**: When the auto-continue cap is hit (line 1572-1580), the observer initially decides to steer, but the hard guard overrides and delivers `msg`. At this point, `msg` is the worker's output from the *current* iteration -- which may be meta-commentary like "Let me check the active PRs" rather than a useful deliverable. The observer's reason ends up in the log line at 1644, but the *logged* reason appears identical to what was sent because the log format `Observer delivered to Telegram: {reason}` is misleading.

**Failure mode B**: The worker's actual output (`msg`) is garbage (meta-commentary about what it's about to do), and the observer has no mechanism to substitute a better message. The observer can only decide *whether* to deliver, not *what* to deliver. When forced to deliver (e.g., by the auto-continue cap), whatever `msg` contains goes to the user -- even if it's useless.

**Failure mode C**: Edge case where `msg` is empty or contains only tool_use blocks with no user-visible text. The empty output guard at line 1508-1515 catches some of these, but not all (e.g., whitespace-only or tool-use-only output).

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

- **Message quality filter**: Logic to detect when the worker's output is meta-commentary (not useful to the user) and substitute a meaningful fallback
- **Reason isolation**: Ensure the observer's `reason` string never reaches any send path, only logging
- **Fallback message**: When the worker output is garbage or empty, generate a context-appropriate fallback message for the user

### Flow

Worker stops -> Observer decides "deliver" -> Message quality check -> If useful: send worker output -> If garbage: send fallback message -> Log reason internally

### Technical Approach

1. **Add a `message_for_user` field to the `deliver_to_telegram` tool**: The observer always curates user-facing text through this field. When delivering, `message_for_user` is what gets sent — not the raw worker output. The field is optional in the schema because the observer may choose to react with an emoji instead of sending text (e.g., for simple completed tasks).

2. **Delivery path uses `message_for_user` as primary**: In `agent/job_queue.py`, when the observer returns `deliver` with `message_for_user`, send that instead of raw `msg`. If `message_for_user` is absent and `msg` is garbage/empty, fall back to stage pipeline (let observer steer to next SDLC stage) or prompt the worker to continue — let the observer discern based on context.

3. **Audit the auto-continue cap path** (line 1572-1580): When the cap forces delivery, the `msg` may be low quality. Apply the same logic: prefer `message_for_user` if available, otherwise let the observer decide whether to steer or prompt the worker.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The observer fallback at line 1543-1551 already handles Observer errors. Verify it still sends worker output (not reason) on error.

### Empty/Invalid Input Handling
- [ ] Test that empty `msg` is caught and replaced with a fallback
- [ ] Test that whitespace-only `msg` is caught
- [ ] Test that tool-use-only output (no text blocks) is caught

### Error State Rendering
- [ ] Test that the fallback message is human-readable and contextual
- [ ] Test that the observer's `reason` string never appears in the message sent to Telegram

## Rabbit Holes

- **Trying to make the observer rewrite all worker output**: The observer should only intervene when the output is clearly garbage. Do not add an LLM-powered "rewrite" step for every delivery -- that adds latency and cost.
- **Parsing worker output for semantic quality**: Keep the heuristic simple (regex patterns for meta-commentary). Do not build an LLM classifier for message quality.
- **Changing the Observer's tool schema significantly**: The `deliver_to_telegram` tool just needs an optional `message_for_user` field. Do not redesign the tool interface.

## Risks

### Risk 1: False positives on meta-commentary detection
**Impact:** Legitimate worker responses that happen to start with "Let me check" get replaced with generic fallbacks.
**Mitigation:** Only apply the fallback when the observer explicitly signals via `message_for_user`. The heuristic is a secondary safety net, not the primary mechanism.

### Risk 2: Observer crafts poor fallback messages
**Impact:** User gets a slightly better but still unhelpful message.
**Mitigation:** The observer prompt can include guidance on what makes a good user-facing message. Worst case, a generic "work completed" message is still better than internal reasoning.

## Race Conditions

No race conditions identified -- the delivery path is single-threaded per job execution. The observer runs synchronously within `send_to_chat`.

## No-Gos (Out of Scope)

- Rewriting the Observer architecture
- Adding an LLM-powered message quality classifier
- Changing how the auto-continue counter works
- Fixing the worker agent to produce better output (that's a separate concern)

## Update System

No update system changes required -- this is a bridge-internal change. Standard `git pull && restart` propagates the fix.

## Agent Integration

No agent integration required -- this is a bridge-internal change affecting `agent/job_queue.py` and `bridge/observer.py`. No MCP server changes needed.

## Documentation

- [ ] Update `docs/features/observer-agent.md` to document the `message_for_user` field and message quality filter
- [ ] Add inline code comments explaining the message quality heuristic

## Success Criteria

- [ ] Observer's `reason` string never appears in Telegram messages (verified by test)
- [ ] When worker output is empty/garbage, observer either steers to next stage or prompts worker to continue
- [ ] The `deliver_to_telegram` tool accepts an optional `message_for_user` field
- [ ] Auto-continue cap path applies the same message quality filter
- [ ] Existing observer tests still pass
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (observer-fix)**
  - Name: observer-builder
  - Role: Implement the message quality filter and observer tool update
  - Agent Type: builder
  - Resume: true

- **Validator (observer-fix)**
  - Name: observer-validator
  - Role: Verify the fix prevents reason leaking
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Update deliver_to_telegram tool schema
- **Task ID**: build-tool-schema
- **Depends On**: none
- **Assigned To**: observer-builder
- **Agent Type**: builder
- **Parallel**: true
- Add optional `message_for_user` field to `deliver_to_telegram` tool input schema in `bridge/observer.py`
- Update the `_dispatch_tool` method to include `message_for_user` in the returned decision dict
- Update the Observer system prompt to instruct when to use `message_for_user`

### 2. Add message quality filter to delivery path
- **Task ID**: build-quality-filter
- **Depends On**: build-tool-schema
- **Assigned To**: observer-builder
- **Agent Type**: builder
- **Parallel**: false
- In `agent/job_queue.py`, before `send_cb` on the delivery path (line 1640), check if `decision` has `message_for_user` and prefer it over raw `msg`
- Add a `_is_meta_commentary(text)` helper for detecting useless worker output
- Apply the same filter on the auto-continue cap path (line 1578)
- Add an empty/whitespace guard alongside the existing empty output guard

### 3. Write tests
- **Task ID**: build-tests
- **Depends On**: build-quality-filter
- **Assigned To**: observer-builder
- **Agent Type**: builder
- **Parallel**: false
- Test: observer delivers with reason -- verify reason is not in sent message
- Test: observer provides message_for_user -- verify it's used instead of garbage worker output
- Test: auto-continue cap path -- verify message quality filter applies
- Test: empty/whitespace worker output -- verify fallback message sent

### 4. Validate fix
- **Task ID**: validate-fix
- **Depends On**: build-tests
- **Assigned To**: observer-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify observer's reason never appears in any send_cb call
- Verify all tests pass
- Run lint and format checks

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-fix
- **Assigned To**: observer-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/observer-agent.md` with message_for_user documentation
- Add inline comments on message quality heuristic

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: observer-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify all success criteria met
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Reason not in send path | `grep -n 'reason' agent/job_queue.py \| grep -i 'send_cb'` | exit code 1 |

---

## Open Questions (Resolved)

1. **Should the observer always provide `message_for_user`?** Yes — the observer always curates what the user sees via `message_for_user`. It remains optional in the schema because the observer doesn't always need to send a message (e.g., for simple completed tasks, a done emoji reaction suffices). But when delivering text, the observer always crafts the user-facing message. Note: in issue #395, the PM persona will take over this curation role.

2. **What should the fallback for empty worker output?** Fall back to the stage pipeline (let the observer steer to the next SDLC stage). If the context suggests the worker should have responded to the user, the observer should prompt the worker to continue or produce a real message. Let the observer discern based on context — no rigid rule.
