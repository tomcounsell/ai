# Message Reconciler

Periodic background loop that detects and recovers Telegram messages missed during a live bridge connection.

## Problem

Telethon can silently drop updates when the Telegram server delivers them out of sequence or the client misses a `pts` (persistent timeline sequence) gap. Existing reliability mechanisms only cover restart and startup scenarios:

- `catch_up=True` replays on reconnect
- `bridge/catchup.py` scans once at boot
- Dedup checks prevent re-processing but cannot detect messages that never arrived

The reconciler fills the gap by scanning continuously while the bridge is alive.

## How It Works

The reconciler runs as an `asyncio.create_task` background loop inside the bridge, alongside the heartbeat and session watchdog.

### Scan Cycle

Every 3 minutes (configurable via `RECONCILE_INTERVAL_SECONDS`):

1. Fetches recent messages from each monitored group via `client.get_messages()`
2. Filters to messages within the lookback window (default 10 minutes)
3. Checks each message against dedup records (`is_duplicate_message()`)
4. Skips outgoing messages, empty-text messages, and messages that fail routing (`should_respond_async()`)
5. Enqueues qualifying missed messages via `enqueue_agent_session()` with `priority="low"`
6. Records dispatched messages in dedup to prevent future re-dispatch

### Data Flow

```
reconciler_loop (every 3min)
    |
    +-- for each monitored group:
    |       get_messages(limit=20)
    |       for each message:
    |           outside lookback window? --> stop scanning group
    |           is outgoing? --> skip
    |           no text? --> skip
    |           is_duplicate? --> skip
    |           should_respond? no --> skip
    |           enqueue_agent_session(priority="low")
    |           record_message_processed()
    |
    +-- log summary: "Scanned N group(s), recovered M message(s)"
```

## Configuration

| Constant | Default | Purpose |
|----------|---------|---------|
| `RECONCILE_INTERVAL_SECONDS` | 180 (3 min) | Time between scans |
| `RECONCILE_LOOKBACK_MINUTES` | 10 | How far back each scan looks |
| `RECONCILE_MESSAGE_LIMIT` | 20 | Max messages fetched per group per scan |

These are module-level constants in `bridge/reconciler.py`. They are not exposed in `projects.json` or `.env` -- adjust by editing the source.

## Logging

| Level | Condition |
|-------|-----------|
| INFO | Reconciler started (once at boot) |
| DEBUG | Scan complete, no gaps found (normal path) |
| WARNING | One or more missed messages recovered |
| ERROR | Exception during scan (loop continues) |

Log lines are prefixed with `[reconciler]` for filtering:

```bash
grep reconciler logs/bridge.log
```

## Relationship to Other Components

| Component | Relationship |
|-----------|-------------|
| `bridge/catchup.py` | Catchup scans once at startup with a longer lookback (up to 24h). The reconciler scans continuously with a shorter 10-minute window. Both use the same dedup and routing interfaces. |
| `bridge/dedup.py` | The reconciler gates all re-dispatches through `is_duplicate_message()` and records recoveries via `record_message_processed()`. |
| `monitoring/session_watchdog.py` | The session watchdog monitors stalled SDK sessions. The reconciler monitors missed Telegram messages. Different failure modes, same background-loop pattern. |
| Bridge self-healing | The reconciler complements crash recovery (watchdog, catchup) by covering a gap that only manifests during a live, healthy connection. |

## Race Conditions

A message could arrive at the event handler and the reconciler simultaneously before either records it in dedup. The session queue handles duplicate session IDs gracefully (second enqueue is a no-op), so this is a benign race with no user-visible effect.

## API Cost

One `get_messages(limit=20)` call per monitored group per interval. With 5 groups at 3-minute intervals, that is approximately 100 API calls per hour -- well within Telethon rate limits.

## Files

| File | Purpose |
|------|---------|
| `bridge/reconciler.py` | Reconciliation loop and single-scan function |
| `bridge/telegram_bridge.py` | Registers reconciler as background task |
| `tests/unit/test_reconciler.py` | Unit tests for gap detection logic |
| `tests/integration/test_reconciler.py` | Integration test for end-to-end recovery |

## Related

- [Bridge Self-Healing](bridge-self-healing.md) -- crash recovery, watchdog, catchup lookback
- [Bridge Module Architecture](bridge-module-architecture.md) -- bridge sub-module organization
- [Message Pipeline](message-pipeline.md) -- deferred enrichment and zero-loss restart
