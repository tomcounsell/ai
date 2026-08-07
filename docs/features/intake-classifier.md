# Intake Classifier

The intake classifier runs intent classification on every incoming non-reply message before routing, on the **local granite model via PydanticAI** (`agent.llm.run_typed_local`, durability plan #2494). It determines whether a message is a follow-up to an active session (`interjection`) or a new work request (`new_work`).

The former third class, `acknowledgment`, retired with the `AgentSession.expectations` field: Jobs — never hard-closed, revived by any reply — replace session-level acknowledgment semantics (see [`durability-model.md`](durability-model.md)). The taxonomy is two-class.

## Problem

The bridge's reply-to fast path only catches messages sent with Telegram's reply feature. Messages sent without it — contextual follow-ups, images shared from other apps, back-to-back messages — would otherwise always be treated as new work, even when they clearly belong to an active session.

## Architecture

```
Message arrives
    |
    v
should_respond_async()
    |
    v
Durable Room-inbox append + shadow Job routing (bridge/room_inbox.py, #2494)
    |  the message is durable BEFORE any classifier runs — classifier
    |  latency/failure is a UX cost, never a durability cost
    |
    v
Reply-to fast path (preserved)
    |  direct reply to running session -> push_steering_message()
    |  [returns early if matched]
    |
    v
INTAKE CLASSIFIER (#320, granite via run_typed_local)
    |  find active/running/dormant sessions in same chat
    |  call classify_message_intent_async() with session context
    |
    +-- interjection -> push to the Redis steering queue (agent/steering.py)
    |                   (ack: 👀 reaction on user's message, or 🫡 for abort keywords)
    |
    +-- new_work -> fall through to enqueue (default)
    |
    v
enqueue_agent_session() (existing path)
```

## Classification Categories

| Intent | Description | When Used |
|--------|-------------|-----------|
| `interjection` | Follow-up to active work: course correction, additional context, answer to a question | Active/running session exists in same chat |
| `new_work` | New task, question, or request unrelated to active session | Default; also used when uncertain (below threshold) or on any classifier failure |

## Key Design Decisions

### Local granite via `run_typed_local`

The call goes through the non-harness LLM wrapper's Ollama leg (`agent.llm.run_typed_local`) with the strict `IntentDecision` Pydantic output model (`intent: Literal["interjection", "new_work"]`, `confidence`, `reason`) — schema-validated by PydanticAI, no hand-rolled JSON parsing. See [`nonharness-llm-wrapper.md`](nonharness-llm-wrapper.md). Spike-3 measured 84.2% raw / ~95% behavioral agreement vs the prior Haiku classifier on replayed real traffic.

### Confidence Threshold (`INTENT_CONFIDENCE_THRESHOLD`, provisional 0.80, env-overridable)

An `interjection` verdict below the threshold routes as `new_work`. This prevents false positives from stealing messages away from the new work queue.

### Graceful Degradation

Any classifier failure (Ollama unreachable, schema exhaustion, invalid confidence) falls through to the enqueue path as `new_work` — classification failure never blocks message handling. The message is already durable in the Room inbox before the classifier runs, so a failure costs routing precision, never the message.

### Session Matching

For non-reply interjections, the classifier finds the most recent active/running/dormant session in the same chat (by `updated_at` or `created_at`). No multi-session disambiguation — just pick the most recent one.

As of #619, the classifier also includes **pending** sessions within an 8-second recency window (`PENDING_MERGE_WINDOW_SECONDS`). This allows follow-up messages sent in quick succession to be recognized as interjections into pending sessions, rather than spawning competing sessions. For sub-200ms arrivals (before the Redis write completes), the in-memory coalescing guard (`_recent_session_by_chat` in `bridge/telegram_bridge.py`) bridges the visibility gap.

### Race Condition Mitigation

After classification returns `interjection`, the session status is re-read before pushing the steering message. If the session completed during classification, the message falls through to enqueue as `new_work`.

## Functions

| Function | Location | Purpose |
|----------|----------|---------|
| `classify_message_intent_async()` | `tools/classifier.py` | Granite intent classification via `run_typed_local` (async only) |
| `IntentDecision` | `tools/classifier.py` | Strict Pydantic output model for the classifier call |

## Integration Points

| Component | How It Connects |
|-----------|-----------------|
| `bridge/telegram_bridge.py` | Calls `classify_message_intent_async()` after the reply-to fast path |
| `agent/steering.py` | `push_steering_message()` buffers interjections and pushes them to the Redis steering list |
| `agent/session_executor.py` | Turn-boundary drain (`pop_all_steering_messages()`) consumes messages populated by the intake classifier |
| `bridge/job_router.py` | The Job bind-or-mint router runs on the same granite/`run_typed_local` substrate at the Room-inbox seam |

## Testing

`tests/unit/test_intake_classifier.py`:

- **Fast path**: empty messages and missing session context classify as `new_work` with no model call (a monkeypatched `run_typed_local` explodes if reached)
- **Mocked verdicts**: both intents, threshold behavior (below/at), invalid confidence and model failure fail open to `new_work`
- **Prompt validation**: both intents present, context placeholders, `acknowledgment` asserted absent
- **Live granite** (skipped unless Ollama with granite is reachable): accuracy smoke on a clear interjection

## Key Files

| File | Purpose |
|------|---------|
| `tools/classifier.py` | `IntentDecision`, prompt, and `classify_message_intent_async()` |
| `bridge/telegram_bridge.py` | Integration point in the message handler |
| `agent/steering.py` | Redis steering list and push/pop/peek helpers |
| `tests/unit/test_intake_classifier.py` | Unit + reachability-gated live coverage |
