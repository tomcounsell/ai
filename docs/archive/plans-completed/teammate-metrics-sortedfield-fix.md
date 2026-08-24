---
status: Ready
type: bug
appetite: Small
owner: Valor
created: 2026-04-10
tracking: https://github.com/tomcounsell/ai/issues/890
last_comment_id:
---

# TeammateMetrics: SortedField type mismatch fix

## Problem

Every call to `record_response_time()` silently fails because the Popoto `SortedField` expects a single numeric scalar, but the code assigns a dict. This produces `float() argument must be a string or a real number, not 'dict'` errors on every save, and the try/except swallows the error at debug level. Response time data is completely lost.

**Current behavior:**
`record_response_time()` in `agent/teammate_metrics.py` builds a dict of `{member: timestamp}` pairs and assigns it to `SortedField`. Popoto tries to convert the dict to a float for the sorted set score, fails, and logs an error. The outer try/except catches it. No response time data is ever persisted.

**Desired outcome:**
Response times are stored correctly using an appropriate Popoto field type, capped at 1000 entries per mode, and retrievable for analysis.

## Freshness Check

**Baseline commit:** `9cee8e0f8916677da25e186afe00bdc5fc796c62`
**Issue filed at:** 2026-04-10T13:42:52Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `models/teammate_metrics.py:31-32` -- SortedField declarations still present, unchanged
- `agent/teammate_metrics.py:59-94` -- record_response_time() still uses dict manipulation pattern
- `agent/sdk_client.py:2020-2024` -- caller still invokes record_response_time() on PM/TEAMMATE sessions
- `tests/unit/test_qa_metrics.py:63-64,71-72` -- mock setup still uses `{}` for response_times fields

**Cited sibling issues/PRs re-checked:** None cited.

**Commits on main since issue was filed (touching referenced files):** None.

**Active plans in `docs/plans/` overlapping this area:** None.

**Notes:** All references verified against current main. The bug is confirmed still present -- SortedField is unchanged and the dict assignment pattern is intact.

## Prior Art

No prior issues or PRs found related to TeammateMetrics SortedField or response time storage.

## Data Flow

1. **Entry point**: `agent/sdk_client.py:2024` -- after SDK responds, calls `record_response_time("teammate"|"work", elapsed)`
2. **agent/teammate_metrics.py:59**: `record_response_time()` fetches the singleton `TeammateMetrics` via `_get_metrics()`
3. **agent/teammate_metrics.py:72-89**: Builds a `"elapsed:timestamp"` string as dict key, sets `timestamp` as dict value, assigns dict to field
4. **models/teammate_metrics.py:31-32**: `SortedField` receives dict, Popoto calls `float(dict)` during save -- ERROR
5. **Output**: Exception caught at debug level, data silently lost

**Fix target**: Steps 3-4. Replace `SortedField` with `ListField` and use `push()` instead of dict manipulation.

## Architectural Impact

- **New dependencies**: None -- `ListField` is already available in Popoto (`popoto.fields.shortcuts`)
- **Interface changes**: `record_response_time()` signature unchanged. Internal storage format changes from dict to list of strings.
- **Coupling**: No change -- same single caller in `sdk_client.py`
- **Data ownership**: Unchanged -- `TeammateMetrics` singleton still owns all metrics
- **Reversibility**: Trivial -- field type change with no downstream consumers of the raw data

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites -- this work has no external dependencies.

## Solution

### Key Elements

- **Model field change**: Replace `SortedField(default=dict)` with `ListField(max_length=1000)` for both response time fields
- **Record function simplification**: Use Popoto `ListField.push()` for append instead of read-modify-write dict pattern
- **Test fixture update**: Change mock initial values from `{}` to `[]`

### Flow

**SDK call completes** -> `record_response_time(mode, elapsed)` -> format `"elapsed:timestamp"` string -> `field.push(entry)` -> Popoto LPUSH+LTRIM (capped at 1000) -> done

### Technical Approach

- Replace `SortedField` import with `ListField` import in `models/teammate_metrics.py`
- Change both field declarations to `ListField(max_length=1000)`
- Update module docstring to reflect list storage instead of sorted set
- Remove `_MAX_RESPONSE_TIMES` constant from both model and agent module (ListField handles capping via `max_length`)
- Simplify `record_response_time()` to: format string, get field reference, call `push(entry)` -- no need to read existing data, no manual trimming, no `save()` call (push writes directly)
- Update test mock initial values from `{}` to `[]`

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `record_response_time()` lines 66-94 has a broad `except Exception` -- existing test `test_no_metrics_does_not_crash` covers the None-metrics path; add test for push() failure path

### Empty/Invalid Input Handling
- [ ] Test `record_response_time()` with mode="" (should not crash)
- [ ] Test with elapsed_seconds=0.0 and negative values (should store without error)

### Error State Rendering
- Not applicable -- no user-visible output from metrics recording

## Test Impact

- [ ] `tests/unit/test_qa_metrics.py::TestRecordResponseTime::test_records_teammate_time` -- UPDATE: change `mock_metrics.teammate_response_times = {}` to `[]`, assert push() called instead of save()
- [ ] `tests/unit/test_qa_metrics.py::TestRecordResponseTime::test_records_work_time` -- UPDATE: change `mock_metrics.work_response_times = {}` to `[]`, assert push() called instead of save()

## Rabbit Holes

- Do not add response time statistics to `get_stats()` -- that is a separate feature request if needed
- Do not migrate existing data -- the SortedField never successfully stored anything, so there is nothing to migrate
- Do not change `record_classification()` or counter fields -- those use `IntField` and work correctly

## Risks

### Risk 1: ListField.push() API assumptions
**Impact:** If `push()` does not exist or behaves differently than expected, the fix fails
**Mitigation:** The issue body documents that ListField supports `push()` with LPUSH+LTRIM semantics. Verify during build by checking Popoto source.

## Race Conditions

No race conditions identified -- `push()` is an atomic Redis LPUSH+LTRIM operation. Even if two concurrent calls push simultaneously, both entries will be stored (order may vary, which is acceptable for response time logging).

## No-Gos (Out of Scope)

- Adding response time stats/averages to `get_stats()` return value
- Migrating historical data (none exists due to the bug)
- Changing classification counter fields or `record_classification()` logic
- Adding dashboarding or alerting on response times

## Update System

No update system changes required -- this is an internal model/field type change with no new dependencies, config files, or migration steps.

## Agent Integration

No agent integration required -- `record_response_time()` is called internally by `sdk_client.py` during session execution. No MCP servers, bridge changes, or tool wrappers needed.

## Documentation

- [ ] Update docstring in `models/teammate_metrics.py` to reflect ListField storage (inline)
- [ ] Update docstring in `agent/teammate_metrics.py` to reflect push()-based writes (inline)
- No feature documentation file needed -- this is an internal bug fix with no user-facing behavior change

## Success Criteria

- [ ] `record_response_time()` stores data without errors
- [ ] No `float() argument must be a string or a real number, not 'dict'` errors in logs
- [ ] Response time history is capped at 1000 entries per mode via ListField max_length
- [ ] Existing classification counters (IntField) are unaffected
- [ ] Unit tests pass (`/do-test`)
- [ ] `python -m ruff check . && python -m ruff format --check .` passes

## Team Orchestration

### Team Members

- **Builder (metrics-fix)**
  - Name: metrics-builder
  - Role: Fix field types and update record function
  - Agent Type: builder
  - Resume: true

- **Validator (metrics-fix)**
  - Name: metrics-validator
  - Role: Verify fix works and tests pass
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Fix model field types
- **Task ID**: build-model
- **Depends On**: none
- **Validates**: tests/unit/test_qa_metrics.py
- **Assigned To**: metrics-builder
- **Agent Type**: builder
- **Parallel**: true
- In `models/teammate_metrics.py`: replace `from popoto import IntField, KeyField, Model, SortedField` with `from popoto import IntField, KeyField, ListField, Model`
- Change `teammate_response_times = SortedField(default=dict)` to `teammate_response_times = ListField(max_length=1000)`
- Change `work_response_times = SortedField(default=dict)` to `work_response_times = ListField(max_length=1000)`
- Remove `_MAX_RESPONSE_TIMES = 1000` class attribute (ListField handles capping)
- Update module docstring to describe ListField storage

### 2. Simplify record_response_time()
- **Task ID**: build-record-fn
- **Depends On**: build-model
- **Validates**: tests/unit/test_qa_metrics.py
- **Assigned To**: metrics-builder
- **Agent Type**: builder
- **Parallel**: false
- In `agent/teammate_metrics.py`: remove `_MAX_RESPONSE_TIMES` module constant
- Replace the dict manipulation block (lines 71-89) with: format entry string, call `metrics.teammate_response_times.push(entry)` or `metrics.work_response_times.push(entry)`
- Remove the `metrics.save()` call (push writes directly to Redis)

### 3. Update tests
- **Task ID**: build-tests
- **Depends On**: build-record-fn
- **Validates**: tests/unit/test_qa_metrics.py
- **Assigned To**: metrics-builder
- **Agent Type**: builder
- **Parallel**: false
- In `tests/unit/test_qa_metrics.py`: change `mock_metrics.teammate_response_times = {}` to `mock_metrics.teammate_response_times = []` (lines 63, 71)
- Change `mock_metrics.work_response_times = {}` to `mock_metrics.work_response_times = []` (lines 64, 72)
- Update assertions: verify `push()` was called on the appropriate field mock instead of `save()` on the metrics object

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-tests
- **Assigned To**: metrics-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_qa_metrics.py -x -q` -- all tests pass
- Run `python -m ruff check models/teammate_metrics.py agent/teammate_metrics.py tests/unit/test_qa_metrics.py`
- Run `python -m ruff format --check models/teammate_metrics.py agent/teammate_metrics.py tests/unit/test_qa_metrics.py`
- Verify no references to `SortedField` remain in the three changed files

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_qa_metrics.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check models/teammate_metrics.py agent/teammate_metrics.py` | exit code 0 |
| Format clean | `python -m ruff format --check models/teammate_metrics.py agent/teammate_metrics.py` | exit code 0 |
| No SortedField refs | `grep -c SortedField models/teammate_metrics.py agent/teammate_metrics.py` | exit code 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

No open questions -- the issue provides complete root cause analysis, solution sketch, and file-level change list. The fix is straightforward field type replacement.
