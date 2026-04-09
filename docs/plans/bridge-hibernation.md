---
status: docs_complete
type: feature
appetite: Medium
owner: valorengels
created: 2026-04-09
tracking: https://github.com/tomcounsell/ai/issues/840
last_comment_id:
---

# Bridge Hibernation: Structured Recovery for Telegram Auth and Connectivity Failures

## Problem

The bridge (`bridge/telegram_bridge.py`) has no distinction between two fundamentally different failure modes:

1. **Auth expiry** — Telegram session token expired or revoked; requires `python scripts/telegram_login.py` by a human. The bridge cannot self-recover.
2. **Transient connectivity** — network blip, DC migration, short Telegram outage. Launchd restart + Telethon reconnect handles this automatically.

**Current behavior:**
- All failures hit the same 8-attempt retry loop → `SystemExit(1)` → watchdog restart → infinite restart loop
- Auth expiry produces a log line ("Run 'python scripts/telegram_login.py'") only visible to log tailers
- Worker continues executing sessions whose output goes to `FileOutputHandler` (logs/worker/) and is never re-delivered when the bridge reconnects
- No flag signal exists to tell humans the bridge is waiting for authentication

**Desired outcome:**
- Bridge detects auth expiry (vs. transient loss) at both startup and runtime
- Bridge writes a `data/bridge-auth-required` flag file and stops restart-looping
- Worker continues running; `FileOutputHandler` captures output during bridge downtime
- macOS notification fires (via `osascript`) when manual authentication is needed
- When bridge reconnects, buffered output from `logs/worker/` is replayed to Telegram
- Flag file is cleared on successful re-authentication

## Prior Art

No prior closed issues or merged PRs address bridge hibernation or auth-expiry differentiation. The flood-backoff feature established the flag-file persistence pattern (`data/flood-backoff`) that this plan reuses.

## Data Flow

### Failure path: auth expiry at startup

1. **Watchdog restarts bridge** → `telegram_bridge.py` starts
2. **`connect_with_retry()`** calls `client.is_user_authorized()`
3. **Auth error detected** → `enter_hibernation()` writes `data/bridge-auth-required` → fires macOS notification → `SystemExit(2)` (distinct exit code)
4. **Watchdog sees flag file on next 60s check** → suppresses restart loop → logs "bridge hibernating: auth required"
5. **Worker continues** executing queued sessions → `FileOutputHandler` writes output to `logs/worker/{session_id}.log`

### Failure path: auth expiry during live session

1. **Telethon fires auth exception** inside or after `run_until_disconnected()`
2. **Bridge catches** `AuthKeyUnregisteredError` / `SessionExpiredError` / `SessionRevokedError` → `is_auth_error()` returns True
3. **Same hibernation path**: `enter_hibernation()` → notification → `SystemExit(2)`

### Recovery path: human re-authenticates

1. **Human runs** `python scripts/telegram_login.py` → session file updated
2. **Human restarts** bridge: `./scripts/valor-service.sh restart`
3. **Bridge startup** clears `data/bridge-auth-required` on successful `is_user_authorized()` check
4. **Buffered output replay**: bridge reads pending `logs/worker/*.log` files (last 24h) → re-delivers to Telegram with timestamp prefix

## Architectural Impact

- **New dependencies**: None. `osascript` is always available on macOS. Flag file reuses existing `data/` directory.
- **Interface changes**: Watchdog must check for flag file before restarting. Minor addition to `monitoring/bridge_watchdog.py`.
- **Coupling**: Adds weak coupling via flag file between bridge and watchdog. Worker is unaffected.
- **Data ownership**: `data/bridge-auth-required` owned by bridge (writer) and watchdog (reader/suppressor).
- **Reversibility**: All changes are additive. No schema changes.

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1-2
- Review rounds: 1

## Prerequisites

No external service prerequisites.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `data/` dir exists | `test -d data/` | Flag file location |
| `osascript` available | `which osascript` | macOS notification delivery |

## Solution

### Key Elements

- **`bridge/hibernation.py`**: New module with `is_auth_error()`, `enter_hibernation()`, `exit_hibernation()`, `is_hibernating()`, `replay_buffered_output()`
- **Auth classifier**: Maps permanent Telethon error types to hibernation; transient errors pass through to existing retry loop
- **Hibernation signal**: `data/bridge-auth-required` flag file written atomically (same pattern as `data/flood-backoff`)
- **Exit code 2**: Bridge exits with code 2 on auth failure so watchdog can distinguish from crash
- **Watchdog hibernation mode**: Checks flag file before Level 1 restart; if present, skips restart and logs hibernation state
- **Output replay**: On successful reconnect, bridge scans `logs/worker/*.log` files (last 24h, skipping files modified in last 5min) and re-delivers to Telegram

### Flow

**Auth expiry detected** → `is_auth_error()` returns True → `enter_hibernation()` writes flag + fires osascript → `SystemExit(2)`

**Watchdog check** → flag file present → skips restart → logs "hibernating: run python scripts/telegram_login.py"

**Human re-authenticates** → `./scripts/valor-service.sh restart` → `is_user_authorized()` succeeds → `exit_hibernation()` clears flag → `replay_buffered_output()` → normal operation

### Technical Approach

- **`bridge/hibernation.py`** module:
  - `is_auth_error(exc) -> bool`: permanent auth types → True; transient → False
  - `enter_hibernation()`: atomic write to `data/bridge-auth-required`; fire `osascript` notification; log error
  - `exit_hibernation()`: delete `data/bridge-auth-required` if present
  - `is_hibernating() -> bool`: check flag file existence
  - `replay_buffered_output(client, max_age_hours=24)`: parse `logs/worker/*.log`, send via Telegram client with header `"--- Buffered output from [timestamp] ---"` before each session's replayed content. Max age hardcoded at 24h (no env var — easy to add later if needed).

- **Permanent auth errors** (→ hibernation): `AuthKeyUnregisteredError`, `AuthKeyError`, `AuthKeyInvalidError`, `AuthKeyPermEmptyError`, `SessionExpiredError`, `SessionRevokedError`, `UnauthorizedError`

- **Transient errors** (→ existing retry loop): `NetworkMigrateError`, `ConnectionError`, `OSError`, `FloodWaitError`

- **`bridge/telegram_bridge.py`** changes:
  - `connect_with_retry()`: after `is_user_authorized() == False`, call `enter_hibernation()` and `sys.exit(2)`
  - Exception handlers: wrap with `is_auth_error()` check → `enter_hibernation()` + `sys.exit(2)` if permanent
  - On successful connect: `exit_hibernation()` then `replay_buffered_output(client)`

- **`monitoring/bridge_watchdog.py`** changes:
  - Before Level 1 restart: check `Path("data/bridge-auth-required").exists()`
  - If flag present: log hibernation state, skip restart, set health status to "hibernating"
  - `--check-only` output: include hibernation state

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `enter_hibernation()` must not raise if `data/` directory is missing or read-only — catch, log warning, continue
- [ ] `replay_buffered_output()` must skip unreadable/malformed log files — catch per-file, log warning, continue
- [ ] `osascript` subprocess call wrapped in try/except (non-macOS, permission denied)
- [ ] Tests assert `logger.warning` is called with observable string for each failure path

### Empty/Invalid Input Handling
- [ ] `is_auth_error(None)` returns False (not TypeError)
- [ ] `replay_buffered_output()` with empty `logs/worker/` returns 0, no crash
- [ ] Log file with no `chat=` line is skipped gracefully

### Error State Rendering
- [ ] `bridge.log` shows "Bridge hibernating: auth required. Run python scripts/telegram_login.py" when hibernation entered
- [ ] macOS notification text is human-readable with exact command
- [ ] Watchdog `--check-only` output shows "hibernating" when flag present

## Test Impact

- [ ] `tests/unit/test_bridge_watchdog.py` — UPDATE: add tests for hibernation state detection; test that Level 1 restart is suppressed when flag file present; test "hibernating" log message
- [ ] `tests/unit/test_bridge_logic.py` — UPDATE: add test that auth errors in startup retry call `enter_hibernation()` and exit with code 2
- [ ] `tests/unit/test_bridge_hibernation.py` (new file) — CREATE: full unit test coverage for `bridge/hibernation.py`

No existing test cases need DELETE or REPLACE — changes are additive.

## Rabbit Holes

- **Redis-based output buffering**: `FileOutputHandler` already writes to disk — no need to duplicate to Redis. Adds complexity with no benefit.
- **Two-way replay deduplication**: Checking Redis TelegramMessage records to avoid duplicate sends is tempting but risky (Redis records may not exist for all sessions). Use simpler 5-minute recency window instead.
- **Cross-machine distributed hibernation**: Each machine's bridge hibernates independently. Do not build shared state.
- **Automatic re-auth**: Headless Telegram login code entry is ToS-risky and fragile. Scope is detect + notify only.
- **Replay of output older than 24h**: Sessions older than a day don't need re-delivery.

## Risks

### Risk 1: Replay sends duplicate messages
**Impact:** User sees same output twice if bridge reconnects while a session is still active.
**Mitigation:** Skip log files modified in the last 5 minutes. Add a `.replayed` marker file after successful replay of a log. Never replay a file with `.replayed` marker.

### Risk 2: macOS notification fails silently
**Impact:** Human not notified; bridge hibernates without visible alert.
**Mitigation:** `osascript` failure is non-fatal and logged. `bridge.log` always contains hibernation message. Watchdog also logs hibernation state. Multiple fallback signals.

### Risk 3: Log file parsing fragility
**Impact:** Malformed entries cause garbled replay messages.
**Mitigation:** `FileOutputHandler` controls the log format (`[timestamp] chat=X reply_to=Y\n{text}\n---`). Parse known format only; unknown blocks are skipped with a warning.

## Race Conditions

### Race 1: Worker writes to log file while bridge replays it
**Location:** `bridge/hibernation.py::replay_buffered_output()` + `agent/output_handler.py::FileOutputHandler.send()`
**Trigger:** Bridge reconnects and replays while worker session is still writing to same log file
**Data prerequisite:** Log file must not be actively written during replay
**State prerequisite:** N/A
**Mitigation:** Skip files modified in last 5 minutes. Worker output is append-only so partial reads are safe.

### Race 2: Watchdog checks flag file during bridge exit
**Location:** `monitoring/bridge_watchdog.py` + `bridge/hibernation.py::enter_hibernation()`
**Trigger:** Watchdog reads flag file between bridge writing it and bridge process fully exiting
**Mitigation:** Flag file presence is sufficient signal regardless of timing. Watchdog acts on the next 60s cycle, so any transient state resolves naturally.

## No-Gos (Out of Scope)

- Anthropic API failures and worker hibernation (#839)
- Automatic re-authentication
- Cross-machine distributed hibernation state
- SMS/email fallback notification channels
- Replay of output older than 24 hours

## Update System

No update script changes needed. `data/bridge-auth-required` is a runtime state file, not configuration. The new `bridge/hibernation.py` module is imported by the bridge — no new env vars or config files need propagating to other machines.

## Agent Integration

No agent integration required. This is bridge-internal resilience. The agent executes sessions normally; output is captured by `FileOutputHandler` during downtime and replayed on reconnect. No new MCP server endpoints or `.mcp.json` changes needed.

## Documentation

- [x] Update `docs/features/bridge-self-healing.md` — add "13. Bridge Hibernation" section covering auth-expiry detection, flag file, watchdog suppression, output replay, and manual recovery steps
- [x] Add entry to `docs/features/README.md` under bridge resilience features

## Success Criteria

- [ ] Bridge detects permanent auth errors and enters hibernation (flag file written, exits code 2)
- [ ] Bridge does NOT enter hibernation for transient errors — existing retry loop handles them
- [ ] Watchdog detects hibernation state and suppresses restart loop
- [ ] macOS notification fires when bridge enters hibernation with correct command text
- [ ] Worker continues executing sessions during bridge hibernation (no worker pause)
- [ ] `FileOutputHandler` captures session output to `logs/worker/` during bridge downtime
- [ ] On successful reconnect, bridge replays `logs/worker/` output (last 24h, skipping recent files)
- [ ] `data/bridge-auth-required` cleared on successful reconnect
- [ ] All unit tests pass
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (bridge-hibernation)**
  - Name: hibernation-builder
  - Role: Implement `bridge/hibernation.py`, update `bridge/telegram_bridge.py`, update `monitoring/bridge_watchdog.py`
  - Agent Type: builder
  - Resume: true

- **Builder (tests)**
  - Name: test-builder
  - Role: Write `tests/unit/test_bridge_hibernation.py` and update existing bridge test files
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: integration-validator
  - Role: Verify hibernation flow, flag file behavior, watchdog suppression
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Update `docs/features/bridge-self-healing.md` and feature index
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Build hibernation module
- **Task ID**: build-hibernation-module
- **Depends On**: none
- **Validates**: `tests/unit/test_bridge_hibernation.py` (create)
- **Assigned To**: hibernation-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `bridge/hibernation.py` with `is_auth_error()`, `enter_hibernation()`, `exit_hibernation()`, `is_hibernating()`, `replay_buffered_output()`
- Auth error classifier: permanent Telethon types → True; transient → False
- Flag file: `data/bridge-auth-required` using atomic write (temp + `os.replace`)
- macOS notification via `subprocess.run(["osascript", ...])` in try/except
- `replay_buffered_output()`: scan `logs/worker/*.log`, skip files modified < 5min ago, parse and send via client

### 2. Integrate hibernation into bridge
- **Task ID**: build-bridge-integration
- **Depends On**: build-hibernation-module
- **Validates**: existing startup flow
- **Assigned To**: hibernation-builder
- **Agent Type**: builder
- **Parallel**: false
- In `connect_with_retry()`: detect `is_user_authorized() == False` → `enter_hibernation()` → `sys.exit(2)`
- In exception handler: `is_auth_error(exc)` → `enter_hibernation()` → `sys.exit(2)`
- On successful connect: `exit_hibernation()` then `replay_buffered_output(client)`

### 3. Update bridge watchdog
- **Task ID**: build-watchdog-update
- **Depends On**: build-hibernation-module
- **Validates**: `tests/unit/test_bridge_watchdog.py`
- **Assigned To**: hibernation-builder
- **Agent Type**: builder
- **Parallel**: true
- Before Level 1 restart: check `Path("data/bridge-auth-required").exists()`
- If flag: log hibernation message, skip restart, set health to "hibernating"
- Update `--check-only` output to show hibernation state

### 4. Write unit tests
- **Task ID**: build-tests
- **Depends On**: build-hibernation-module
- **Validates**: `tests/unit/test_bridge_hibernation.py`, `tests/unit/test_bridge_watchdog.py`
- **Assigned To**: test-builder
- **Agent Type**: test-engineer
- **Parallel**: true
- Create `tests/unit/test_bridge_hibernation.py`: test all `is_auth_error()` exception types; `enter_hibernation()` flag creation; `exit_hibernation()` deletion; `replay_buffered_output()` with mock logs; graceful failure paths
- Update `tests/unit/test_bridge_watchdog.py`: hibernation detection; restart suppression; log message check

### 5. Validate builds
- **Task ID**: validate-builds
- **Depends On**: build-bridge-integration, build-watchdog-update, build-tests
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_bridge_hibernation.py tests/unit/test_bridge_watchdog.py tests/unit/test_bridge_logic.py -v`
- Verify module imports work
- Verify `ruff check bridge/hibernation.py`

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-builds
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/bridge-self-healing.md`: add section 13
- Add entry to `docs/features/README.md`

### 7. Final validation
- **Task ID**: validate-final
- **Depends On**: document-feature
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- `pytest tests/unit/ -q` — all pass
- `python -m ruff check bridge/hibernation.py monitoring/bridge_watchdog.py`
- `python -m ruff format --check bridge/hibernation.py`
- All success criteria confirmed

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check bridge/hibernation.py` | exit code 0 |
| Format clean | `python -m ruff format --check bridge/hibernation.py` | exit code 0 |
| Module importable | `python -c "from bridge.hibernation import is_auth_error, enter_hibernation, exit_hibernation, replay_buffered_output"` | exit code 0 |
| Auth classifier | `python -c "from bridge.hibernation import is_auth_error; from telethon.errors import SessionExpiredError; assert is_auth_error(SessionExpiredError())"` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

~~1. Should buffered output replay prepend a header ("--- Buffered output from [time] ---") to indicate delay? Or deliver silently?~~
**Decision:** Add timestamp header. Replayed messages delivered silently look current but may be hours old — actively misleading. Use `"--- Buffered output from [time] ---"` to make delay explicit.

~~2. Is 24 hours the right replay window? Could expose as `BRIDGE_REPLAY_MAX_AGE_HOURS=24` in `.env` if flexibility needed.~~
**Decision:** Hardcode 24h, no env var. If the bridge has been down for more than a day, the output is stale enough that selective replay becomes a manual decision. Easy to add env var later if a consistent tuning need arises.
