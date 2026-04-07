---
status: Ready
type: bug
appetite: Small
owner: tomcounsell
created: 2026-04-07
tracking: https://github.com/tomcounsell/ai/issues/792
last_comment_id: none
---

# Timestamp Timezone Labels

## Problem

Two timestamp display surfaces show times with no timezone label, causing agents and humans to compute session durations that are off by 7 hours when mixing the two sources.

**Current behavior:**

```
# valor_session status (UTC, unlabeled):
Created:  2026-04-07 05:49:00
Started:  2026-04-07 06:04:28
Updated:  2026-04-07 06:34:12

# worker.log (local UTC+7, unlabeled):
2026-04-07 13:03:54 agent.health_check INFO [health_check] Running health check ...
```

An agent subtracting "13:03 (log)" from "05:49 (status)" computes **7h14m** instead of the correct **~30 minutes**. This error has occurred repeatedly across multiple sessions and agents.

**Desired outcome:**

Every timestamp in both surfaces includes an explicit timezone label so comparisons are unambiguous regardless of who (human or agent) is reading them.

```
# valor_session status:
Created:  2026-04-07 05:49:00 UTC

# worker.log:
2026-04-07 13:03:54+0700 agent.health_check INFO ...
```

## Prior Art

- **Issue #542 / PR #557**: UTC timestamp normalization — normalized internal timestamps to tz-aware UTC. The internal data is now correct; the display formatting is the remaining gap.
- **Issue #777 / PR #787**: Session watchdog `_to_timestamp` treated naive datetimes as local, inflating LIFECYCLE_STALL durations. Fixed at the computation layer. This issue addresses the display layer so future comparisons are safe regardless of internal representation.

## Data Flow

1. **`tools/valor_session.py`**: Redis stores timestamps as floats or ISO strings → `_format_ts()` converts to `datetime` with UTC tz (correct) → `strftime` strips the tz label (broken) → CLI output shows bare `YYYY-MM-DD HH:MM:SS`
2. **`worker/__main__.py`**: Python `logging.basicConfig` uses `%(asctime)s` with no `datefmt` → defaults to `logging.Formatter.formatTime` which calls `time.localtime()` (local time, UTC+7) → log entries show bare `YYYY-MM-DD HH:MM:SS` in local time

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Was Incomplete |
|-----------|-------------|----------------------|
| PR #557 | Made internal datetime objects tz-aware | Only fixed internal representation, not display formatting — `strftime` calls still strip the label |
| PR #787 | Fixed watchdog stall duration calculation | Addressed a single symptom at the computation layer; did not fix the display surfaces |

**Root cause pattern:** Each fix was targeted at a specific symptom. The display formatting layer was never addressed — no one audited all output surfaces for tz labels.

## Architectural Impact

- **New dependencies**: None — purely formatting changes
- **Interface changes**: `_format_ts()` return value changes (appends ` UTC`); log line format changes (gains `+0700` or `UTC` suffix)
- **Coupling**: No change in coupling
- **Data ownership**: No change
- **Reversibility**: Trivially reversible — one-line changes

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

- **`tools/valor_session.py:_format_ts`**: Append ` UTC` to the strftime format string. The value is already correctly UTC at line 119; just label it.
- **`worker/__main__.py` logging config**: Add `datefmt="%Y-%m-%d %H:%M:%S"` and set `converter=time.gmtime` on the root handler so log timestamps emit in UTC. Optionally append ` UTC` to the format string.
- **`ui/app.py:_filter_format_timestamp`**: Same-day `%H:%M` format could show a TZ note but the existing relative strings ("5m ago", "just now") are inherently timezone-agnostic — no change needed for those. For the fallback `%Y-%m-%d` (old dates, no time shown), no TZ label needed either.

### Technical Approach

All three changes are isolated formatting tweaks:

1. `tools/valor_session.py` line 122: `"%Y-%m-%d %H:%M:%S"` → `"%Y-%m-%d %H:%M:%S UTC"`
2. `worker/__main__.py` `_setup_logging`: add `import time`; set `logging.Formatter.converter = time.gmtime` before `basicConfig`; add `datefmt="%Y-%m-%d %H:%M:%S"` to `basicConfig`; change format string to include ` UTC` in `%(asctime)s` output by appending it to the datefmt or by using a custom Formatter subclass with `usesTime()`.

The cleanest approach for the worker is a custom Formatter that forces UTC and appends the label, rather than monkey-patching the global `Formatter.converter`.

## Failure Path Test Strategy

### Exception Handling Coverage

- The `except Exception` block in `_format_ts` (line 123) falls back to `str(ts)[:19]` — this path never showed a TZ label and still won't after the fix (the fallback is a raw string, not a formatted datetime). This is acceptable — the fallback is for malformed data.
- No new exception handlers introduced.

### Empty/Invalid Input Handling

- `_format_ts(None)` returns `"—"` (no change)
- `_format_ts("garbage")` returns the raw 19-char prefix via the except block (no change)
- Both edge cases still work correctly after the format string change

### Error State Rendering

- No user-visible error states change — the fix only adds a label to success-path output

## Test Impact

- [ ] `tests/unit/test_ui_app.py::test_format_timestamp_value` — UPDATE: the function returns relative strings ("Xm ago", "just now") not raw timestamps, so this test is unaffected by the `_format_ts` change in `valor_session.py`. No update needed unless the test asserts exact format strings for `ui/app.py`'s filter (which is out of scope here).
- [ ] `tests/unit/test_ui_reflections_data.py::test_format_timestamp` — UPDATE: same as above — tests `ui.app._filter_format_timestamp`, not `tools.valor_session._format_ts`. Unaffected.
- [ ] `tests/unit/test_worker_entry.py` — UPDATE: if any test asserts log line format, it will need updating to expect the UTC suffix. Review `test_worker_main_importable` and related tests.

New tests needed:
- [ ] `tests/unit/test_valor_session_format_ts.py` — CREATE: test that `_format_ts()` output ends with ` UTC` for float inputs, ISO string inputs, and that `None` still returns `"—"`
- [ ] Worker logging format test — CREATE or UPDATE in `tests/unit/test_worker_entry.py`: assert that log records from the configured handler contain a UTC indicator

## Rabbit Holes

- Switching all internal timestamps to a single shared timezone — out of scope; internal representation is already UTC-aware per #557.
- Changing the UI dashboard's relative-time display ("5m ago") — the relative format is intentionally timezone-agnostic and needs no label.
- Adding a user-configurable timezone preference — premature; the system is single-user and UTC is the right canonical display.
- Auditing every log message in every module for tz consistency — only the entry point formatter matters; individual log calls use `%(asctime)s` which flows through the single configured formatter.

## Risks

### Risk 1: Custom Formatter breaks existing log parsing
**Impact:** If any downstream log parser (e.g., a grep script, the reflections system) expects the old bare timestamp format, appending ` UTC` will break the parse.
**Mitigation:** Grep the codebase for log line parsers before applying the worker change. The reflections system uses log tailing but parses by log level, not timestamp — low risk.

### Risk 2: `_format_ts` return value change breaks callers
**Impact:** Any caller that does string processing on the return value of `_format_ts` (e.g., asserting exact format in tests) will fail.
**Mitigation:** Grep all callers of `_format_ts` — it is a private function used only within `tools/valor_session.py`. Only test files need updating.

## Race Conditions

No race conditions identified — all operations are synchronous and single-threaded string formatting changes.

## No-Gos (Out of Scope)

- Changing internal datetime representation (already UTC-aware)
- Modifying the UI dashboard's relative-time filter (`_filter_format_timestamp` in `ui/app.py`)
- Adding timezone preference settings
- Auditing log output in modules other than `worker/__main__.py`

## Update System

No update system changes required — this feature is purely internal formatting. No new dependencies or config files are introduced.

## Agent Integration

No agent integration required — this is a display-only change to CLI output and log file format. The MCP server wrapping `valor_session` will automatically benefit from the updated `_format_ts` output.

## Documentation

- [ ] Update `docs/features/session-management.md` (if it exists) to note that all timestamp displays include explicit UTC labels
- [ ] If no feature doc exists for session CLI tools, no new doc is required — the change is self-evident from the output

## Success Criteria

- [ ] `python -m tools.valor_session status --id <any>` shows `Created`, `Started`, `Updated` with an explicit ` UTC` suffix
- [ ] `logs/worker.log` entries include an explicit timezone indicator (`UTC` or `+0700`)
- [ ] `_format_ts(1700000000.0)` returns a string ending in ` UTC`
- [ ] An agent computing session duration by subtracting `Started` from current time using either source cannot silently get a result off by more than a few seconds
- [ ] Tests pass (`/do-test`)
- [ ] No existing test assertions broken without explicit update

## Team Orchestration

### Team Members

- **Builder (timestamp-fix)**
  - Name: timestamp-builder
  - Role: Apply the two formatting changes and write the new tests
  - Agent Type: builder
  - Resume: true

- **Validator (timestamp-fix)**
  - Name: timestamp-validator
  - Role: Verify output format, run tests, confirm acceptance criteria
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Fix `_format_ts` in `tools/valor_session.py`
- **Task ID**: build-format-ts
- **Depends On**: none
- **Validates**: tests/unit/test_valor_session_format_ts.py (create)
- **Assigned To**: timestamp-builder
- **Agent Type**: builder
- **Parallel**: true
- Change line 122: `dt.strftime("%Y-%m-%d %H:%M:%S")` → `dt.strftime("%Y-%m-%d %H:%M:%S UTC")`
- Handle the `fromisoformat` branch: if `dt` is naive (no tzinfo), convert to UTC before formatting
- Create `tests/unit/test_valor_session_format_ts.py` with assertions that output ends with ` UTC`

### 2. Fix worker logging config in `worker/__main__.py`
- **Task ID**: build-worker-logging
- **Depends On**: none
- **Validates**: tests/unit/test_worker_entry.py (update)
- **Assigned To**: timestamp-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `import time` at top of file
- Before `logging.basicConfig`, set `logging.Formatter.converter = time.gmtime`
- Add `datefmt="%Y-%m-%d %H:%M:%S UTC"` to `basicConfig` call
- Update or add test in `test_worker_entry.py` asserting the configured formatter uses UTC

### 3. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-format-ts, build-worker-logging
- **Assigned To**: timestamp-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_valor_session_format_ts.py tests/unit/test_worker_entry.py -v`
- Run `python -m tools.valor_session status --id test` and confirm ` UTC` suffix visible
- Verify all success criteria are met

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/ -x -q` | exit code 0 |
| Format ts has UTC label | `python -c "from tools.valor_session import _format_ts; r = _format_ts(1700000000.0); assert r.endswith(' UTC'), repr(r)"` | exit code 0 |
| Worker log uses UTC | `python -c "import logging, time; assert logging.Formatter.converter is time.gmtime"` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

None — scope is clear and recon is complete.
