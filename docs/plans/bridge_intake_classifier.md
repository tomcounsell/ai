---
status: Planning
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-03-10
tracking:
---

# Bridge Intake Classifier: Haiku Triage on Every Incoming Message

## Problem

When a human replies to a session message in Telegram while a worker is active, the bridge currently treats it as either a steering message (if it's a direct reply-to a running session) or a brand new job. There's no classification step that decides **what kind of message this is** before routing it.

**Current behavior:**
1. Reply-to running session → routed to `agent/steering.py` queue (lines 802-844 of `telegram_bridge.py`)
2. Everything else → enqueued as a new job via `enqueue_job()` (line 923)
3. The only classification happening is `classify_request_async()` which runs async fire-and-forget and only sets `classification_type` (sdlc/feature/bug/chore) — it doesn't influence routing

**Desired outcome:**
Run the Haiku classifier **immediately** on every incoming message before enqueueing. Use the classification to decide:
1. **Interjection into active session** → push to `queued_steering_messages` on the active AgentSession (for Observer to read)
2. **New work request** → enqueue as a new job (current behavior)
3. **Acknowledgment/completion** → mark session as appropriate (thumbs-up equivalent)

This is a prerequisite for the Observer Agent (#309) to handle human interjections reliably. The Observer reads `queued_steering_messages` but doesn't own the logic for populating it — that's the bridge's responsibility.

## Prior Art

No closed issues or merged PRs found specifically for intake classification. Related work:

- **#309 (Observer Agent)**: Assumes `queued_steering_messages` is populated by the bridge. Observer reads the field but doesn't own populating it. This issue fills that gap.
- **#318 (Expectations-based routing)**: Semantic session routing for unthreaded messages. Complements this work — intake classifier handles replied messages, semantic router handles unthreaded messages.
- Current `classify_request_async()` in `tools/classifier.py` classifies work type (bug/feature/chore/sdlc) but doesn't classify message intent (interjection/new work/acknowledgment).

## Data Flow

### Current Flow (no intake classification)

1. **Entry point**: Telegram `NewMessage` event arrives at `handler()` in `telegram_bridge.py:524`
2. **Dedup check**: `is_duplicate_message()` filters catch_up replays
3. **Bridge commands**: `/update` bypassed early
4. **Message storage**: All messages stored to Redis history
5. **Should-respond check**: `should_respond_async()` decides if this message is for Valor
6. **Reply-to check** (line 802): If reply to running session → `push_steering_message()` into Redis steering queue
7. **Revival check**: Git state check for unfinished branches
8. **Fire-and-forget classification**: `classify_request_async()` runs in background, result stored in mutable `classification_result` dict
9. **Enqueue**: `enqueue_job()` with whatever `classification_type` is available (may be None if Haiku hasn't responded yet)

### Proposed Flow (with intake classifier)

1. **Entry point**: Same — Telegram event at `handler()`
2. **Dedup, bridge commands, storage**: Unchanged
3. **Should-respond check**: Unchanged
4. **🆕 Intake classification** (after should_respond, before routing): Await Haiku to classify message intent:
   - `interjection` → message is a follow-up to an active session (course correction, additional context, answer to a question)
   - `new_work` → message is a new task/request/conversation
   - `acknowledgment` → message signals completion or approval (thumbs-up, "looks good", "done")
5. **Route based on classification**:
   - `interjection` + active session found → `push_steering_message()` to AgentSession's `queued_steering_messages`
   - `interjection` + no active session → fall through to enqueue (treat as new work)
   - `new_work` → enqueue as normal
   - `acknowledgment` → mark session complete, set reaction
6. **Enqueue**: Same as current (for `new_work` messages)

### Key Difference

Currently, the reply-to-running-session steering check (line 802) only catches **direct Telegram reply-to messages**. The intake classifier catches **all follow-up messages**, including those that are contextually a follow-up but not using Telegram's reply feature. This populates `queued_steering_messages` on the AgentSession for the Observer (#309) to read.

## Architectural Impact

- **New dependencies**: None — Haiku already used via `anthropic` SDK
- **Interface changes**: New function `classify_message_intent()` in `tools/classifier.py`. Bridge handler routes based on intent before enqueueing.
- **Coupling**: Bridges `telegram_bridge.py` to `models/agent_session.py` `push_steering_message()` (already exists, just not called from this path)
- **Data ownership**: No change — AgentSession still owns `queued_steering_messages`
- **Reversibility**: Easy to revert — classification is additive, not replacing existing logic. Remove the classifier call and fall through to current behavior.

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (approach validation)
- Review rounds: 1 (code review)

The classifier logic is straightforward. The tricky part is the session-matching heuristic — finding which active session an interjection belongs to. The existing steering check (line 802) handles the easy case (direct reply). The new classifier extends this to non-reply interjections.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `ANTHROPIC_API_KEY` | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env').get('ANTHROPIC_API_KEY')"` | Haiku API access |
| Observer Agent field exists | `python -c "from models.agent_session import AgentSession; assert hasattr(AgentSession, 'queued_steering_messages')"` | Target field for interjections |

Run all checks: `python scripts/check_prerequisites.py docs/plans/bridge_intake_classifier.md`

## Solution

### Key Elements

- **Intent Classifier**: Haiku-powered function that classifies message intent as `interjection`, `new_work`, or `acknowledgment`
- **Session Finder**: Logic to find the most likely active session for an interjection (same chat, running/active status)
- **Routing Switch**: Bridge handler routes based on intent classification before enqueueing

### Flow

**Message arrives** → Should-respond check → **Intake classifier (Haiku)** → Intent routing:
- `interjection` → Find active session → `push_steering_message()` → Ack to sender
- `new_work` → Enqueue job (current path)
- `acknowledgment` → Mark session complete → Set reaction

### Technical Approach

- **Classifier call is blocking (awaited)**: Unlike the current fire-and-forget `classify_request_async()`, the intake classifier must complete before routing. Cost: ~100-200ms (Haiku). This is acceptable because the reaction emoji is already set (👀) and the user knows the message was received.
- **Session matching for interjections**: For non-reply interjections, find the most recent running/active session in the same chat. If multiple sessions exist, use recency. If no session exists, treat as `new_work`.
- **Existing steering preserved**: The current reply-to-running-session check (line 802) stays as a fast path. The intake classifier runs only for messages that don't hit the fast path.
- **Classification prompt includes context**: Pass the last few messages from the conversation to give Haiku context about whether this is a follow-up or new work.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `classify_message_intent()` failure → fall through to current behavior (treat as `new_work`). Log at WARNING.
- [ ] Active session lookup failure (Redis connection error) → fall through to `new_work`. Log at ERROR.
- [ ] `push_steering_message()` failure → fall through to enqueue. Log at ERROR.

### Empty/Invalid Input Handling
- [ ] Empty message text → classify as `new_work` (default)
- [ ] Classifier returns unexpected type → fall through to `new_work`
- [ ] No active sessions in chat → interjection treated as `new_work`

### Error State Rendering
- [ ] If classification fails, user still gets 👀 reaction and message is queued normally
- [ ] If interjection push fails, user gets informed ("Couldn't add to current task, queuing as new work")

## Rabbit Holes

- **Trying to classify every message type exhaustively** — keep it to three categories (interjection/new_work/acknowledgment). Don't add conversation/question/greeting etc. Those are all `new_work`.
- **Building sophisticated multi-session matching** — just pick the most recent active session in the same chat. Multi-session disambiguation is #318 territory.
- **Replacing the existing steering check (line 802)** — keep it as a fast path for direct replies. The intake classifier is for messages that bypass the fast path.
- **Adding message history to the classifier prompt** — start simple (message text + session context_summary). If accuracy is low, add history later.

## Risks

### Risk 1: Classifier latency blocks message handling
**Impact:** User perceives slow response to messages (~100-200ms added)
**Mitigation:** 👀 reaction is set before classification runs. User already knows message was received. If latency is a problem, can fire-and-forget and retroactively route (but this adds complexity, defer to v2).

### Risk 2: False positive interjections steal messages from new work queue
**Impact:** User sends a new request but it gets routed as an interjection to an active session, which ignores or mishandles it
**Mitigation:** Require high confidence (>= 0.80) for interjection classification. Below threshold, default to `new_work`. Also, interjections only route to running/active sessions — if no active session exists, always `new_work`.

### Risk 3: Acknowledgment classification prematurely completes sessions
**Impact:** User says "ok" meaning "ok, I'll look at this later" but bridge marks session complete
**Mitigation:** Acknowledgment classification requires both high confidence AND an active session waiting for a response (status = dormant with expectations set). Active/running sessions can't be acknowledged to completion.

## Race Conditions

### Race 1: Classification result vs session state change
**Location:** `telegram_bridge.py` handler, between intake classifier return and session lookup
**Trigger:** Worker finishes while intake classifier is running, session transitions from running → completed
**Data prerequisite:** Session must be in running/active state for interjection routing
**State prerequisite:** Session status must be re-checked after classification
**Mitigation:** Re-read session status immediately before pushing steering message. If session completed during classification, fall through to `new_work`.

### Race 2: Multiple messages classified concurrently
**Location:** `telegram_bridge.py` handler (Telethon dispatches events concurrently)
**Trigger:** Two messages arrive simultaneously, both classified as interjection for the same session
**Data prerequisite:** Both messages must see the same active session
**State prerequisite:** `push_steering_message()` must handle concurrent pushes
**Mitigation:** `push_steering_message()` uses Redis RPUSH which is atomic. Both messages end up in the queue safely. Observer reads them all.

## No-Gos (Out of Scope)

- **Multi-session disambiguation** — if multiple sessions are active, just pick the most recent. Complex routing is #318.
- **Replacing the existing reply-to steering check** — keep it as a fast path. Don't break what works.
- **Classifying outgoing messages** — only incoming human messages.
- **Modifying the Observer Agent** — Observer already reads `queued_steering_messages`. No changes needed there.
- **Adding new Telegram bot commands** — pure internal routing logic, no user-facing commands.

## Update System

No update system changes required — this feature is purely bridge-internal. No new dependencies, no new config files. Remote machines get the code via `git pull`.

## Agent Integration

No agent integration required — this is a bridge-internal change. The intake classifier runs inside the Telegram event handler. It uses the Claude API directly (Haiku), not MCP tools. No changes to `.mcp.json` or `mcp_servers/`.

The bridge (`bridge/telegram_bridge.py`) calls the new classifier function directly. The AgentSession `push_steering_message()` method already exists.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/intake-classifier.md` describing the classification flow and routing logic
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Update `docs/features/observer-agent.md` to reference how `queued_steering_messages` gets populated

### Inline Documentation
- [ ] Docstrings for `classify_message_intent()` function
- [ ] Comments in `telegram_bridge.py` handler explaining the routing decision tree

## Success Criteria

- [ ] Every incoming message runs through intake classification before routing
- [ ] Messages classified as `interjection` are pushed to `queued_steering_messages` on the active session
- [ ] Messages classified as `new_work` follow the existing enqueue path unchanged
- [ ] Messages classified as `acknowledgment` mark dormant sessions as complete
- [ ] Classification failure degrades gracefully to `new_work` (current behavior)
- [ ] Existing reply-to steering fast path (line 802) preserved and still works
- [ ] Observer Agent (#309) can read interjection messages from `queued_steering_messages`
- [ ] Latency impact < 300ms p95 (Haiku classification)
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (classifier)**
  - Name: classifier-builder
  - Role: Implement `classify_message_intent()` function in `tools/classifier.py`
  - Agent Type: builder
  - Resume: true

- **Builder (integration)**
  - Name: integration-builder
  - Role: Wire intake classifier into bridge handler, implement routing switch
  - Agent Type: builder
  - Resume: true

- **Validator (routing)**
  - Name: routing-validator
  - Role: Verify all routing paths work correctly, no regressions
  - Agent Type: validator
  - Resume: true

- **Test Engineer**
  - Name: intake-tester
  - Role: Unit and integration tests for classifier and routing
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian**
  - Name: intake-docs
  - Role: Create feature documentation
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Implement Intent Classifier
- **Task ID**: build-classifier
- **Depends On**: none
- **Assigned To**: classifier-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `classify_message_intent()` and `classify_message_intent_async()` to `tools/classifier.py`
- Classify into: `interjection`, `new_work`, `acknowledgment`
- Include session context (context_summary, expectations) in prompt for accuracy
- Return structured dict: `{"intent": str, "confidence": float, "reason": str, "target_session_id": str|null}`
- Unit tests with representative messages for each intent type

### 2. Wire Intake Classifier into Bridge Handler
- **Task ID**: build-integration
- **Depends On**: build-classifier
- **Assigned To**: integration-builder
- **Agent Type**: builder
- **Parallel**: false
- In `telegram_bridge.py` handler, after `should_respond_async()` and before the existing steering check:
  - Find active/running/dormant sessions in the same chat
  - If sessions exist, call `classify_message_intent_async()` with message + session context
  - Route based on classification result
- For `interjection`: find target session, call `push_steering_message()`, send ack to user
- For `acknowledgment`: find dormant session, mark complete, set reaction
- For `new_work` or classifier failure: fall through to existing enqueue path
- Preserve existing reply-to-running-session fast path (line 802) — intake classifier runs ONLY for messages that don't hit the fast path

### 3. Validate Routing
- **Task ID**: validate-routing
- **Depends On**: build-integration
- **Assigned To**: routing-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all three routing paths (interjection, new_work, acknowledgment)
- Verify existing reply-to steering still works
- Verify classifier failure degrades to `new_work`
- Verify no regressions in current message handling

### 4. Integration Tests
- **Task ID**: test-integration
- **Depends On**: validate-routing
- **Assigned To**: intake-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Test interjection routing: message during active session → appears in `queued_steering_messages`
- Test new work routing: message with no active session → enqueued as job
- Test acknowledgment routing: "looks good" to dormant session → session marked complete
- Test classifier failure: mock API error → message enqueued as normal
- Test race condition: session completes during classification → message enqueued as normal

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: test-integration
- **Assigned To**: intake-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/intake-classifier.md`
- Add entry to `docs/features/README.md` index table
- Update `docs/features/observer-agent.md` with population details

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: routing-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met
- Generate final report

## Validation Commands

- `python -m pytest tests/ -x -q` - Run all tests
- `python -m ruff check .` - Lint check
- `python -m ruff format --check .` - Format check
- `python -c "from tools.classifier import classify_message_intent; print('Intent classifier importable')"` - Verify function exists
- `python -c "from models.agent_session import AgentSession; s = AgentSession(); s.push_steering_message('test'); assert s.pop_steering_messages() == ['test']"` - Verify steering message lifecycle
- `grep -n 'classify_message_intent' bridge/telegram_bridge.py` - Verify classifier wired into handler

---

## Open Questions

1. **Should the intake classifier run for ALL messages or only replied messages?** The issue says "every incoming message" but the practical value is mainly for replies to active sessions. Running on every message adds ~100-200ms latency to all message handling. Proposal: Run on all messages that pass `should_respond_async()`, but only route to interjection if an active session exists in the same chat. Otherwise, fast-path to `new_work`.

2. **What context to include in the classifier prompt?** Options range from just the message text (fast, cheap) to including the active session's context_summary + expectations + last few history entries (more accurate but larger prompt). Proposal: Include session context_summary and expectations if an active session exists. Skip conversation history for v1.

3. **Should acknowledgment handle thumbs-up reactions too?** Currently 👍 reactions are handled separately in the bridge. Should the intake classifier also catch text-based acknowledgments ("done", "looks good", "approved")? Proposal: Yes, classify text-based acknowledgments but only act on them for dormant sessions with expectations set.
