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
    |                   context-recall advisory rides as a SEPARATE steering
    |                   message pushed at the back (#2694)
    |
    +-- new_work -> fall through to enqueue (default)
    |               context-recall advisory rides on extra_context (#2694)
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

### Context-recall advisory (#2694)

The classifier also judges whether the message leans on conversation the session cannot see — a bare `"yes"`, `"go ahead"`, `"not that one"`, `"the second one"`. Before #2694 that judgment was formed and then discarded; only `intent` was branched on. Now the same pass returns two extra fields, `context_recall_advised` and `context_recall_reason`, and a positive verdict hands the PM a fully-formed `valor-telegram read` command with the real chat id already interpolated. The judgment is the model's, not a keyword allowlist (Development Principle 3).

**This edge ships dark.** `CONTEXT_RECALL_INBOUND_ENABLED` defaults to `false` because the 3B local model has not cleared an accuracy bar: a first prompt scored 5/10 and missed every canonical positive, while a few-shot revision fixed all 8 positives but over-triggered on 5 of 6 self-contained messages. Prompt and schema are chosen together — with the switch off, the classifier uses the original prompt and the original `IntentDecision`, so the dark path costs nothing.

Delivery differs per branch because the two branches have different reachability:

| Branch | Mechanism | Why not the other one |
|--------|-----------|-----------------------|
| `new_work` | Rides `extra_context["context_recall_advisory"]`, prepended to the turn in `agent/session_executor.py` | The session does not exist yet, so there is nothing to steer |
| `interjection` | A **separate** steering message pushed at the **back** (`front=False`) | The target session already read its `extra_context` when it was picked up; mutating it is a no-op |

Two constraints on the interjection branch are load-bearing. The advisory is never appended to the human's text, because abort detection matches the human's string exactly (`agent/steering.py`) and concatenating anything onto a bare `"stop"` would silently destroy abort. And it goes at the back rather than the front, because the cold-path drain consumes only `steering_msgs[0]` and re-queues the rest — a front-pushed advisory would displace the human's own message for that turn.

On the `new_work` branch the advisory is prepended **before** the injection-screen banner, never after. `build_risk_banner` is an open-ended prefix with no closing delimiter, so its untrusted zone runs to the end of the prompt; anything placed after the delimiter would be both distrusted by the PM and forgeable by an attacker. The embedded model-authored reason is sanitized through `bridge.injection_inspection._sanitize_reason` before interpolation, which is what actually earns the pre-banner position.

Both fields carry a safe default on **every** return path, including the fail-open `except` branch, so callers never `KeyError`. The advisory itself is best-effort: a failure to build it is logged and dropped, and the human's message is unaffected.

Inbound scope is Telegram only. The intake classifier has no email caller — `bridge/email_bridge.py` never invokes it. The outbound half of #2694 lives in [Context-Recall Advisory](context-recall-advisory.md).

## Functions

| Function | Location | Purpose |
|----------|----------|---------|
| `classify_message_intent_async()` | `tools/classifier.py` | Granite intent classification via `run_typed_local` (async only) |
| `IntentDecision` | `tools/classifier.py` | Strict Pydantic output model for the classifier call |
| `IntentDecisionWithRecall` | `tools/classifier.py` | `IntentDecision` plus `context_recall_advised` / `context_recall_reason`; used only when `CONTEXT_RECALL_INBOUND_ENABLED` is on, paired with the extended prompt (#2694) |
| `build_context_recall_advisory()` | `bridge/context_recall.py` | Composes the advisory text with the real chat id interpolated, or returns `None` (#2694) |

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
| `tools/classifier.py` | `IntentDecision`, `IntentDecisionWithRecall`, prompts, and `classify_message_intent_async()` |
| `bridge/telegram_bridge.py` | Integration point in the message handler; `_build_context_recall_advisory_for_intent` and `_merge_context_recall_into_extra_overrides` (#2694) |
| `bridge/context_recall.py` | Advisory builder and kill switches shared by both edges (#2694) |
| `agent/session_executor.py` | Prepends the `new_work` advisory ahead of the injection banner (#2694) |
| `agent/steering.py` | Redis steering list and push/pop/peek helpers |
| `tests/unit/test_intake_classifier.py` | Unit + reachability-gated live coverage |
| `tests/unit/test_context_recall_wiring.py` | Both inbound delivery branches, banner ordering, and abort survival (#2694) |
