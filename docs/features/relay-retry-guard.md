# Relay Retry Guard

Bounded retry and dead-letter routing for the Telegram relay's outbox message processing.

## Problem

The relay in `bridge/telegram_relay.py` previously re-queued failed messages to the queue tail unconditionally. Messages that were structurally undeliverable (wrong type, missing fields, unrecognized format) would never succeed, creating an infinite retry loop that blocked session outbox queues and stalled agent sessions.

## How It Works

### Bounded Retries

Each message gets a `_relay_attempts` counter embedded in the JSON payload. On failure, the counter increments and the message is re-queued. After `MAX_RELAY_RETRIES` (default 3) failed attempts, the message is routed to the dead letter queue instead of re-queuing.

### Type Validation

Before dispatch, each message's `type` field is checked against `KNOWN_MESSAGE_TYPES` (`None`, `"reaction"`, `"custom_emoji_message"`). Unknown types are logged and discarded immediately without entering the retry loop.

### Dead Letter Routing

The `_dead_letter_message()` helper routes exhausted messages based on type:

- **Text messages** (type=None): persisted to `bridge/dead_letters.py` via `persist_failed_delivery()` for later replay
- **Reactions and custom emoji messages**: logged at WARNING level and discarded (ephemeral, not worth replaying)

### Unified Failure Handling

All three message type paths (reaction, custom_emoji_message, default text) use the same bounded-retry logic. Handler dispatch is wrapped in try/except so unexpected exceptions feed into the retry path rather than crashing or falling through.

## Configuration

| Constant | Default | Description |
|----------|---------|-------------|
| `MAX_RELAY_RETRIES` | 3 | Maximum delivery attempts before dead-lettering |
| `KNOWN_MESSAGE_TYPES` | `{None, "reaction", "custom_emoji_message"}` | Accepted message types |

## Files

- `bridge/telegram_relay.py` -- all implementation changes
- `bridge/dead_letters.py` -- called by dead-letter routing (no changes)
- `tests/unit/test_bridge_relay.py` -- unit tests covering all retry/dead-letter paths
