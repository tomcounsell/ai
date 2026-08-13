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

#### Group/supergroup-safe guard

Group and supergroup chat IDs are legitimately negative integers (e.g. `-1003900483201`); only `chat_id == 0` is an invalid Telegram peer, and it causes `PeerIdInvalidError` in a loop. Five sites guard against it:

| Site | Function | Behavior on `chat_id == 0` |
|------|----------|----------------------------|
| Send — text/files | `_send_queued_message` | dropped, WARNING |
| Send — reactions | `_send_queued_reaction` | dropped, WARNING |
| Send — custom emoji messages | `_send_custom_emoji_message` | dropped, WARNING |
| Persist | `_dead_letter_message` | discarded, WARNING |
| Replay | `replay_dead_letters` (`dead_letters.py`) | deleted on next bridge startup |

The three send paths share one helper, `_deliverable_peer(chat_id, kind, message)`, which also separates the two drop reasons by log level: a local session id (`local-<uuid>`) is routine and logs at DEBUG, while a zero peer is the chatless-session placeholder leaking into an outbound payload and logs at WARNING. Before #2644 only the text/file path was guarded; the other two reached Telethon and raised (#2644).

All five derive the peer from `utils.peer.numeric_peer`, which is the single home for this parse across the source side too. It is deliberately stricter than a bare `int()` — it rejects `"+5"`, `5.9`, and `True` — so re-deriving the check locally makes the relay and the source side disagree about the same payload. That is not hypothetical: while the replay side parsed locally, `int("+5")` was 5, so a stored record with `chat_id="+5"` was replayed to peer 5 while every send path dropped it. Only legacy rows can carry such forms now that the persist side rejects them on the way in, and replay is exactly where they surface.

Two sites keep their own log wording while sharing the parse: `_dead_letter_message` (it discards a stored record rather than dropping a send, and logs only the chat_id rather than the whole payload) and `replay_dead_letters` (its message is about discarding a stored record on startup). In the replay guard, unparseable and zero collapse into one branch — `numeric_peer` returns `None` for the former, which the old `except -> 0` was already folding into the latter.

The persist and replay guards must stay in lockstep — narrowing only one side is a no-op, because a negative-chat_id record persisted by the relay side will be deleted by the replay side on the next bridge startup.

#### `cleanup_file` honoring at DLQ placement

When the payload carries `cleanup_file: True` (set by `valor-telegram send
--cleanup-after-send`, used by `/do-debrief`), the relay calls
`_safe_unlink(path)` for each file path on the payload at DLQ placement
time — **not** just on success. This makes the relay the sole owner of
temp-file lifecycle across the asynchronous retry boundary: the producer
(`/do-debrief`) pushes the payload and exits, and the relay deletes the
file whether the send eventually succeeds or terminally fails. Synchronous
deletion by the producer would race the retry loop and trip the
"file not found at send time" branch. `_safe_unlink` swallows missing-file
errors so cleanup never raises. See [TTS](tts.md#temp-file-ownership-the-load-bearing-detail)
for the full rationale.

### FloodWait Handling

`FloodWaitError` raised by Telethon during a relay send is caught as a first-class case in `process_outbox`, separate from the bounded-retry path:

1. Sleeps `min(flood_err.seconds + RELAY_FLOOD_WAIT_BUFFER_SECS, RELAY_FLOOD_WAIT_MAX_SLEEP_SECS)`.
2. Increments `message["_flood_waits"]` (a separate counter, not `_relay_attempts`).
3. Re-queues via `RPUSH` without touching `_relay_attempts`.
4. Dead-letters with reason `flood_backstop` after `RELAY_FLOOD_WAIT_MAX` consecutive flood events.

This keeps flood events out of the retry budget. Each inner send helper also re-raises `FloodWaitError` before any broad `except Exception` block so it propagates correctly to the process-outbox handler.

This is the send-path flood handler; the connect-loop flood handler in `telegram_bridge.py` is distinct and also calls `_write_flood_backoff` to throttle reconnects — the relay handler deliberately omits that side-effect.

### Unified Failure Handling

All three message type paths (reaction, custom_emoji_message, default text) use the same bounded-retry logic. Handler dispatch is wrapped in try/except so unexpected exceptions feed into the retry path rather than crashing or falling through.

## Configuration

| Constant | Default | Env override | Description |
|----------|---------|--------------|-------------|
| `MAX_RELAY_RETRIES` | 3 | `MAX_RELAY_RETRIES` | Maximum delivery attempts before dead-lettering |
| `KNOWN_MESSAGE_TYPES` | `{None, "reaction", "custom_emoji_message"}` | — | Accepted message types |
| `RELAY_FLOOD_WAIT_BUFFER_SECS` | 5 | `RELAY_FLOOD_WAIT_BUFFER_SECS` | Seconds added to Telegram's requested wait before resuming |
| `RELAY_FLOOD_WAIT_MAX_SLEEP_SECS` | 300 | `RELAY_FLOOD_WAIT_MAX_SLEEP_SECS` | Hard ceiling on a single flood-wait sleep |
| `RELAY_FLOOD_WAIT_MAX` | 10 | `RELAY_FLOOD_WAIT_MAX` | Consecutive flood events before dead-lettering with reason `flood_backstop` |

## Files

- `bridge/telegram_relay.py` -- all relay implementation
- `bridge/dead_letters.py` -- `persist_failed_delivery`, `replay_dead_letters`; the replay-side guard must stay in lockstep with the persist-side guard in `telegram_relay.py`
- `tests/unit/test_bridge_relay.py` -- unit tests covering all retry/dead-letter paths
- `tests/unit/test_dead_letters.py` -- unit tests for dead-letter persist and replay

## See Also

- [Bridge Worker Architecture](bridge-worker-architecture.md) — full detail on the four relay defects patched in issue #1749: file-send idempotency, oversized-text guard for file+text messages, group/supergroup-safe dead-letter (lockstep guards), and send-path FloodWait handling.
