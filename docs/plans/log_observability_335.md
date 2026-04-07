---
status: Planning
type: feature
appetite: Medium
owner: Valor
created: 2026-03-10
tracking: https://github.com/valorengels/ai/issues/335
---

# Log Observer Reasoning, Routing Decisions, and Enrichment Outcomes

## Problem

Several critical decision points in the SDLC pipeline produce no logs, making post-hoc debugging impossible. The system makes consequential decisions (route this message, continue or deliver, enrich with media) but the reasoning behind those decisions is invisible in the log stream.

**Current behavior:**
- Observer runs up to 5 tool-use iterations internally but only logs the final transition count and error cases. You cannot see which tools it called, what data it read, or why it chose "steer" vs "deliver."
- Routing decisions (SDLC vs question vs passthrough, new session vs continuation, semantic routing match) happen silently. The classification result is logged at DEBUG level only.
- Enrichment failures are caught and warned, but there is no summary of what enrichment was attempted, what succeeded, and what the agent actually received.
- Stage detector logs applied transitions but not which patterns were checked or why some didn't match.
- The prompt sent to the agent (message length, context sections, task list ID) is never summarized.

**Desired outcome:**
Every operational decision point logs a single INFO-level line with consistent prefix tags, making it possible to trace any message from arrival through routing, enrichment, observer decision, and agent prompt construction by reading the log stream.

## Prior Art

No prior issues found related to this work.

## Data Flow

1. **Entry point**: Message arrives in `bridge/telegram_bridge.py` event handler
2. **Routing**: `bridge/routing.py:classify_work_request()` determines SDLC/question/passthrough; semantic router in `bridge/session_router.py` matches or creates session
3. **Enqueue**: Job is pushed to `agent/job_queue.py` with metadata (media type, URLs, reply-to ID, classification)
4. **Enrichment**: `bridge/enrichment.py:enrich_message()` processes media, YouTube, links, reply chains before agent invocation
5. **Prompt construction**: `agent/sdk_client.py:get_agent_response_sdk()` builds enriched message with context prefix, session ID, workflow context
6. **Agent execution**: SDK client sends prompt, receives response
7. **Observer**: `bridge/observer.py:Observer.run()` decides steer (auto-continue) or deliver (send to Telegram)
8. **Stage detection**: `bridge/stage_detector.py:detect_stages()` parses transcript for SDLC stage transitions

## Architectural Impact

- **New dependencies**: None
- **Interface changes**: None -- all changes are additive logging statements
- **Coupling**: No change -- logging is write-only, no new data flows
- **Data ownership**: No change
- **Reversibility**: Trivially reversible -- remove log statements

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

This is a pure logging addition with no behavioral changes. The volume of files touched (6) is the main complexity, not the logic.

## Prerequisites

No prerequisites -- this work has no external dependencies.

## Solution

### Key Elements

- **Observer iteration logging**: Log each tool-use iteration with tool name and result preview
- **Routing decision logging**: Log classification result, semantic routing match/miss, session continuity decision
- **Enrichment outcome summary**: Log a single summary line after all enrichment steps complete
- **Stage detector reasoning**: Log which patterns matched and which were skipped
- **Prompt summary logging**: Log message length, included context sections, and task list ID

### Flow

Message arrives -> `[routing]` logs classification and session decision -> `[enrichment]` logs summary of all steps -> `[prompt-summary]` logs what was sent to agent -> Agent runs -> `[stage-detector]` logs pattern matches -> `[observer]` logs each iteration and final decision

### Technical Approach

- All new logging at INFO level using consistent prefix tags: `[routing]`, `[observer]`, `[enrichment]`, `[stage-detector]`, `[prompt-summary]`
- Truncate long values to 120 chars max for previews
- No behavioral changes -- logging is purely observational
- Each file gets a focused set of `logger.info()` calls at decision points

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] No new exception handlers are being added. Existing handlers in enrichment.py already have warning-level logs. The new logging is additive and sits outside try/except blocks.

### Empty/Invalid Input Handling
- [ ] Verify that logging handles None/empty values gracefully (e.g., `worker_output[:120]` when output is empty)
- [ ] Ensure truncation logic doesn't crash on zero-length strings

### Error State Rendering
- [ ] Not applicable -- no user-visible output changes

## Rabbit Holes

- **Structured logging / JSON log format**: Tempting but separate concern. Plain text with prefix tags is sufficient for now.
- **Log aggregation / dashboards**: Out of scope. This just gets the data into the log stream.
- **Performance metrics / timing**: Each decision point could be timed, but adding timers is a separate concern from adding decision reasoning.
- **Logging the full observer LLM prompt/response**: Would be useful for debugging but risks sensitive data exposure. Stay with summaries only.

## Risks

### Risk 1: Log volume increase
**Impact:** Logs get noisier, harder to read
**Mitigation:** One INFO line per decision point, not per iteration. Use consistent prefix tags so grep filtering works.

### Risk 2: Sensitive data in logs
**Impact:** Message content, API keys, or user data appears in logs
**Mitigation:** Log summaries only (length, hash, section names). Never log full message content. Truncate previews to 120 chars.

## Race Conditions

No race conditions identified. All new code is synchronous logging within existing call paths. No shared mutable state is introduced.

## No-Gos (Out of Scope)

- Changing any routing, enrichment, or observer behavior
- Adding structured/JSON logging format
- Adding timing/performance metrics
- Building a log dashboard or aggregation pipeline
- Logging full message content or LLM prompts (sensitive data risk)

## Update System

No update system changes required -- this feature adds logging statements only, no new dependencies or config files.

## Agent Integration

No agent integration required -- this is a bridge-internal change that adds observability logging to existing code paths.

## Documentation

- [ ] Create `docs/features/operational-logging.md` describing the logging prefix tags and what each logs
- [ ] Add entry to `docs/features/README.md` index table

### Inline Documentation
- [ ] Code comments on the logging format conventions (prefix tags, truncation length)

## Success Criteria

- [ ] Observer logs each tool-use iteration: tool name, result preview (truncated to 120 chars)
- [ ] Observer logs iteration count and final decision reason
- [ ] Routing logs classification result (sdlc/question/passthrough) at INFO level
- [ ] Routing logs semantic routing match/miss with session ID
- [ ] Routing logs new session vs continuation decision
- [ ] Enrichment logs a single summary line: "media=yes/no, youtube=N, links=N, reply_chain=N messages"
- [ ] Stage detector logs which patterns matched and which were checked
- [ ] Stage detector logs implicit completions with reason
- [ ] Prompt summary logs message length, context sections included, task list ID
- [ ] All new logging uses INFO level
- [ ] All new logging uses consistent prefix tags: `[routing]`, `[observer]`, `[enrichment]`, `[stage-detector]`, `[prompt-summary]`
- [ ] No values longer than 120 chars in log previews
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (logging)**
  - Name: logging-builder
  - Role: Add INFO-level logging to all 6 files
  - Agent Type: builder
  - Resume: true

- **Validator (logging)**
  - Name: logging-validator
  - Role: Verify all logging criteria are met
  - Agent Type: validator
  - Resume: true

### Step by Step Tasks

### 1. Add observer iteration logging
- **Task ID**: build-observer
- **Depends On**: none
- **Assigned To**: logging-builder
- **Agent Type**: builder
- **Parallel**: true
- In `bridge/observer.py`, inside the tool-use loop (line ~383-424):
  - Log each iteration: `[observer] Iteration {i+1}/{MAX_TOOL_ITERATIONS}: tool={tool_name}, result={result_preview:.120}`
  - Log when max iterations reached without decision
  - Log final decision with reason: `[observer] Decision: {action} (reason: {reason:.120})`
  - Log session context summary at start of run: `[observer] Session {session_id}: is_sdlc={is_sdlc}, auto_continue={count}/{max}, remaining_stages={has_remaining}`

### 2. Add routing decision logging
- **Task ID**: build-routing
- **Depends On**: none
- **Assigned To**: logging-builder
- **Agent Type**: builder
- **Parallel**: true
- In `bridge/routing.py:classify_work_request()` (line ~346):
  - Log classification result at INFO: `[routing] Classified as {result}: {text[:120]}`
- In `bridge/telegram_bridge.py` event handler (line ~680-714):
  - Log session continuity decision: `[routing] Session {session_id} (continuation={is_continuation})`
  - Log semantic routing match/miss: `[routing] Semantic routing: {matched/no_match} (confidence: {conf})`

### 3. Add enrichment outcome summary
- **Task ID**: build-enrichment
- **Depends On**: none
- **Assigned To**: logging-builder
- **Agent Type**: builder
- **Parallel**: true
- In `bridge/enrichment.py:enrich_message()`, after all steps:
  - Log a single summary: `[enrichment] Summary: media={yes/no}, youtube={count}, links={count}, reply_chain={count} messages, result_length={len}`
  - On partial failure, include which steps failed

### 4. Add stage detector reasoning
- **Task ID**: build-stage-detector
- **Depends On**: none
- **Assigned To**: logging-builder
- **Agent Type**: builder
- **Parallel**: true
- In `bridge/stage_detector.py:detect_stages()`:
  - Log which patterns were checked and which matched: `[stage-detector] Checked {n} patterns, matched: {list}`
  - Log implicit completions: `[stage-detector] Implicitly completing {stage} (reason: {stage} started)`
- In `apply_transitions()`:
  - Log skipped transitions: `[stage-detector] Skipping {stage}->{status} (current: {current_status})`

### 5. Add prompt summary logging
- **Task ID**: build-prompt-summary
- **Depends On**: none
- **Assigned To**: logging-builder
- **Agent Type**: builder
- **Parallel**: true
- In `agent/sdk_client.py:get_agent_response_sdk()` (line ~905-951):
  - Log: `[prompt-summary] Sending to agent: {len} chars, classification={classification}, has_workflow={bool}, task_list={id}`
  - Log session context: `[prompt-summary] Context: soul=yes, sdlc_workflow=yes, workflow_context={yes/no}, session_id={id}`

### 6. Validate all logging
- **Task ID**: validate-logging
- **Depends On**: build-observer, build-routing, build-enrichment, build-stage-detector, build-prompt-summary
- **Assigned To**: logging-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all prefix tags are used consistently
- Verify no log line exceeds 120 char preview limit
- Verify all logging is at INFO level (not DEBUG)
- Run `ruff check` and `ruff format` on all modified files

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-logging
- **Assigned To**: logging-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/operational-logging.md`
- Add entry to `docs/features/README.md`

### 8. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: logging-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify all success criteria met

## Validation Commands

- `grep -rn '\[routing\]\|\[observer\]\|\[enrichment\]\|\[stage-detector\]\|\[prompt-summary\]' bridge/ agent/` - Verify all prefix tags are present
- `python -m ruff check bridge/observer.py bridge/enrichment.py bridge/stage_detector.py bridge/routing.py bridge/telegram_bridge.py agent/sdk_client.py` - Lint all modified files
- `python -m ruff format --check bridge/observer.py bridge/enrichment.py bridge/stage_detector.py bridge/routing.py bridge/telegram_bridge.py agent/sdk_client.py` - Format check
- `pytest tests/ -x -q` - Run test suite

---

## Open Questions

1. **Log level for observer iterations**: Should individual observer iterations be at INFO or DEBUG? INFO gives full visibility but adds 2-5 lines per message. DEBUG reduces noise but requires debug-level log configuration to see them. The plan currently specifies INFO.

2. **Enrichment summary on no-op**: When a message has no media, no URLs, and no reply chain, should we still log the enrichment summary (`media=no, youtube=0, links=0, reply_chain=0`)? This makes every message traceable but adds noise for simple text messages.
