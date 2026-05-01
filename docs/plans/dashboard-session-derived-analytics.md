---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-05-01
tracking: https://github.com/tomcounsell/ai/issues/1245
last_comment_id:
---

# Dashboard Analytics: Derive Cost/Turn Stats from AgentSession Fields

## Problem

The dashboard at `localhost:8500/dashboard.json` exposes an `analytics` block that should reflect
real session activity, but `cost_today_usd` and `turns_today` are stuck at `0.0` even though
individual sessions in the same document carry correct `total_cost_usd` values (e.g. `$2.44`).

**Current behavior:**
- `curl -s localhost:8500/dashboard.json | jq .analytics` returns `cost_today_usd: 0.0`,
  `cost_7d_usd: 0.0`, `turns_today: 0.0`, `turns_7d: 0.0`, `turns_avg_today: 0.0`, `turns_avg_7d: 0.0`
- Every `AgentSession.turn_count` is `0` and `last_turn_at` is `null` — even for sessions that ran
- Per-session `total_cost_usd` and token counts in `dashboard.json` ARE correct

**Root cause:**
`ui/data/analytics.py:29-33` reads cost and turns from `query_metric_total("session.cost_usd")` and
`query_metric_total("session.turns")`. The only emit site for those metrics is inside
`get_response_via_sdk` (the in-process SDK path). The worker uses `get_response_via_harness`
exclusively — the SDK emit site is unreachable in production.

**Desired outcome:**
- Single source of truth: AgentSession fields (`total_cost_usd`, `turn_count`) are the only ledger
  for session-attributed sums
- Dashboard derives cost/turn analytics by querying AgentSession over time windows
- `turn_count` and `tool_call_count` on AgentSession advance after every harness call
- Atomic increments eliminate the read-modify-write race on `total_cost_usd` and related fields

## Freshness Check

**Baseline commit:** `6ed64d53dc6dc8a6fbe0929c7a295fb6860fe78f`
**Issue filed at:** 2026-05-01T03:51:24Z
**Disposition:** Unchanged

**File:line references re-verified:**

- `ui/data/analytics.py:29-33` — reads `query_metric_total("session.cost_usd", days=1)` and
  `query_metric_total("session.turns", days=1)` — **still holds exactly** (lines 29-33)
- `agent/sdk_client.py:1560-1562` — `record_metric("session.cost_usd", cost, dims)` and
  `record_metric("session.turns", float(turns), dims)` — **still holds** (lines 1578-1580 after nearby drift)
- `agent/session_executor.py:1429` — confirmed only call site is `get_response_via_harness` —
  **drift: the referenced line is now in the 1430s range (persona block was added above)**, but the
  claim holds: the executor calls the harness path only
- `agent/sdk_client.py:291` — `accumulate_session_tokens` helper — **still at line 286**
- `analytics/collector.py:104` — `POPOTO_REDIS_DB.hincrbyfloat` pattern — **still holds**
- `agent/sdk_client.py:79` — `_session_turn_counts` registry — **still at line 79**
- `models/agent_session.py:130` — turn_count / tool_call_count fields — **still at lines 175-176**
- `models/agent_session.py:141` — `started_at` / `completed_at` are plain `DatetimeField` not
  `SortedField` — **confirmed at lines 152-154**

**Cited sibling issues/PRs re-checked:**

- #895 (PR, MERGED 2026-04-11) — "Unified analytics system" — introduced the analytics module;
  `session.cost_usd` / `session.turns` emit sites still present and still dead in production
- #925 (PR, MERGED 2026-04-13) — "Dashboard analytics partial" — surface area unchanged
- #1127 (issue, CLOSED 2026-04-22) — compaction hardening — not related to this fix
- PR #1138 (MERGED 2026-04-23) — watchdog hardening, introduced `accumulate_session_tokens` on
  harness path — confirms tokens work; `turn_count` was NOT addressed in that PR

**Commits on main since issue was filed (touching referenced files):**

- `8f4e75f4` fix(harness): email.persona — touched `session_executor.py`, added persona
  resolution block above the harness call. No change to cost/turn accounting.
- `e7e96f0a` feat(#1227): prompt-cache stabilization — touched `sdk_client.py` (TTFT metrics). No
  change to cost/turn accounting or analytics aggregation.
- `3cbd4602` feat(#1192): chat_message_log — touched `agent_session.py` (new field). No change.
- `7adb302b` fix(#1228): stage-conditional worker_key — touched `session_executor.py`. No change.
- `72eb1867` feat(drafter): bigram-Jaccard filter — touched `agent_session.py`. No change.

**Active plans in `docs/plans/` overlapping this area:** None found.

**Notes:** Line numbers drifted slightly in `sdk_client.py` and `session_executor.py` due to
above commits; all claims still hold against the current main HEAD.

## Prior Art

- **PR #895** (MERGED 2026-04-11): "Unified analytics system for metrics collection and dashboard"
  — Introduced `analytics/collector.py`, `record_metric`, and the dashboard analytics block.
  Added `session.cost_usd` and `session.turns` emit sites inside `get_response_via_sdk`. This PR
  is the root cause of the current situation: the emit sites were wired to the SDK path only,
  and the harness path had no live callers at the time. This issue partially undoes that work for
  session-attributed sums.
- **PR #925** (MERGED 2026-04-13): "Dashboard analytics metrics, remove histogram, add Stats toggle"
  — Updated `ui/templates/_partials/analytics_stats.html`. No changes to aggregation logic.
- **PR #1138** (MERGED 2026-04-23): "Watchdog hardening: idle teardown, per-session token tracking"
  — Added `accumulate_session_tokens` call on the harness path. Fixed `total_cost_usd` / token
  fields. Did NOT fix `turn_count`, `tool_call_count`, or the analytics aggregation in
  `ui/data/analytics.py`. This issue builds directly on that foundation.

## Research

No relevant external findings — all concerns are internal to this codebase. The technologies
involved (Redis HINCRBYFLOAT, Popoto ORM, Python asyncio subprocess) are mature and well-understood.

## Spike Results

No spikes needed. The issue's Recon Summary is thorough and all claims verified during the
freshness check. The three planning decisions delegated by the issue are resolved below.

**Planning decision 1: Indexing strategy for time-range queries on AgentSession**

Recommendation from issue: **Option B** — maintain a Redis sorted set keyed by completion
timestamp (`analytics:sessions:completed_at`), updated on the same write path that sets
`completed_at` in `finalize_session()`.

Rationale: `completed_at` is a plain `DatetimeField(null=True)` and cannot become a `SortedField`
without a sentinel default and a data migration. Option B is purely additive: a `ZADD` call in
`finalize_session()` writes `{session.db_key.redis_key: completed_at_ts}`. The analytics query
does a `ZRANGEBYSCORE(analytics:sessions:completed_at, start_ts, end_ts)` and resolves each key
to its AgentSession for sum aggregation. This is the same pattern already used in
`tools/email_history/__init__.py`.

**Planning decision 2: `tool_call_count` increment mechanism**

The harness `stream_event` events include `content_block_start` items. When
`event.get("type") == "content_block_start"` and the `content_block` has `type == "tool_use"`,
that marks a new tool invocation. The harness subprocess loop in `_run_harness_subprocess` can
count these and return `tool_call_count` as a 7th element in the return tuple. This is cheap
(no extra subprocess spawning), accurate (fires once per tool call), and available from both
the primary and fallback harness call sites.

Alternatively, we can use the `result` event's `num_turns` field (already extracted from the
harness `result` event for the SDK-path `record_turn_count` call). The harness `result` event
does NOT currently emit `num_turns` — the `num_turns` field exists only in the SDK's
`ResultMessage` object. So turn count must be counted differently.

**Simplest viable approach for both counters:**
- Count tool invocations from `content_block_start` events with `type == "tool_use"` inside
  `_run_harness_subprocess` — return as 7th element `tool_calls`
- Count assistant-turn boundaries from `content_block_stop` events or, simpler, count the number
  of `message_stop` events which correspond to one assistant turn each. Each `message_stop` in
  the stream = one turn. Return as 8th element `num_turns` (same semantics as SDK `num_turns`).
- Caller in `get_response_via_harness` uses these to increment `session.turn_count` and
  `session.tool_call_count` via the same atomic helper used for token fields.

**Planning decision 3: Atomic increment helper API**

Put it on the AgentSession class as a free function in `agent/sdk_client.py` (co-located with
`accumulate_session_tokens`), named `atomic_increment_session_field`. This keeps the pattern
consistent with the existing helper and avoids a new module. It uses `HINCRBYFLOAT` for float
fields and `HINCRBY` for int fields, bypassing the Popoto read-modify-write cycle entirely.

## Data Flow

**Current (broken) path for analytics:**

1. Session runs via harness: `accumulate_session_tokens` writes `total_cost_usd` to AgentSession
2. `record_metric("session.cost_usd", cost)` in `get_response_via_sdk` — **never reached**
3. `ui/data/analytics.py::get_analytics_summary()` calls `query_metric_total("session.cost_usd")`
4. Returns 0.0 because no emit ever happened

**Desired path after fix:**

**Turn/tool counting:**
1. Harness subprocess runs; `_run_harness_subprocess` counts `message_stop` events (turns) and
   `content_block_start` with `type=="tool_use"` (tool calls)
2. Returns 8-tuple: `(result_text, session_id, returncode, usage, cost_usd, stderr, num_turns, tool_calls)`
3. `get_response_via_harness` calls `atomic_increment_session_field(session_id, "turn_count", num_turns)`
   and `atomic_increment_session_field(session_id, "tool_call_count", tool_calls)`

**Analytics aggregation:**
1. `finalize_session()` writes `ZADD analytics:sessions:completed_at {completed_at_ts: redis_key}`
2. `get_analytics_summary()` calls `zrangebyscore` for the time window → set of Redis keys
3. Loads each AgentSession from Redis → sums `total_cost_usd` and `turn_count`
4. Returns correct non-zero values

**Atomic increment path (replacing read-modify-write):**
1. `accumulate_session_tokens` replaced by `atomic_increment_session_field` calls for each field
2. Uses `HINCRBYFLOAT` for `total_cost_usd` / float fields; `HINCRBY` for int fields
3. No round-trip read, no lost-update race

## Architectural Impact

- **New dependencies**: None — uses existing `POPOTO_REDIS_DB` and Popoto patterns
- **Interface changes**: `_run_harness_subprocess` return tuple grows from 6-tuple to 8-tuple;
  all three call sites in `get_response_via_harness` must be updated. `accumulate_session_tokens`
  gains integer-field variants or is replaced by the new helper.
- **Coupling**: `finalize_session()` in `models/session_lifecycle.py` gains a Redis side-write.
  `ui/data/analytics.py` gains a dependency on `models.agent_session.AgentSession` (currently
  only depends on `analytics.query`).
- **Data ownership**: Session-attributed sums live exclusively on AgentSession. Analytics module
  retains ownership of counts (`session.started`, `session.completed`, `memory.*`).
- **Reversibility**: Low-risk — the analytics query change is isolated. The sorted-set ZADD is
  additive. Reverting means removing the ZADD and restoring the two `query_metric_total` calls.

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (confirm 8-tuple approach for `_run_harness_subprocess` is acceptable)
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies. Redis is already running.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis up | `python -c "from popoto.redis_db import POPOTO_REDIS_DB; POPOTO_REDIS_DB.ping()"` | Sorted-set writes need Redis |

## Solution

### Key Elements

- **Turn/tool event counter**: Count assistant turns (`message_stop` events) and tool invocations
  (`content_block_start` with `type=="tool_use"`) inside `_run_harness_subprocess`
- **8-tuple harness return**: Add `num_turns` and `tool_calls` as 7th and 8th elements of the
  `_run_harness_subprocess` return tuple; update all three call sites
- **Atomic increment helper**: `atomic_increment_session_field(session_id, field, delta)` — uses
  `HINCRBYFLOAT` / `HINCRBY` directly on the AgentSession Redis hash. Replaces the read-modify-write
  in `accumulate_session_tokens` and is used for `turn_count` / `tool_call_count` increments.
- **`finalize_session` ZADD**: On `completed_at` write, also `ZADD analytics:sessions:completed_at`
  so the analytics query has a time-indexed lookup surface
- **Analytics re-aggregation**: `get_analytics_summary()` queries AgentSession via the sorted set
  for the time window, sums `total_cost_usd` and `turn_count` directly from model fields
- **Dead emit removal**: Delete `record_metric("session.cost_usd", ...)` and
  `record_metric("session.turns", ...)` from `sdk_client.py`

### Flow

Session runs → harness counts turns + tool calls → `get_response_via_harness` atomically
increments `turn_count` and `tool_call_count` on AgentSession → session completes →
`finalize_session` ZADDs completion ts → dashboard queries sorted set → sums AgentSession
fields → returns correct non-zero analytics

### Technical Approach

1. **`_run_harness_subprocess`**: Add two local counters in the event loop:
   - `_turn_count = 0`: incremented on each `message_stop` stream event
   - `_tool_call_count = 0`: incremented on each `content_block_start` where `content_block.type == "tool_use"`
   Return tuple becomes 8-element. Update docstring.

2. **All three `_run_harness_subprocess` call sites**: Change 6-tuple unpack to 8-tuple. The
   `result_text`, `session_id_from_harness`, `returncode`, `usage`, `cost_usd`, `stderr_snippet`
   positions are unchanged; `num_turns`, `tool_calls` are new positions 7 and 8.

3. **`get_response_via_harness`** (post-subprocess block around line 2294):
   After `accumulate_session_tokens`, add:
   ```python
   if session_id and num_turns:
       atomic_increment_session_field(session_id, "turn_count", num_turns)
   if session_id and tool_calls:
       atomic_increment_session_field(session_id, "tool_call_count", tool_calls)
   ```

4. **`accumulate_session_tokens`**: Replace the read-modify-write `+=` pattern for all five fields
   with `atomic_increment_session_field` calls. This fixes the race condition on `total_cost_usd`
   and token totals — not just the new fields.

5. **`atomic_increment_session_field(session_id, field, delta)`**: New function in `sdk_client.py`.
   Looks up AgentSession by `session_id`, resolves the raw Redis hash key via `session.db_key`,
   calls `HINCRBYFLOAT` or `HINCRBY` depending on whether the field is float or int. Fail-quiet.

6. **`finalize_session` in `models/session_lifecycle.py`**: After `session.completed_at = time.time()`
   at line 390, add best-effort ZADD:
   ```python
   try:
       POPOTO_REDIS_DB.zadd(
           "analytics:sessions:completed_at",
           {session.db_key.redis_key: session.completed_at}
       )
   except Exception:
       pass
   ```

7. **`ui/data/analytics.py::get_analytics_summary()`**: Replace the two `query_metric_total` calls
   with a new helper `_query_session_sums(days)` that:
   - Calls `ZRANGEBYSCORE("analytics:sessions:completed_at", start_ts, "+inf")`
   - Pipeline-fetches `total_cost_usd` and `turn_count` from each AgentSession hash
   - Returns `(total_cost, total_turns)`
   Session count metrics (`session.started`, `session.completed`) are unchanged — keep using
   `query_metric_count`.

8. **Remove dead emit sites**: Delete `record_metric("session.cost_usd", ...)` and
   `record_metric("session.turns", ...)` from `get_response_via_sdk` in `sdk_client.py`.

## Failure Path Test Strategy

### Exception Handling Coverage

- `atomic_increment_session_field`: wraps all Redis calls in `try/except` with `logger.warning` —
  test that a Redis failure does NOT propagate to the caller (fail-quiet contract)
- `finalize_session` ZADD: best-effort block — test that a ZADD failure does NOT affect the
  terminal status write (status still saved)
- `_query_session_sums`: test that a Redis failure returns `(0.0, 0)` rather than raising
- `accumulate_session_tokens` atomic rewrite: existing fail-quiet contract must be preserved —
  update existing tests to mock `HINCRBYFLOAT` raising and assert no exception propagates

### Empty/Invalid Input Handling

- `atomic_increment_session_field(session_id=None, ...)` → no-op (same as `accumulate_session_tokens`)
- `atomic_increment_session_field(..., delta=0)` → no-op (avoid unnecessary Redis round-trip)
- `_query_session_sums(days=0)` → returns `(0.0, 0)` without querying Redis
- Empty sorted set (`ZRANGEBYSCORE` returns `[]`) → `(0.0, 0)` without error

### Error State Rendering

- `get_analytics_summary()` error path already returns zero dict — unchanged. No user-visible
  rendering change; zeros are the existing fallback behavior.

## Test Impact

- [ ] `tests/unit/test_analytics_query.py::TestQueryMetricTotal::test_total` — UPDATE: this
  test exercises `query_metric_total("session.cost_usd")` which is no longer called in production
  code. Keep the test (it validates the analytics.query module itself) but add a note that the
  production code path now uses AgentSession. The test itself still passes.
- [ ] `tests/integration/test_analytics_dashboard.py::TestDashboardAnalytics::test_analytics_summary_graceful_without_db`
  — UPDATE: the test monkeypatches `analytics.query._DB_PATH` — this may need updating if the
  analytics summary no longer calls `query_metric_total` for cost/turns. Replace with a test that
  monkeypatches `_query_session_sums` to raise and asserts graceful zero fallback.
- [ ] `tests/integration/test_analytics_dashboard.py::TestDashboardAnalytics::test_analytics_summary_returns_valid_structure`
  — UPDATE: still valid (schema test), no changes needed to assertion logic.
- [ ] Any test that asserts `record_metric("session.cost_usd", ...)` is called — DELETE: emit
  sites are being removed. Search: `grep -rn "session.cost_usd\|session.turns" tests/`

## Rabbit Holes

- **Backfilling historical `turn_count`**: Out of scope. Pre-fix sessions stay at 0. Only new
  sessions after deploy will have accurate values.
- **Making `completed_at` a `SortedField`**: Too invasive. The ZADD approach achieves the same
  query capability without touching the model schema.
- **Replacing `session.started` / `session.completed` count metrics with AgentSession queries**:
  Out of scope. Those metrics work correctly today.
- **Memory metric refactor**: Out of scope. `memory.recall_attempt` / `memory.extraction` are
  counts, not sums, and they work correctly.
- **Real-time streaming analytics** (WebSocket, SSE): Not requested. Dashboard JSON poll is
  sufficient.
- **Pipeline aggregation optimization** (Redis PIPELINE for bulk HGET): Premature. The sessions
  count in typical windows is small enough that sequential reads are fine.

## Risks

### Risk 1: Sorted-set key format
**Impact:** If the key stored in the sorted set doesn't match how AgentSession hashes are keyed
in Redis, `ZRANGEBYSCORE` returns keys that can't be resolved to AgentSession objects.
**Mitigation:** Use `session.db_key.redis_key` (same key used by the defensive srem in
`finalize_session` at line 403) — this is the canonical key string already used for index ops.

### Risk 2: Harness event format variation
**Impact:** If the harness's `stream_event` format differs across claude CLI versions (e.g.,
`message_stop` event type renamed), `num_turns` and `tool_calls` counters stay at 0.
**Mitigation:** Default to 0 when events aren't found — no regression from current behavior.
Log a debug warning on unexpected event types during development.

### Risk 3: accumulate_session_tokens atomic rewrite
**Impact:** If `HINCRBYFLOAT` on the AgentSession hash field keys is wrong (e.g., Popoto uses
a different field key format inside the hash), token accounting breaks silently.
**Mitigation:** Unit test that compares `HINCRBYFLOAT` key resolution against what Popoto reads
back via `AgentSession.query.filter(session_id=...)`. Verify with a real session in integration.

### Risk 4: Three call sites for `_run_harness_subprocess`
**Impact:** Missing one 6→8-tuple update causes an unpack error at runtime (test or production).
**Mitigation:** The test suite exercises all three code paths; a missed unpack will fail tests.
Use `grep -n "_run_harness_subprocess" agent/sdk_client.py` to enumerate all call sites before
submitting.

## Race Conditions

### Race 1: Concurrent `accumulate_session_tokens` calls
**Location:** `agent/sdk_client.py:354-394`
**Trigger:** Worker process and a Claude Code hook both call `accumulate_session_tokens` for the
same session within a small window. Current read-modify-write does:
`session.total_cost_usd = float(session.total_cost_usd or 0.0) + cost_delta` — reads stale value.
**Data prerequisite:** AgentSession must exist in Redis before increment is called.
**State prerequisite:** None.
**Mitigation:** Atomic rewrite uses `HINCRBYFLOAT` which is atomic at the Redis level — no lost
updates regardless of concurrent callers.

### Race 2: ZADD in `finalize_session` vs. analytics query read
**Location:** `models/session_lifecycle.py:390` (ZADD), `ui/data/analytics.py` (ZRANGEBYSCORE)
**Trigger:** Session finalizes just before a dashboard refresh; ZADD and ZRANGEBYSCORE are not
transactional.
**Data prerequisite:** ZADD must complete before ZRANGEBYSCORE reads the window.
**State prerequisite:** None.
**Mitigation:** Redis operations are serialized per connection; ZADD is atomic. Worst case: a
session that completed in the same millisecond as the dashboard refresh doesn't appear until the
next refresh cycle. This is acceptable — dashboard is a polling endpoint.

### Race 3: `_run_harness_subprocess` turn/tool count vs. fallback paths
**Location:** `agent/sdk_client.py` call sites 2 and 3 (image-dimension fallback, stale-UUID fallback)
**Trigger:** A fallback path reruns `_run_harness_subprocess`; counters from the failed primary
call should not be double-counted.
**Data prerequisite:** Each fallback returns fresh counters from the new subprocess.
**State prerequisite:** Counters are local to each `_run_harness_subprocess` invocation.
**Mitigation:** Each call returns its own `num_turns`/`tool_calls` counters. Caller uses the
final returned values (fallback overwrites primary). No double-counting.

## No-Gos (Out of Scope)

- Backfilling historical `turn_count` on pre-fix sessions
- Replacing `session.started` / `session.completed` analytics count metrics with AgentSession queries
- Memory metric refactor (`memory.recall_attempt`, `memory.extraction`)
- Any analytics that doesn't naturally live on AgentSession (pipeline metrics, crash counts)
- Making `started_at` or `completed_at` a `SortedField` on AgentSession
- Backend-swap abstraction or Agent Communication Protocol work
- Parallel-run migration — cutover happens in one PR, no dual-write period

## Update System

No update system changes required — this feature is purely internal. The sorted set key
`analytics:sessions:completed_at` is created lazily on first `finalize_session` call and requires
no migration. No new config keys, no new dependencies, no changes to `scripts/remote-update.sh`.

## Agent Integration

No agent integration required — this is an internal analytics re-plumbing change. No new CLI
entry points, no bridge changes, no MCP servers affected.

## Documentation

- [ ] Update `docs/features/dashboard.md` (if it exists) to note that cost/turn analytics are
  now derived from AgentSession fields, not the analytics ledger — add a note to the "analytics
  block" section explaining the data source change
- [ ] Update inline docstrings on `accumulate_session_tokens` and `get_analytics_summary` to
  reflect the new implementation
- [ ] No new feature docs required — this is a bug fix, not a new capability

## Success Criteria

- [ ] `curl -s localhost:8500/dashboard.json | jq .analytics` shows non-zero `cost_today_usd` and
  `turns_today` once at least one new session completes after deploy
- [ ] `curl -s localhost:8500/dashboard.json | jq '.sessions[].turn_count'` shows non-zero values
  for sessions that completed after deploy
- [ ] `grep -rn 'record_metric("session.cost_usd"\|record_metric("session.turns"' agent/ ui/`
  returns zero hits (worktrees excluded)
- [ ] `grep -rn 'query_metric_total("session.cost_usd"\|query_metric_total("session.turns"' .`
  returns zero hits in production code (worktrees excluded)
- [ ] No read-modify-write `+=` on `total_cost_usd`, `turn_count`, or any token total field in
  `agent/` (replaced by atomic increment helper)
- [ ] Atomic increment unit test: two concurrent calls produce the correct sum (no lost updates)
- [ ] `memory.recall_attempt`, `memory.extraction`, `session.started`, `session.completed`
  metrics continue to populate `dashboard.json.analytics` correctly
- [ ] Tests pass: `pytest tests/ -x -q`

## Team Orchestration

### Team Members

- **Builder (analytics-rewrite)**
  - Name: analytics-builder
  - Role: Implement all code changes: harness counter, atomic helper, finalize ZADD, analytics query rewrite, dead emit removal
  - Agent Type: builder
  - Resume: true

- **Validator (analytics-rewrite)**
  - Name: analytics-validator
  - Role: Run tests, grep for dead emit sites, verify schema compatibility
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: analytics-documentarian
  - Role: Update docstrings and feature docs
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Extend `_run_harness_subprocess` to count turns and tool calls
- **Task ID**: build-harness-counters
- **Depends On**: none
- **Validates**: `tests/unit/test_sdk_client_harness.py` (create or update)
- **Assigned To**: analytics-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `_turn_count = 0` and `_tool_call_count = 0` locals inside the event loop
- Increment `_turn_count` on each `message_stop` stream event (`event_type == "stream_event"` and `event.get("type") == "message_stop"`)
- Increment `_tool_call_count` on each `content_block_start` stream event where `event.get("content_block", {}).get("type") == "tool_use"`
- Change return tuple from 6-element to 8-element: `(result_text, session_id_from_harness, returncode, usage, cost_usd, stderr_snippet, _turn_count, _tool_call_count)`
- Update docstring to document positions 7 and 8
- Update all three call sites in `get_response_via_harness` to unpack the 8-tuple

### 2. Add `atomic_increment_session_field` and wire into `get_response_via_harness`
- **Task ID**: build-atomic-increment
- **Depends On**: build-harness-counters
- **Validates**: `tests/unit/test_accumulate_session_tokens.py` (update), new concurrent-increment unit test
- **Assigned To**: analytics-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `atomic_increment_session_field(session_id, field, delta)` function in `agent/sdk_client.py`, using `HINCRBYFLOAT` for float fields and `HINCRBY` for int fields. No-op when `session_id` is None or delta is 0.
- Replace read-modify-write `+=` in `accumulate_session_tokens` with `atomic_increment_session_field` calls for all five fields (`total_input_tokens`, `total_output_tokens`, `total_cache_read_tokens`, `total_cost_usd`)
- Add `atomic_increment_session_field(session_id, "turn_count", num_turns)` and `atomic_increment_session_field(session_id, "tool_call_count", tool_calls)` in `get_response_via_harness` after the `accumulate_session_tokens` call block

### 3. Add `ZADD` to `finalize_session` for time-range lookup
- **Task ID**: build-finalize-zadd
- **Depends On**: none
- **Validates**: `tests/unit/test_session_lifecycle.py` (update)
- **Assigned To**: analytics-builder
- **Agent Type**: builder
- **Parallel**: true
- In `models/session_lifecycle.py`, after `session.completed_at = time.time()` (line ~390), add best-effort `ZADD analytics:sessions:completed_at {session.db_key.redis_key: session.completed_at}` wrapped in `try/except`
- Use the existing `from popoto.redis_db import POPOTO_REDIS_DB` import (already present in the file)

### 4. Rewrite `get_analytics_summary()` to query AgentSession
- **Task ID**: build-analytics-query
- **Depends On**: build-finalize-zadd
- **Validates**: `tests/integration/test_analytics_dashboard.py` (update), new unit test for `_query_session_sums`
- **Assigned To**: analytics-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `_query_session_sums(days: int) -> tuple[float, int]` helper in `ui/data/analytics.py`:
  - Computes `start_ts = time.time() - days * 86400`
  - Calls `ZRANGEBYSCORE("analytics:sessions:completed_at", start_ts, "+inf")`
  - For each key, fetches `total_cost_usd` and `turn_count` via pipeline HGET
  - Returns `(sum_cost, sum_turns)`
- Replace `query_metric_total("session.cost_usd", days=N)` and `query_metric_total("session.turns", days=N)` calls with `_query_session_sums(N)` unpack
- Remove `from analytics.query import query_metric_count, query_metric_total` if `query_metric_total` is no longer imported (keep `query_metric_count`)

### 5. Remove dead emit sites from `get_response_via_sdk`
- **Task ID**: build-remove-dead-emits
- **Depends On**: build-analytics-query
- **Validates**: `grep -rn 'record_metric.*session\.cost_usd\|record_metric.*session\.turns' agent/ ui/` returns zero hits
- **Assigned To**: analytics-builder
- **Agent Type**: builder
- **Parallel**: false
- Delete `record_metric("session.cost_usd", cost, dims)` and `record_metric("session.turns", float(turns), dims)` calls in `agent/sdk_client.py` (currently lines 1578-1580)
- Leave surrounding `try/except` block if other code uses it; otherwise remove the whole try block

### 6. Validate
- **Task ID**: validate-all
- **Depends On**: build-atomic-increment, build-remove-dead-emits
- **Assigned To**: analytics-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/ -x -q` — all tests pass
- Run `grep -rn 'record_metric.*session\.cost_usd\|record_metric.*session\.turns' agent/ ui/` — zero hits
- Run `grep -rn 'query_metric_total.*session\.cost_usd\|query_metric_total.*session\.turns' .` — zero hits in production code
- Run `grep -rn '\+= .*cost_usd\|turn_count.*+=' agent/` — zero hits (no surviving read-modify-write)
- Verify `python -c "from ui.data.analytics import get_analytics_summary; print(get_analytics_summary())"` runs without error

### 7. Documentation
- **Task ID**: document-analytics
- **Depends On**: validate-all
- **Assigned To**: analytics-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Update `accumulate_session_tokens` docstring to reflect atomic implementation
- Update `get_analytics_summary` docstring to reflect AgentSession-derived aggregation
- Update `docs/features/dashboard.md` if it exists: note that `analytics.cost_today_usd` and `analytics.turns_today` are derived from `AgentSession.total_cost_usd` and `AgentSession.turn_count` via a Redis sorted set, not from the analytics ledger

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No dead emit sites (cost) | `grep -rn 'record_metric.*session\.cost_usd' agent/ ui/` | exit code 1 |
| No dead emit sites (turns) | `grep -rn 'record_metric.*session\.turns' agent/ ui/` | exit code 1 |
| No stale query_metric_total (cost) | `grep -rn 'query_metric_total.*session\.cost_usd' ui/` | exit code 1 |
| No stale query_metric_total (turns) | `grep -rn 'query_metric_total.*session\.turns' ui/` | exit code 1 |
| No read-modify-write on cost | `grep -rn 'total_cost_usd.*+=' agent/` | exit code 1 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| | | | | |

---

## Open Questions

No open questions — all three planning decisions from the issue are resolved above (indexing
strategy → Option B ZADD; turn counter → `message_stop` events; atomic helper → free function
in `sdk_client.py`). No human input needed before building.
