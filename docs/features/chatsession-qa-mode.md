# ChatSession Q&A Mode

## Overview

ChatSession Q&A mode adds a fast path for informational queries. When a user asks a question (e.g., "where is the observer prompt?", "what tests are failing?"), the ChatSession answers directly using read-only tools instead of spawning a full DevSession. This reduces latency and cost for simple lookups while preserving the full SDLC pipeline for actual work requests.

Q&A mode is not a new session type. It is a routing decision within the existing ChatSession, gated by a binary intent classifier.

## Architecture

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

## Components

### Intent Classifier (`agent/intent_classifier.py`)

A lightweight Haiku-based binary classifier that determines whether a message is an informational query or a work request.

- **Input**: message text, optional conversation context (last 3 messages)
- **Output**: `IntentResult` with `intent` ("qa" or "work"), `confidence` (0.0-1.0), and `reasoning`
- **Threshold**: Q&A routing requires confidence above 0.90
- **Fail-safe**: any error, timeout, or low confidence defaults to DevSession (current behavior preserved)
- **API**: uses the Anthropic API directly (not Claude Code SDK) for low-latency classification via `MODEL_FAST`

Classification signals:

| Signal | Intent |
|--------|--------|
| Question words (what/where/how/when/who), "status of", "show me", "explain" | Q&A |
| Imperative verbs (fix/add/create/update/deploy/merge), "make it", code snippets | Work |

### Q&A Handler (`agent/qa_handler.py`)

Provides Q&A-specific instructions that replace the PM dispatch block when a message is classified as Q&A.

- **Tools available**: Read, Glob, Grep, Bash (read-only commands: git log, git status, gh issue view, gh pr list)
- **Tools blocked**: file writes, branch creation, test execution, Agent tool (no DevSession spawning)
- **Nudge cap**: 10 (vs 50 for normal sessions), set via `QA_MAX_NUDGE_COUNT`
- **Persona**: same PM persona with Q&A-specific additions (conversational tone, cite file paths, direct answers)

### Metrics (`agent/qa_metrics.py`)

Redis-backed counters for observability. All operations are fire-and-forget -- metrics failures never affect message processing.

- `qa_classified_count`: messages routed to Q&A
- `work_classified_count`: messages routed to DevSession
- `qa_low_confidence_count`: Q&A-classified messages below the 0.90 threshold (routed to DevSession)
- Response time tracking: sorted set per mode with time-windowed analysis, capped at 1000 entries
- `get_stats()` returns a summary dict of current counters

### Escape Hatch

No special mechanism needed. Each incoming message is classified independently. If a user asks a question and then follows up with "ok fix that", the follow-up is classified as a work request and routes to DevSession. Session continuity (reply-to threading) ensures the DevSession sees the prior Q&A context.

## Integration Points

### `agent/sdk_client.py`

In `_execute_agent_request()`, after determining the session type is "chat":

1. Calls `classify_intent(message)` to get the intent result
2. Calls `record_classification()` to track metrics
3. If `is_qa` is true, injects Q&A instructions via `build_qa_instructions()` instead of PM dispatch instructions
4. If `is_work` or classifier fails, preserves current behavior exactly

### `agent/job_queue.py`

In the nudge loop, checks the session's `qa_mode` field:

- If `True`, uses `QA_MAX_NUDGE_COUNT` (10) instead of the default `MAX_NUDGE_COUNT` (50)
- Q&A sessions resolve faster; the reduced cap prevents runaway sessions

## Key Design Decisions

1. **Conservative threshold (0.90)**: false negatives (Q&A classified as work) cause no harm -- just unnecessary DevSession spawn. False positives (work classified as Q&A) are more costly, so the threshold is high.
2. **No new session type**: Q&A is a routing decision, not a new `session_type` value. A `qa_mode` boolean field on AgentSession tracks the routing decision separately from `classification_type` (which preserves the bridge's original classification).
3. **No bridge changes**: Q&A vs work routing happens entirely in the agent layer. The bridge continues routing all messages to ChatSession.
4. **No caching**: each message is classified independently for simplicity.

## Key Files

| File | Purpose |
|------|---------|
| `agent/intent_classifier.py` | Haiku-based binary classifier with few-shot prompt |
| `agent/qa_handler.py` | Q&A instruction builder and nudge cap constant |
| `agent/qa_metrics.py` | Redis-backed classification and response time counters |
| `agent/sdk_client.py` | Integration point: classifier call and instruction injection |
| `agent/job_queue.py` | Integration point: reduced nudge cap for Q&A sessions |
