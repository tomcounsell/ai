# Semantic Session Routing

## Overview

Semantic session routing matches unthreaded Telegram messages to active or dormant sessions based on declared expectations. When a session has told the agent "I'm waiting for the user to provide X," and a new message arrives without reply-to threading that matches that expectation, the message is automatically routed to the existing session instead of creating a new one.

This solves a common workflow problem: PM feedback often arrives as fresh messages (image + separate comment, next-day replies, multi-message feedback) without using Telegram's reply-to feature, causing lost context and fragmented conversations.

## How It Works

### Phase 1: Structured Summarizer Output

The summarizer (`bridge/summarizer.py`) now produces structured output via Haiku `tool_use` calls, extracting three fields:

- **context_summary** (max 200 chars): A brief description of what the session is working on. Example: "Building dark mode toggle for settings page"
- **response**: The summarized text sent to Telegram (existing behavior, now structured).
- **expectations** (nullable, max 500 chars): What the agent needs from the human next. Example: "Waiting for feedback on the color palette choices". Null when the session is complete or not waiting for input.

These fields are persisted to the `AgentSession` model after every summarization call (`bridge/response.py`).

### Phase 2: Semantic Router

When a message arrives without reply-to threading, the router (`bridge/session_router.py`) activates:

1. **Candidate query**: Finds active/dormant sessions in the same chat with non-null `expectations` fields.
2. **Zero candidates = zero cost**: If no sessions have expectations, no LLM call is made.
3. **Candidate cap**: At most 5 candidates (sorted by most recent activity) are considered.
4. **Classifier prompt**: Haiku evaluates whether the incoming message is responding to one of the candidate sessions' expectations.
5. **Confidence threshold**: Only matches with confidence >= 0.80 are auto-routed. Below that, a new session is created (current behavior preserved).
6. **Graceful degradation**: Any failure (API error, parse error, invalid session ID) silently falls through to new session creation.

### Feature Flag

Semantic routing is disabled by default. Enable via environment variable:

```bash
SEMANTIC_ROUTING=true  # or "1" or "yes" (case-insensitive)
```

When disabled, the system behaves exactly as before -- all non-reply messages create new sessions.

## Architecture

```
Message arrives (no reply-to)
    |
    v
is_semantic_routing_enabled()? --NO--> Create new session (current behavior)
    |
    YES
    v
find_matching_session(chat_id, message_text, project_key)
    |
    v
Query AgentSession: active/dormant with non-null expectations
    |
    v
0 candidates? --YES--> Return (None, 0.0) -- no LLM cost
    |
    NO (up to 5)
    v
Haiku classifier: "Is this message responding to a session's expectations?"
    |
    v
confidence >= 0.80? --YES--> Route to matched session
    |
    NO
    v
Create new session (current behavior)
```

## Model Fields

Two fields added to `AgentSession` (`models/agent_session.py`):

| Field | Type | Max Length | Description |
|-------|------|-----------|-------------|
| `context_summary` | `Field(null=True)` | 200 | Brief description of session's current work |
| `expectations` | `Field(null=True)` | 500 | What the agent needs from the human next |

These are nullable Popoto/Redis fields. No migration is needed -- Redis is schemaless, so existing sessions simply have `None` for these fields.

## Summarizer Changes

The summarizer was upgraded from plain text output to structured extraction:

- **Primary path**: Haiku `tool_use` with `structured_summary` tool schema returning `StructuredSummary` dataclass
- **Fallback within Haiku**: If tool_use fails, falls back to text-only Haiku response
- **Secondary fallback**: OpenRouter (replaces Ollama) with the same structured extraction attempt
- **Final fallback**: Truncation (unchanged)

The `StructuredSummary` dataclass:

```python
@dataclass
class StructuredSummary:
    context_summary: str
    response: str
    expectations: str | None
```

The `SummarizedResponse` now carries `context_summary` and `expectations` fields through to the persistence layer in `bridge/response.py`.

## Persistence

After summarization succeeds (`bridge/response.py`), routing fields are saved to the session:

```python
if session and summarized.context_summary:
    session.context_summary = summarized.context_summary
if session and summarized.expectations is not None:
    session.expectations = summarized.expectations
elif session:
    session.expectations = None  # Clear stale expectations
session.save()
```

This is non-fatal -- save failures are caught and logged without affecting message delivery.

## Confidence Threshold

The routing confidence threshold is set at 0.80 (`ROUTING_CONFIDENCE_THRESHOLD` in `session_router.py`):

- **>= 0.80**: High confidence. Auto-route to the matched session.
- **< 0.80**: Uncertain. Create a new session (preserves current behavior).
- **Medium confidence disambiguation (0.50-0.80)**: Deferred to a future phase. Would present the user with "Did you mean to reply to session X?" options.

The threshold is intentionally conservative. False positives (routing to the wrong session) are worse than false negatives (creating a new session), since the user can always reply-to the correct message to resume context.

## Files Changed

| File | Change |
|------|--------|
| `models/agent_session.py` | Added `context_summary` and `expectations` fields |
| `bridge/summarizer.py` | Structured `tool_use` output, `StructuredSummary` dataclass, OpenRouter fallback (replaces Ollama) |
| `bridge/session_router.py` | New module: `find_matching_session()`, `is_semantic_routing_enabled()` |
| `bridge/response.py` | Persist routing fields after summarization |
| `bridge/telegram_bridge.py` | Integrate semantic router in non-reply-to message handling |
| `tests/test_summarizer.py` | Updated mocks for `StructuredSummary` returns and OpenRouter fallback |

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `SEMANTIC_ROUTING` | disabled | Feature flag: set to `true`, `1`, or `yes` to enable |
| `ROUTING_CONFIDENCE_THRESHOLD` | 0.80 | Minimum confidence for auto-routing (code constant) |

## Testing

All 120 summarizer tests pass. The semantic router is tested through the existing test infrastructure. Key test updates:

- Mock returns changed from `str` to `StructuredSummary` objects
- Ollama fallback tests renamed to OpenRouter fallback tests
- All `_summarize_with_ollama` patches replaced with `_summarize_with_openrouter`
