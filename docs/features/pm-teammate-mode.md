# PM session Teammate Mode

## Overview

PM session teammate mode adds a fast path for informational queries. When a user asks a question (e.g., "where is the observer prompt?", "what tests are failing?"), the PM session answers directly using read-only tools instead of spawning a full Dev session. This reduces latency and cost for simple lookups while preserving the full SDLC pipeline for actual work requests.

Teammate mode is now a first-class session type (`SessionType.TEAMMATE`). It was previously a routing decision within the PM session, gated by an intent classifier. Teammate sessions are indicated by the `session_mode` field set to `PersonaType.TEAMMATE` (from `config/enums.py`). The `ChatMode` enum has been removed -- `PersonaType` is the sole persona identifier.

## Architecture

```
Telegram Message
    |
    v
PM session receives message
    |
    v
Intent Classifier (Haiku, four-way, ~$0.0001/call)
    |
    |-- Teammate (confidence > 0.90) --> Teammate Handler (read-only tools)
    |                                       |
    |                                       v
    |                                   Direct response to Telegram
    |
    |-- Collaboration/Other --> PM direct-action mode (handle with tools)
    |                               |
    |                               v
    |                           Telegram Response
    |
    |-- Work request (or low confidence) --> Normal Dev session spawn
```

## Components

### Intent Classifier (`agent/intent_classifier.py`)

A lightweight Haiku-based four-way classifier that determines message intent for PM routing.

- **Input**: message text, optional conversation context (last 3 messages)
- **Output**: `IntentResult` with `intent` ("teammate", "collaboration", "other", or "work"), `confidence` (0.0-1.0), and `reasoning`
- **Collaboration/Other**: direct tasks the PM can handle without a dev-session; see [PM Routing: Collaboration](pm-routing-collaboration.md)
- **Threshold**: teammate routing requires confidence above `TEAMMATE_CONFIDENCE_THRESHOLD` (0.90)
- **Fail-safe**: any error, timeout, or low confidence defaults to Dev session (current behavior preserved)
- **API**: uses the Anthropic API directly (not Claude Code SDK) for low-latency classification via `MODEL_FAST`

Classification signals:

| Signal | Intent |
|--------|--------|
| Question words (what/where/how/when/who), "status of", "show me", "explain" | Teammate |
| Imperative verbs (fix/add/create/update/deploy/merge), "make it", code snippets | Work |

### Teammate Handler (`agent/teammate_handler.py`)

Provides teammate-specific instructions that replace the PM dispatch block when a message is classified as teammate.

- **Research-first behavior**: instructions prioritize evidence gathering before answering -- search code with Grep/Glob, query memory system, consult docs, then cite findings
- **Tools available**: Read, Glob, Grep, Bash (read-only commands: git log, git status, gh issue view, gh pr list)
- **Tools blocked**: file writes, branch creation, test execution, Agent tool (no Dev session spawning)
- **Nudge cap**: 10 (vs 50 for normal sessions), set via `TEAMMATE_MAX_NUDGE_COUNT`
- **Persona**: same PM persona with teammate-specific additions (conversational tone, cite file paths, direct answers)
- **Delivery**: teammate sessions use the [stop-hook review gate](agent-message-delivery.md) when Telegram-triggered, giving the agent final say over output (SEND/EDIT/REACT/SILENT/CONTINUE). Falls through to the summarizer when no delivery instruction is set.

### Metrics (`agent/teammate_metrics.py`)

Popoto-backed counters for observability (migrated from raw Redis in PR #650). All operations are fire-and-forget -- metrics failures never affect message processing.

- `teammate_classified_count`: messages routed to teammate mode
- `work_classified_count`: messages routed to Dev session
- `teammate_low_confidence_count`: teammate-classified messages below the 0.90 threshold (routed to Dev session)
- Response time tracking: sorted set per mode with time-windowed analysis, capped at 1000 entries
- `get_stats()` returns a summary dict of current counters

### Escape Hatch

No special mechanism needed. Each incoming message is classified independently. If a user asks a question and then follows up with "ok fix that", the follow-up is classified as a work request and routes to Dev session. Session continuity (reply-to threading) ensures the Dev session sees the prior teammate context.

## Integration Points

### `agent/sdk_client.py`

In `_execute_agent_request()`, after determining the session type is "pm" or "teammate":

1. Calls `classify_intent(message)` to get the intent result
2. Calls `record_classification()` to track metrics
3. If `is_teammate` is true, injects teammate instructions via `build_teammate_instructions()` instead of PM dispatch instructions
4. If `is_work` or classifier fails, preserves current behavior exactly

### `bridge/summarizer.py`

Teammate sessions bypass structured formatting entirely:

- `_build_summary_prompt()` appends `persona=teammate` context so the LLM produces conversational prose instead of bullets
- `_compose_structured_summary()` returns the LLM summary directly without emoji prefix, bullet parsing, or structured template
- `SUMMARIZER_SYSTEM_PROMPT` includes a teammate format rule: respond in prose, no bullets, no status emoji

### `agent/output_router.py` + `agent/agent_session_queue.py`

In the output router (`route_session_output()`), checks the `is_teammate` flag:

- If `is_teammate=True`, uses `TEAMMATE_MAX_NUDGE_COUNT` (10) instead of the default `MAX_NUDGE_COUNT` (50)
- Teammate sessions resolve faster; the reduced cap prevents runaway sessions
- On successful completion, teammate sessions clear the processing reaction (set to `None`) instead of setting a completion emoji

## Key Design Decisions

1. **Conservative threshold (0.90)**: false negatives (teammate classified as work) cause no harm -- just unnecessary Dev session spawn. False positives (work classified as teammate) are more costly, so the threshold is high.
2. **First-class session type**: teammate now has its own `SessionType.TEAMMATE` enum value. The bridge routes teammate-persona messages directly to `session_type="teammate"` instead of routing through PM sessions.
3. **No bridge changes**: teammate vs work routing happens entirely in the agent layer. The bridge continues routing all messages to PM session.
4. **No caching**: each message is classified independently for simplicity.

## Key Files

| File | Purpose |
|------|---------|
| `agent/intent_classifier.py` | Haiku-based four-way classifier (teammate/collaboration/other/work) |
| `agent/teammate_handler.py` | Teammate instruction builder (research-first) and nudge cap constant |
| `bridge/summarizer.py` | Teammate prose bypass in `_compose_structured_summary()` and prompt context |
| `agent/teammate_metrics.py` | Popoto-backed classification and response time counters (see [Popoto Index Hygiene](popoto-index-hygiene.md)) |
| `agent/sdk_client.py` | Integration point: classifier call and instruction injection |
| `agent/agent_session_queue.py` | Integration point: reduced nudge cap for teammate sessions |
