---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-05-01
revised: 2026-05-01
revision_count: 2
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
- `turn_count` and `tool_call_count` on AgentSession are populated after every harness call —
  `num_turns` arrives final (not delta) from the harness `result` event, so a one-shot field
  assignment via Popoto suffices (no atomic increment helper required for these fields)
- `total_cost_usd` and token totals continue to flow through the existing
  `accumulate_session_tokens` helper (out of scope to rewrite — its read-modify-write race is a
  separate concern, not a blocker for this fix)

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
- `models/agent_session.py:459-465, 710-711` — Popoto auto-converts float assignments to
  `DatetimeField` into `datetime` objects via the `__init__` shim and `__setattr__` hook —
  **confirmed; this means `session.completed_at = time.time()` already passes through Popoto's
  conversion path**
- `agent/sdk_client.py:2351-2548` — `_run_harness_subprocess` only handles `event_type ==
  "result"` and `event_type == "stream_event"` today; **NO `assistant`-event handler exists**
- `agent/health_check.py:288-298` — confirms the harness DOES emit top-level
  `{"type": "assistant", "message": {"content": [{"type": "tool_use", ...}, ...]}}` events
  (separate code path that already parses this shape from log lines)

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
involved (Redis ZADD/ZRANGEBYSCORE, Popoto ORM, Python asyncio subprocess) are mature and
well-understood.

## Spike Results

A spike was run before plan finalization. The findings below are empirical — they REPLACE the
earlier planning assumptions about a `message_stop` turn-counter subsystem and an atomic
increment helper.

**Finding 1: `num_turns` IS in the harness `result` event**

Sample shape from a real harness `result` event:

```json
{
  "type": "result",
  "subtype": "success",
  "num_turns": 2,
  "duration_ms": 4838,
  "total_cost_usd": 0.128,
  "stop_reason": "end_turn",
  "modelUsage": { ... }
}
```

Available fields: `num_turns`, `duration_ms`, `total_cost_usd`, `stop_reason`, `modelUsage`. The
existing `result`-event handler at `agent/sdk_client.py:2472-2500` already extracts `result`,
`session_id`, `usage`, `total_cost_usd` but stops short of these others. **Adding them is a
one-line change per field. No `message_stop` accumulation, no fictional event subsystem.**

**Finding 2: `assistant` events carry tool_use blocks; NO existing handler exists**

Confirmed by reading `agent/health_check.py:288-298` (which parses logged stream lines): the
real harness emits top-level `{"type": "assistant", "message": {"content": [{"type":
"tool_use", "name": ...}, ...]}}` events. Each `content_blocks[].type == "tool_use"` entry is
one tool invocation.

**Important:** `_run_harness_subprocess` today handles ONLY `event_type == "result"` and
`event_type == "stream_event"`. There is **no existing handler for `event_type ==
"assistant"`** — the implementation must ADD a new branch, not extend an existing one.

**Two valid counting paths exist:**

- **Path A (chosen):** Count `tool_use` blocks in the top-level `assistant` event's
  `message.content[]` array — fully assembled, no streaming reconstruction:
  ```python
  if event_type == "assistant":
      message = data.get("message", {}) or {}
      content_blocks = message.get("content", []) or []
      _tool_call_count += sum(1 for b in content_blocks if b.get("type") == "tool_use")
      continue
  ```
- **Path B (alternative):** Count `content_block_start` stream events where
  `content_block.type == "tool_use"`. Also valid; same arithmetic outcome.

Path A wins on simplicity (no streaming-state reconstruction; one accumulation site) and is
adopted in the Step by Step Tasks below. Path B remains as a fallback if the `assistant` shape
drifts.

**Finding 3 (resolved): Indexing strategy for time-range queries on AgentSession**

Confirmed: **Option B** — maintain a Redis sorted set keyed by completion timestamp
(`analytics:sessions:completed_at`), updated on the same write path that sets `completed_at`
in `finalize_session()`. `completed_at` is a plain `DatetimeField(null=True)` and cannot
become a `SortedField` without a sentinel default and a data migration. Option B is purely
additive: a `ZADD` call in `finalize_session()` writes
`{session.db_key.redis_key: ts}`. The analytics query does a
`ZRANGEBYSCORE(analytics:sessions:completed_at, start_ts, +inf)` and resolves each key to
its AgentSession for sum aggregation. Same pattern already used in
`tools/email_history/__init__.py`.

**Finding 4 (resolved): `completed_at` field type and ZADD score semantics**

Popoto's AgentSession at `models/agent_session.py:459-465` and `:710-711` **auto-converts**
float values assigned to `DatetimeField` into `datetime` objects. The existing line at
`models/session_lifecycle.py:390` already does `session.completed_at = time.time()` (a
float), and Popoto auto-converts it to datetime on write. **No model field change is
needed.** The model field continues to hold a datetime when read back.

What's NEW is an additive ZADD using a **locally-computed numeric `ts`** as the score. The
ZADD score is the locally-computed float — **not** the model field value. The score and the
model field are independent: the analytics query uses the sorted set (numeric score), it
does NOT read `completed_at` from the AgentSession.

**Original critique B1 fix:** the ZADD score must be a numeric float, not a `datetime` object.
This is achieved by always passing the locally-computed `ts = time.time()` to ZADD; Popoto's
auto-conversion of the model field is irrelevant to the index. The success criterion is NOT
"isinstance(session.completed_at, float)" — that would fail because of Popoto auto-conversion.

**Finding 5 (resolved): Where does the AgentSession write happen?**

`session_id_from_harness` (returned from `_run_harness_subprocess`) is the **Claude Code
transcript UUID**, NOT the AgentSession `session_id`. The AgentSession `session_id` is a
Telegram-derived identifier (e.g., `tg_project_chatid_msgid`), stored as an `IndexedField`
on the model. They are mapped via `_store_claude_session_uuid()` after each subprocess.
Inside `_run_harness_subprocess`, only the Claude UUID is known.

Filtering AgentSession by Claude UUID (`AgentSession.query.filter(session_id=
session_id_from_harness).first()`) would silently return nothing because `session_id` holds
the Telegram-derived ID, not the Claude UUID.

**Decision:** persist `turn_count` and `tool_call_count` in `get_response_via_harness`
(NOT in `_run_harness_subprocess`). `get_response_via_harness` already has the AgentSession
`session_id` in scope as a function parameter — same identifier used for the existing
`accumulate_session_tokens(session_id, ...)` call at line 2304. Place the new write block
immediately after that call. This is symmetric with the tokens path: same scope, same
identifier, same fail-quiet pattern.

**Finding 6 (resolved): Return-shape change from `_run_harness_subprocess`**

The earlier critique flagged a 6→8-tuple change as breaking call sites. Reality check shows
there are exactly **three production call sites** (lines 2204, 2233, 2280 in
`get_response_via_harness`, all in the same file) PLUS **at least 25 references across 5
test files** that mock `_run_harness_subprocess` and unpack a 6-tuple — including
`tests/unit/test_harness_token_capture.py::test_binary_not_found_returns_six_tuple` which
literally asserts `len(out) == 6`. The original critique B3 ("zero existing tests reference
the 6-tuple arity") was wrong — the tests DO pin the arity.

**Decision:** swap the positional return tuple for a `HarnessResult` dataclass with named
fields. This is a bounded refactor — three production call sites + 5 test files, all
mechanical updates from `(...) = await ...` to `result = await ...; result.field`. The
dataclass wins on readability for future fields (`duration_ms`, `stop_reason`,
`model_usage`). **Fallback:** extend to 8-tuple if dataclass switch is too invasive — same
set of files updated, mechanical change. Pick one and apply consistently.

**Decision (post-spike): Drop the atomic-increment helper**

Because `num_turns` and `tool_call_count` arrive as final values per harness call (not deltas
accumulated over external events), a single Popoto field assignment via the existing write
path is sufficient. There is no concurrent-writer scenario for these fields within a single
session run, and no read-modify-write race that needs a Redis-level atomic. The
`atomic_increment_session_field` helper proposed in earlier drafts is therefore **deleted**
from this plan.

**`accumulate_session_tokens` is left untouched** — its read-modify-write race on
`total_cost_usd` and token totals is a real but separate issue, **out of scope** for this
fix. A future plan can revisit it. The original critique B4 (atomic helper would violate the
Popoto raw-Redis rule by writing `HINCRBYFLOAT`/`HINCRBY` directly against Popoto-managed
hashes) is resolved by deletion.

## Data Flow

**Current (broken) path for analytics:**

1. Session runs via harness: `accumulate_session_tokens` writes `total_cost_usd` to AgentSession
2. `record_metric("session.cost_usd", cost)` in `get_response_via_sdk` — **never reached**
3. `ui/data/analytics.py::get_analytics_summary()` calls `query_metric_total("session.cost_usd")`
4. Returns 0.0 because no emit ever happened

**Desired path after fix:**

**Turn/tool counting (no atomic helper, persist in `get_response_via_harness`):**
1. Harness subprocess runs. Inside the existing `result`-event handler at
   `agent/sdk_client.py:2472-2500`, extract `num_turns`, `duration_ms`, `stop_reason`, and
   `modelUsage` directly from the event payload alongside the already-extracted `result`,
   `session_id`, `usage`, and `total_cost_usd`.
2. Inside a NEW `assistant`-event handler branch (added parallel to the existing `result` and
   `stream_event` branches), count tool_use blocks in `data["message"]["content"]` and
   accumulate into a local `_tool_call_count`.
3. After the subprocess loop exits, `_run_harness_subprocess` returns a `HarnessResult`
   dataclass (or 8-tuple, fallback) carrying the new `num_turns` and `tool_call_count`
   fields alongside the existing six.
4. `get_response_via_harness` (which has the AgentSession `session_id` in scope) writes
   final values to AgentSession via the standard Popoto field-assign + save path,
   immediately after the existing `accumulate_session_tokens(session_id, ...)` call.

**Analytics aggregation:**
1. `finalize_session()` computes `ts = time.time()` (numeric float). The existing line
   `session.completed_at = ts` is unchanged — Popoto auto-converts to datetime on write.
   New: `ZADD analytics:sessions:completed_at {session.db_key.redis_key: ts}` using the
   locally-computed numeric `ts` as the score.
2. `get_analytics_summary()` calls `ZRANGEBYSCORE` for the time window → set of Redis keys
3. Pipeline-fetches `total_cost_usd` and `turn_count` from each AgentSession hash and sums
4. Returns correct non-zero values

**No atomic-increment work.** `accumulate_session_tokens` is untouched. `total_cost_usd` and
token totals continue to flow through the existing helper. The proposed
`atomic_increment_session_field` is deleted from this plan.

## Architectural Impact

- **New dependencies**: None — uses existing `POPOTO_REDIS_DB` and Popoto patterns
- **Interface changes**: `_run_harness_subprocess` return shape changes from 6-tuple to a
  `HarnessResult` dataclass (or 8-tuple as fallback) carrying two new fields (`num_turns`,
  `tool_call_count`). Three production call sites and 25+ test fixtures across 5 test files
  update to the new shape. **`accumulate_session_tokens` is untouched.** No new atomic helper.
- **Coupling**: `finalize_session()` in `models/session_lifecycle.py` gains a Redis side-write
  (additive ZADD). `ui/data/analytics.py` gains a dependency on `models.agent_session.AgentSession`
  (currently only depends on `analytics.query`). `get_response_via_harness` gains an import of
  `AgentSession` and a new fail-quiet write block for `turn_count` / `tool_call_count`.
- **Data ownership**: Session-attributed sums live exclusively on AgentSession. Analytics module
  retains ownership of counts (`session.started`, `session.completed`, `memory.*`).
- **Reversibility**: Low-risk — the analytics query change is isolated. The sorted-set ZADD is
  additive. Reverting means removing the ZADD, restoring the two `query_metric_total` calls,
  and reverting the dataclass to the 6-tuple.

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (spike resolved all open questions; dataclass return shape preferred,
  8-tuple fallback acceptable)
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies. Redis is already running.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis up | `python -c "from popoto.redis_db import POPOTO_REDIS_DB; POPOTO_REDIS_DB.ping()"` | Sorted-set writes need Redis |

## Solution

### Key Elements

- **`result`-event field extraction**: At `agent/sdk_client.py:2472-2500`, the existing handler
  pulls `result`, `session_id`, `usage`, `total_cost_usd`. Add four more pulls: `num_turns`,
  `duration_ms`, `stop_reason`, `modelUsage` from the same `data` dict.
- **NEW `assistant`-event handler**: ADD a new `if event_type == "assistant":` branch (no such
  handler exists today — only `result` and `stream_event` are handled). Accumulate
  `len([b for b in data["message"]["content"] if b.get("type") == "tool_use"])` into a local
  `_tool_call_count`. (Path A in Spike Results.)
- **`HarnessResult` dataclass return**: Replace the 6-tuple positional return from
  `_run_harness_subprocess` with a `HarnessResult` dataclass carrying the existing six fields
  PLUS `num_turns` and `tool_call_count`. Three production call sites + 25+ test fixtures
  update to attribute access. (8-tuple fallback if dataclass is too invasive.)
- **AgentSession field write in `get_response_via_harness`**: Persist the new counters via
  Popoto field-assign + save, immediately after the existing `accumulate_session_tokens`
  call. Use the AgentSession `session_id` (already in scope as a parameter), NOT the Claude
  UUID. **No atomic helper needed** — `num_turns` is final-not-delta, written once per call.
- **`finalize_session` numeric ZADD**: Compute `ts = time.time()` (float). The existing line
  `session.completed_at = time.time()` is unchanged (Popoto auto-converts to datetime). Add
  an additive `ZADD analytics:sessions:completed_at {redis_key: ts}` using the locally-
  computed numeric `ts` as the score (B1 fix — score is numeric float, not the model field).
- **Analytics re-aggregation**: `get_analytics_summary()` queries AgentSession via the sorted
  set for the time window, sums `total_cost_usd` and `turn_count` directly from model fields.
- **Dead emit removal**: Delete `record_metric("session.cost_usd", ...)` and
  `record_metric("session.turns", ...)` from `sdk_client.py` (`get_response_via_sdk` path).

### Flow

Session runs → harness `result` event yields `num_turns` directly → `_run_harness_subprocess`
counts tool_use blocks from `assistant` events → returns `HarnessResult(...)` →
`get_response_via_harness` writes final values to `session.turn_count` and
`session.tool_call_count` via Popoto save → session completes → `finalize_session` writes
`session.completed_at` (Popoto auto-converts to datetime) and ZADDs the numeric `ts` to the
sorted set → dashboard queries sorted set → sums AgentSession fields → returns correct
non-zero analytics.

### Technical Approach

1. **Define `HarnessResult` dataclass** at module scope in `agent/sdk_client.py`:
   ```python
   from dataclasses import dataclass

   @dataclass
   class HarnessResult:
       result_text: str | None
       session_id_from_harness: str | None
       returncode: int | None
       usage: dict | None
       cost_usd: float | None
       stderr_snippet: str | None
       num_turns: int = 0
       tool_call_count: int = 0
   ```
   (Fallback: extend the 6-tuple to an 8-tuple. Same set of files updated either way.)

2. **Extend the `result`-event handler** at `agent/sdk_client.py:2472-2500`. The block already
   pulls `result`, `session_id`, `usage`, `total_cost_usd` from `data`. Add:
   ```python
   num_turns = int(data.get("num_turns") or 0)
   duration_ms = int(data.get("duration_ms") or 0)
   stop_reason = data.get("stop_reason")
   model_usage = data.get("modelUsage")
   ```
   Initialize all four locals to safe defaults (`0`, `0`, `None`, `None`) at the top of
   `_run_harness_subprocess` so they exist even on early exits.

3. **ADD a new `assistant`-event handler branch** in `_run_harness_subprocess` (parallel to
   the existing `result` and `stream_event` branches; place BEFORE `stream_event`):
   ```python
   if event_type == "assistant":
       message = data.get("message", {}) or {}
       content_blocks = message.get("content", []) or []
       _tool_call_count += sum(1 for b in content_blocks if b.get("type") == "tool_use")
       continue
   ```
   Initialize `_tool_call_count = 0` at the top of `_run_harness_subprocess`.
   (Path A from Spike Results. Path B via `content_block_start` is a fallback.)

4. **Update all three production return points** in `_run_harness_subprocess` (lines ~2540,
   ~2546, ~2548) PLUS the binary-not-found path (~line 2419) to construct `HarnessResult(...)`
   with the new fields. Update docstring to describe the new return type.

5. **Update all three call sites in `get_response_via_harness`** (lines 2204, 2233, 2280) to
   unpack via attribute access:
   ```python
   harness_result = await _run_harness_subprocess(...)
   result_text = harness_result.result_text
   session_id_from_harness = harness_result.session_id_from_harness
   returncode = harness_result.returncode
   usage = harness_result.usage
   cost_usd = harness_result.cost_usd
   stderr_snippet = harness_result.stderr_snippet
   ```
   (Or, with 8-tuple fallback: extend the 6-element unpack to 8.) Done at all three call sites.

6. **AgentSession field write** in `get_response_via_harness`, immediately after the existing
   `accumulate_session_tokens(session_id, ...)` call (around line 2304). Use the
   AgentSession `session_id` parameter (Telegram-derived ID), **NOT** the Claude UUID:
   ```python
   if session_id and (harness_result.num_turns or harness_result.tool_call_count):
       try:
           from models.agent_session import AgentSession
           session = AgentSession.query.filter(session_id=session_id).first()
           if session is not None:
               if harness_result.num_turns:
                   session.turn_count = harness_result.num_turns
               if harness_result.tool_call_count:
                   session.tool_call_count = harness_result.tool_call_count
               session.save()
       except Exception as e:
           logger.warning(
               "Failed to persist turn_count/tool_call_count for session %s: %s",
               session_id, e,
           )
   ```
   This is a one-shot write per harness invocation; no read-modify-write race because
   `num_turns` arrives final from the harness and is written once per session run.
   `duration_ms`, `stop_reason`, `model_usage` are extracted but NOT persisted in this plan
   (see Open Questions for follow-up).

7. **`finalize_session` in `models/session_lifecycle.py:390`**: The existing line
   `session.completed_at = time.time()` is **unchanged** (Popoto auto-converts the float to
   datetime). Add an additive ZADD using a locally-computed numeric `ts`:
   ```python
   ts = time.time()
   session.completed_at = ts          # Popoto auto-converts to datetime — unchanged behavior
   session.save()                     # existing line
   try:
       from popoto.redis_db import POPOTO_REDIS_DB
       POPOTO_REDIS_DB.zadd(
           "analytics:sessions:completed_at",
           {session.db_key.redis_key: ts},      # numeric float as score (B1 fix)
       )
   except Exception as e:
       logger.debug(
           "[lifecycle] ZADD analytics:sessions:completed_at failed (non-fatal): %s", e
       )
   ```
   The ZADD score is the locally-computed `ts` — not the model field value. The score and
   the model field need not stay equal across reads; the analytics query uses the sorted
   set (numeric score), it does NOT read `completed_at` from the AgentSession. **No
   field-type change is needed.**

8. **`ui/data/analytics.py::get_analytics_summary()`**: Replace the two `query_metric_total`
   calls with a new helper `_query_session_sums(days)`:
   ```python
   def _query_session_sums(days: int) -> tuple[float, int]:
       try:
           from popoto.redis_db import POPOTO_REDIS_DB
           if days <= 0:
               return (0.0, 0)
           start_ts = time.time() - days * 86400
           member_keys = POPOTO_REDIS_DB.zrangebyscore(
               "analytics:sessions:completed_at", start_ts, "+inf"
           )
           if not member_keys:
               return (0.0, 0)
           pipe = POPOTO_REDIS_DB.pipeline()
           for k in member_keys:
               pipe.hget(k, "total_cost_usd")
               pipe.hget(k, "turn_count")
           values = pipe.execute()
           sum_cost = 0.0
           sum_turns = 0
           for i in range(0, len(values), 2):
               cost_raw, turns_raw = values[i], values[i + 1]
               try:
                   if cost_raw is not None:
                       sum_cost += float(cost_raw)
                   if turns_raw is not None:
                       sum_turns += int(turns_raw)
               except (TypeError, ValueError):
                   continue
           return (sum_cost, sum_turns)
       except Exception:
           return (0.0, 0)
   ```
   Replace `query_metric_total("session.cost_usd", days=N)` and
   `query_metric_total("session.turns", days=N)` calls with `_query_session_sums(N)` unpack.
   Session count metrics (`session.started`, `session.completed`) are unchanged.

9. **Remove dead emit sites**: Delete `record_metric("session.cost_usd", ...)` and
   `record_metric("session.turns", ...)` from `get_response_via_sdk` in `sdk_client.py`
   (currently lines 1578-1580). Leave the surrounding `try/except` block intact if other
   code uses it; otherwise remove the whole try block.

## Failure Path Test Strategy

### Exception Handling Coverage

- AgentSession field-write block (`session.turn_count = ...`, `session.tool_call_count = ...`,
  `session.save()`): wrapped in `try/except` with `logger.warning` — test that a Popoto failure
  does NOT propagate to the caller (fail-quiet contract — session output must still return)
- `finalize_session` ZADD: best-effort block — test that a ZADD failure does NOT affect the
  terminal status write (status still saved)
- `_query_session_sums`: test that a Redis failure returns `(0.0, 0)` rather than raising

### Empty/Invalid Input Handling

- `harness_result.num_turns == 0` and `harness_result.tool_call_count == 0` → field-write
  block is skipped entirely (no Popoto round-trip)
- `session_id` is None → field-write block is skipped
- AgentSession lookup returns None → fail-quiet logging, no exception
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
- [ ] `tests/unit/test_harness_streaming.py:74-122` — DELETE/REPLACE: the fixture asserting a
  top-level `event.type == "tool_use"` shape is fictitious (the spike confirmed the real
  harness never emits that event shape). Replace with a fixture exercising the real
  `assistant.message.content[]` path so the new tool_use accumulation has correct coverage.
- [ ] Any test that asserts `record_metric("session.cost_usd", ...)` is called — DELETE: emit
  sites are being removed. Search: `grep -rn "session.cost_usd\|session.turns" tests/`
- [ ] No tests reference an `atomic_increment_session_field` helper (none was ever shipped) —
  zero impact from dropping that proposed helper.

### Tests pinning the 6-tuple arity (must update for new return shape)

There are at least **25 references across 5 test files** that mock `_run_harness_subprocess`
and unpack its 6-tuple return. ALL must be updated to the new return shape (`HarnessResult`
dataclass attributes OR 8-tuple positions, depending on the chosen approach). Inventory:

- [ ] **`tests/unit/test_harness_token_capture.py::test_missing_usage_returns_none`** — UPDATE:
  6-tuple unpack `_, _, _, usage_out, cost_out, _ = await _run_harness_subprocess(...)`.
- [ ] **`tests/unit/test_harness_token_capture.py::test_binary_not_found_returns_six_tuple`** —
  UPDATE/RENAME: explicitly asserts `len(out) == 6`. If dataclass-route, rename to
  `test_binary_not_found_returns_harness_result` and assert `isinstance(out, HarnessResult)`.
- [ ] **`tests/unit/test_harness_token_capture.py` (other cases)** — UPDATE per file inventory.
- [ ] **`tests/unit/test_sdk_client.py`** — 10 mock sites. Each `fake_run` body returns a
  tuple; update all to the new shape.
- [ ] **`tests/unit/test_harness_thinking_block_sentinel.py`** — 5 mock sites.
- [ ] **`tests/unit/test_sdk_client_image_sentinel.py`** — 5 mock sites.
- [ ] **`tests/integration/test_harness_env_pm_injection.py`** — 3 mock sites.

### New tests to add

- [ ] **NEW**: `tests/unit/test_sdk_client_harness.py::test_turn_count_persisted` — assert that
  after a fixtured harness run with `num_turns=2` in the result event, the AgentSession has
  `turn_count == 2`.
- [ ] **NEW**: `tests/unit/test_sdk_client_harness.py::test_tool_call_count_persisted` — assert
  that after a fixtured harness run with N `assistant.message.content[].type == "tool_use"`
  blocks, the AgentSession has `tool_call_count == N`.
- [ ] **NEW**: `tests/unit/test_session_lifecycle.py::test_finalize_session_zadd` — assert that
  `finalize_session()` writes `analytics:sessions:completed_at` with `session.db_key.redis_key`
  as the member and a numeric float as the score.
- [ ] **NEW**: `tests/unit/test_session_lifecycle.py::test_finalize_session_zadd_failure_is_quiet`
  — assert that a ZADD failure does NOT prevent `session.status = "completed"` from being saved.
- [ ] **NEW**: `tests/integration/test_analytics_dashboard.py::test_cost_today_from_agent_session`
  — finalize a real test AgentSession with `total_cost_usd=1.23` and `turn_count=4`, then call
  `get_analytics_summary()` and assert `cost_today_usd >= 1.23` and `turns_today >= 4`.
- [ ] **NEW**: `tests/unit/test_analytics_query_session_sums.py::test_query_session_sums_empty`
  — assert empty sorted set returns `(0.0, 0)` without raising.
- [ ] **NEW**: `tests/unit/test_analytics_query_session_sums.py::test_query_session_sums_redis_failure`
  — assert Redis failure returns `(0.0, 0)` without raising.

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
- **Pipeline aggregation optimization** (Redis PIPELINE for bulk HGET): Already pipelined in
  the `_query_session_sums` implementation above; no further optimization needed.

## Risks

### Risk 1: Sorted-set key format
**Impact:** If the key stored in the sorted set doesn't match how AgentSession hashes are keyed
in Redis, `ZRANGEBYSCORE` returns keys that can't be resolved to AgentSession objects.
**Mitigation:** Use `session.db_key.redis_key` (same key used by the defensive srem in
`finalize_session` at line 403) — this is the canonical key string already used for index ops.

### Risk 2: Harness `result` event field absence
**Impact:** If a future claude CLI version drops `num_turns` from the `result` event, the new
`session.turn_count` write becomes a no-op and analytics regresses to 0 turns/day.
**Mitigation:** `int(data.get("num_turns") or 0)` defaults to 0 — no exception. Document this
as a known signal source dependency in the Verification table. The dashboard still renders;
the value is just stale-zero. Log once at warning level if `num_turns` is missing on a
`result` event in dev environments.

### Risk 3: `assistant`-event content-block shape drift
**Impact:** If the `assistant` event's `message.content[]` shape changes (e.g., tool_use blocks
move under a sub-key), `_tool_call_count` stays at 0.
**Mitigation:** Same fail-soft default. Path B (`content_block_start`) remains as a fallback
counting site if Path A drifts; spike confirmed both shapes appear in the real stream.

### Risk 4: Return-shape change reaches every call site
**Impact:** Missing one return-shape update (production or test fixture) causes an unpack error
at runtime or test failure.
**Mitigation:** The Test Impact section enumerates every site (3 production + 25+ test fixtures
across 5 files). The implementer should run `grep -rn "_run_harness_subprocess" agent/ tests/`
to enumerate all sites before submitting and verify every one is updated. CI will fail loudly
on any missed site (test failure or runtime ImportError).

## Race Conditions

### Race 1: ZADD in `finalize_session` vs. analytics query read
**Location:** `models/session_lifecycle.py:~390` (ZADD), `ui/data/analytics.py` (ZRANGEBYSCORE)
**Trigger:** Session finalizes just before a dashboard refresh; ZADD and ZRANGEBYSCORE are not
transactional.
**Data prerequisite:** ZADD must complete before ZRANGEBYSCORE reads the window.
**State prerequisite:** None.
**Mitigation:** Redis operations are serialized per connection; ZADD is atomic. Worst case: a
session that completed in the same millisecond as the dashboard refresh doesn't appear until
the next refresh cycle. This is acceptable — dashboard is a polling endpoint.

### Race 2: Fallback harness invocations and `turn_count` overwrite
**Location:** `agent/sdk_client.py` — `_run_harness_subprocess` is called from primary plus
fallback paths (image-dimension fallback, stale-UUID fallback)
**Trigger:** A fallback path reruns `_run_harness_subprocess`. The new `session.turn_count =
num_turns` write is an assignment (not an increment), so each invocation overwrites the prior
value with its own final `num_turns`.
**Data prerequisite:** Each invocation reads fresh `num_turns` from its own `result` event.
**State prerequisite:** None — assignments are idempotent within a single harness call.
**Mitigation:** Assignment semantics are exactly what we want: the final harness call's value
wins. If a primary fails and a fallback succeeds, the fallback's `num_turns` is the truth. If
both succeed (shouldn't happen), the later value wins. No double-counting.

**Note (out of scope):** A separate read-modify-write race exists in
`accumulate_session_tokens` (concurrent worker + hook calls for `total_cost_usd` and token
totals). That race is real but **not addressed by this plan**. Critique blocker B4 (atomic
helper vs. Popoto raw-Redis rule) is resolved by simply not introducing the helper. A future
plan can revisit `accumulate_session_tokens` if the race becomes observable.

## No-Gos (Out of Scope)

- Backfilling historical `turn_count` on pre-fix sessions
- Replacing `session.started` / `session.completed` analytics count metrics with AgentSession queries
- Memory metric refactor (`memory.recall_attempt`, `memory.extraction`)
- Any analytics that doesn't naturally live on AgentSession (pipeline metrics, crash counts)
- Making `started_at` or `completed_at` a `SortedField` on AgentSession
- Backend-swap abstraction or Agent Communication Protocol work
- Parallel-run migration — cutover happens in one PR, no dual-write period
- Atomic increment helper (`atomic_increment_session_field`) — `num_turns` is final-not-delta
- Rewriting `accumulate_session_tokens` — its read-modify-write race is real but separate

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
- [ ] Update inline docstrings on `_run_harness_subprocess` (new return type) and
  `get_analytics_summary` (new aggregation source) to reflect the new implementation
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
- [ ] `grep -rn 'atomic_increment_session_field' .` returns zero hits — proposed helper was
  deleted from this plan and must NOT appear in the final implementation
- [ ] `_run_harness_subprocess` returns a `HarnessResult` dataclass (or extended 8-tuple) — all
  three production call sites and 25+ test fixtures updated to match
- [ ] ZADD score is a numeric float (verified by `ZRANGEBYSCORE analytics:sessions:completed_at
  -inf +inf WITHSCORES` showing numeric scores)
- [ ] `memory.recall_attempt`, `memory.extraction`, `session.started`, `session.completed`
  metrics continue to populate `dashboard.json.analytics` correctly
- [ ] Tests pass: `pytest tests/ -x -q`

## Team Orchestration

### Team Members

- **Builder (analytics-rewrite)**
  - Name: analytics-builder
  - Role: Implement all code changes: HarnessResult dataclass, harness counters, new assistant-event handler, AgentSession persist block, finalize ZADD, analytics query rewrite, dead emit removal, test-fixture updates
  - Agent Type: builder
  - Resume: true

- **Validator (analytics-rewrite)**
  - Name: analytics-validator
  - Role: Run tests, grep for dead emit sites, verify schema compatibility, run end-to-end smoke
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: analytics-documentarian
  - Role: Update docstrings and feature docs
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Define `HarnessResult` dataclass and extract counters from harness events
- **Task ID**: build-harness-result
- **Depends On**: none
- **Validates**: `tests/unit/test_harness_streaming.py` (replace fictitious fixture with real shape), `tests/unit/test_harness_token_capture.py` (update for new return shape)
- **Assigned To**: analytics-builder
- **Agent Type**: builder
- **Parallel**: true
- **Implementation Note (post-spike):** the `result` event already carries `num_turns`. No `message_stop` accumulation. The return shape changes — the original "no return-tuple growth" claim was based on a wrong assumption about where persistence would happen.
- Define `HarnessResult` dataclass at module scope in `agent/sdk_client.py` (preferred), OR
  extend `_run_harness_subprocess`'s return tuple to 8 positions (fallback). Pick one and use it
  consistently.
- In `_run_harness_subprocess`, initialize at the top alongside `usage` / `cost_usd`:
  ```python
  num_turns: int = 0
  duration_ms: int = 0
  stop_reason: str | None = None
  model_usage: dict | None = None
  _tool_call_count: int = 0
  ```
- Extend the existing `result`-event handler at `agent/sdk_client.py:2472-2500` to populate
  `num_turns`, `duration_ms`, `stop_reason`, `model_usage` from `data.get(...)`.
- **ADD a new** `assistant`-event handler branch (no such handler exists today):
  ```python
  if event_type == "assistant":
      message = data.get("message", {}) or {}
      content_blocks = message.get("content", []) or []
      _tool_call_count += sum(1 for b in content_blocks if b.get("type") == "tool_use")
      continue
  ```
  Place this branch BEFORE the `if event_type == "stream_event":` branch.
- Update all three production return points (lines ~2540, ~2546, ~2548) plus the
  binary-not-found path (~line 2419) to construct `HarnessResult(...)` (or the 8-tuple)
  with the new fields.
- Delete the fictitious `tests/unit/test_harness_streaming.py:74-122` fixture for top-level
  `event.type == "tool_use"`; replace with a fixture exercising the real
  `assistant.message.content[]` path.

### 2. Update all three call sites in `get_response_via_harness` and add AgentSession persist
- **Task ID**: build-harness-callers
- **Depends On**: build-harness-result
- **Validates**: `grep -nE '_run_harness_subprocess' agent/sdk_client.py` returns three production call sites all unpacking the new shape
- **Assigned To**: analytics-builder
- **Agent Type**: builder
- **Parallel**: false
- Update unpacks at lines 2204, 2233, 2280 in `get_response_via_harness` to use the new
  return shape (dataclass attributes or 8-tuple positions).
- The existing `accumulate_session_tokens(session_id, ...)` call at line 2304 is **untouched**
  — it continues to use `usage` and `cost_usd` (now accessed via dataclass / extended tuple).
- Add the AgentSession turn/tool persist block immediately after the
  `accumulate_session_tokens` call. Use the AgentSession `session_id` (already in scope as a
  parameter to `get_response_via_harness`):
  ```python
  if session_id and (harness_result.num_turns or harness_result.tool_call_count):
      try:
          from models.agent_session import AgentSession
          session = AgentSession.query.filter(session_id=session_id).first()
          if session is not None:
              if harness_result.num_turns:
                  session.turn_count = harness_result.num_turns
              if harness_result.tool_call_count:
                  session.tool_call_count = harness_result.tool_call_count
              session.save()
      except Exception as e:
          logger.warning(
              "Failed to persist turn_count/tool_call_count for session %s: %s",
              session_id, e,
          )
  ```

### 3. Update test fixtures across 5 test files
- **Task ID**: build-test-harness-fixtures
- **Depends On**: build-harness-result
- **Validates**: `pytest tests/unit/test_sdk_client.py tests/unit/test_harness_token_capture.py tests/unit/test_harness_thinking_block_sentinel.py tests/unit/test_sdk_client_image_sentinel.py tests/integration/test_harness_env_pm_injection.py -x` — all pass
- **Assigned To**: analytics-builder
- **Agent Type**: builder
- **Parallel**: true
- Update each `fake_run`/`fake_subprocess` `AsyncMock(return_value=...)` to return the new
  shape (dataclass or 8-tuple). See Test Impact section for the exact per-file inventory.
- Update `test_binary_not_found_returns_six_tuple` to assert the new shape (rename to
  `test_binary_not_found_returns_harness_result` if dataclass is chosen).
- Update the 6-tuple unpack in `test_missing_usage_returns_none` to match.

### 4. Add `ZADD` to `finalize_session` (additive — no model field change)
- **Task ID**: build-finalize-zadd
- **Depends On**: none
- **Validates**: `tests/unit/test_session_lifecycle.py::test_finalize_session_zadd` (new)
- **Assigned To**: analytics-builder
- **Agent Type**: builder
- **Parallel**: true
- **Implementation Note (B1 corrected):** The existing line `session.completed_at = time.time()`
  already passes a numeric float; Popoto auto-converts to datetime. **No model field change.**
  Add an additive ZADD using a locally-computed numeric `ts`. The ZADD score is the locally-
  computed float, not the model field value.
- In `models/session_lifecycle.py`, modify around line 390:
  ```python
  ts = time.time()
  session.completed_at = ts          # Popoto auto-converts to datetime — unchanged
  session.save()                     # existing line
  try:
      from popoto.redis_db import POPOTO_REDIS_DB
      POPOTO_REDIS_DB.zadd(
          "analytics:sessions:completed_at",
          {session.db_key.redis_key: ts},      # numeric float as score
      )
  except Exception as e:
      logger.debug(
          "[lifecycle] ZADD analytics:sessions:completed_at failed (non-fatal): %s", e
      )
  ```
- Add a unit test `test_finalize_session_zadd` that asserts the sorted-set entry exists with a
  numeric float score after `finalize_session(session, "completed")` returns.
- Add a unit test `test_finalize_session_zadd_failure_is_quiet` that monkeypatches
  `POPOTO_REDIS_DB.zadd` to raise and asserts the session still saves with `status="completed"`.

### 5. Rewrite `get_analytics_summary()` to query AgentSession via the sorted set
- **Task ID**: build-analytics-query
- **Depends On**: build-finalize-zadd
- **Validates**: `tests/integration/test_analytics_dashboard.py` (update), new unit tests for `_query_session_sums`
- **Assigned To**: analytics-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `_query_session_sums(days: int) -> tuple[float, int]` helper in `ui/data/analytics.py`
  per the Technical Approach section above. Wrap in a try/except returning `(0.0, 0)` on any
  Redis failure.
- Replace `query_metric_total("session.cost_usd", days=N)` and
  `query_metric_total("session.turns", days=N)` calls with `_query_session_sums(N)` unpack.
- Remove `query_metric_total` from imports if no longer used (keep `query_metric_count`).
- Add unit tests `test_query_session_sums_empty` and `test_query_session_sums_redis_failure`
  per the Test Impact section.

### 6. Remove dead emit sites from `get_response_via_sdk`
- **Task ID**: build-remove-dead-emits
- **Depends On**: build-analytics-query
- **Validates**: `grep -rn 'record_metric.*session\.cost_usd\|record_metric.*session\.turns' agent/ ui/` returns zero hits
- **Assigned To**: analytics-builder
- **Agent Type**: builder
- **Parallel**: false
- Delete `record_metric("session.cost_usd", cost, dims)` and `record_metric("session.turns", float(turns), dims)` calls in `agent/sdk_client.py` (currently lines 1578-1580).
- Leave the surrounding `try/except` block intact if other code uses it; otherwise remove the whole try block.

### 7. Validate
- **Task ID**: validate-all
- **Depends On**: build-test-harness-fixtures, build-harness-callers, build-remove-dead-emits
- **Assigned To**: analytics-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/ -x -q` — all tests pass.
- Run `grep -rn 'record_metric.*session\.cost_usd\|record_metric.*session\.turns' agent/ ui/` — zero hits.
- Run `grep -rn 'query_metric_total.*session\.cost_usd\|query_metric_total.*session\.turns' .` — zero hits in production code.
- Run `grep -rn 'atomic_increment_session_field' .` — **zero hits** (proposed helper was deleted; verify no orphan references).
- Verify `python -c "from ui.data.analytics import get_analytics_summary; print(get_analytics_summary())"` runs without error.
- Run an end-to-end smoke: enqueue a tiny test AgentSession via
  `python -m tools.valor_session create --role pm --message "echo test" --project-key ai`,
  wait for completion, then
  `curl -s localhost:8500/dashboard.json | jq '.analytics.turns_today, .sessions[].turn_count'`
  shows non-zero values.

### 8. Documentation
- **Task ID**: document-analytics
- **Depends On**: validate-all
- **Assigned To**: analytics-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Update `_run_harness_subprocess` docstring to describe the new `HarnessResult` return type
  (or 8-tuple shape) and the new `num_turns` / `tool_call_count` fields.
- Update `get_analytics_summary` docstring to reflect AgentSession-derived aggregation via
  the `analytics:sessions:completed_at` sorted set.
- Update `docs/features/dashboard.md` if it exists: note that `analytics.cost_today_usd` and
  `analytics.turns_today` are derived from `AgentSession.total_cost_usd` and
  `AgentSession.turn_count` via the sorted set, not from the analytics ledger.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No dead emit sites (cost) | `grep -rn 'record_metric.*session\.cost_usd' agent/ ui/` | exit code 1 |
| No dead emit sites (turns) | `grep -rn 'record_metric.*session\.turns' agent/ ui/` | exit code 1 |
| No stale query_metric_total (cost) | `grep -rn 'query_metric_total.*session\.cost_usd' ui/` | exit code 1 |
| No stale query_metric_total (turns) | `grep -rn 'query_metric_total.*session\.turns' ui/` | exit code 1 |
| No orphan atomic helper refs | `grep -rn 'atomic_increment_session_field' .` | exit code 1 |
| `_run_harness_subprocess` returns new shape | `grep -nE 'class HarnessResult\|-> HarnessResult\|-> tuple\[' agent/sdk_client.py` | matches HarnessResult definition + return annotation |
| `num_turns` flows to AgentSession | run a real session, then `python -m tools.valor_session inspect --id <ID>` | `turn_count > 0` |
| ZADD score is numeric | `redis-cli ZRANGEBYSCORE analytics:sessions:completed_at -inf +inf WITHSCORES` | scores are numeric (e.g. `1730000000.0`) |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| Blocker | B1 | `finalize_session` ZADD score was a `datetime` object, not numeric — would fail `ZADD` validation or store an unusable score | Step 4 (build-finalize-zadd) + Spike Finding 4 | Compute `ts = time.time()` once locally, use as ZADD score. Existing line `session.completed_at = ts` is unchanged (Popoto auto-converts to datetime). The ZADD score and the model field are independent — the analytics query reads only the sorted set. |
| Blocker | B2 | Plan invented a turn-counter subsystem (`message_stop` accumulation, fictional event subsystem) when `num_turns` is already in the harness `result` event | Spike Finding 1 + Step 1 (build-harness-result) | One-line extraction at `agent/sdk_client.py:2472-2500` alongside existing `result`/`session_id`/`usage`/`total_cost_usd` pulls. No subsystem, no message_stop counting. |
| Blocker | B3 | `_run_harness_subprocess` 6→8-tuple change would break call sites and test fixtures pinning the arity | Spike Finding 6 + Steps 1, 2, 3 | **Confirmed real**: 3 production call sites + 25+ test fixtures across 5 files. Plan now explicitly lists every site to update and prefers a `HarnessResult` dataclass for readability. **Critique was correct — the original plan's claim that "no tests reference 6-tuple arity" was wrong.** |
| Blocker | B4 | `atomic_increment_session_field` helper would violate the Popoto raw-Redis rule (`HINCRBYFLOAT`/`HINCRBY` directly against Popoto-managed hashes) | Spike Decision — helper deleted | `num_turns` arrives final-not-delta; one Popoto field-assign + save is sufficient. `accumulate_session_tokens` race is real but **explicitly out of scope** for this plan. |
| Blocker | B5 (new) | Persisting in `_run_harness_subprocess` via `AgentSession.query.filter(session_id=session_id_from_harness)` would silently fail — `session_id_from_harness` is the Claude UUID, NOT the AgentSession session_id | Spike Finding 5 + Step 2 | Persistence moves to `get_response_via_harness`, which has the AgentSession `session_id` parameter in scope (same identifier used for `accumulate_session_tokens(session_id, ...)`). |
| Blocker | B6 (new) | Plan said "extend the assistant-event handler" but no such handler exists in `_run_harness_subprocess` today (only `result` and `stream_event` are handled) | Spike Finding 2 + Step 1 | Plan now says "ADD a new `assistant`-event handler branch" parallel to the existing branches. Task-level code shows the new `if event_type == "assistant":` block. |
| Concern | Operator | Fictitious test fixture in `tests/unit/test_harness_streaming.py:74-122` for top-level `event.type == "tool_use"` — passes only because production handler ignores unknown events | Test Impact + Step 1 | Fixture is replaced with one exercising the real `assistant.message.content[]` shape. |
| Concern | Skeptic | Two valid tool-use counting paths (`assistant.message.content[]` vs. `content_block_start`) — plan must commit to one | Spike Finding 2 — Path A chosen | Path A (assemble-from-`assistant`) wins on simplicity. Path B is documented as a fallback if the `assistant` event handler is awkwardly placed. |
| Concern | Adversary | `completed_at` is a `DatetimeField` per the model — assigning a float may serialize oddly | Spike Finding 4 + Step 4 | Popoto auto-conversion is an existing, documented behavior at `models/agent_session.py:459-465, 710-711`. The model field's auto-conversion is irrelevant to the ZADD index because the index uses the locally-computed numeric `ts`, not the model field value. |
| Concern | Archaeologist | `accumulate_session_tokens` race is a known issue but unfixed here | Race Conditions section + No-Gos | Explicitly out of scope. Risk acknowledged; future plan can revisit. |

## Plan Revision History

| Iteration | Date | Trigger | Summary |
|-----------|------|---------|---------|
| Initial | 2026-05-01 | New plan | First draft based on issue #1245 recon. Proposed `message_stop` turn-counter subsystem and `atomic_increment_session_field` helper. |
| Revision 1 | 2026-05-01 (commit `17a826d8`) | Critique verdict: NEEDS REVISION (B1–B4) | Spike findings replaced earlier assumptions: `num_turns` is in the `result` event (no subsystem); `assistant`-event handler must be ADDED (none exists); `session_id_from_harness` is the Claude UUID, NOT the AgentSession ID — persist in `get_response_via_harness` (Spike Finding 5). Atomic helper deleted (Decision post-spike). New blockers B5/B6 surfaced and addressed in same revision. |
| Revision 2 | 2026-05-01 (this commit) | Stale verdict; PM re-dispatched `/do-plan` | No structural changes — adds revision metadata (`revision_count: 2`), this Plan Revision History table, and minor doc tightening. All B1–B6 fixes from Revision 1 stand. Ready for re-critique. |

**Files changed across revisions:**
- `agent/sdk_client.py` — `_run_harness_subprocess` return shape, new `assistant`-event handler, four new fields extracted from `result` event
- `agent/sdk_client.py` — `get_response_via_harness` AgentSession persist block (NEW location, post-Revision 1)
- `agent/sdk_client.py` — `get_response_via_sdk` dead emit deletion
- `models/session_lifecycle.py` — additive ZADD in `finalize_session()` (no model field change)
- `ui/data/analytics.py` — new `_query_session_sums` helper, replaces two `query_metric_total` calls
- 5 test files — return-shape updates (3+ production call sites, 25+ mock fixtures)

---

## Open Questions

The post-spike + post-revision plan introduced one follow-up that need not block the build:

- **Should we persist `duration_ms`, `stop_reason`, and `modelUsage` to AgentSession too?**
  These fields are now extracted (essentially free) from the `result` event in Step 1 but are
  not written to AgentSession by this plan. The dashboard could surface them with little
  extra work. Deferred because the issue's acceptance criteria scope only `cost_today_usd` and
  `turns_today`. **Recommendation:** ship this plan first; a follow-up issue can add new
  AgentSession fields and dashboard surface area for the others.

The three original planning decisions from the issue remain resolved:
- Indexing strategy → Option B ZADD with numeric float score (B1 fix applied)
- Turn counter → `result.num_turns` direct extraction (no `message_stop` accumulation)
- Atomic helper → **deleted from plan** (num_turns is final-not-delta; helper was unneeded)

**No human input needed before re-critique.** All planning decisions are resolved; the deferred follow-up above is explicitly out of scope and not a blocker for this plan.
