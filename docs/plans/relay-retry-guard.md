---
status: Draft
type: bug
appetite: Small
owner: Valor
created: 2026-04-05
tracking: https://github.com/tomcounsell/ai/issues/698
last_comment_id:
---

# Relay Retry Guard: Bounded Retries for Outbox Messages

> The Telegram relay re-queues undeliverable messages infinitely, blocking session outbox queues and stalling agent sessions. Add bounded retries, type validation, and dead-letter routing so poisoned messages are evicted after a configurable number of attempts.

## Problem

**Current behavior:**

The relay's `process_outbox()` in `bridge/telegram_relay.py` unconditionally re-pushes failed messages to the queue tail (line 382-387). Messages that are structurally undeliverable -- wrong type, missing required fields, unrecognized format -- will never succeed no matter how many retries. This creates an infinite retry loop that:

1. **Blocks the session's outbox queue** -- the poisoned message cycles endlessly, preventing subsequent messages from delivering
2. **Stalls agent sessions** -- sessions waiting on delivery never complete (observed: sessions 405, 410, 411, 414, 415, 416 stuck 7+ hours before manual kill)
3. **Wastes resources** -- the relay polls every 100ms, reprocessing the same undeliverable messages

The specific trigger was `custom_emoji_message` payloads that fell through to `_send_queued_message()`, which rejected them (no `text` or `file_paths` field) and returned `None`, causing infinite re-queue.

**Additional failure modes:**

- `reaction` and `custom_emoji_message` handlers silently drop failures (no re-queue), while default messages re-queue forever. The asymmetry means some message types get zero retries and others get infinite retries.
- If a handler raises an unexpected exception, it could bypass the `continue` statement and fall through to the re-queue path, creating a different infinite loop flavor.

## Solution

Four changes to `bridge/telegram_relay.py`, all within `process_outbox()` and its helpers:

### 1. Inject retry counter into re-queued messages

Add a `MAX_RELAY_RETRIES` constant (default 3). Before re-pushing a failed message, increment an `_relay_attempts` field in the JSON payload. If `_relay_attempts >= MAX_RELAY_RETRIES`, route to dead letter instead of re-queuing.

```
# Constants
MAX_RELAY_RETRIES = 3

# In process_outbox, replace the unconditional re-queue:
attempts = message.get("_relay_attempts", 0) + 1
message["_relay_attempts"] = attempts
if attempts >= MAX_RELAY_RETRIES:
    # Route to dead letter queue
    await _dead_letter_message(message, reason="max retries exceeded")
else:
    raw = json.dumps(message)
    await asyncio.to_thread(r.rpush, key, raw)
```

### 2. Validate message type before dispatch

After JSON parsing, check the message type against an explicit allowlist. Unknown types are logged and discarded immediately (no retry).

```
KNOWN_MESSAGE_TYPES = {None, "reaction", "custom_emoji_message"}

msg_type = message.get("type")
if msg_type not in KNOWN_MESSAGE_TYPES:
    logger.warning(f"Relay: unknown message type '{msg_type}', discarding: {message}")
    continue
```

### 3. Wrap all handler paths in try/except

Each handler dispatch (`_send_queued_reaction`, `_send_custom_emoji_message`, `_send_queued_message`) already has internal exception handling, but the dispatch logic in `process_outbox()` does not. Wrap the entire dispatch block so that an unexpected exception in any handler feeds into the bounded retry path rather than crashing or falling through.

### 4. Unify failure handling across message types

Currently, `reaction` and `custom_emoji_message` failures silently continue (no retry), while default messages retry infinitely. Unify: all message types use the same bounded-retry logic. Each handler returns a success indicator; failures go through the shared retry/dead-letter path.

Add a helper function `_dead_letter_message()` that calls the existing `persist_failed_delivery()` from `bridge/dead_letters.py` for text messages, and logs+discards for non-text types (reactions, emoji) since those are not worth replaying.

### Implementation detail

- The `_relay_attempts` field is injected into the JSON payload itself, so it survives serialization/deserialization through Redis. This avoids any external state tracking.
- The dead letter path uses the existing `DeadLetter` Popoto model (`models/dead_letter.py`) which already has `chat_id`, `text`, `reply_to`, and `attempts` fields.
- For reactions and custom emoji messages that exhaust retries, log at WARNING level and discard (these are ephemeral and not worth dead-lettering).

## Tasks

- [ ] Add `MAX_RELAY_RETRIES = 3` constant to `bridge/telegram_relay.py`
- [ ] Add `KNOWN_MESSAGE_TYPES` allowlist set to `bridge/telegram_relay.py`
- [ ] Add `_dead_letter_message()` async helper that routes text messages to `bridge/dead_letters.py:persist_failed_delivery()` and discards non-text types with a warning log
- [ ] Refactor `process_outbox()` dispatch: add type validation early-exit for unknown types
- [ ] Refactor `process_outbox()` dispatch: wrap each handler call in try/except, feed exceptions into the retry path
- [ ] Refactor `process_outbox()` re-queue path: inject `_relay_attempts`, check against `MAX_RELAY_RETRIES`, route to dead letter or re-queue
- [ ] Unify `reaction` and `custom_emoji_message` failure paths to use the same bounded-retry logic as default messages
- [ ] Unit tests: retry counter increments correctly across re-queue cycles
- [ ] Unit tests: message is dead-lettered after exceeding max retries
- [ ] Unit tests: unknown message type is discarded without re-queue
- [ ] Unit tests: handler exception is caught and feeds into retry path
- [ ] Unit tests: successful messages are unaffected (no behavioral change)
- [ ] Unit tests: reactions and emoji messages use bounded retry on failure

## Success Criteria

- [ ] Messages that fail delivery are retried at most 3 times (configurable via `MAX_RELAY_RETRIES` constant)
- [ ] After max retries, text messages are routed to the dead letter queue via `bridge/dead_letters.py`; reactions and emoji messages are logged and discarded
- [ ] Unknown message types (not in `KNOWN_MESSAGE_TYPES` allowlist) are detected and dropped without entering the retry loop
- [ ] Handler exceptions in any message type path are caught and feed into the bounded retry path, never causing infinite re-queue
- [ ] All three message type paths (reaction, custom_emoji_message, default) use the same unified retry/dead-letter flow
- [ ] No behavioral change for messages that succeed on first try
- [ ] Unit tests cover: retry exhaustion, unknown type handling, handler exception recovery, unified failure path for all message types

## Scope

### In Scope

- `bridge/telegram_relay.py` -- all changes live here
- `bridge/dead_letters.py` -- called by the new dead-letter routing, no changes needed
- `tests/unit/test_bridge_relay.py` -- new test cases

### Out of Scope

- Modifying the `DeadLetter` model schema (it already has the fields we need)
- Adding a dead letter replay mechanism for relay messages (the existing `replay_dead_letters()` handles this)
- Changing the relay poll interval or batch size
- Adding alerting or metrics for dead-lettered messages (future work)
- Changes to `tools/send_telegram.py` (the producer side is fine)

## No-Gos

- Do not add external dependencies
- Do not change the Redis key pattern or queue contract
- Do not modify the `DeadLetter` Popoto model
- Do not add configurable retry count via environment variable (hardcoded constant is sufficient for now)
- Do not retry reactions or emoji messages differently from text messages (unified path)

## Update System

No update system changes required. This is a bridge-internal bug fix that changes runtime behavior of an existing module. No new dependencies, no config files, no migration steps. The fix deploys automatically with `git pull && restart`.

## Agent Integration

No agent integration required. The relay is a bridge-internal async task that processes Redis queues. No MCP servers, no tool wrappers, and no `.mcp.json` changes needed. The agent interacts with the relay indirectly by pushing messages to Redis via `tools/send_telegram.py`, which is unchanged.

## Failure Path Test Strategy

Each failure mode maps to a specific test:

| Failure Mode | Test Strategy |
|-------------|--------------|
| Retry exhaustion | Mock `_send_queued_message` to return `None` N times, verify dead letter called on Nth attempt |
| Unknown type | Push message with `type: "bogus"`, verify discarded without re-queue |
| Handler exception | Mock handler to raise `Exception`, verify caught and retry path entered |
| Malformed JSON | Already tested (existing `test_skips_malformed_json`) |
| Dead letter persistence failure | Mock `persist_failed_delivery` to raise, verify message is still discarded (not re-queued) |
| Mixed success/failure in batch | Push 3 messages (success, fail, success), verify correct counts and only the failure retries |

## Test Impact

- [ ] `tests/unit/test_bridge_relay.py::TestProcessOutbox::test_requeues_on_send_failure` -- UPDATE: the re-queued JSON will now include `_relay_attempts` field; assert on the serialized payload
- [ ] `tests/unit/test_bridge_relay.py::TestProcessOutbox::test_processes_queued_messages` -- no change expected, but verify it still passes (success path unchanged)

The remaining existing tests (`TestRelayConstants`, `TestSendQueuedMessage`, `TestRecordSentMessage`, `TestGetOutboxLength`) are unaffected since they test lower-level functions that are not modified.

## Rabbit Holes

- **Exponential backoff**: Tempting to add delay between retries, but the relay processes all queues in a single loop -- adding per-message delays would stall the entire relay. The bounded retry count is sufficient; if a message fails 3 times in rapid succession, it is genuinely undeliverable.
- **Per-session retry tracking**: Tracking retries in a separate Redis hash (keyed by message ID) would be cleaner but adds complexity. Embedding `_relay_attempts` in the payload is simpler and survives serialization.
- **Atomic retry-or-dead-letter**: The lpop + conditional rpush is not atomic, but the relay is single-consumer so there is no race condition.

## Documentation

- [ ] Create `docs/features/relay-retry-guard.md` describing the bounded retry behavior, dead letter routing, and type validation
- [ ] Update the docstring at the top of `bridge/telegram_relay.py` to mention the retry limit and dead letter routing
