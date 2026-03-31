# Intake Classifier

The intake classifier runs Haiku-powered intent classification on every incoming message before routing. It determines whether a message is a follow-up to an active session (interjection), a new work request (new_work), or an approval signal (acknowledgment).

## Problem

Previously, the bridge only caught direct Telegram reply-to messages for steering into active sessions (the "fast path" at line 802 of `telegram_bridge.py`). Messages sent without using Telegram's reply feature -- such as contextual follow-ups, images shared from other apps, or back-to-back messages -- were always treated as new work, even when they clearly belonged to an active session.

## Architecture

```
Message arrives
    |
    v
should_respond_async()
    |
    v
Reply-to fast path (existing, preserved)
    |  direct reply to running session -> push_steering_message()
    |  [returns early if matched]
    |
    v
INTAKE CLASSIFIER (new, #320)
    |  find active/running/dormant sessions in same chat
    |  call classify_message_intent_async() with session context
    |
    +-- interjection -> push to queued_steering_messages + Redis steering queue
    |                   (ack: "Adding to current task")
    |
    +-- acknowledgment -> mark dormant session as completed
    |                     (requires dormant status + expectations set)
    |
    +-- new_work -> fall through to enqueue (current behavior)
    |
    v
enqueue_agent_session() (existing path)
```

## Classification Categories

| Intent | Description | When Used |
|--------|-------------|-----------|
| `interjection` | Follow-up to active work: course correction, additional context, answer to a question | Active/running session exists in same chat |
| `new_work` | New task, question, or request unrelated to active session | Default; also used when uncertain (< 0.80 confidence) |
| `acknowledgment` | Signal that work is done/approved ("LGTM", "ship it", "done") | Only when session is dormant with expectations |

## Key Design Decisions

### Confidence Threshold (0.80)

Interjection and acknowledgment classifications require >= 0.80 confidence. Below that threshold, the message is routed as `new_work`. This prevents false positives from stealing messages away from the new work queue (Risk 2 in the plan).

### Graceful Degradation

If the classifier fails for any reason (API error, invalid response, timeout), the message falls through to the existing enqueue path as `new_work`. Classification failure never blocks message handling. This is implemented via a try/except that catches all exceptions and returns a default `new_work` result.

### Blocking vs Fire-and-Forget

Unlike the existing `classify_request_async()` (which runs fire-and-forget), the intake classifier is **awaited** because routing depends on the result. The latency cost (~100-200ms for Haiku) is acceptable because the `REACTION_RECEIVED` emoji is already set before classification runs.

### Session Matching

For non-reply interjections, the classifier finds the most recent active/running/dormant session in the same chat (by `last_activity` or `created_at`). No multi-session disambiguation -- just pick the most recent one.

As of #619, the classifier also includes **pending** sessions within a 7-second recency window (`PENDING_MERGE_WINDOW_SECONDS`). This allows follow-up messages sent in quick succession to be recognized as interjections into pending sessions, rather than spawning competing sessions. Pending sessions older than 7 seconds are excluded to prevent unrelated messages from attaching to stale jobs.

### Race Condition Mitigation

After classification returns `interjection`, the session status is re-read before pushing the steering message. If the session completed during classification (Race 1), the message falls through to enqueue as `new_work`.

### Acknowledgment Safety

Acknowledgment only marks sessions as completed when:
1. The session status is `dormant` (not running/active)
2. The session has `expectations` set (agent explicitly asked for human input)

This prevents "ok" from accidentally completing a running session (Risk 3).

## Functions

| Function | Location | Purpose |
|----------|----------|---------|
| `classify_message_intent()` | `tools/classifier.py` | Sync Haiku classification of message intent |
| `classify_message_intent_async()` | `tools/classifier.py` | Async version for use in bridge handler |
| `_parse_json_response()` | `tools/classifier.py` | Shared JSON parsing with markdown code block handling |

## Integration Points

| Component | How It Connects |
|-----------|-----------------|
| `bridge/telegram_bridge.py` | Calls `classify_message_intent_async()` after the reply-to fast path |
| `models/agent_session.py` | `push_steering_message()` buffers interjections for Observer |
| `agent/steering.py` | `push_steering_message()` pushes to Redis for PostToolUse hook |
| `agent/agent_session_queue.py` | Nudge loop processes `queued_steering_messages` populated by the intake classifier |

## Testing

30 tests in `tests/test_intake_classifier.py`:

- **Unit tests (fast path)**: Empty messages, no session context, response structure
- **Unit tests (mocked API)**: All three intents, confidence threshold, API failure, invalid types, markdown code blocks
- **Prompt validation**: All intents present, JSON format, context placeholders
- **AgentSession integration**: push/pop steering messages
- **Real Haiku integration**: Classification accuracy for representative messages across all three intent types

## Key Files

| File | Purpose |
|------|---------|
| `tools/classifier.py` | Intent classification functions and prompt |
| `bridge/telegram_bridge.py` | Integration point in the message handler |
| `models/agent_session.py` | `queued_steering_messages` field and push/pop helpers |
| `tests/test_intake_classifier.py` | 30 tests covering all routing paths |
