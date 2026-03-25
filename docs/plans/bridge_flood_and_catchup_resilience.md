---
status: Planning
type: bug
appetite: Small
owner: Valor
created: 2026-03-25
tracking: https://github.com/tomcounsell/ai/issues/510
last_comment_id:
---

# Bridge Flood-Backoff Persistence & Dynamic Catchup Lookback

## Problem

When the bridge hits a Telegram FloodWaitError with a long duration (minutes to hours), launchd restarts compound the problem. Each restart triggers a new connection attempt, which increments Telegram's flood counter, escalating the wait from seconds to hours. Separately, when the bridge recovers after a multi-hour outage, the fixed 60-minute catchup window silently drops messages sent during the full outage period.

**Current behavior:**
1. FloodWaitError > 60s causes the process to eventually exhaust retries and exit. Launchd restarts it immediately, firing another connection attempt that compounds the flood timer. No state persists across restarts.
2. `CATCHUP_LOOKBACK_MINUTES = 60` is hardcoded. After a 4-hour outage, messages older than 60 minutes are silently missed forever.

**Desired outcome:**
1. On FloodWaitError, write a backoff file with expiry timestamp. On startup, check this file and sleep (or refuse to connect) until the flood clears. Launchd restarts become harmless.
2. Persist a "last connected" timestamp. On startup, use this timestamp for catchup lookback instead of a fixed window, ensuring all messages during the outage are scanned.

## Prior Art

- **Issue #509**: Duplicate of #510, closed. Same root cause analysis.
- **PR #194**: Fixed catchup scanner race condition (Redis dedup). Did not address lookback window sizing.
- **PR #78**: Fixed bridge message handling delays after restart. Unrelated to flood or catchup window.
- **Issue #495**: Bridge resilience for dependency outages. Related concept (graceful degradation) but focused on external services, not Telegram rate limiting.

## Data Flow

### Bug 2: FloodWait backoff persistence

1. **Entry**: Bridge starts via launchd → `main()` in `telegram_bridge.py`
2. **New**: Check `data/flood-backoff` file. If exists and expiry > now, sleep until expiry
3. **Connect**: `client.connect()` → Telegram DC
4. **FloodWaitError**: Telegram returns FloodWaitError with `e.seconds`
5. **New**: Write `data/flood-backoff` with `now + e.seconds` as expiry timestamp
6. **Sleep**: `asyncio.sleep(e.seconds + 5)` then retry
7. **On success**: Delete `data/flood-backoff` file

### Bug 3: Dynamic catchup lookback

1. **Entry**: Bridge connects successfully → `_run_catchup()` at line 1569
2. **New**: Read `data/last_connected` timestamp file
3. **Compute cutoff**: `max(last_connected_time, now - timedelta(hours=24))` (24h safety cap)
4. **Scan**: `scan_for_missed_messages()` uses dynamic cutoff instead of fixed 60 min
5. **Periodic heartbeat**: While running, update `data/last_connected` every 5 minutes
6. **Shutdown**: Write final `data/last_connected` timestamp on graceful shutdown

## Architectural Impact

- **New dependencies**: None — uses stdlib `pathlib`, `json`, `datetime`
- **Interface changes**: `scan_for_missed_messages()` gains optional `lookback_override` parameter (backward compatible)
- **Coupling**: No new coupling. Both features use simple file-based state in `data/`
- **Data ownership**: `data/flood-backoff` owned by connection retry logic; `data/last_connected` owned by catchup + main loop
- **Reversibility**: Trivially reversible — delete the file reads and the code falls back to existing behavior

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work uses only stdlib and existing bridge infrastructure.

## Solution

### Key Elements

- **Flood-backoff file** (`data/flood-backoff`): JSON file with `{"expiry_ts": <unix_timestamp>, "seconds": <original_wait>}`. Checked on startup, written on FloodWaitError, deleted on successful connect.
- **Last-connected file** (`data/last_connected`): Plain text file containing ISO 8601 timestamp. Updated periodically via heartbeat and on graceful shutdown. Read by catchup to determine lookback window.
- **Dynamic lookback**: `scan_for_missed_messages()` accepts optional `lookback_override` parameter. Caller computes it from `data/last_connected`. Capped at 24 hours to avoid scanning excessive history.

### Flow

**Startup** → Check flood-backoff → Sleep if needed → Connect → Check last_connected → Compute lookback → Run catchup → Start heartbeat → **Running**

**FloodWaitError** → Write flood-backoff file → Sleep → Retry → On success: delete flood-backoff → **Connected**

**Running** → Heartbeat writes last_connected every 5 min → **Running**

**Shutdown** → Write final last_connected → **Stopped**

### Technical Approach

- Flood-backoff as JSON for extensibility (could add retry count, last error, etc.)
- Last-connected as plain ISO timestamp for simplicity and debuggability
- Heartbeat via `asyncio.create_task` alongside existing periodic tasks
- 24-hour cap on lookback to prevent scanning thousands of messages after very long outages
- All file operations wrapped in try/except — missing files fall back to current behavior

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_read_flood_backoff()` must handle missing file, corrupt JSON, and expired entries gracefully (return None)
- [ ] `_read_last_connected()` must handle missing file, invalid timestamp (return None → fall back to 60 min)
- [ ] Heartbeat task must not crash the bridge if file write fails

### Empty/Invalid Input Handling
- [ ] Empty `data/flood-backoff` file → treated as no backoff
- [ ] `data/last_connected` with empty or whitespace content → fall back to default lookback
- [ ] `data/last_connected` with future timestamp → clamp to now

### Error State Rendering
- [ ] Flood-backoff sleep logs remaining wait time so operators can see it in `bridge.log`
- [ ] Dynamic lookback logs the computed window and source (file vs default)

## Test Impact

- [ ] `tests/integration/test_catchup_revival.py` — UPDATE: tests currently assume fixed 60-min lookback via `CATCHUP_LOOKBACK_MINUTES`. Update to test with `lookback_override` parameter.

## Rabbit Holes

- **Distributed state**: Don't add Redis-based flood state — file-based is correct because the bridge is a single process per machine and launchd restarts need to see the state immediately
- **Smart retry scheduling**: Don't implement a job scheduler to wake the bridge at the exact flood expiry — sleeping in-process is simpler and sufficient
- **Catchup pagination**: Don't increase `MAX_MESSAGES_PER_CHAT` beyond 50 — the 24h cap on lookback keeps the scan bounded

## Risks

### Risk 1: Stale flood-backoff file prevents connection
**Impact:** Bridge refuses to connect even though flood has cleared
**Mitigation:** Always delete the file on successful connect. Add a safety check: if file is older than 24 hours, ignore it.

### Risk 2: Last-connected heartbeat fails silently
**Impact:** After a crash, catchup uses stale timestamp and rescans already-processed messages
**Mitigation:** Redis dedup (`is_duplicate_message`) already prevents double-processing. Worst case is wasted API calls, not duplicate messages.

## Race Conditions

### Race 1: Heartbeat writes during shutdown
**Location:** `telegram_bridge.py` main loop + shutdown handler
**Trigger:** Heartbeat task writes `last_connected` at the same moment the shutdown handler does
**Data prerequisite:** Both write to the same file
**State prerequisite:** Bridge is shutting down
**Mitigation:** Both writes are the same value (current timestamp). Last writer wins. No data corruption risk since writes are atomic (write to temp file + rename).

## No-Gos (Out of Scope)

- **Distributed flood state**: No Redis or cross-machine flood coordination
- **Automatic session re-auth**: If session is truly unauthorized, the fix is manual `telegram_login.py`, not auto-recovery
- **Catchup for DMs**: Only group chats are scanned; DM catchup is a separate feature
- **Backoff file cleanup cron**: The file is self-cleaning (deleted on success, ignored if stale)

## Update System

No update system changes required — the `data/` directory already exists on all machines, and both new files are created on-demand by the bridge. No new dependencies, no config changes, no migration steps.

## Agent Integration

No agent integration required — this is a bridge-internal change. The flood-backoff and last-connected files are managed entirely within `bridge/telegram_bridge.py` and `bridge/catchup.py`. No MCP server exposure needed.

## Documentation

- [ ] Update `docs/features/bridge-self-healing.md` — add flood-backoff persistence and dynamic catchup to the self-healing documentation
- [ ] Add entry to `docs/features/README.md` index table if not already present

## Success Criteria

- [ ] `data/flood-backoff` file written on FloodWaitError, respected on startup, deleted on success
- [ ] `data/last_connected` file written periodically and on shutdown, read by catchup on startup
- [ ] Catchup lookback window computed from last_connected timestamp (capped at 24h)
- [ ] Unit tests cover: flood-backoff read/write/expiry, last-connected read/write, dynamic lookback computation
- [ ] Integration test updated: `test_catchup_revival.py` tests lookback_override path
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (bridge-resilience)**
  - Name: bridge-builder
  - Role: Implement flood-backoff persistence and dynamic catchup lookback
  - Agent Type: builder
  - Resume: true

- **Validator (bridge-resilience)**
  - Name: bridge-validator
  - Role: Verify file persistence, catchup behavior, and edge cases
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Update self-healing docs and feature index
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Implement flood-backoff file persistence
- **Task ID**: build-flood-backoff
- **Depends On**: none
- **Validates**: tests/unit/test_flood_backoff.py (create)
- **Assigned To**: bridge-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `_read_flood_backoff()` and `_write_flood_backoff()` helpers to `bridge/telegram_bridge.py`
- On startup (before connect loop), check `data/flood-backoff` — if valid and not expired, sleep until expiry
- In FloodWaitError catch block, write backoff file before sleeping
- On successful connect, delete backoff file
- Add 24-hour staleness check as safety valve
- Use atomic write (write temp file + `os.replace`) to prevent corruption
- Create `tests/unit/test_flood_backoff.py` with cases: write/read round-trip, expired file ignored, missing file returns None, corrupt JSON returns None, stale file (>24h) ignored

### 2. Implement last-connected timestamp persistence
- **Task ID**: build-last-connected
- **Depends On**: none
- **Validates**: tests/unit/test_last_connected.py (create)
- **Assigned To**: bridge-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `_read_last_connected()` and `_write_last_connected()` helpers
- Write timestamp on successful connect (after "Connected to Telegram" log)
- Add heartbeat task: `asyncio.create_task` that writes every 5 minutes
- Write final timestamp in graceful shutdown handler
- Create `tests/unit/test_last_connected.py` with cases: write/read round-trip, missing file returns None, invalid content returns None, future timestamp clamped

### 3. Wire dynamic lookback into catchup
- **Task ID**: build-dynamic-lookback
- **Depends On**: build-last-connected
- **Validates**: tests/integration/test_catchup_revival.py (update)
- **Assigned To**: bridge-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `lookback_override: timedelta | None = None` parameter to `scan_for_missed_messages()`
- When provided, use it instead of `CATCHUP_LOOKBACK_MINUTES` for cutoff computation
- In `_run_catchup()`, read `data/last_connected`, compute lookback as `now - last_connected` (capped at 24h), pass to scan function
- If no last_connected file, fall back to existing 60-minute default
- Update `tests/integration/test_catchup_revival.py` to test with lookback_override

### 4. Validate implementation
- **Task ID**: validate-all
- **Depends On**: build-flood-backoff, build-last-connected, build-dynamic-lookback
- **Assigned To**: bridge-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify file operations use atomic writes
- Verify all error paths log appropriately
- Verify no import cycles introduced
- Check that missing files fall back gracefully

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/bridge-self-healing.md` with flood-backoff and dynamic catchup sections
- Add/update entry in `docs/features/README.md`

### 6. Final Validation
- **Task ID**: validate-final
- **Depends On**: document-feature
- **Assigned To**: bridge-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all verification commands
- Verify all success criteria met
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Flood backoff tests | `pytest tests/unit/test_flood_backoff.py -v` | exit code 0 |
| Last connected tests | `pytest tests/unit/test_last_connected.py -v` | exit code 0 |
| Catchup integration | `pytest tests/integration/test_catchup_revival.py -v` | exit code 0 |
| Flood backoff helper exists | `grep -c '_read_flood_backoff' bridge/telegram_bridge.py` | output > 0 |
| Last connected helper exists | `grep -c '_read_last_connected' bridge/telegram_bridge.py` | output > 0 |
| Dynamic lookback wired | `grep -c 'lookback_override' bridge/catchup.py` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

No open questions — the issue is well-scoped with clear root causes and straightforward file-based solutions.
