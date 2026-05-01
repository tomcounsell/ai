---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-05-01
revised: 2026-05-01
revision_count: 3
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
- Dashboard derives cost/turn analytics by querying AgentSession via Popoto (single source of truth)
- `turn_count` and `tool_call_count` on AgentSession are populated after every harness call
- `total_cost_usd` continues to flow through the existing `accumulate_session_tokens` helper

## Freshness Check

**Baseline commit:** `6ed64d53dc6dc8a6fbe0929c7a295fb6860fe78f`
**Issue filed at:** 2026-05-01T03:51:24Z
**Disposition:** Unchanged

**File:line references re-verified:**

- `ui/data/analytics.py:29-33` — reads `query_metric_total("session.cost_usd", days=1)` and
  `query_metric_total("session.turns", days=1)` — **still holds**
- `agent/sdk_client.py:1578-1580` — `record_metric("session.cost_usd", cost, dims)` and
  `record_metric("session.turns", float(turns), dims)` in `get_response_via_sdk` — **still holds**
- `agent/sdk_client.py:2472-2500` — existing `result`-event handler in
  `_run_harness_subprocess` extracts `result`, `session_id`, `usage`, `total_cost_usd`
- `models/agent_session.py:175-176` — `turn_count = IntField(default=0)` and
  `tool_call_count = IntField(default=0)` **already exist** — no schema change needed
- `agent/sdk_client.py:2304` — `accumulate_session_tokens(session_id, ...)` call site has the
  AgentSession `session_id` (Telegram-derived) in scope — same scope chosen for the new write

**Cited sibling issues/PRs re-checked:**

- #895 (PR, MERGED 2026-04-11) — "Unified analytics system" — introduced `session.cost_usd` /
  `session.turns` emits; dead in production because worker uses harness path only
- #1138 (PR, MERGED 2026-04-23) — added `accumulate_session_tokens` on harness path; did NOT
  fix `turn_count` or analytics aggregation

**Active plans in `docs/plans/` overlapping this area:** None found.

## Prior Art

- **PR #895** (MERGED 2026-04-11): "Unified analytics system for metrics collection and dashboard"
  — Wired `session.cost_usd` and `session.turns` emit sites inside `get_response_via_sdk`. This
  PR is the root cause of the current situation.
- **PR #1138** (MERGED 2026-04-23): "Watchdog hardening: idle teardown, per-session token tracking"
  — Added `accumulate_session_tokens` call on the harness path. This issue builds directly on
  that foundation.

## Research

No relevant external findings — all concerns are internal to this codebase.

## Spike Results

**Finding 1: `num_turns` IS in the harness `result` event**

The existing `result`-event handler at `agent/sdk_client.py:2472-2500` already extracts `result`,
`session_id`, `usage`, `total_cost_usd` but stops short of `num_turns`. Adding it is a one-line
change.

**Finding 2: `assistant` events carry tool_use blocks; NO existing handler exists**

`_run_harness_subprocess` today handles ONLY `event_type == "result"` and `event_type ==
"stream_event"`. There is **no existing handler for `event_type == "assistant"`** — the
implementation must ADD a new branch. Top-level `assistant` events carry
`message.content[]` arrays where each `content_blocks[].type == "tool_use"` entry is one tool
invocation.

**Finding 3: Persistence happens in `get_response_via_harness`, NOT `_run_harness_subprocess`**

`session_id_from_harness` returned by `_run_harness_subprocess` is the **Claude Code transcript
UUID**, NOT the AgentSession `session_id`. The AgentSession `session_id` is a Telegram-derived
identifier. The two are mapped via `_store_claude_session_uuid()` after each subprocess.

**Decision:** persist in `get_response_via_harness`, which already has the AgentSession
`session_id` in scope as a function parameter — same identifier used for the existing
`accumulate_session_tokens(session_id, ...)` call at line 2304.

**Finding 4: AgentSession fields already exist**

`turn_count = IntField(default=0)` and `tool_call_count = IntField(default=0)` are present at
`models/agent_session.py:175-176`. **No schema change needed.**

**Finding 5: Dashboard surfaces only cost and turns**

`ui/data/analytics.py::get_analytics_summary()` returns `cost_today_usd`, `cost_7d_usd`,
`turns_today`, `turns_7d`, `turns_avg_today`, `turns_avg_7d` plus session counts and memory
counts. **`tool_call_count` is captured for future surface area but not currently rendered;**
`duration_ms`, `stop_reason`, and `modelUsage` are NOT surfaced anywhere — they will NOT be
extracted by this plan.

## Data Flow

**Current (broken) path:**

1. Session runs via harness: `accumulate_session_tokens` writes `total_cost_usd` to AgentSession
2. `record_metric("session.cost_usd", cost)` in `get_response_via_sdk` — **never reached**
3. `ui/data/analytics.py::get_analytics_summary()` calls `query_metric_total("session.cost_usd")`
4. Returns 0.0 because no emit ever happened

**Desired path after fix:**

1. Harness `result` event → extract `num_turns` (one line) → carry through `_run_harness_subprocess`
   return.
2. Harness `assistant` event → NEW handler counts tool_use blocks in `data["message"]["content"]`
   → carry through return.
3. `get_response_via_harness` writes via Popoto: `session.turn_count += this_call_num_turns`
   (accumulate; primary + fallback paths add up); same for `session.tool_call_count`.
4. `get_analytics_summary()` calls `AgentSession.query.filter(status="completed").all()`,
   filters in Python by `completed_at >= cutoff`, and sums `total_cost_usd` / `turn_count`.

## Architectural Impact

Small change:
- **Add 2 fields:** N/A — `turn_count` and `tool_call_count` already exist.
- **Extract from harness `result` event:** one-line `num_turns` extraction; new
  `assistant`-event handler branch counts tool_use blocks.
- **Persist via Popoto:** field-assign + save in `get_response_via_harness` (accumulate via `+=`).
- **Aggregate via Popoto query:** `AgentSession.query.filter(status="completed").all()` →
  Python-side date filter and sum.
- **No raw Redis. No ZSET. No parallel ledger.** Architecture-rule compliant.
- **Reversibility:** Trivial — revert two functions in `agent/sdk_client.py` and one in
  `ui/data/analytics.py`.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

None — Redis is already running, Popoto is already in use.

## Solution

### Key Elements

- **`num_turns` extraction**: One-line addition to the existing `result`-event handler at
  `agent/sdk_client.py:2472-2500`. Initialize a local `num_turns = 0` at the top of
  `_run_harness_subprocess`; populate from `data.get("num_turns")` inside the existing handler.
- **NEW `assistant`-event handler**: Add an `if event_type == "assistant":` branch counting
  `tool_use` blocks in `data["message"]["content"]`. Initialize `_tool_call_count = 0` at the
  top of `_run_harness_subprocess`.
- **Carry counts through return**: Extend `_run_harness_subprocess` return to include
  `num_turns` and `tool_call_count` (8-tuple, simplest mechanical change; or `HarnessResult`
  dataclass if preferred — pick one and apply consistently to all 3 production call sites and
  the test fixtures).
- **Accumulating Popoto write in `get_response_via_harness`**: Immediately after the existing
  `accumulate_session_tokens(session_id, ...)` call, look up the AgentSession by `session_id`
  and **accumulate** the new counts via `+=` (so primary + fallback subprocess invocations sum
  rather than overwrite). Wrapped in `try/except` with `logger.warning`.
- **Dashboard aggregation via Popoto**: Replace `query_metric_total("session.cost_usd")` /
  `query_metric_total("session.turns")` calls in `ui/data/analytics.py` with Popoto queries
  on `AgentSession`. Sum `total_cost_usd` and `turn_count` in Python. Filter by
  `completed_at >= now - days*86400`.
- **Dead emit removal**: Delete `record_metric("session.cost_usd", ...)` and
  `record_metric("session.turns", ...)` from `get_response_via_sdk`.

### Flow

Session runs → harness `result` event yields `num_turns` directly → `_run_harness_subprocess`
counts tool_use blocks from `assistant` events → returns counts → `get_response_via_harness`
accumulates onto `session.turn_count` / `session.tool_call_count` via Popoto save → session
completes → `finalize_session` writes `completed_at` (unchanged) → dashboard queries via
`AgentSession.query.filter(status="completed").all()` → returns correct non-zero analytics.

### Technical Approach

1. **Extend the `result`-event handler** at `agent/sdk_client.py:2472-2500`. The block
   already pulls `result`, `session_id`, `usage`, `total_cost_usd`. Add ONE pull:
   ```python
   num_turns = int(data.get("num_turns") or 0)
   ```
   If `num_turns` is missing from a `result` event, log once at warning level
   (`logger.warning("[harness] result event missing num_turns")`) — this keeps the dashboard
   green but surfaces a known signal-source dependency.

2. **ADD a new `assistant`-event handler branch** in `_run_harness_subprocess`, parallel to
   the existing `result` and `stream_event` branches (place BEFORE `stream_event`):
   ```python
   if event_type == "assistant":
       message = data.get("message", {}) or {}
       content_blocks = message.get("content", []) or []
       _tool_call_count += sum(1 for b in content_blocks if b.get("type") == "tool_use")
       continue
   ```
   Initialize `_tool_call_count = 0` and `num_turns = 0` at the top of `_run_harness_subprocess`.

3. **Carry through return**. Extend the existing 6-tuple return to 8-tuple by appending
   `num_turns` and `_tool_call_count`. (A `HarnessResult` dataclass is acceptable — pick one
   and apply everywhere.) Update all three production return points plus the binary-not-found
   path.

4. **Update all three call sites in `get_response_via_harness`** (lines ~2204, 2233, 2280) to
   unpack the new shape.

5. **Accumulate onto AgentSession** in `get_response_via_harness`, immediately after the
   existing `accumulate_session_tokens(session_id, ...)` call:
   ```python
   if session_id and (this_num_turns or this_tool_call_count):
       try:
           from models.agent_session import AgentSession
           session = AgentSession.query.filter(session_id=session_id).first()
           if session is not None:
               if this_num_turns:
                   session.turn_count = (session.turn_count or 0) + this_num_turns
               if this_tool_call_count:
                   session.tool_call_count = (session.tool_call_count or 0) + this_tool_call_count
               session.save()
       except Exception as e:
           logger.warning(
               "Failed to persist turn/tool counts for session %s: %s",
               session_id, e,
           )
   ```
   Accumulation (`+=`) is intentional: a fallback subprocess (image-dimension fallback,
   stale-UUID fallback) reruns `_run_harness_subprocess`; its counts ADD to the prior counts
   so the session-level total is correct.

6. **Rewrite `get_analytics_summary()` in `ui/data/analytics.py`** using Popoto only:
   ```python
   import time
   from models.agent_session import AgentSession

   def _query_completed_sessions_in_window(days: int) -> list:
       if days <= 0:
           return []
       cutoff = time.time() - days * 86400
       try:
           # status="completed" is a small slice; filter completed_at in Python
           sessions = AgentSession.query.filter(status="completed").all()
           return [
               s for s in sessions
               if s.completed_at and s.completed_at.timestamp() >= cutoff
           ]
       except Exception as e:
           logger.warning("[analytics-dashboard] Popoto query failed: %s", e)
           return []

   def _sum_cost_and_turns(sessions: list) -> tuple[float, int]:
       sum_cost = 0.0
       sum_turns = 0
       for s in sessions:
           try:
               sum_cost += float(s.total_cost_usd or 0.0)
               sum_turns += int(s.turn_count or 0)
           except (TypeError, ValueError):
               continue
       return (sum_cost, sum_turns)
   ```
   Then in `get_analytics_summary()`:
   ```python
   today_sessions = _query_completed_sessions_in_window(days=1)
   week_sessions = _query_completed_sessions_in_window(days=7)
   cost_today, turns_today = _sum_cost_and_turns(today_sessions)
   cost_7d, turns_7d = _sum_cost_and_turns(week_sessions)
   ```
   `query_metric_count("session.started", ...)` and `query_metric_count("session.completed", ...)`
   are unchanged. Memory metrics are unchanged.

7. **Remove dead emit sites**: Delete `record_metric("session.cost_usd", cost, dims)` and
   `record_metric("session.turns", float(turns), dims)` from `get_response_via_sdk` in
   `agent/sdk_client.py` (currently lines 1578-1580). Remove the surrounding `try/except` if
   nothing else uses it.

## Failure Path Test Strategy

### Exception Handling Coverage

- AgentSession field-write block (`session.turn_count = ...; session.save()`): wrapped in
  `try/except` with `logger.warning` — Popoto failure does NOT propagate to the caller.
- `_query_completed_sessions_in_window`: returns `[]` on any Popoto exception.
- `_sum_cost_and_turns`: skips records with non-numeric fields (per-record try/except continue).

### Empty/Invalid Input Handling

- `this_num_turns == 0` and `this_tool_call_count == 0` → field-write block is skipped entirely.
- `session_id` is None → field-write block is skipped.
- AgentSession lookup returns None → fail-quiet logging, no exception.
- `_query_completed_sessions_in_window(days=0)` → returns `[]` without querying.
- No completed sessions → `_sum_cost_and_turns([])` returns `(0.0, 0)`.

### Error State Rendering

- `get_analytics_summary()` outer `try/except` already returns zero dict on any error —
  unchanged. No user-visible rendering change; zeros are the existing fallback.

## Test Impact

- [ ] `tests/unit/test_analytics_query.py::TestQueryMetricTotal::test_total` — UPDATE: this
  test exercises `query_metric_total("session.cost_usd")` which is no longer called in
  production. Keep the test (validates the analytics.query module itself); add a code-comment
  noting the production code path now uses Popoto.
- [ ] `tests/integration/test_analytics_dashboard.py::TestDashboardAnalytics::test_analytics_summary_graceful_without_db`
  — REPLACE: monkeypatch `_query_completed_sessions_in_window` to raise; assert graceful zero
  fallback.
- [ ] `tests/integration/test_analytics_dashboard.py::TestDashboardAnalytics::test_analytics_summary_returns_valid_structure`
  — UPDATE: still valid (schema test); no logic changes.
- [ ] `tests/unit/test_harness_streaming.py:74-122` — REPLACE: the fictitious top-level
  `event.type == "tool_use"` fixture must be replaced with a fixture exercising the real
  `assistant.message.content[]` shape.
- [ ] Any test asserting `record_metric("session.cost_usd", ...)` is called — DELETE: emit
  sites are being removed. (`grep -rn "session.cost_usd\|session.turns" tests/`.)

### Tests pinning the 6-tuple arity (must update for new return shape)

At least **25 references across 5 test files** mock `_run_harness_subprocess` and unpack a
6-tuple. ALL update to the new 8-tuple (or `HarnessResult` dataclass) shape:

- [ ] `tests/unit/test_harness_token_capture.py::test_missing_usage_returns_none` — UPDATE
- [ ] `tests/unit/test_harness_token_capture.py::test_binary_not_found_returns_six_tuple` —
  UPDATE/RENAME (rename to `..._returns_eight_tuple` or `..._returns_harness_result`)
- [ ] `tests/unit/test_harness_token_capture.py` (other cases) — UPDATE per file inventory
- [ ] `tests/unit/test_sdk_client.py` — 10 mock sites; update all `fake_run` returns
- [ ] `tests/unit/test_harness_thinking_block_sentinel.py` — 5 mock sites
- [ ] `tests/unit/test_sdk_client_image_sentinel.py` — 5 mock sites
- [ ] `tests/integration/test_harness_env_pm_injection.py` — 3 mock sites

### New tests to add

- [ ] **NEW**: `tests/unit/test_sdk_client_harness.py::test_turn_count_persisted` — fixture
  harness run with `num_turns=2`; assert AgentSession `turn_count == 2`.
- [ ] **NEW**: `tests/unit/test_sdk_client_harness.py::test_turn_count_accumulates_across_fallback`
  — two harness invocations with `num_turns=2` and `num_turns=3`; assert AgentSession
  `turn_count == 5`.
- [ ] **NEW**: `tests/unit/test_sdk_client_harness.py::test_tool_call_count_persisted` —
  fixture harness run with N `assistant.message.content[].type == "tool_use"` blocks;
  assert AgentSession `tool_call_count == N`.
- [ ] **NEW**: `tests/integration/test_analytics_dashboard.py::test_cost_today_from_agent_session`
  — finalize a real test AgentSession with `total_cost_usd=1.23` and `turn_count=4`; call
  `get_analytics_summary()`; assert `cost_today_usd >= 1.23` and `turns_today >= 4`. Clean up
  the test session after via `AgentSession.delete()` (Popoto only — no raw Redis).
- [ ] **NEW**: `tests/unit/test_analytics_query_session_sums.py::test_query_session_sums_empty`
  — empty AgentSession query result returns `(0.0, 0)`.
- [ ] **NEW**: `tests/unit/test_analytics_query_session_sums.py::test_query_session_sums_popoto_failure`
  — Popoto failure returns `(0.0, 0)` without raising.

## Rabbit Holes

- **Backfilling historical `turn_count`**: Out of scope. Pre-fix sessions stay at 0.
- **Persisting `duration_ms`, `stop_reason`, `modelUsage`**: Out of scope. The dashboard does
  not surface them. Per the user direction (cycle-2): "remove any fields or methods that aren't
  absolutely necessary".
- **Replacing `session.started` / `session.completed` count metrics with AgentSession
  queries**: Out of scope. Those work correctly today.
- **Memory metric refactor**: Out of scope.
- **Real-time streaming analytics** (WebSocket, SSE): Not requested.
- **Optimization for very large `status="completed"` slices**: If the slice grows beyond
  a few thousand sessions, a Popoto-side date filter or pagination may be needed. Not a
  near-term concern; revisit when observable.

## Risks

### Risk 1: Harness `result` event field absence
**Impact:** A future claude CLI version drops `num_turns` → `turn_count` write becomes a
no-op and analytics regress to 0.
**Mitigation:** `int(data.get("num_turns") or 0)` defaults to 0. Plus the warn-once log when
`num_turns` is missing surfaces the regression in dev environments without crashing.

### Risk 2: `assistant`-event content-block shape drift
**Impact:** Tool_use blocks move under a sub-key → `tool_call_count` stays at 0.
**Mitigation:** Same fail-soft default. (`tool_call_count` is captured but not surfaced today,
so a regression here is invisible to users until a future dashboard surface uses it.)

### Risk 3: Return-shape change reaches every call site
**Impact:** A missing return-shape update in production or test fixtures causes runtime
unpack errors.
**Mitigation:** Test Impact section enumerates every site (3 production + 25+ test fixtures
across 5 files). Implementer should run `grep -rn "_run_harness_subprocess" agent/ tests/`
and verify every site updates.

### Risk 4: Popoto query slow with large completed-session slice
**Impact:** `AgentSession.query.filter(status="completed").all()` materializes the full slice
into memory; on a long-running deployment this could grow large.
**Mitigation:** Today the slice is small (a few hundred). Revisit with a date-filtered
Popoto query or pagination if it becomes observable. Wrapping in `try/except` already
returns zeros on failure — dashboard never crashes.

## Race Conditions

### Race 1: Fallback harness invocations and turn accumulation
**Location:** `agent/sdk_client.py` — `_run_harness_subprocess` is called from primary plus
fallback paths (image-dimension fallback, stale-UUID fallback)
**Trigger:** Fallback path reruns `_run_harness_subprocess`. The new write
`session.turn_count += this_num_turns` is read-modify-write.
**Mitigation:** Fallback paths run sequentially within a single `get_response_via_harness`
call (not concurrently). The accumulating `+=` is the desired semantic — fallback's counts
add to primary's. No concurrent writers for the same `session_id` from different
processes within a single harness invocation. (Concurrent multi-session writes would each
target a different `session_id` row, no contention.)

**Note (out of scope):** A separate read-modify-write race exists in
`accumulate_session_tokens` (concurrent worker + hook calls for `total_cost_usd`). That race
is real but **not addressed by this plan**.

## No-Gos (Out of Scope)

- Backfilling historical `turn_count` on pre-fix sessions
- Replacing `session.started` / `session.completed` analytics count metrics with AgentSession queries
- Memory metric refactor (`memory.recall_attempt`, `memory.extraction`)
- Persisting `duration_ms`, `stop_reason`, `modelUsage` (dashboard does not surface them)
- Any analytics that doesn't naturally live on AgentSession (pipeline metrics, crash counts)
- Backend-swap abstraction or Agent Communication Protocol work
- Parallel-run migration — cutover happens in one PR
- Atomic increment helper — accumulation uses Popoto field-assign
- Rewriting `accumulate_session_tokens` — its read-modify-write race is real but separate
- **Raw Redis access** — explicitly forbidden by repo architecture rules. No ZADD, no ZSET,
  no `POPOTO_REDIS_DB.zrangebyscore`, no parallel ledger. Popoto only.

## Update System

No update system changes required — internal change. No new config, no new dependencies, no
changes to `scripts/remote-update.sh`.

## Agent Integration

No agent integration required — internal analytics re-plumbing. No new CLI entry points, no
bridge changes, no MCP servers affected.

## Documentation

- [ ] Update `docs/features/dashboard.md` (if it exists) to note that cost/turn analytics
  are now derived from `AgentSession.total_cost_usd` and `AgentSession.turn_count` via
  Popoto query, not the analytics ledger
- [ ] Update `docs/features/unified-analytics.md` near line 107 — the existing
  `query_metric_total("session.cost_usd", days=7)` example becomes misleading after this
  change. Replace it with a non-session metric (e.g. `memory.recall_attempt`) and add a
  pointer noting that session-attributed sums now derive from AgentSession Popoto fields,
  not the metrics ledger.
- [ ] Update inline docstrings on `_run_harness_subprocess` (new return shape) and
  `get_analytics_summary` (new aggregation source)

## Success Criteria

- [ ] `curl -s localhost:8500/dashboard.json | jq .analytics` shows non-zero `cost_today_usd`
  and `turns_today` once at least one new session completes after deploy
- [ ] `curl -s localhost:8500/dashboard.json | jq '.sessions[].turn_count'` shows non-zero
  values for sessions that completed after deploy
- [ ] `grep -rn 'record_metric("session.cost_usd"\|record_metric("session.turns"' agent/ ui/`
  returns zero hits
- [ ] `grep -rn 'query_metric_total("session.cost_usd"\|query_metric_total("session.turns"' .`
  returns zero hits in production code
- [ ] `grep -rn 'POPOTO_REDIS_DB.zadd\|analytics:sessions:completed_at' .` returns zero hits
  in production code (raw Redis is forbidden — verify nothing leaked through)
- [ ] `grep -rn 'atomic_increment_session_field' .` returns zero hits
- [ ] Tests pass: `pytest tests/ -x -q`

## Team Orchestration

### Team Members

- **Builder**
  - Name: analytics-builder
  - Role: Implement code changes — harness counters, return-shape extension, AgentSession
    accumulating persist, Popoto-only analytics query, dead emit removal, test-fixture updates
  - Agent Type: builder
  - Resume: true

- **Validator**
  - Name: analytics-validator
  - Role: Run tests, grep for dead emit sites and raw-Redis leaks, end-to-end smoke
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: analytics-documentarian
  - Role: Update docstrings, `docs/features/unified-analytics.md` example, dashboard docs
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Extract `num_turns` from `result` event and add `assistant`-event handler
- **Task ID**: build-harness-counters
- **Depends On**: none
- **Validates**: `tests/unit/test_harness_streaming.py` (replace fictitious fixture),
  `tests/unit/test_harness_token_capture.py` (update for new return shape)
- **Assigned To**: analytics-builder
- **Agent Type**: builder
- **Parallel**: true
- In `_run_harness_subprocess`, initialize `num_turns: int = 0` and `_tool_call_count: int = 0`
  at the top, alongside `usage` / `cost_usd`.
- Extend the existing `result`-event handler at `agent/sdk_client.py:2472-2500` with
  `num_turns = int(data.get("num_turns") or 0)`. Log a one-time warning if `num_turns` is
  missing.
- ADD a new `assistant`-event handler branch (placed BEFORE the `stream_event` branch):
  ```python
  if event_type == "assistant":
      message = data.get("message", {}) or {}
      content_blocks = message.get("content", []) or []
      _tool_call_count += sum(1 for b in content_blocks if b.get("type") == "tool_use")
      continue
  ```
- Update all three production return points and the binary-not-found path to return the
  extended 8-tuple (or `HarnessResult` dataclass — pick one). New return shape:
  `(result_text, session_id, returncode, usage, cost_usd, stderr_snippet, num_turns, tool_call_count)`.
- Replace the fictitious `tests/unit/test_harness_streaming.py:74-122` fixture with one
  exercising the real `assistant.message.content[]` shape.

### 2. Update `get_response_via_harness` call sites and add accumulating Popoto write
- **Task ID**: build-harness-callers
- **Depends On**: build-harness-counters
- **Validates**: `grep -nE '_run_harness_subprocess' agent/sdk_client.py` returns three call
  sites all unpacking the new shape
- **Assigned To**: analytics-builder
- **Agent Type**: builder
- **Parallel**: false
- Update unpacks at lines ~2204, 2233, 2280 to use the extended shape.
- Immediately after the existing `accumulate_session_tokens(session_id, ...)` call (~line
  2304), add the accumulating Popoto write:
  ```python
  if session_id and (this_num_turns or this_tool_call_count):
      try:
          from models.agent_session import AgentSession
          session = AgentSession.query.filter(session_id=session_id).first()
          if session is not None:
              if this_num_turns:
                  session.turn_count = (session.turn_count or 0) + this_num_turns
              if this_tool_call_count:
                  session.tool_call_count = (session.tool_call_count or 0) + this_tool_call_count
              session.save()
      except Exception as e:
          logger.warning(
              "Failed to persist turn/tool counts for session %s: %s", session_id, e,
          )
  ```
  Accumulation is intentional — fallback subprocess paths add to prior counts.

### 3. Update test fixtures across 5 test files
- **Task ID**: build-test-fixtures
- **Depends On**: build-harness-counters
- **Validates**: `pytest tests/unit/test_sdk_client.py tests/unit/test_harness_token_capture.py
  tests/unit/test_harness_thinking_block_sentinel.py tests/unit/test_sdk_client_image_sentinel.py
  tests/integration/test_harness_env_pm_injection.py -x` — all pass
- **Assigned To**: analytics-builder
- **Agent Type**: builder
- **Parallel**: true
- Update each `fake_run`/`fake_subprocess` `AsyncMock(return_value=...)` to return the new
  shape. See Test Impact section for per-file inventory.
- Rename `test_binary_not_found_returns_six_tuple` to match the new shape and update its
  assertion.

### 4. Rewrite `get_analytics_summary()` to query AgentSession via Popoto
- **Task ID**: build-analytics-query
- **Depends On**: none
- **Validates**: `tests/integration/test_analytics_dashboard.py` (update),
  `tests/unit/test_analytics_query_session_sums.py` (new)
- **Assigned To**: analytics-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `_query_completed_sessions_in_window(days)` and `_sum_cost_and_turns(sessions)` helpers
  in `ui/data/analytics.py` per the Technical Approach section.
- Replace `query_metric_total("session.cost_usd", days=N)` and
  `query_metric_total("session.turns", days=N)` calls with helper invocations.
- Remove `query_metric_total` from imports if no longer used (keep `query_metric_count`).
- Add unit tests `test_query_session_sums_empty` and `test_query_session_sums_popoto_failure`.

### 5. Remove dead emit sites from `get_response_via_sdk`
- **Task ID**: build-remove-dead-emits
- **Depends On**: build-analytics-query
- **Validates**: `grep -rn 'record_metric.*session\.cost_usd\|record_metric.*session\.turns'
  agent/ ui/` returns zero hits
- **Assigned To**: analytics-builder
- **Agent Type**: builder
- **Parallel**: false
- Delete `record_metric("session.cost_usd", ...)` and `record_metric("session.turns", ...)`
  calls in `agent/sdk_client.py` (currently lines 1578-1580). Remove the surrounding
  `try/except` if nothing else uses it.

### 6. Validate
- **Task ID**: validate-all
- **Depends On**: build-test-fixtures, build-harness-callers, build-remove-dead-emits
- **Assigned To**: analytics-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/ -x -q` — all tests pass.
- Run `grep -rn 'record_metric.*session\.cost_usd\|record_metric.*session\.turns' agent/ ui/`
  — zero hits.
- Run `grep -rn 'query_metric_total.*session\.cost_usd\|query_metric_total.*session\.turns' .`
  — zero hits in production code.
- Run `grep -rn 'POPOTO_REDIS_DB.zadd\|analytics:sessions:completed_at' .` — **zero hits**
  (no raw Redis must leak through).
- Run `grep -rn 'atomic_increment_session_field' .` — zero hits.
- Verify `python -c "from ui.data.analytics import get_analytics_summary; print(get_analytics_summary())"`
  runs without error.
- End-to-end smoke: enqueue a tiny test AgentSession via
  `python -m tools.valor_session create --role pm --message "echo test" --project-key ai`,
  wait for completion, then
  `curl -s localhost:8500/dashboard.json | jq '.analytics.turns_today, .sessions[].turn_count'`
  shows non-zero values. Clean up via `AgentSession.delete()`.

### 7. Documentation
- **Task ID**: document-analytics
- **Depends On**: validate-all
- **Assigned To**: analytics-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Update `_run_harness_subprocess` docstring to describe the new return shape and
  `num_turns` / `tool_call_count` fields.
- Update `get_analytics_summary` docstring to reflect Popoto-derived aggregation.
- Update `docs/features/unified-analytics.md` near line 107: replace the
  `query_metric_total("session.cost_usd", days=7)` example with a non-session metric
  (e.g. `memory.recall_attempt`); add a note that session-attributed sums now derive from
  AgentSession Popoto fields.
- Update `docs/features/dashboard.md` if it exists.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No dead cost emits | `grep -rn 'record_metric.*session\.cost_usd' agent/ ui/` | exit code 1 |
| No dead turn emits | `grep -rn 'record_metric.*session\.turns' agent/ ui/` | exit code 1 |
| No stale `query_metric_total` (cost) | `grep -rn 'query_metric_total.*session\.cost_usd' ui/` | exit code 1 |
| No stale `query_metric_total` (turns) | `grep -rn 'query_metric_total.*session\.turns' ui/` | exit code 1 |
| No raw-Redis leak | `grep -rn 'POPOTO_REDIS_DB.zadd\|analytics:sessions:completed_at' .` | exit code 1 |
| No orphan atomic helper refs | `grep -rn 'atomic_increment_session_field' .` | exit code 1 |
| `_run_harness_subprocess` returns new shape | `grep -nE '_run_harness_subprocess.*->' agent/sdk_client.py` | matches updated annotation |
| `num_turns` flows to AgentSession | run a real session, then `python -m tools.valor_session inspect --id <ID>` | `turn_count > 0` |

## Critique Results

| Severity | Critic | Cycle | Finding | Addressed By | Implementation Note |
|----------|--------|-------|---------|--------------|---------------------|
| Blocker | B1 | 1 | `finalize_session` ZADD score was a `datetime` object, not numeric | DELETED in cycle 3 | ZSET removed entirely per user direction "never use redis directly". Issue dissolved. |
| Blocker | B2 | 1 | Plan invented a turn-counter subsystem when `num_turns` is in the harness `result` event | Step 1 (build-harness-counters) | One-line extraction; no subsystem. |
| Blocker | B3 | 1 | 6→8-tuple change breaks 25+ test fixtures | Steps 1, 2, 3 | Plan now lists every site to update; 8-tuple (or `HarnessResult` dataclass — pick one). |
| Blocker | B4 | 1 | `atomic_increment_session_field` would violate Popoto raw-Redis rule | DELETED in cycle 1 | Helper deleted; counts written via Popoto field-assign + accumulate. |
| Blocker | B5 | 1 | Persisting in `_run_harness_subprocess` would silently fail (Claude UUID vs AgentSession session_id) | Step 2 | Persistence moves to `get_response_via_harness`. |
| Blocker | B6 | 1 | Plan said "extend the assistant-event handler" but no such handler exists | Step 1 | Now says "ADD a new branch". |
| Blocker | O2 | 2 | ZSET rebuild path missing for pre-fix sessions | DISSOLVED in cycle 3 | No ZSET to rebuild — user direction removed it. |
| Concern | S1 | 2 | Use `update_fields=` with Popoto save | DISSOLVED in cycle 3 | No parallel ledger to update. |
| Concern | S2 | 2 | ZREMRANGEBYSCORE 8-day trim missing | DISSOLVED in cycle 3 | No ZSET; nothing to trim. |
| Concern | O1 | 2 | Warning log on ZADD failure missing | DISSOLVED in cycle 3 | No ZADD. |
| Concern | O3 | 2 | Missing-`num_turns` warn log | Step 1 | One-time `logger.warning` when `num_turns` is absent from a `result` event. |
| Concern | AD1 | 2 | Fallback subprocess paths overwrite `turn_count` instead of accumulating | Step 2 (`+=` accumulation) | Per user direction "probably accumulate, but i don't care that much. take the simple option" — write uses `(session.turn_count or 0) + this_num_turns`. |
| Concern | SI1 | 2 | Extracted `duration_ms` / `stop_reason` / `modelUsage` are unused | Solution + No-Gos | Per user direction "remove any fields or methods that aren't absolutely necessary" — extraction dropped. Only `num_turns` and tool_use counts (the dashboard's actual surface) are extracted. |
| Concern | CA1 | 2 | `docs/features/unified-analytics.md:107` example becomes misleading | Documentation task 7 | Doc task replaces the `query_metric_total("session.cost_usd")` example with a non-session metric and adds a pointer note. |
| Concern | Operator | 1 | Fictitious `tests/unit/test_harness_streaming.py:74-122` fixture | Test Impact + Step 1 | Replaced with `assistant.message.content[]` shape. |
| Concern | Skeptic | 1 | Two valid tool-use counting paths | Path A chosen (Step 1) | Path A wins on simplicity. |
| Concern | Archaeologist | 1 | `accumulate_session_tokens` race is unfixed | Race Conditions + No-Gos | Out of scope; future plan. |

## Plan Revision History

| Iteration | Date | Trigger | Summary |
|-----------|------|---------|---------|
| Initial | 2026-05-01 | New plan | First draft. Proposed `message_stop` turn-counter subsystem and atomic helper. |
| Revision 1 | 2026-05-01 (`17a826d8`) | Cycle-1 critique (B1–B4) | Spike replaced assumptions; atomic helper deleted; persistence moved to `get_response_via_harness`. |
| Revision 2 | 2026-05-01 | Stale verdict; PM re-dispatched | Revision metadata only. |
| Revision 3 | 2026-05-01 (this commit) | Cycle-2 critique + user direction | **Major simplification.** ZSET / ZADD / parallel ledger / atomic helper / rebuild path / 8-day trim **all deleted** per user rule "never use redis directly". Aggregation switches to `AgentSession.query.filter(status="completed").all()` Popoto query. Unused field extractions (`duration_ms`, `stop_reason`, `modelUsage`) dropped per user rule "remove any fields or methods that aren't absolutely necessary". Fallback path now accumulates via `+=` per user direction "probably accumulate". Plan shrank from ~1000 lines to ~500. |

**Files changed in implementation:**
- `agent/sdk_client.py` — `_run_harness_subprocess` return shape, new `assistant`-event
  handler, `num_turns` extraction
- `agent/sdk_client.py` — `get_response_via_harness` accumulating Popoto write
- `agent/sdk_client.py` — `get_response_via_sdk` dead emit deletion
- `ui/data/analytics.py` — Popoto-only aggregation; replaces two `query_metric_total` calls
- 5 test files — return-shape updates (3 production call sites, 25+ mock fixtures)
- `docs/features/unified-analytics.md` — example update

**No raw Redis. No ZSET. No parallel ledger.** Architecture-rule compliant.

---

## Open Questions

None. All cycle-2 findings are addressed by the user's architectural direction. Plan is ready
for cycle-3 critique.
