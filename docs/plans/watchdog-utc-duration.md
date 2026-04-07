---
status: Ready
type: bug
appetite: Small
owner: Valor
created: 2026-04-07
tracking: https://github.com/tomcounsell/ai/issues/777
last_comment_id:
---

# Fix Watchdog UTC Duration: Naive Datetime Treated as Local Time

## Problem

The session watchdog (`monitoring/session_watchdog.py`) fires false `LIFECYCLE_STALL` events for sessions that are only seconds old. On a machine running at UTC+7 (Asia/Bangkok), newly created `pending` sessions immediately show stall durations of ~25200–25500 seconds.

**Current behavior:** `LIFECYCLE_STALL session=cli_1775528945 status=pending duration=25401s threshold=300s` fires immediately after session creation. The duration is approximately one UTC offset (7 hours × 3600 = 25200 s).

**Desired outcome:** Stall duration reflects actual session age. A session created 5 seconds ago shows `duration=5s`, not `duration=25405s`.

## Prior Art

- **Issue #542 / PR #557** — "Normalize all timestamps to tz-aware UTC" — normalized ~50 `datetime.now()` calls across the codebase. However, `_to_timestamp()` in `session_watchdog.py` was not addressed because the bug manifests in *deserialization* from Redis, not in datetime construction. The field type (`SortedField`) has no automatic UTC-aware deserialization unlike `DatetimeField`. That fix was incomplete for this specific call site.
- **Issue #440** — "Session watchdog failures" — addressed earlier watchdog crashes, not timezone arithmetic. Unrelated.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #557 | Changed `datetime.now()` → `datetime.now(tz=UTC)` in ~50 locations | Only fixed *construction* sites. `_to_timestamp()` receives values deserialized from Redis via `SortedField`, which returns naive datetimes. The fix was applied at the wrong layer — datetime construction was already UTC, but the read path still produced naive objects. |

**Root cause pattern:** The `SortedField` ORM type lacks the UTC-aware deserialization that `DatetimeField` provides. Any code that receives datetime values from `SortedField` fields and calls `.timestamp()` on them is vulnerable to this offset bug.

## Data Flow

1. **Session creation**: `AgentSession.created_at` is set via `SortedField(type=datetime)`. The value is stored as a Unix timestamp score in a Redis sorted set.
2. **Redis read**: When the watchdog queries `AgentSession.query.filter(status=...)`, Popoto reconstructs the model. `SortedField` deserializes the score back to a `datetime` object — but without `tzinfo`, producing a timezone-naive `datetime` whose numeric value represents UTC.
3. **`_to_timestamp()` call**: The watchdog calls `_to_timestamp(session.created_at)`. Current code calls `val.timestamp()` on the naive datetime. Python interprets this as local time (UTC+7), adding 7 × 3600 = 25200 seconds to the effective Unix timestamp.
4. **Duration calculation**: `now = time.time()` (correct POSIX UTC) minus the inflated `ref_time` yields a negative or hugely inflated duration depending on the offset direction.
5. **False LIFECYCLE_STALL**: `duration > threshold` is immediately true for new sessions, triggering a warning.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **`_to_timestamp()` fix**: When the input `datetime` is timezone-naive, attach `UTC` as `tzinfo` before calling `.timestamp()`. This preserves correctness for already-aware datetimes and fixes naive ones.
- **Import addition**: Add `UTC` to the `from datetime import datetime` import at the top of the module.
- **All three call sites covered automatically**: `find_stalled_sessions` (line ~267), `_check_session_health` (line ~376), and `fix_unhealthy_session` (line ~630) all route through `_to_timestamp()`, so fixing the helper fixes all three.
- **Unit test**: Add a test asserting that `_to_timestamp(naive_utc_datetime)` == `_to_timestamp(aware_utc_datetime)` — the naive form must not add the local UTC offset.

### Technical Approach

```python
from datetime import UTC, datetime

def _to_timestamp(val) -> float | None:
    """Convert a datetime or float to a Unix timestamp.

    Naive datetimes are assumed to represent UTC (matching how Popoto
    SortedField stores them). This prevents local-time interpretation
    on machines running in non-UTC timezones.
    """
    if val is None:
        return None
    if isinstance(val, datetime):
        if val.tzinfo is None:
            val = val.replace(tzinfo=UTC)
        return val.timestamp()
    if isinstance(val, (int, float)):
        return float(val)
    return None
```

No other files need changes. The fix is fully contained within the one helper function.

## Failure Path Test Strategy

### Exception Handling Coverage

- `_to_timestamp()` currently returns `None` for unrecognized types. That path is unchanged and already implicitly tested by callers that handle `None`. No new exception handler is added, so no new test is needed here.

### Empty/Invalid Input Handling

- `None` input → returns `None` (unchanged behavior, already handled)
- `str` input → returns `None` (unchanged behavior)
- Naive datetime → now returns UTC-based timestamp instead of local-time-based timestamp
- Aware UTC datetime → behavior unchanged (`.replace(tzinfo=UTC)` is skipped)

### Error State Rendering

- No user-visible output. The watchdog logs `LIFECYCLE_STALL`; after the fix, false positives stop appearing. No rendering changes needed.

## Test Impact

- [ ] `tests/unit/test_stall_detection.py` — UPDATE: verify `_make_agent_session` uses naive datetime for `created_at` in at least one new test to confirm the fix. Existing tests pass float timestamps, which bypass the bug; add a test using `datetime.utcnow()` (naive UTC) to cover the fixed path.
- [ ] `tests/unit/test_session_watchdog.py` — No changes required; existing tests use `time.time()` (float) and `datetime.now(tz=UTC)` (aware), both of which already work correctly.

## Rabbit Holes

- **Fixing `SortedField` deserialization in Popoto**: The correct long-term fix would be to patch `SortedField` to return UTC-aware datetimes. However, Popoto is an external dependency and modifying it is out of scope. The watchdog-local fix is safe and complete.
- **Auditing all datetime fields across all models**: Issue #542 already addressed the broader codebase normalization. This plan is narrowly scoped to the one remaining bug in `_to_timestamp()`.
- **Changing `time.time()` to `datetime.now(tz=UTC)`**: The current `now = time.time()` pattern is correct and consistent. No reason to change it.

## Risks

### Risk 1: Other callers of `_to_timestamp()` exist outside the watchdog
**Impact:** If something else calls this function with intentionally naive local-time datetimes, the fix would break them.
**Mitigation:** `grep` confirms `_to_timestamp` is a module-private function (leading underscore) defined and used only within `monitoring/session_watchdog.py`. No external callers.

### Risk 2: SortedField deserialization behavior changes in a Popoto update
**Impact:** If a future Popoto release makes `SortedField` return UTC-aware datetimes, `_to_timestamp()` would apply a redundant `.replace(tzinfo=UTC)` on an already-aware datetime — which would raise a `TypeError`.
**Mitigation:** The guard `if val.tzinfo is None` prevents this: the replace only runs for naive datetimes. Aware datetimes skip it. No breakage from Popoto improvements.

## Race Conditions

No race conditions identified — `_to_timestamp()` is a pure function with no shared mutable state. The watchdog itself is single-threaded within each invocation cycle.

## No-Gos (Out of Scope)

- Patching `SortedField` in Popoto to return UTC-aware datetimes
- Auditing other Popoto model fields that may have the same behavior
- Changing the watchdog's broader stall detection thresholds or logic
- Addressing issue #542's remaining `datetime.now()` calls in other modules

## Update System

No update system changes required — this is a single-file bug fix with no new dependencies, config, or migration steps.

## Agent Integration

No agent integration required — `_to_timestamp()` is an internal watchdog helper. It is not exposed via MCP or callable by the agent.

## Documentation

- [ ] Update the docstring on `_to_timestamp()` in `monitoring/session_watchdog.py` to document the UTC assumption for naive datetimes (inline, not a separate doc file)

No feature documentation file needed — this is a narrow bug fix, not a new feature. The issue description in #777 serves as the root cause record.

## Success Criteria

- [ ] `_to_timestamp()` treats naive datetimes as UTC (`val.replace(tzinfo=UTC)` before `.timestamp()`)
- [ ] A `pending` session created seconds ago shows `duration < 10s` in watchdog logs on a UTC+7 machine
- [ ] No false `LIFECYCLE_STALL` events fire for newly-created sessions
- [ ] Unit test: `_to_timestamp(datetime.utcnow())` == `_to_timestamp(datetime.now(tz=UTC))` within 1 second
- [ ] `tests/unit/test_stall_detection.py` passes with the new naive-datetime test case
- [ ] Tests pass (`/do-test`)

## Team Orchestration

### Team Members

- **Builder (watchdog-fix)**
  - Name: watchdog-builder
  - Role: Fix `_to_timestamp()`, update import, add unit test
  - Agent Type: builder
  - Resume: true

- **Validator (watchdog-fix)**
  - Name: watchdog-validator
  - Role: Verify fix correctness, run tests, confirm no regressions
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Fix `_to_timestamp()` and add unit test
- **Task ID**: build-watchdog-utc
- **Depends On**: none
- **Validates**: `tests/unit/test_stall_detection.py`, `tests/unit/test_session_watchdog.py`
- **Informed By**: Issue #777 root cause analysis
- **Assigned To**: watchdog-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `UTC` to the `from datetime import datetime` import in `monitoring/session_watchdog.py`
- Replace `_to_timestamp()` body with the UTC-safe version (guard on `val.tzinfo is None`)
- Update `_to_timestamp()` docstring to document the naive=UTC assumption
- Add test `test_to_timestamp_naive_datetime_treated_as_utc` in `tests/unit/test_stall_detection.py`:
  - Create a naive UTC datetime (e.g., `datetime.utcnow()`)
  - Create an equivalent aware UTC datetime (e.g., `datetime.now(tz=UTC)`)
  - Assert `abs(_to_timestamp(naive) - _to_timestamp(aware)) < 1.0`
- Run `python -m ruff format monitoring/session_watchdog.py` and `python -m ruff check monitoring/session_watchdog.py`

### 2. Validate fix
- **Task ID**: validate-watchdog-utc
- **Depends On**: build-watchdog-utc
- **Assigned To**: watchdog-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_stall_detection.py tests/unit/test_session_watchdog.py -v`
- Confirm the new `test_to_timestamp_naive_datetime_treated_as_utc` test passes
- Confirm no regressions in existing stall detection tests
- Verify `_to_timestamp` is not called from any file outside `monitoring/session_watchdog.py`
- Report pass/fail status

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `pytest tests/unit/test_stall_detection.py tests/unit/test_session_watchdog.py -v` | exit code 0 |
| Lint clean | `python -m ruff check monitoring/session_watchdog.py` | exit code 0 |
| Format clean | `python -m ruff format --check monitoring/session_watchdog.py` | exit code 0 |
| UTC fix present | `grep -n "tzinfo is None" monitoring/session_watchdog.py` | output contains `tzinfo is None` |
| No external callers | `grep -rn "_to_timestamp" . --include="*.py" \| grep -v "session_watchdog"` | exit code 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| CONCERN | [agent-type] | [The concern raised] | [How/whether it was addressed] |
