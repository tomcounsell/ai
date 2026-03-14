---
status: Ready
type: bug
appetite: Small
owner: Valor
created: 2026-03-13
tracking: https://github.com/tomcounsell/ai/issues/401
last_comment_id:
---

# Fix Observer Reason Leak and False Promise Halts

## Problem

Two related failures in the observer delivery path cause the user to receive useless or misleading messages on Telegram:

1. **Reason leak**: The observer's internal `reason` string (meant for logging) ends up being sent to the user instead of the agent's actual response.
2. **False promise halt**: The agent outputs process narration like "Let me check how X is configured" and then the session ends. The user receives a promise of investigation that never happens — the agent announced work but halted before doing it.

Both are symptoms of the same root issue: the delivery path has no quality gate between the worker's raw output and the message sent to the user.

**Current behavior:**
- The user sees internal system logic like "Auto-continue limit exceeded (4 > 3)..."
- The user sees false promises like "Let me check the actual code to see what happened" when no checking occurred — the session ended after that message.

**Desired outcome:**
- The observer's `reason` is logged internally only, never sent to the user.
- Worker output consisting entirely of process narration ("Let me check...", "Let me look at...") without substantive findings is detected as non-deliverable. The observer either auto-continues the worker (so it actually does the work) or sends a meaningful fallback.
- The user always receives either substantive results or an honest status message.

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

The code at line 1640 correctly sends `msg` (worker output), not the reason. However, there are four failure modes:

**Failure mode A — Reason leak**: When the auto-continue cap is hit (line 1572-1580), the observer initially decides to steer, but the hard guard overrides and delivers `msg`. At this point, `msg` is the worker's output from the *current* iteration -- which may be meta-commentary like "Let me check the active PRs" rather than a useful deliverable. The observer's reason ends up in the log line at 1644, but the *logged* reason appears identical to what was sent because the log format `Observer delivered to Telegram: {reason}` is misleading.

**Failure mode B — Garbage delivery**: The worker's actual output (`msg`) is garbage (meta-commentary about what it's about to do), and the observer has no mechanism to substitute a better message. The observer can only decide *whether* to deliver, not *what* to deliver. When forced to deliver (e.g., by the auto-continue cap), whatever `msg` contains goes to the user -- even if it's useless.

**Failure mode C — Empty output**: Edge case where `msg` is empty or contains only tool_use blocks with no user-visible text. The empty output guard at line 1508-1515 catches some of these, but not all (e.g., whitespace-only or tool-use-only output).

**Failure mode D — False promise halt**: The worker's output is entirely process narration announcing future work ("Let me check how X is configured. Let me check the actual code to see what happened.") but the session halts after producing this output. The agent promised investigation but never performed it. This is the most misleading failure because it implies active work is happening when the session is already over. The observer sees this output and decides to deliver it as-is, not recognizing it as a non-substantive announcement.

The key behavioral signal for Mode D: the worker's output matches process narration patterns AND the output contains NO substantive findings, code references, error details, or conclusions. The summarizer already has `_PROCESS_NARRATION_PATTERNS` in `bridge/summarizer.py` (line 50-67) that detect these patterns for pre-summarization stripping — but they're not used for delivery gating.

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

- **False promise detection**: Reuse `_PROCESS_NARRATION_PATTERNS` from `bridge/summarizer.py` to detect when the worker output is entirely process narration with no substantive content. This is the primary defense against Mode D.
- **Message quality filter**: When false-promise output is detected, the observer auto-continues the worker (so it actually does the promised work) instead of delivering empty promises. This is a behavioral change: narration-only output triggers a steer, not a deliver.
- **`message_for_user` field**: The observer curates user-facing text through this field on `deliver_to_telegram`. Prevents Modes A and B by giving the observer control over what gets sent.
- **Reason isolation**: Ensure the observer's `reason` string never reaches any send path, only logging.

### Flow

Worker stops -> Check if output is narration-only (false promise) -> If yes: auto-continue worker with "you announced work but didn't do it, continue" -> If no: Observer decides deliver/steer -> If deliver: use `message_for_user` if available -> Send to user -> Log reason internally

### Technical Approach

1. **Extract `_is_narration_only(text)` helper**: Move/reuse `_PROCESS_NARRATION_PATTERNS` from `bridge/summarizer.py` into a shared utility (`bridge/message_quality.py`). The function returns `True` when EVERY line in the output matches a narration pattern and the output contains NO substantive content (code blocks, URLs, error messages, file paths, data).

   Detection criteria for "narration-only" output:
   - All non-empty lines match at least one `_PROCESS_NARRATION_PATTERNS` regex
   - Output contains NO code fences (` ``` `)
   - Output contains NO URLs (http/https)
   - Output contains NO file paths (slash-separated tokens like `bridge/observer.py`)
   - Output contains NO error tracebacks or stack traces
   - Output length is < 500 chars (long outputs likely contain findings even if they start with narration)

2. **Pre-observer narration gate in `agent/job_queue.py`**: Before running the observer, check `_is_narration_only(msg)`. If true AND auto-continue budget remains, bypass the observer entirely and auto-continue with coaching: "You announced you would investigate but stopped before producing findings. Continue the investigation and report actual results." This catches Mode D without needing the observer's LLM call.

3. **Add `message_for_user` field to `deliver_to_telegram` tool**: The observer curates user-facing text through this field. When delivering, `message_for_user` is what gets sent — not the raw worker output. The field is optional in the schema because the observer may choose to react with an emoji instead of sending text (e.g., for simple completed tasks).

4. **Delivery path uses `message_for_user` as primary**: In `agent/job_queue.py`, when the observer returns `deliver` with `message_for_user`, send that instead of raw `msg`. If `message_for_user` is absent and `msg` is garbage/empty, send a honest fallback like "I wasn't able to complete the investigation. Please re-trigger if needed."

5. **Auto-continue cap path** (line 1572-1580): When the cap forces delivery and `msg` is narration-only, substitute with a fallback message: "Investigation was incomplete. Here's what I found so far: [summarize any non-narration content, or state nothing was found]."

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The observer fallback at line 1543-1551 already handles Observer errors. Verify it still sends worker output (not reason) on error.

### Empty/Invalid Input Handling
- [ ] Test that empty `msg` is caught and replaced with a fallback
- [ ] Test that whitespace-only `msg` is caught
- [ ] Test that tool-use-only output (no text blocks) is caught

### False Promise / Narration-Only Handling
- [ ] Test that narration-only output ("Let me check X. Let me look at Y.") triggers auto-continue, not delivery
- [ ] Test that narration + substantive content (e.g., "Let me check X. Found the issue in line 42.") is NOT caught by narration gate
- [ ] Test that narration gate respects auto-continue budget (doesn't exceed cap)
- [ ] Test that when narration gate fires at cap, a fallback message is sent instead of the narration

### Error State Rendering
- [ ] Test that the fallback message is human-readable and contextual
- [ ] Test that the observer's `reason` string never appears in the message sent to Telegram

## Rabbit Holes

- **Trying to make the observer rewrite all worker output**: The observer should only intervene when the output is clearly garbage. Do not add an LLM-powered "rewrite" step for every delivery -- that adds latency and cost.
- **Parsing worker output for semantic quality**: Keep the heuristic simple (regex patterns for meta-commentary). Do not build an LLM classifier for message quality.
- **Changing the Observer's tool schema significantly**: The `deliver_to_telegram` tool just needs an optional `message_for_user` field. Do not redesign the tool interface.

## Risks

### Risk 1: False positives on narration detection
**Impact:** Legitimate worker responses that happen to start with "Let me check" followed by actual findings get caught by the narration gate and auto-continued unnecessarily.
**Mitigation:** The `_is_narration_only()` check requires ALL lines to match narration patterns AND no substantive content markers (code blocks, URLs, file paths, data). A response like "Let me check the config. The issue is in line 42 of observer.py" will NOT trigger because it contains a file path and findings. The 500-char length cap also prevents false positives on long substantive responses.

### Risk 2: Observer crafts poor `message_for_user` text
**Impact:** User gets a slightly better but still unhelpful message.
**Mitigation:** The observer prompt includes guidance on what makes a good user-facing message. Worst case, a generic "work completed" message is still better than internal reasoning or false promises.

### Risk 3: Narration gate consumes auto-continue budget
**Impact:** Narration-only output burns an auto-continue slot, potentially hitting the cap sooner on legitimate work.
**Mitigation:** The narration gate counts toward the same auto-continue budget (this is intentional — it IS an auto-continue). If the worker keeps producing narration-only output after multiple continues, that's a deeper agent problem outside this fix's scope.

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

- [ ] Update `docs/features/observer-agent.md` to document the `message_for_user` field, narration gate, and message quality filter
- [ ] Add inline code comments explaining the narration detection heuristic in `bridge/message_quality.py`
- [ ] Add docstrings to `_is_narration_only()` with examples of what triggers and what doesn't

## Success Criteria

- [ ] Observer's `reason` string never appears in Telegram messages (verified by test)
- [ ] Narration-only worker output ("Let me check...") triggers auto-continue instead of delivery (verified by test)
- [ ] `_is_narration_only()` correctly distinguishes pure narration from narration + substantive findings
- [ ] When worker output is empty/garbage, observer either steers to next stage or prompts worker to continue
- [ ] The `deliver_to_telegram` tool accepts an optional `message_for_user` field
- [ ] Auto-continue cap path sends fallback message instead of narration-only output
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

### 1. Create shared message quality module
- **Task ID**: build-message-quality
- **Depends On**: none
- **Assigned To**: observer-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `bridge/message_quality.py` with `_is_narration_only(text) -> bool`
- Move/reuse `_PROCESS_NARRATION_PATTERNS` from `bridge/summarizer.py` (import from shared module, keep backward compat)
- Add substantive content detection: code fences, URLs, file paths, tracebacks, length > 500
- The function returns True only when ALL non-empty lines match narration patterns AND no substantive content markers are present

### 2. Add pre-observer narration gate
- **Task ID**: build-narration-gate
- **Depends On**: build-message-quality
- **Assigned To**: observer-builder
- **Agent Type**: builder
- **Parallel**: false
- In `agent/job_queue.py`, BEFORE the Observer is created (~line 1517), check `_is_narration_only(msg)`
- If true AND `chat_state.auto_continue_count < effective_max`, auto-continue with coaching: "You announced you would investigate but stopped before producing findings. Continue the investigation and report actual results."
- If true AND at cap, substitute `msg` with fallback: "Investigation was incomplete — please re-trigger if needed."
- Log narration gate activation at INFO level for debugging

### 3. Update deliver_to_telegram tool schema
- **Task ID**: build-tool-schema
- **Depends On**: none
- **Assigned To**: observer-builder
- **Agent Type**: builder
- **Parallel**: true
- Add optional `message_for_user` field to `deliver_to_telegram` tool input schema in `bridge/observer.py`
- Update the `_dispatch_tool` method to include `message_for_user` in the returned decision dict
- Update the Observer system prompt to instruct when to use `message_for_user`

### 4. Update delivery path to use message_for_user
- **Task ID**: build-delivery-path
- **Depends On**: build-tool-schema
- **Assigned To**: observer-builder
- **Agent Type**: builder
- **Parallel**: false
- In `agent/job_queue.py`, at the delivery point (~line 1640), check if `decision` has `message_for_user` and prefer it over raw `msg`
- On auto-continue cap path (~line 1578), apply narration check and prefer `message_for_user` if available
- Add an empty/whitespace guard alongside the existing empty output guard

### 5. Write tests
- **Task ID**: build-tests
- **Depends On**: build-narration-gate, build-delivery-path
- **Assigned To**: observer-builder
- **Agent Type**: builder
- **Parallel**: false
- Test `_is_narration_only()`: pure narration returns True, narration + findings returns False, empty returns False
- Test narration gate: narration-only msg triggers auto-continue with correct coaching message
- Test narration gate at cap: narration-only msg at cap sends fallback, not narration
- Test: observer delivers with reason -- verify reason is not in sent message
- Test: observer provides message_for_user -- verify it's used instead of garbage worker output
- Test: empty/whitespace worker output -- verify fallback message sent

### 6. Validate fix
- **Task ID**: validate-fix
- **Depends On**: build-tests
- **Assigned To**: observer-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify observer's reason never appears in any send_cb call
- Verify all tests pass
- Run lint and format checks

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-fix
- **Assigned To**: observer-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/observer-agent.md` with message_for_user documentation
- Add inline comments on message quality heuristic

### 8. Final Validation
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
| Message quality module exists | `python -c "from bridge.message_quality import is_narration_only"` | exit code 0 |
| Narration gate in job_queue | `grep -c 'is_narration_only' agent/job_queue.py` | output > 0 |

---

## Open Questions (Resolved)

1. **Should the observer always provide `message_for_user`?** Yes — the observer always curates what the user sees via `message_for_user`. It remains optional in the schema because the observer doesn't always need to send a message (e.g., for simple completed tasks, a done emoji reaction suffices). But when delivering text, the observer always crafts the user-facing message. Note: in issue #395, the PM persona will take over this curation role.

2. **What should the fallback for empty worker output?** Fall back to the stage pipeline (let the observer steer to the next SDLC stage). If the context suggests the worker should have responded to the user, the observer should prompt the worker to continue or produce a real message. Let the observer discern based on context — no rigid rule.
