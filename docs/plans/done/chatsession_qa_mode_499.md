# ChatSession Q&A Mode

**Issue:** #499
**Slug:** chatsession-qa-mode-499
**Status:** planned
**Branch:** session/chatsession-qa-mode-499

## Problem

Every incoming message follows the same ChatSession -> DevSession path regardless of intent. Simple informational queries ("what's the status of feature X?", "how does the bridge work?", "where is the observer prompt?") spawn a full DevSession with all tools and permissions, adding 2-5 seconds of startup latency and unnecessary cost. ChatSession already has read-only capabilities and the PM persona — it just needs a decision point to answer directly instead of always delegating.

## Solution

Add a binary intent classifier and Q&A handler within the ChatSession message processing path. When a message is classified as an informational query with high confidence, ChatSession answers directly using read-only tools without spawning a DevSession.

### Architecture

```
Telegram Message
    |
    v
ChatSession receives message
    |
    v
Intent Classifier (Haiku, ~$0.0001/call)
    |
    |-- Q&A (confidence > 0.90) --> Q&A Handler (read-only tools)
    |                                   |
    |                                   v
    |                               Direct response to Telegram
    |
    |-- Work request (or low confidence) --> Normal DevSession spawn
```

### Component 1: Intent Classifier

**File:** `agent/intent_classifier.py` (new)

A lightweight Haiku-based binary classifier that determines whether an incoming message is an informational query (Q&A) or a work request.

- Input: message text, sender context, recent conversation history (last 3 messages if available)
- Output: `{"intent": "qa" | "work", "confidence": float, "reasoning": str}`
- Threshold: classify as Q&A only if confidence > 0.90 (conservative)
- Fail-safe: any ambiguity defaults to DevSession (current behavior preserved)
- Uses the Anthropic API directly (not Claude Code SDK) for low-latency classification
- Few-shot prompt with examples of each category

**Q&A signals:** question marks, "what/where/how/when/who/which", "status of", "show me", "explain", status checks, architecture questions
**Work signals:** imperative verbs ("fix", "add", "create", "update", "deploy", "merge"), issue/PR references with action intent, "make it", "change", code snippets

**Edge cases:**
- "What's broken in the bridge?" -> Q&A (asking for information)
- "Fix the bridge" -> Work
- "Where is the observer prompt?" -> Q&A
- "The observer prompt has a bug" -> Work (implicit fix request)
- "ok fix that" -> Work (escape hatch from prior Q&A)

### Component 2: Q&A Handler

**File:** `agent/qa_handler.py` (new)

Runs the ChatSession with Q&A-specific instructions instead of PM dispatch instructions. Uses the same SDK client infrastructure but with different prompt injection.

- Tools: Read, Glob, Grep, WebFetch, plus Bash (read-only commands only: git log, git status, gh issue view, gh pr list, etc.)
- No access to: file writes, branch creation, test execution, Agent tool (no DevSession spawning from Q&A path)
- System prompt: PM persona with Q&A-specific instructions ("answer directly, be conversational, cite file paths")
- Nudge cap: lower than SDLC (e.g., 10 nudges vs 50) since Q&A should resolve quickly

**Integration point:** The Q&A handler modifies the enriched message and system prompt construction in `sdk_client.py`'s `_execute_agent_request()` function. When intent=qa:
1. Replace PM dispatch instructions with Q&A instructions
2. Keep the same PM persona system prompt
3. Set `classification_type="qa"` on AgentSession so nudge loop uses reduced cap
4. Use a reduced nudge cap in `job_queue.py`

### Component 3: Escape Hatch

When a Q&A session receives a follow-up that looks like a work request ("ok fix that", "can you update it?", "make that change"), the intent classifier re-evaluates and routes to DevSession. This works naturally because each new message goes through classification independently. The follow-up message carries conversational context from the Q&A exchange, so the DevSession has the background it needs.

No special mechanism needed — the per-message classification handles this automatically. The session continuity (reply-to threading) ensures the DevSession sees what was discussed.

### Component 4: Metrics

**File:** `agent/qa_metrics.py` (new)

Track classification distribution and response times for observability.

- Redis-backed counters: `qa_classified_count`, `work_classified_count`, `qa_low_confidence_count`
- Response time tracking: time from message receipt to Telegram delivery for Q&A vs DevSession paths
- Logged to bridge.log for immediate visibility
- Exposed via existing health/monitoring infrastructure

## Implementation Plan

- [ ] 1. Create `agent/intent_classifier.py` with Haiku-based binary classifier
  - Few-shot prompt with 10+ examples each of Q&A and work requests
  - Async `classify_intent(message: str, context: dict) -> IntentResult` function
  - Conservative threshold (0.90) with fail-safe to DevSession
  - Unit tests with golden examples

- [ ] 2. Create `agent/qa_handler.py` with Q&A-specific message enrichment
  - `build_qa_instructions() -> str` that replaces PM dispatch block
  - Q&A-specific system prompt additions (conversational tone, cite sources)
  - `classification_type="qa"` field on AgentSession (set via Popoto ORM)

- [ ] 3. Integrate classifier into `agent/sdk_client.py` message processing
  - Call `classify_intent()` before PM dispatch instruction injection (around line 1460)
  - When intent=qa with high confidence, use Q&A instructions instead of PM dispatch
  - When intent=work or low confidence, preserve current behavior exactly
  - Log classification result for every message

- [ ] 4. Add reduced nudge cap for Q&A sessions in `agent/job_queue.py`
  - Check `classification_type` field on AgentSession in nudge loop
  - Q&A sessions: max 10 nudges (vs 50 for SDLC)
  - Q&A sessions: shorter inactivity timeout

- [ ] 5. Create `agent/qa_metrics.py` with Redis-backed counters
  - Increment on each classification
  - Track response times
  - Log summary stats periodically

- [ ] 6. Write tests
  - Unit tests for intent classifier (golden set of 30+ examples)
  - Unit tests for Q&A handler message enrichment
  - Integration test: Q&A message -> direct response (no DevSession)
  - Integration test: work request -> DevSession spawn (no regression)
  - Integration test: escape hatch (Q&A -> follow-up work request -> DevSession)

## Success Criteria

- [ ] Intent classifier distinguishes Q&A from work requests with >90% accuracy on a golden test set of 30+ examples
- [ ] Q&A queries resolve without DevSession spawn (measurable latency improvement in logs)
- [ ] All work requests still route through full SDLC pipeline (no regressions in existing integration tests)
- [ ] Q&A responses use PM persona with conversational tone (cite file paths, direct answers)
- [ ] Escape hatch works: Q&A exchange followed by "fix that" spawns DevSession
- [ ] Classifier failure (API error/timeout) falls back to DevSession (current behavior preserved)
- [ ] Metrics tracked: Q&A vs work classification counts, response times logged to bridge.log
- [ ] Reduced nudge cap (10) enforced for Q&A sessions

## No-Gos

- No shortcuts for "simple" code changes — all code work goes through full SDLC
- No new session type — Q&A mode is a routing decision within the existing ChatSession, not a third session type
- No changes to the bridge layer — Q&A vs work routing happens entirely in the agent layer
- No caching of classifier results — each message is classified independently for simplicity
- No custom model selection for Q&A responses — use the same model as ChatSession

## Update System

No update system changes required. The new files (`agent/intent_classifier.py`, `agent/qa_handler.py`, `agent/qa_metrics.py`) will be picked up automatically by `git pull` during updates. No new dependencies, config files, or migration steps needed. The Anthropic SDK (already a dependency for memory extraction) provides the Haiku API access.

## Agent Integration

No new MCP server or tool exposure needed. The Q&A mode is internal to the ChatSession processing pipeline — it modifies how the ChatSession handles messages, not what tools are available to the agent. The existing read-only tools (Read, Glob, Grep, WebFetch) are already registered. The intent classifier uses the Anthropic API directly (like the memory extraction Haiku calls), not through MCP.

The bridge does not need changes — it continues to route messages to ChatSession via `job_queue.py`. The Q&A vs DevSession decision happens downstream in `sdk_client.py`.

## Failure Path Test Strategy

1. **Classifier failure (API error):** Falls back to DevSession spawn (current behavior). Test: mock Haiku API timeout, verify DevSession is spawned.
2. **Low confidence classification:** Defaults to DevSession. Test: ambiguous messages ("the bridge seems slow") should route to DevSession.
3. **Q&A session hangs:** Reduced nudge cap (10) ensures timeout. Test: verify nudge cap is enforced for Q&A sessions.
4. **False positive (work classified as Q&A):** User sends follow-up "fix that", which gets classified as work and spawns DevSession. Test: verify escape hatch works.
5. **False negative (Q&A classified as work):** No harm — just unnecessary DevSession spawn. Current behavior preserved. Acceptable failure mode.

## Test Impact

No existing tests affected — this is a greenfield feature adding new files (`agent/intent_classifier.py`, `agent/qa_handler.py`, `agent/qa_metrics.py`) and modifying the ChatSession message enrichment path in `sdk_client.py`. The modification is additive (new code path gated by classifier result) and preserves the existing DevSession path as the default/fallback. Existing integration tests that send messages through the pipeline will continue to work because the classifier defaults to DevSession on any ambiguity.

## Rabbit Holes

- **Multi-turn Q&A context:** Tempting to build a conversation memory for Q&A sessions, but each message is already independently classified and the PM persona has access to chat history. Keep it simple.
- **Tool restriction enforcement:** Considered adding a hook to block writes during Q&A, but the Q&A path already omits the Agent tool and uses Q&A-specific instructions. Trust the prompt, don't over-engineer enforcement.
- **Classifier fine-tuning:** Resist the urge to make the classifier more sophisticated (multi-class, fine-tuned model). Binary Haiku classification with few-shot examples is sufficient. Iterate based on production data.
- **Separate Q&A persona:** The PM persona already works well for informational exchanges. Adding a separate Q&A persona overlay is unnecessary complexity.

## Documentation

- [ ] Create `docs/features/chatsession-qa-mode.md` describing the Q&A routing architecture, classifier design, and escape hatch
- [ ] Update `docs/features/chat-dev-session-architecture.md` to document the Q&A fast path within ChatSession routing
- [ ] Add entry to `docs/features/README.md` index table for the new feature doc
