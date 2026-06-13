# Semantic Session Routing

## Overview

Semantic session routing matches unthreaded Telegram messages to active or dormant sessions based on declared expectations. When a session has told the agent "I'm waiting for the user to provide X," and a new message arrives without reply-to threading that matches that expectation, the message is automatically routed to the existing session instead of creating a new one.

This solves a common workflow problem: PM feedback often arrives as fresh messages (image + separate comment, next-day replies, multi-message feedback) without using Telegram's reply-to feature, causing lost context and fragmented conversations.

## How It Works

### Phase 1: Drafter Routing Fields

The message drafter (`bridge/message_drafter.py`, formerly `bridge/summarizer.py` — renamed per [#1035](https://github.com/tomcounsell/ai/issues/1035)) derives two routing hint fields deterministically on every delivery pass — no LLM call:

- **context_summary** (max 140 chars): First non-blank, non-heading line of the narration-stripped agent output, capped at a word boundary. Example: "Building dark mode toggle for settings page". Populated by `_derive_context_summary()`.
- **expectations** (nullable): Open questions extracted from a `## Open Questions` section in the agent's output. Example: "Waiting for feedback on the color palette choices". `None` when the section is absent or empty, never `""`.

These fields are persisted to the `AgentSession` model after every delivery via `_persist_routing_fields` in `agent/output_handler.py`. (The old `response` field was part of the removed `StructuredDraft` dataclass — it is not persisted; the delivered text flows through the outbox directly.)

### Phase 2: Semantic Router

When a message arrives without reply-to threading, the router (`bridge/session_router.py`) activates:

1. **Candidate query**: Finds active/dormant sessions in the same chat with non-null `expectations` fields.
2. **Zero candidates = zero cost**: If no sessions have expectations, no LLM call is made.
3. **Candidate cap**: At most 5 candidates (sorted by most recent activity) are considered.
4. **Classifier prompt**: Haiku evaluates whether the incoming message is responding to one of the candidate sessions' expectations.
5. **Confidence threshold**: Only matches with confidence >= 0.80 are auto-routed. Below that, a new session is created (current behavior preserved).
6. **Graceful degradation**: Any failure (API error, parse error, invalid session ID) silently falls through to new session creation.

### Always-On (No Feature Flag)

Semantic routing is always enabled. The `SEMANTIC_ROUTING` environment variable and `is_semantic_routing_enabled()` function were removed in issue #705. The router runs on every non-reply message with zero-cost short-circuit when no sessions have expectations.

## Architecture

```
Message arrives (no reply-to)
    |
    v
Check in-memory coalescing guard (_recent_session_by_chat)
    |
    v
Recent session for this chat? --YES--> Push to queued_steering_messages + ack
    |
    NO
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
confidence >= 0.80? --YES--> Check matched session status
    |                              |
    NO                             v
    v                    running/active? --YES--> Push to steering queue + ack
Create new session           |
(current behavior)           NO (dormant/other)
                             v
                        Resume session (use session_id)
```

### Phase 3: Active Session Steering (#318)

When semantic routing matches an unthreaded message to a session that is currently **running or active**, the message is pushed to the session's steering queue (`agent/steering.py`) instead of creating a competing session. The Observer picks up the message at its next checkpoint.

**Decision matrix:**

| Session Status | Match Confidence | Action |
|---|---|---|
| running/active | >= 0.80 | Push to `queued_steering_messages` via `push_steering_message()`, send ack |
| dormant | >= 0.80 | Resume session using `session_id` (existing behavior) |
| any | < 0.80 | Create new session (existing behavior) |

**User feedback:** When a message is steered into an active session, the bridge attaches an emoji reaction directly to the user's message — 👀 (`REACTION_RECEIVED`) for the standard steer path, 🫡 (`REACTION_ABORT`) for abort keywords (`stop`, `cancel`, `abort`, `nevermind`). No inline text reply is emitted; the eventual agent response arrives as a normal PM-authored message in the thread.

**Implementation** (`bridge/telegram_bridge.py`): After `find_matching_session()` returns a match, the bridge loads the `AgentSession` and checks its status. Active sessions get `push_steering_message()` + early return. Dormant/other sessions fall through to existing behavior. Any failure in the active session check falls through gracefully with a warning log.

## Model Fields

Two fields added to `AgentSession` (`models/agent_session.py`):

| Field | Type | Max Length | Description |
|-------|------|-----------|-------------|
| `context_summary` | `Field(null=True)` | 200 | Brief description of session's current work |
| `expectations` | `Field(null=True)` | 500 | What the agent needs from the human next |

These are nullable Popoto/Redis fields. No migration is needed -- Redis is schemaless, so existing sessions simply have `None` for these fields.

## Drafter Changes

The message drafter was initially upgraded from plain text output to Haiku-based structured extraction (the `StructuredDraft` dataclass era). That architecture was later replaced by a deterministic pass-through. The `StructuredDraft` dataclass, `STRUCTURED_DRAFT_TOOL` schema, `_draft_with_haiku`, and `_draft_with_openrouter` have all been removed.

`context_summary` and `expectations` now come from `_derive_context_summary()` and `_extract_open_questions()` respectively — both are deterministic Python functions with no LLM dependency. `MessageDraft` carries these fields and they are persisted via `_persist_routing_fields` in `agent/output_handler.py` after every delivery (not just after LLM calls).

## Persistence

After drafting, routing fields are saved to the session via `_persist_routing_fields` in `agent/output_handler.py`:

```python
if session is not None and draft is not None:
    if draft.context_summary:
        session.context_summary = draft.context_summary
    if draft.expectations is not None:
        session.expectations = draft.expectations
    else:
        session.expectations = None  # Clear stale expectations
    session.save()
```

Persistence is non-fatal — save failures are caught and logged without affecting message delivery. Fields are now persisted on **every** drafting pass (not only after LLM calls), so `context_summary` and `expectations` are populated even for short-output pass-throughs and teammate sessions.

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
| `bridge/message_drafter.py` (née `bridge/summarizer.py`) | Deterministic `_derive_context_summary()` and `_extract_open_questions()` for routing fields (LLM rewrite machinery removed) |
| `bridge/session_router.py` | Semantic router: `find_matching_session()` (always-on, no feature flag) |
| `agent/output_handler.py` | `_persist_routing_fields` persists `context_summary` and `expectations` after every delivery |
| `bridge/telegram_bridge.py` | Integrate semantic router in non-reply-to message handling; active session steering (#318) |
| `tests/unit/test_message_drafter.py` | Updated for pass-through validation and deterministic routing field extraction |
| `tests/test_unthreaded_routing.py` | Decision matrix tests: active steering, dormant passthrough, abort detection, FIFO ordering, missing session fallthrough |

## In-Memory Coalescing Guard

The in-memory coalescing guard (`_recent_session_by_chat`) bridges the Redis visibility gap for rapid-fire messages (issue #705). When two messages arrive within ~200ms, the second message cannot find the first's session in Redis because `AgentSession.async_create()` hasn't completed yet.

**How it works:**

1. A module-level dict `_recent_session_by_chat: dict[str, tuple[str, float]]` maps `chat_id` to `(session_id, timestamp)`.
2. Just before `enqueue_agent_session()`, the dict is set with the new session_id and current timestamp.
3. When the next message arrives, the dict is checked first (before Redis). If a recent session exists within `PENDING_MERGE_WINDOW_SECONDS` (8s), the message is pushed to `queued_steering_messages` on the existing session.
4. Stale entries (older than the merge window) are lazily cleaned up on each check.
5. If the `AgentSession` doesn't exist in Redis yet (Race 2), a single retry after 200ms is attempted. If still missing, falls through to normal session creation.

**Key properties:**

- Process-local (not cross-process), which is fine since the bridge is a single asyncio process
- Bounded by active chat count (at most one entry per chat)
- Wrapped in try/except -- failures silently fall through to normal session creation
- Complements (not replaces) the Redis-based pending merge check

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `ROUTING_CONFIDENCE_THRESHOLD` | 0.80 | Minimum confidence for auto-routing (code constant) |
| `PENDING_MERGE_WINDOW_SECONDS` | 8 | Time window for coalescing messages into existing sessions |

## Testing

The semantic router is tested through the existing test infrastructure. The drafter tests cover deterministic routing field extraction (`_derive_context_summary`, `_extract_open_questions`) without LLM mocks.

### Unthreaded Routing Tests (`tests/test_unthreaded_routing.py`)

7 tests covering the active session steering decision matrix:

- **Active session steering**: `push_steering_message()` queues message, verifiable via `pop_all_steering_messages()`
- **Abort detection**: Abort keywords (`stop`, `cancel`, `abort`, `nevermind`) set `is_abort=True` on steered messages
- **FIFO ordering**: Multiple unthreaded messages queue in order
- **Dormant passthrough**: Dormant sessions do not receive steering messages (resumed via session_id instead)
- **Missing session fallthrough**: If matched session_id no longer exists in Redis, falls through to normal routing
