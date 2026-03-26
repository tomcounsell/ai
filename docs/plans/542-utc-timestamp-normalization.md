---
status: Draft
type: enhancement
appetite: Medium
owner: Valor
created: 2026-03-26
tracking: https://github.com/tomcounsell/ai/issues/542
last_comment_id: 4132057114
---

# Normalize All Log and Display Timestamps to UTC

## Problem

The system mixes timezones across components, making log correlation and debugging difficult:

- **Telethon** reports message timestamps in UTC
- **Bridge logs** use local machine time (e.g. America/Los_Angeles) via `datetime.now()`
- **Redis/Popoto** timestamps vary depending on how they were written
- **Monitoring** timestamps (watchdog, session tracker, alerts) all use naive local time

During incident investigation (issues #539, #281), correlating "message sent at 05:17 UTC" with "handler fired at 12:17 in bridge.log" requires mental timezone conversion. This wastes time and causes errors.

## Scope

The codebase audit found **~50 instances** of `datetime.now()` without timezone info across these directories:

| Directory | Naive `datetime.now()` calls | Notes |
|-----------|------------------------------|-------|
| `bridge/` | 6 | telegram_bridge, session_transcript, telegram_relay, escape_hatch |
| `agent/` | 6 | branch_manager, messenger |
| `monitoring/` | 11 | bridge_watchdog, resource_monitor, session_tracker, alerts |
| `scripts/` | ~10 | reflections, docs_auditor, update/git |
| `tools/` | ~10 | test_scheduler, valor_telegram, image_gen, selfie, sms_reader |
| `tests/` | ~10 | benchmarks, judge, unit tests |
| `ui/` | 1 | app.py |
| `.claude/hooks/` | 2 uses `datetime.utcnow()` (deprecated) |

**Primary targets** (bridge/agent/monitoring): 23 instances that directly affect log timestamps and debugging.

**Secondary targets** (scripts/tools): ~20 instances in operational scripts.

**Test files**: Update only where they construct timestamps fed into production code. Leave relative-time calculations (`datetime.now() - timedelta(...)`) in tests alone when they only compare against themselves.

## Prior Art

- Python docs recommend `datetime.now(timezone.utc)` over deprecated `datetime.utcnow()`
- Telethon already provides UTC timestamps on all message objects
- `bridge/catchup.py` and `bridge/summarizer.py` already import and use `datetime.UTC`
- `monitoring/telemetry.py` and `agent/build_pipeline.py` already use `datetime.UTC`

## Data Flow

1. **Telethon event** arrives with UTC timestamp
2. **Bridge handler** logs the event with `datetime.now()` (local time) -- MISMATCH
3. **Session transcript** records `_now_iso()` using `datetime.now()` (local time) -- MISMATCH
4. **Lifecycle transitions** use `time.time()` (epoch, timezone-neutral) -- OK but related logs are local
5. **Monitoring** (watchdog, session tracker) stamps everything with local time -- MISMATCH
6. **JSON log formatter** uses `self.formatTime()` which defaults to local time -- MISMATCH

After this change, all steps use UTC. Display conversion happens only when presenting timestamps to humans in Telegram messages.

## Architectural Impact

- **No new dependencies**: `datetime.timezone.utc` and `datetime.UTC` (Python 3.11+) are stdlib
- **Interface changes**: New utility function `utc_now()` in a shared module; new `to_local()` for display
- **Coupling**: Minimal -- each file's `datetime.now()` call is replaced independently
- **Reversibility**: Trivially reversible per-file

## Appetite

**Size:** Medium (23 primary files, mechanical changes, one new utility module)

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

Most changes are mechanical find-and-replace of `datetime.now()` with `utc_now()`. The interesting parts are the JSON formatter UTC enforcement and the display-layer conversion utility.

## Prerequisites

No prerequisites -- all changes are internal to existing code.

## Solution

### Key Elements

1. **Shared utility module** (`bridge/utc.py`): Central `utc_now()` and `to_local()` functions
2. **JSON formatter UTC enforcement**: Override `formatTime` in `StructuredJsonFormatter` to always emit UTC
3. **Mechanical replacement**: Replace `datetime.now()` with `utc_now()` across bridge/agent/monitoring
4. **Display conversion**: Add `to_local()` for human-facing timestamp formatting in Telegram messages
5. **Deprecated `utcnow()` cleanup**: Replace 2 instances of `datetime.utcnow()` in hooks

### Technical Approach

#### 1. Create `bridge/utc.py` utility module

```python
from datetime import datetime, timezone

def utc_now() -> datetime:
    """Return current time as tz-aware UTC datetime."""
    return datetime.now(timezone.utc)

def to_local(ts: datetime) -> datetime:
    """Convert a tz-aware UTC datetime to machine-local time for display."""
    return ts.astimezone()  # Uses system timezone

def utc_iso() -> str:
    """Return current UTC time as ISO 8601 string with Z suffix."""
    return utc_now().isoformat().replace("+00:00", "Z")
```

#### 2. Update StructuredJsonFormatter

Override `formatTime` to always use UTC and add `"utc": true` marker:

```python
def formatTime(self, record, datefmt=None):
    from datetime import datetime, timezone
    ct = datetime.fromtimestamp(record.created, tz=timezone.utc)
    if datefmt:
        return ct.strftime(datefmt)
    return ct.isoformat().replace("+00:00", "Z")
```

Add `"utc": True` to the JSON output dict so the convention is explicit.

#### 3. Mechanical replacements in primary targets

For each file in bridge/, agent/, monitoring/:
- Replace `from datetime import datetime` with `from bridge.utc import utc_now` (or add the import)
- Replace `datetime.now()` with `utc_now()`
- Replace `datetime.now().isoformat()` with `utc_iso()` where appropriate

Files requiring changes:

**bridge/**
- `telegram_bridge.py` (3 instances: lines 689, 703, 1617)
- `session_transcript.py` (1 instance: `_now_iso()` function at line 40)
- `telegram_relay.py` (1 instance: line 161)
- `escape_hatch.py` (1 instance: line 72)

**agent/**
- `branch_manager.py` (2 instances: lines 149, 227)
- `messenger.py` (4 instances: lines 70, 133, 147, 165)

**monitoring/**
- `bridge_watchdog.py` (2 instances: lines 495, 571)
- `resource_monitor.py` (2 instances: lines 36, 46)
- `session_tracker.py` (5 instances: lines 31, 82, 137, 181, 208)
- `alerts.py` (2 instances: lines 123, 157)

**hooks/**
- `.claude/hooks/hook_utils/constants.py` (2 instances of deprecated `datetime.utcnow()`: line 55, 63)

#### 4. Secondary targets (scripts/tools)

- `scripts/reflections.py` (~8 instances)
- `scripts/docs_auditor.py` (2 instances)
- `scripts/update/git.py` (1 instance)
- `tools/test_scheduler/__init__.py` (4 instances)
- `tools/valor_telegram.py` (2 instances)
- `tools/image_gen/__init__.py` (1 instance)
- `tools/selfie/__init__.py` (1 instance)
- `tools/sms_reader/__init__.py` (4 instances)
- `tools/valor_calendar.py` (1 instance -- already uses `.astimezone()`, just needs UTC source)
- `ui/app.py` (1 instance)

#### 5. Display layer

Add `to_local()` calls where timestamps are formatted for human display in Telegram messages. This is primarily in persona output and messenger display code. The key principle: store UTC, convert to local only at the final display boundary.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `utc_now()` and `to_local()` are pure stdlib calls with no failure modes
- [ ] `to_local()` on a naive datetime should raise (intentional -- forces callers to use tz-aware timestamps)
- [ ] JSON formatter `formatTime` override must not break if `record.created` is unexpected

### Empty/Invalid Input Handling
- [ ] `to_local(None)` should raise TypeError (do not silently return None)
- [ ] `to_local(naive_datetime)` should raise ValueError to catch missed conversions

### Error State Rendering
- [ ] No user-visible errors -- all changes are internal timestamp sources

## Test Impact

- [ ] `tests/unit/test_messenger.py` -- UPDATE: line 285 uses `datetime.now()` to track message timing; update to use `utc_now()` for consistency
- [ ] `tests/performance/test_benchmarks.py` -- UPDATE: lines 56, 64 construct timestamps with `datetime.now()`; update to `utc_now()`
- [ ] `tests/unit/test_valor_telegram.py` -- NO CHANGE: lines 20-44 use `datetime.now()` for relative time comparisons against themselves (self-consistent, no cross-boundary comparison)
- [ ] `tests/unit/test_docs_auditor.py` -- UPDATE: lines 163-297 use `datetime.now()` to construct test fixtures; update for consistency
- [ ] `tests/integration/test_reflections_redis.py` -- UPDATE: lines 206, 228 use `datetime.now()` for date formatting
- [ ] `tests/tools/test_telegram_history.py` -- NO CHANGE: relative time calculations that are self-consistent
- [ ] `tests/integration/test_lifecycle_transition.py` -- NO CHANGE: tests lifecycle logging but does not construct timestamps directly

## Rabbit Holes

- **Popoto/Redis model timestamps**: Popoto models store `created_at`/`updated_at` via their own mechanism. Do not modify the ORM layer -- that is a separate concern with migration implications
- **Telethon message timestamps**: Already UTC. Do not add conversion code for incoming Telethon timestamps
- **time.time() calls**: Epoch timestamps are timezone-neutral. Do not convert `time.time()` calls to datetime -- they are fine as-is (e.g., in `log_lifecycle_transition`)
- **Test files using datetime.now() for relative comparisons**: Tests that compute `datetime.now() - timedelta(...)` and compare only against each other are self-consistent. Do not change these unless they feed timestamps into production code
- **strftime date-only formats**: Calls like `datetime.now().strftime("%Y-%m-%d")` for date-only strings (no time component) are timezone-safe for most practical purposes. Update them for consistency but do not treat as high priority

## Risks

### Risk 1: Mixed tz-aware and naive datetime comparisons
**Impact:** `TypeError: can't compare offset-naive and offset-aware datetimes` at runtime
**Mitigation:** Grep for all comparison operations involving datetime objects in modified files. Ensure both sides are tz-aware after changes. Test thoroughly.

### Risk 2: Lock age calculation in watchdog uses naive datetime
**Impact:** `bridge_watchdog.py` line 571 parses a lock file timestamp and compares with `datetime.now()`. If lock file contains naive timestamp and we compare with tz-aware UTC, it will crash.
**Mitigation:** Update lock file parsing to produce tz-aware datetimes. Add fallback for legacy lock files.

### Risk 3: Session tracker `last_activity` stored as naive datetime
**Impact:** `session_tracker.py` stores `datetime.now()` in `last_activity` attribute. If some code paths write naive and others write tz-aware, comparisons will crash.
**Mitigation:** Update all write paths atomically within session_tracker.py. All reads and writes in one PR.

## Race Conditions

No race conditions -- each `datetime.now()` call is independent and local. There is no shared mutable timestamp state between threads or processes.

## No-Gos (Out of Scope)

- Modifying Popoto ORM timestamp behavior or Redis model `created_at`/`updated_at` fields
- Changing Telethon's timestamp handling (already correct)
- Converting `time.time()` epoch calls to datetime
- Adding timezone selection UI or per-user timezone preferences
- Modifying log rotation or log file naming conventions

## Update System

No update system changes required -- this modifies internal timestamp handling only. The new `bridge/utc.py` module is a standard Python file with no dependencies. No config changes, no new packages.

## Agent Integration

No agent integration required -- timestamps are internal to bridge/agent/monitoring code. No MCP server changes, no new tools exposed, no `.mcp.json` modifications.

## Documentation

- [ ] Create `docs/features/utc-timestamps.md` documenting the UTC convention, the `bridge/utc.py` utility, and the display-layer conversion pattern
- [ ] Add entry to `docs/features/README.md` index table

## Success Criteria

- [ ] All timestamps in `logs/bridge.log` are UTC (verify by comparing Telethon event time with bridge log time for same message)
- [ ] `grep -rn "datetime\.now()" bridge/ agent/ monitoring/` returns zero hits (excluding comments)
- [ ] `grep -rn "datetime\.utcnow()" .` returns zero hits
- [ ] JSON log entries include `"utc": true` field
- [ ] `bridge/utc.py` exists with `utc_now()`, `to_local()`, `utc_iso()` functions
- [ ] No `TypeError` from mixed naive/aware datetime comparisons (verified by test suite)
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (timestamp-normalization)**
  - Name: utc-builder
  - Role: Create utility module and replace all datetime.now() calls
  - Agent Type: builder
  - Resume: true

- **Validator (timestamp-normalization)**
  - Name: utc-validator
  - Role: Verify zero naive datetime.now() calls remain and no runtime errors
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Create bridge/utc.py utility module
- **Task ID**: create-utc-module
- **Depends On**: none
- **Validates**: tests/unit/test_utc.py (create)
- **Assigned To**: utc-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `bridge/utc.py` with `utc_now()`, `to_local()`, `utc_iso()` functions
- Create `tests/unit/test_utc.py` with tests for all three functions including error cases (naive datetime input to `to_local`)
- Verify `to_local()` correctly converts UTC to system local time

### 2. Update StructuredJsonFormatter for UTC
- **Task ID**: update-json-formatter
- **Depends On**: create-utc-module
- **Validates**: tests/unit/test_log_format.py (create or update)
- **Assigned To**: utc-builder
- **Agent Type**: builder
- **Parallel**: true
- Override `formatTime` in `bridge/log_format.py` to always emit UTC ISO 8601 timestamps
- Add `"utc": True` to the JSON output dict
- Add/update tests verifying the formatter outputs UTC timestamps

### 3. Replace datetime.now() in bridge/ files
- **Task ID**: fix-bridge-timestamps
- **Depends On**: create-utc-module
- **Validates**: existing bridge tests
- **Assigned To**: utc-builder
- **Agent Type**: builder
- **Parallel**: true
- Update `telegram_bridge.py` (3 instances)
- Update `session_transcript.py` (`_now_iso()` function)
- Update `telegram_relay.py` (1 instance)
- Update `escape_hatch.py` (1 instance)

### 4. Replace datetime.now() in agent/ files
- **Task ID**: fix-agent-timestamps
- **Depends On**: create-utc-module
- **Validates**: existing agent tests
- **Assigned To**: utc-builder
- **Agent Type**: builder
- **Parallel**: true
- Update `branch_manager.py` (2 instances)
- Update `messenger.py` (4 instances)

### 5. Replace datetime.now() in monitoring/ files
- **Task ID**: fix-monitoring-timestamps
- **Depends On**: create-utc-module
- **Validates**: existing monitoring tests
- **Assigned To**: utc-builder
- **Agent Type**: builder
- **Parallel**: true
- Update `bridge_watchdog.py` (2 instances)
- Update `resource_monitor.py` (2 instances)
- Update `session_tracker.py` (5 instances)
- Update `alerts.py` (2 instances)
- Pay special attention to lock file timestamp parsing in watchdog (Risk 2)

### 6. Replace datetime.utcnow() in hooks
- **Task ID**: fix-hook-timestamps
- **Depends On**: create-utc-module
- **Assigned To**: utc-builder
- **Agent Type**: builder
- **Parallel**: true
- Update `.claude/hooks/hook_utils/constants.py` (2 instances of deprecated `datetime.utcnow()`)

### 7. Replace datetime.now() in scripts/ and tools/
- **Task ID**: fix-scripts-tools-timestamps
- **Depends On**: create-utc-module
- **Assigned To**: utc-builder
- **Agent Type**: builder
- **Parallel**: true
- Update `scripts/reflections.py`, `scripts/docs_auditor.py`, `scripts/update/git.py`
- Update `tools/test_scheduler/__init__.py`, `tools/valor_telegram.py`, `tools/image_gen/__init__.py`, `tools/selfie/__init__.py`, `tools/sms_reader/__init__.py`, `tools/valor_calendar.py`
- Update `ui/app.py`

### 8. Update affected test files
- **Task ID**: fix-test-timestamps
- **Depends On**: fix-bridge-timestamps, fix-agent-timestamps, fix-monitoring-timestamps
- **Assigned To**: utc-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `tests/unit/test_messenger.py`
- Update `tests/performance/test_benchmarks.py`
- Update `tests/unit/test_docs_auditor.py`
- Update `tests/integration/test_reflections_redis.py`

### 9. Validate full test suite
- **Task ID**: validate-all
- **Depends On**: fix-test-timestamps, fix-scripts-tools-timestamps, fix-hook-timestamps
- **Assigned To**: utc-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/ -x -q` to verify all unit tests pass
- Run `python -m ruff check .` and `python -m ruff format --check .`
- Run `grep -rn "datetime\.now()" bridge/ agent/ monitoring/` and verify zero hits
- Run `grep -rn "datetime\.utcnow()" .` and verify zero hits
- Verify all success criteria met

### 10. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: utc-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/utc-timestamps.md`
- Update `docs/features/README.md` index table

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No naive now() in primary code | `grep -rn "datetime\.now()" bridge/ agent/ monitoring/` | zero matches |
| No deprecated utcnow() | `grep -rn "datetime\.utcnow()" .` | zero matches |
| UTC utility exists | `python -c "from bridge.utc import utc_now, to_local, utc_iso; print(utc_now())"` | UTC timestamp printed |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

No open questions. The approach is mechanical and well-defined. The only judgment call is whether to update scripts/tools in the same PR or a follow-up -- the plan includes them in the same PR since the utility module makes the changes trivial.
