---
status: Draft
type: bugfix
appetite: Small
owner: Valor
created: 2026-03-30
tracking: https://github.com/tomcounsell/ai/issues/588
---

# Periodic Message Reconciliation for Live Bridge Gaps

## Problem

The Telegram bridge silently misses messages during a live connection. Telethon can drop updates when the Telegram server delivers them out of sequence or the client misses a `pts` (persistent timeline sequence) gap. The current reliability mechanisms only cover reconnect/restart scenarios:

- `catch_up=True` replays on reconnect only
- Startup catchup scan (`bridge/catchup.py`) runs once at boot
- Dedup checks prevent re-processing but cannot detect messages that never arrived
- Session watchdog monitors stalled SDK sessions, not missed messages

Evidence from issue #588: message 8198 in Agent Builders Chat was never delivered to the handler despite the bridge being alive with consistent heartbeats. Zero trace in logs -- the Telethon `NewMessage` event simply never fired.

## Scope

Add a periodic reconciliation loop that runs every few minutes during the bridge's lifetime, reusing the existing `bridge/catchup.py` scanning pattern. The loop detects messages that were never delivered to the event handler and re-dispatches them through the normal pipeline.

| Component | Change |
|-----------|--------|
| `bridge/reconciler.py` | New module: periodic reconciliation loop |
| `bridge/telegram_bridge.py` | Register reconciler as background task |
| `bridge/catchup.py` | Extract shared helper for message scanning (minor refactor) |
| `tests/unit/test_reconciler.py` | Unit tests for gap detection logic |
| `tests/integration/test_reconciler.py` | Integration test for gap detection and recovery |

**Not in scope:** Changing `sequential_updates` setting, implementing raw update handlers for pts gaps, or modifying the dedup TTL/capacity.

## Prior Art

- `bridge/catchup.py` -- startup gap scanner. The reconciler reuses the same pattern: fetch recent messages from monitored groups, compare against dedup records, re-dispatch unprocessed messages. The key difference is that catchup runs once at startup while the reconciler runs periodically.
- `monitoring/session_watchdog.py` -- established pattern for a periodic background loop registered as an `asyncio.create_task` in the bridge startup sequence.
- `bridge/dedup.py` / `models/dedup.py` -- dedup interface with `is_duplicate_message()` and `record_message_processed()`. The reconciler gates all re-dispatches through this.

## Data Flow

1. **Reconciler wakes up** every N minutes (configurable, default 3 minutes)
2. **For each monitored group**, fetches last M messages via `client.get_messages()` (M = configurable, default 20)
3. **Compares message IDs** against dedup records (`is_duplicate_message()`)
4. **For unprocessed messages**: checks `should_respond_async()` routing logic
5. **Dispatches** qualifying messages via `enqueue_job()` with `priority="low"`
6. **Records** dispatched messages in dedup to prevent future re-dispatch
7. **Logs** reconciliation results: messages scanned, gaps found, messages recovered

```
heartbeat_loop (every 30s)
    |
reconciler_loop (every 3min)
    |
    +-- for each monitored group:
    |       get_messages(limit=20)
    |       for each message:
    |           is_duplicate? --> skip
    |           is outgoing? --> skip
    |           should_respond? --> enqueue_job(priority="low")
    |           record_message_processed()
    |
    +-- log summary: "Reconciled N groups, recovered M messages"
```

## Architectural Impact

- **New module**: `bridge/reconciler.py` (~120 lines). Self-contained async loop with clear inputs.
- **Interface reuse**: Uses existing `should_respond_async()`, `enqueue_job()`, `is_duplicate_message()`, and `record_message_processed()` -- no new interfaces needed.
- **API cost**: One `get_messages(limit=20)` call per monitored group per interval. With 5 groups at 3-minute intervals, that is ~100 API calls/hour -- well within Telethon rate limits.
- **Coupling**: The reconciler depends on the same functions that `catchup.py` depends on. No new coupling introduced.
- **Reversibility**: Removing the reconciler is a single `asyncio.create_task` deletion in `telegram_bridge.py`.

## Appetite

**Size:** Small (one new module, one registration line, tests)

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

The reconciler is a straightforward adaptation of the existing catchup scanner into a periodic loop. Most of the complexity is already handled by existing functions.

## Prerequisites

No prerequisites. All required interfaces exist.

## Solution

### Key Elements

1. **`bridge/reconciler.py`** -- New module containing the reconciliation loop
2. **Registration in `telegram_bridge.py`** -- Start the loop as a background task alongside other loops
3. **Shared scanning logic** -- Reuse pattern from `bridge/catchup.py` but with a tighter lookback window and smaller message limit

### Technical Approach

#### 1. Create `bridge/reconciler.py`

The reconciler is a periodic async loop that scans monitored groups for messages that were not delivered to the event handler.

```python
"""Periodic message reconciliation: detect and recover missed messages during live connection."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)

# Configuration
RECONCILE_INTERVAL_SECONDS = 180  # 3 minutes
RECONCILE_LOOKBACK_MINUTES = 10   # Look back 10 minutes
RECONCILE_MESSAGE_LIMIT = 20      # Messages per group per scan


async def reconciler_loop(
    client,
    monitored_groups: list[str],
    should_respond_fn,
    enqueue_job_fn,
    find_project_fn,
):
    """Run periodic reconciliation to detect missed messages.

    Scans monitored groups for messages that bypassed the event handler.
    Gates all re-dispatches through dedup to prevent duplicate processing.
    """
    logger.info(
        "[reconciler] Started (interval=%ds, lookback=%dm, limit=%d)",
        RECONCILE_INTERVAL_SECONDS,
        RECONCILE_LOOKBACK_MINUTES,
        RECONCILE_MESSAGE_LIMIT,
    )

    while True:
        await asyncio.sleep(RECONCILE_INTERVAL_SECONDS)
        try:
            recovered = await reconcile_once(
                client=client,
                monitored_groups=monitored_groups,
                should_respond_fn=should_respond_fn,
                enqueue_job_fn=enqueue_job_fn,
                find_project_fn=find_project_fn,
            )
            if recovered > 0:
                logger.warning(
                    "[reconciler] Recovered %d missed message(s)", recovered
                )
            else:
                logger.debug("[reconciler] Scan complete, no gaps found")
        except Exception as e:
            logger.error("[reconciler] Error in reconciliation: %s", e, exc_info=True)
```

The `reconcile_once()` function follows the same pattern as `scan_for_missed_messages()` in `catchup.py` but with a shorter lookback window and smaller message limit. It iterates monitored groups, fetches recent messages, checks dedup, runs routing logic, and enqueues missed messages.

Key differences from the startup catchup:
- **Shorter lookback** (10 min vs 60 min) -- only needs to cover a few reconciliation intervals
- **Smaller message limit** (20 vs 50) -- fewer messages to check per scan
- **Runs continuously** -- not just once at startup
- **Debug-level logging** when no gaps found -- avoids log spam on the normal path
- **Warning-level logging** when gaps are recovered -- signals a real gap was detected

#### 2. Register in `telegram_bridge.py`

Add the reconciler alongside the existing background tasks (watchdog, heartbeat, relay, etc.):

```python
# Start message reconciler (detects live-session gaps)
try:
    from bridge.reconciler import reconciler_loop

    asyncio.create_task(reconciler_loop(
        client=client,
        monitored_groups=ALL_MONITORED_GROUPS,
        should_respond_fn=should_respond_async,
        enqueue_job_fn=_enqueue_job,
        find_project_fn=find_project_for_chat,
    ))
    logger.info("Message reconciler started")
except Exception as e:
    logger.error(f"Failed to start message reconciler: {e}")
```

#### 3. Refactor shared scanning logic (optional)

The core message-scanning logic in `catchup.py` (lines 62-209) and the reconciler share the same pattern: get dialogs, match monitored groups, fetch messages, check dedup, check routing, enqueue. Rather than duplicating this, extract a shared helper function that both can call with different parameters (lookback, limit, log prefix). This is a minor refactor -- if it adds complexity, the reconciler can simply inline the logic.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `reconcile_once()` catches and logs per-group errors without stopping the scan of other groups
- [ ] `reconciler_loop()` catches and logs all exceptions without crashing the loop
- [ ] Telethon API errors (e.g., `FloodWaitError`) are caught and the loop continues after the next interval

### Empty/Invalid Input Handling
- [ ] Empty `monitored_groups` list results in a no-op scan (no crash)
- [ ] Group not found in dialogs is logged and skipped
- [ ] Messages with no text are skipped
- [ ] Outgoing messages (our own) are skipped

### Error State Rendering
- [ ] No user-visible errors -- reconciler is internal bridge infrastructure
- [ ] Recovered messages are logged at WARNING level for observability

## Test Impact

No existing tests affected -- this is a new module (`bridge/reconciler.py`) with no changes to existing interfaces. The existing `tests/unit/test_catchup.py` and `tests/unit/test_dedup.py` tests remain unchanged since the reconciler uses the same interfaces without modifying them.

## Rabbit Holes

- **Extracting a shared scanner from catchup.py**: Nice to have but not required. If the refactor adds complexity or creates merge conflicts, skip it and inline the scanning logic in the reconciler. The two scanners have different parameters and slightly different behavior (startup vs periodic).
- **Configurable interval via projects.json**: Over-engineering for the initial version. Hard-code the interval; make it configurable later if needed.
- **Reconciling DMs**: Only reconcile monitored groups. DMs use a different delivery path and are less likely to be affected by pts gaps.
- **Adjusting `sequential_updates`**: Tempting but orthogonal. Changing this setting affects Telethon's internal behavior in ways that are hard to test. The reconciler works regardless of this setting.
- **Modifying dedup TTL or capacity**: The current 2-hour TTL and 50-message capacity are sufficient for the reconciler's 10-minute lookback. Do not change these.

## Risks

### Risk 1: Reconciler causes FloodWaitError from Telegram
**Impact:** Bridge gets rate-limited by Telegram, affecting real-time message delivery.
**Mitigation:** Use a conservative interval (3 min) and small message limit (20 per group). Monitor for `FloodWaitError` in reconciler logs. If hit, back off exponentially.

### Risk 2: Reconciler re-dispatches a message that is currently being processed
**Impact:** Duplicate processing, duplicate response in Telegram.
**Mitigation:** The dedup check (`is_duplicate_message()`) gates all re-dispatches. The normal handler records messages in dedup before processing, so the reconciler will see them as already processed. The `_check_if_handled()` pattern from catchup.py provides an additional safety net.

### Risk 3: Reconciler adds latency to the event loop
**Impact:** Slow down real-time message handling during reconciliation scans.
**Mitigation:** `get_messages()` is async and yields to the event loop. The scan processes at most `N_groups * 20` messages, each with a fast Redis lookup. Total scan time should be under 1 second.

## Race Conditions

**Concurrent handler + reconciler processing the same message**: The handler records in dedup before enqueuing. The reconciler checks dedup before enqueuing. If both see the message simultaneously before either records it, both could enqueue. Mitigation: the job queue itself handles duplicate session IDs gracefully (the second enqueue is a no-op because the session already exists). This is a benign race -- worst case is a duplicate enqueue that gets deduplicated downstream.

## No-Gos (Out of Scope)

- Changing `sequential_updates` Telethon setting
- Implementing raw update handlers for pts gap detection
- Modifying dedup TTL, capacity, or model structure
- Reconciling DMs (only group messages)
- Adding user-facing configuration for reconciliation interval
- Modifying the startup catchup behavior

## Update System

No update system changes required -- this adds a new Python module with no new dependencies. The reconciler starts automatically with the bridge. No config file changes, no migration steps.

## Agent Integration

No agent integration required -- the reconciler is internal bridge infrastructure. It uses existing message routing and job queue interfaces. No MCP server changes, no new tools, no `.mcp.json` modifications.

## Documentation

- [ ] Create `docs/features/message-reconciler.md` documenting the reconciliation loop, its configuration constants, and how it complements the startup catchup and dedup systems
- [ ] Add entry to `docs/features/README.md` index table

## Success Criteria

- [ ] Missed messages in monitored groups are detected and processed within 2 reconciliation intervals (~6 minutes)
- [ ] Reconciliation loop runs every 3 minutes without impacting normal message handling
- [ ] Reconciliation results are logged: groups scanned, messages checked, messages recovered
- [ ] No duplicate processing: dedup check gates all reconciled messages
- [ ] Integration test covers gap detection and recovery path
- [ ] `bridge.log` shows `[reconciler] Started` on bridge startup
- [ ] Tests pass (`/do-test`)

## Team Orchestration

### Team Members

- **Builder (bridge-message-reconciler)**
  - Name: reconciler-builder
  - Role: Create reconciler module, register in bridge, write tests
  - Agent Type: builder
  - Resume: true

## Step by Step Tasks

### 1. Create bridge/reconciler.py module
- **Task ID**: create-reconciler
- **Depends On**: none
- **Validates**: tests/unit/test_reconciler.py
- **Assigned To**: reconciler-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `bridge/reconciler.py` with `reconciler_loop()` and `reconcile_once()` functions
- `reconcile_once()` scans all monitored groups, compares against dedup, re-dispatches missed messages
- Follow the same pattern as `bridge/catchup.py` but with shorter lookback and smaller limit
- Use existing `is_duplicate_message()`, `record_message_processed()`, `should_respond_async()`, `enqueue_job()`
- Log at DEBUG when no gaps found, WARNING when messages are recovered

### 2. Create unit tests for reconciler
- **Task ID**: create-unit-tests
- **Depends On**: create-reconciler
- **Validates**: tests/unit/test_reconciler.py
- **Assigned To**: reconciler-builder
- **Agent Type**: builder
- **Parallel**: true
- Test `reconcile_once()` with mocked client, dedup, and routing functions
- Test: message already in dedup is skipped
- Test: outgoing message is skipped
- Test: message with no text is skipped
- Test: message that fails routing check is skipped
- Test: qualifying missed message is enqueued and recorded in dedup
- Test: per-group errors do not stop scan of other groups
- Test: empty monitored_groups results in no-op

### 3. Register reconciler in telegram_bridge.py
- **Task ID**: register-reconciler
- **Depends On**: create-reconciler
- **Validates**: bridge starts with reconciler task
- **Assigned To**: reconciler-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `asyncio.create_task()` call for `reconciler_loop()` in the bridge startup sequence
- Place after the catchup scan and before the heartbeat loop
- Pass `client`, `ALL_MONITORED_GROUPS`, `should_respond_async`, `enqueue_job`, `find_project_for_chat`

### 4. Create integration test for gap detection and recovery
- **Task ID**: create-integration-test
- **Depends On**: create-reconciler, register-reconciler
- **Validates**: tests/integration/test_reconciler.py
- **Assigned To**: reconciler-builder
- **Agent Type**: builder
- **Parallel**: false
- Simulate a gap: set up dedup with messages 1-5, provide messages 1-7 from mock client
- Verify messages 6 and 7 are detected as missed
- Verify they are dispatched through enqueue_job
- Verify they are recorded in dedup after dispatch
- Verify a second reconcile_once() call finds no new gaps

### 5. Validate and lint
- **Task ID**: validate-all
- **Depends On**: create-integration-test
- **Assigned To**: reconciler-builder
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_reconciler.py -x -q`
- Run `pytest tests/integration/test_reconciler.py -x -q`
- Run `python -m ruff check bridge/reconciler.py tests/unit/test_reconciler.py tests/integration/test_reconciler.py`
- Run `python -m ruff format --check bridge/reconciler.py`

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: reconciler-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/message-reconciler.md`
- Update `docs/features/README.md` index table

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `pytest tests/unit/test_reconciler.py -x -q` | exit code 0 |
| Integration test passes | `pytest tests/integration/test_reconciler.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check bridge/reconciler.py` | exit code 0 |
| Format clean | `python -m ruff format --check bridge/reconciler.py` | exit code 0 |
| Module importable | `python -c "from bridge.reconciler import reconciler_loop, reconcile_once"` | no error |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

No open questions. The approach directly adapts the proven `catchup.py` pattern into a periodic loop. The only design choice is whether to extract shared scanning logic from `catchup.py` -- the plan treats this as optional to avoid scope creep.
