---
status: Ready
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-07-23
tracking: https://github.com/tomcounsell/ai/issues/2212
last_comment_id:
revision_applied: true
revision_applied_at: 2026-07-23T03:02:49Z
---

# Dashboard thread-level timing/turn aggregation across resumes

## Problem

When a Telegram thread is resumed by a reply, the dashboard misreports the
thread's history as if the whole conversation were only the most recent resume.

**Current behavior:**
On 2026-07-22 (thread `tg_psyoptimal_-1002600253717_2820`) the dashboard showed
`Started 3m ago · 27s, Tools/turns 2/3` for a thread whose original run started
~30 min earlier and took ~6 minutes (message in 09:21 UTC, first response 09:27
UTC). Each reply-resume enqueues a **new** `AgentSession` record for the same
`session_id`, and `_delete_stale_terminal_duplicates`
(`agent/agent_session_queue.py:1342`) is invoked inside `_push_agent_session`
(the coroutine `enqueue_agent_session` delegates to; `_push_agent_session` starts
at line 224) at line 326, deleting the prior terminal record **before** the new
pending record is created by `async_create` (line 336). The new record is born
with a fresh `created_at=datetime.now(tz=UTC)` and
`turn_count=0`/`tool_call_count=0`. The dashboard
(`ui/app.py:696-708` → `_pipeline_progress_from_session`,
`ui/data/sdlc.py:936-1076`) renders that single surviving record, so a 27-second
resume looks like the entire thread history.

**Desired outcome:**
The dashboard shows both **per-run** stats (this resume: 27s, 2 tools, 3 turns)
and **per-thread** stats (whole conversation: started 30m ago, cumulative tools
and turns across all runs, run count N). No data loss — prior-run rows already
survive in `data/session_archive.db`; this is purely a presentation/aggregation
gap on the live record.

## Freshness Check

**Baseline commit:** 6482f3dbc5cd992b65977e3243b333d24205556c
**Issue filed at:** 2026-07-22T10:00:46Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `agent/agent_session_queue.py:1342` — `_delete_stale_terminal_duplicates` deletes every terminal `AgentSession` for a `session_id` — still holds.
- `agent/agent_session_queue.py:326` — reconcile (`_delete_stale_terminal_duplicates`) invoked inside `_push_agent_session` before `async_create` (line 336) — still holds. (`enqueue_agent_session` at line 1483 is the public wrapper; the reconcile/create seam lives in `_push_agent_session`, line 224.)
- `agent/agent_session_queue.py:1911` — the pop-loop `StatusConflictError` escalation also calls `_delete_stale_terminal_duplicates`, using only the returned int count for logging — still holds (second call site; see Blocker resolution in Technical Approach).
- `ui/app.py:696-708` — dashboard emits `created_at`/`started_at`/`turn_count`/`tool_call_count` from one record — still holds.
- `models/agent_session.py:179-180` — `turn_count`/`tool_call_count` are `IntField(default=0)`; no thread-level counters exist — still holds.
- `agent/session_archive.py:70` — archive `sessions` table has `session_id TEXT` column, PK `id` per run — prior runs retained — still holds.

**Cited sibling issues/PRs re-checked:** None cited in the issue body.

**Commits on main since issue was filed (touching referenced files):** None
(only `68b052295`, the memory-telemetry commit, which is the baseline at issue
creation time and does not touch the referenced files).

**Active plans in `docs/plans/` overlapping this area:** none.

**Notes:** No drift. All issue claims verified against current main.

## Prior Art

- **Issue #1269** (closed 2026-05-06): "Dashboard: expand session list rows and detail modals with lifecycle state, PID, and liveness" — expanded the dashboard row/modal fields (the `_session_to_json` surface this plan extends). Relevant as the pattern to follow for adding new emitted fields; did not address cross-run aggregation.
- **Issue #1536** (closed 2026-06-26): "[EPIC] Record and learn from Claude Code session telemetry" — established `turn_count`/`tool_call_count` telemetry on the per-run record. This plan builds thread-level rollups on top of those per-run counters; the epic never aggregated across resume records.
- **PR #2027** (merged): "Fix teammate cold-start session finalize gap and duplicate-record pop-loop spin" — touched the same duplicate-reconcile machinery. Confirms `_delete_stale_terminal_duplicates` is the live reconcile path; no aggregation concern was in scope there.

No prior attempt tried to preserve or aggregate thread history across resume
records, so there is no failed-fix pattern to analyze.

## Research

No relevant external findings — proceeding with codebase context. This is a
purely internal change (Popoto model, enqueue path, dashboard serialization) with
no external libraries, APIs, or ecosystem patterns involved.

## Data Flow

1. **Entry point:** A Telegram reply resumes a thread → bridge calls
   `enqueue_agent_session()` (`agent/agent_session_queue.py:1483`), which delegates
   to the private coroutine `_push_agent_session()` (line 224) with the same
   `session_id`. Every step below happens inside `_push_agent_session`.
2. **Reconcile (loss point):** `_delete_stale_terminal_duplicates(session_id)`
   (line 326, via `asyncio.to_thread`) deletes the prior terminal record — the
   only carrier of the prior run's `created_at`/`turn_count`/`tool_call_count`.
3. **New record:** `AgentSession.async_create(... created_at=now, ...)` (line
   336) creates a fresh pending record with zeroed counters.
4. **Live accrual:** the worker increments `turn_count`/`tool_call_count` on the
   new record as the resume runs.
5. **Dashboard read:** `_pipeline_progress_from_session()`
   (`ui/data/sdlc.py:936-1076`) → `PipelineProgress` → `_session_to_json()`
   (`ui/app.py:682`) emits per-record fields.
6. **Output:** dashboard renders the single record as the whole thread.

The fix inserts a **capture-before-delete** step *before* (2), inside
`_push_agent_session`: a dedicated read helper snapshots the prior terminal
record's thread rollup into a detached dict, folds its per-run counters in, and the
snapshot seeds the new record's thread-level fields at (3) so history rides forward
on the live record. Because the snapshot is a detached dict, the delete at (2) — or
any racing reconciler's delete — cannot invalidate it.

## Architectural Impact

- **New dependencies:** none.
- **Interface changes:** four new nullable fields on `AgentSession`
  (`thread_first_created_at`, `thread_turn_count`, `thread_tool_call_count`,
  `thread_run_count`); additive fields on `PipelineProgress` and the dashboard
  JSON payload. No signature changes to `enqueue_agent_session`.
- **Coupling:** keeps thread history on the live ORM record — does NOT couple the
  live dashboard to the async-exported `session_archive.db`. Lower coupling than
  the archive-query alternative (see Rabbit Holes).
- **Data ownership:** `_push_agent_session` (the enqueue path) becomes the owner of
  thread-level rollups; the worker continues to own per-run counters.
- **Reversibility:** high. Fields are nullable and additive (non-indexed), so no
  schema migration or backfill is needed — Popoto's schemaless Redis hashes plus the
  descriptor self-heal path (#1099/#1172) absorb the new fields on existing records,
  and the helper's `or`-default bootstrap + dashboard fallback render a
  null-`thread_*` record identically to a seeded one. See the Popoto Migration
  Decision under Technical Approach.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 1-2 (confirm per-run vs per-thread display semantics)
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies (no API keys, no new
services). Redis + the existing test suite are the only requirements, already
present.

## Solution

### Key Elements

- **Thread rollup fields on `AgentSession`**: `thread_first_created_at`
  (DatetimeField, null), `thread_turn_count` (IntField, default 0),
  `thread_tool_call_count` (IntField, default 0), `thread_run_count` (IntField,
  default 0). These represent all **prior completed runs** for the `session_id`.
- **Capture-before-delete in `_push_agent_session`**: a dedicated read helper
  (`_capture_thread_rollup`), run in its own `asyncio.to_thread` *before* the
  existing `_delete_stale_terminal_duplicates` call, snapshots the most-recent
  terminal record for the `session_id` into a detached dict and computes the new
  record's rollup, which is passed into `async_create`.
  `_delete_stale_terminal_duplicates` and its second caller (the pop-loop
  escalation, line 1911) are left unchanged.
- **Dashboard surface**: `PipelineProgress` gains `thread_first_created_at`,
  `thread_turn_count`, `thread_tool_call_count`, `thread_run_count`; the dashboard
  emits both per-run (existing fields, unchanged) and per-thread values.
- **No Popoto migration**: the fields are nullable/additive and non-indexed;
  existing records self-heal and render via the fallback path. See the Popoto
  Migration Decision under Technical Approach for the full rationale.

### Flow

Reply resumes thread → `_push_agent_session` pre-reads the most-recent prior
terminal record's rollup (detached dict) → folds prior run's per-run counters into
thread totals + increments `thread_run_count` + preserves earliest `created_at` as
`thread_first_created_at` → stale terminal record deleted → new pending record
created carrying the rollup → worker accrues this run's per-run counters →
dashboard renders **per-run** (this resume) and **per-thread** (rollup + current
run) side by side.

### Technical Approach

- **Rollup accumulation semantics** (computed at enqueue, on the NEW record):
  - `thread_first_created_at = prior.thread_first_created_at or prior.created_at`
    (prior is always older than `now`, so it is the thread's true start).
  - `thread_run_count = (prior.thread_run_count or 1) + 1`
    (bootstraps to 1 for the prior run when the field is unset, then +1 for the
    prior run now completing — i.e. count of runs that have *started*).
  - `thread_turn_count = (prior.thread_turn_count or 0) + (prior.turn_count or 0)`
  - `thread_tool_call_count = (prior.thread_tool_call_count or 0) + (prior.tool_call_count or 0)`
  - The new record's own `turn_count`/`tool_call_count` stay per-run (start at 0
    and accrue live).
- **Display totals** (computed at render, not stored): per-thread turns =
  `thread_turn_count + turn_count`; per-thread tools =
  `thread_tool_call_count + tool_call_count`; thread start =
  `thread_first_created_at or created_at`. This folds the in-flight run into the
  thread total without a second write when the run completes.
- **First-run (no prior terminal record)**: rollup fields stay null/0; the render
  fallback (`thread_first_created_at or created_at`) makes a never-resumed thread
  display identically to today.
- **Single-base selection (and why the child-guard does NOT apply to capture)**:
  the accumulation base is the **single most-recently-`created_at` terminal record**
  for the `session_id` — read **unconditionally**, regardless of whether that
  record has child sessions. This is the deliberate divergence from
  `_delete_stale_terminal_duplicates`, whose `if dup.get_child_sessions(): continue`
  guard exists solely to avoid orphaning children on **delete**; it has no bearing
  on **reading counters**. Applying the delete-guard to capture (the original plan's
  implicit behavior) would have skipped folding the routine case of a
  parent-with-children terminal record — CONCERN #2. Reading the single most-recent
  base unconditionally folds it. This does **not** double-count, because of the
  **forward-accumulation invariant**: every resume folds exactly one base, and that
  base's `thread_*` fields already carry all earlier runs forward. A child-guarded
  record that survives deletion has a strictly lower `created_at` than any later
  completed run, so once a newer run exists it is never re-selected as "most
  recent"; it is folded exactly once, at the single resume immediately following its
  own completion. Stale older duplicates are therefore never summed — only the one
  most-recent base is. Document this invariant inline at the helper.
- **Capture mechanism and site (Blocker resolution)**: add a dedicated pure read
  helper `_capture_thread_rollup(session_id) -> dict | None` that queries
  `AgentSession.query.filter(session_id=session_id)`, filters to `TERMINAL_STATUSES`,
  selects the max-`created_at` record, and returns a **detached dict** of the four
  rollup kwargs (or `None` when no terminal record exists). In `_push_agent_session`,
  call it in its **own** `asyncio.to_thread` **immediately before** the existing
  `_delete_stale_terminal_duplicates` `to_thread` at line 326, and pass the returned
  dict (spread as kwargs, or null-defaults when `None`) into `async_create` at line
  336.
  - **Why a separate pre-read rather than modifying the shared helper**: the rollup
    is snapshotted into a plain dict *before* any delete, so atomicity between read
    and delete is **not required** — a delete (by this path or a racing reconciler)
    after the snapshot cannot invalidate the captured dict. This lets
    `_delete_stale_terminal_duplicates` keep its `-> int` return contract untouched,
    which matters because its **second caller** — the pop-loop `StatusConflictError`
    escalation at line 1911 (`offload_redis(_delete_stale_terminal_duplicates,
    _conflict_sid)`, whose return is used only for a log count) — must not be
    disturbed. That escalation needs **no** capture change: it fires only *after* a
    pending record (and its already-captured rollup) exists, and it deletes stale
    duplicates that are never the most-recent base a future capture would select.
    This is called out explicitly as a no-change second call site in Step-by-Step
    Tasks and Test Impact.
- **Null-capture degrade metric (NIT)**: when `_capture_thread_rollup` returns
  `None` on a resume where a prior record for the `session_id` existed (the Race 1
  degrade — prior base already deleted), emit a counter so operators can see how
  often the rollup silently falls back to per-run. Increment an analytics counter
  (`tools.analytics`, e.g. `thread_rollup_capture_null`) and log at `info`. A
  first-run enqueue (no prior terminal *or* non-terminal record for the `session_id`)
  is the expected common `None` and does **not** increment the degrade counter.

#### Popoto Migration Decision (CONCERN #3)

**Decision: no migration.** The repo's "Popoto Schema Migration Requirement"
convention (`docs/sdlc/do-plan.md`) targets schema/index-affecting model changes.
The four new fields are **nullable / `default=0` value fields, none `IndexedField`**
(mirroring the existing `turn_count`/`tool_call_count = IntField(default=0)`), so
they add no index and need no rebuild. A data backfill would be purely cosmetic: the
helper's `or`-default bootstrap (`prior.thread_run_count or 1`, etc.) and the
render-time fallback (`thread_first_created_at or created_at`) already make a
null-`thread_*` record behave and display identically to a seeded one, and Popoto's
descriptor self-heal for additive nullable AgentSession fields (#1099/#1172) means no
per-record write is needed for correctness. Writing a backfill across a large
`AgentSession` corpus would be redundant write traffic for zero behavioral change
(the former Risk 3). **Tradeoff accepted:** we consciously depart from the "add a
migration for Popoto model changes" convention because that convention is aimed at
schema/index changes; these additive nullable non-indexed fields are the exact case
the #1099/#1172 self-heal path was built to absorb without a migration.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The new `_capture_thread_rollup` pre-read `asyncio.to_thread` (in
  `_push_agent_session`, immediately before the line-326 delete block) must be
  wrapped in its own `try/except Exception` that logs a warning and yields a null
  rollup — a capture failure must NOT block the new record's creation. Add a test
  asserting: when capture raises, `async_create` still runs (with null/0 rollup)
  and a warning is logged.
- [ ] No new bare `except: pass` blocks — the capture path either produces a rollup
  dict or logs and continues with null rollup.

### Empty/Invalid Input Handling
- [ ] First run (no prior terminal record) → `_capture_thread_rollup` returns
  `None`, rollup fields null/0; test the dashboard renders per-thread == per-run.
  Assert the null-degrade counter is **not** incremented for a genuine first run.
- [ ] Prior record with null per-run counters → treated as 0 (no `None + int`
  crash); unit test the accumulation helper with `turn_count=None`.
- [ ] Prior record with null `thread_*` (pre-existing record with no rollup fields
  yet, resumed) → bootstrap path (`or 1` / `or 0` / `or created_at`) engages; unit
  test. (No migration exists — this is the routine pre-existing-record path, not a
  pre-migration window.)

### Error State Rendering
- [ ] Dashboard degrades gracefully: when `thread_*` fields are null, per-thread
  display falls back to per-run values (never blank, never a crash). Test
  `_session_to_json` / `_pipeline_progress_from_session` with null thread fields.

## Test Impact
- [ ] `tests/unit/test_ui_app.py` — UPDATE: assert the new `thread_*` keys appear in
  the `_session_to_json` payload and that null thread fields fall back to per-run
  values.
- [ ] `tests/unit/` agent_session_queue enqueue/reconcile tests (locate via
  `grep -rln "_delete_stale_terminal_duplicates\|_push_agent_session\|enqueue_agent_session" tests/`)
  — UPDATE: add assertions that a resume carries forward the prior run's rollup and
  that first-run enqueue leaves rollup null/0.
- [ ] Add NEW unit tests for the accumulation helper (bootstrap, null counters,
  most-recent base selection, **child-guarded base is still folded** — a terminal
  base with `get_child_sessions()` non-empty must have its counters captured, since
  the child-guard governs only deletion) — no existing test covers this logic.
- [ ] Add NEW unit test for the **forward-accumulation / no-double-count invariant**:
  simulate run A (terminal, with children → survives delete) → resume B folds A → B
  terminal → resume C folds B (not A again). Assert A's counters appear exactly once
  in C's `thread_*`.
- [ ] Add NEW unit test for the **null-degrade counter**: when the prior terminal
  base is deleted between capture and use (simulate `_capture_thread_rollup`
  returning `None` where a prior record existed), the `thread_rollup_capture_null`
  counter increments and the new record is created with per-run display.
- [ ] `agent/agent_session_queue.py:1911` pop-loop escalation caller — NO CHANGE
  required: it keeps consuming `_delete_stale_terminal_duplicates`'s `-> int`
  return. Add/confirm a regression assertion that the escalation call site is
  untouched (the helper signature is unchanged).

## Rabbit Holes

- **Querying `session_archive.db` at render time** (the issue's alternative
  direction): couples the live dashboard to an async-exported SQLite store that can
  lag terminal exports, requires deserializing per-run JSON payloads on every
  dashboard render, and reintroduces a read against a store the dashboard otherwise
  never touches. Rejected in favor of carrying the rollup on the live record. Do NOT
  implement archive queries for this fix.
- **Persisting per-thread totals as a second write on run completion**: unneeded —
  fold the in-flight run into the thread total at render time instead.
- **A separate `Thread` model / dedicated thread registry**: over-engineered for a
  presentation gap; four nullable fields on `AgentSession` suffice.
- **Reconciling historical archived runs into the rollup**: the rollup seeds forward
  from the live record only; back-computing rollups from `session_archive.db` is out
  of scope (the gap is forward-looking).

## Risks

### Risk 1: Double-counting / undercounting across terminal duplicates
**Impact:** Inflated (double-count) or dropped (undercount) thread turn/tool counts
across resumes, including the routine parent-with-children case.
**Mitigation:** Fold the **single most-recently-`created_at` terminal record** as
the base, read **unconditionally** (the delete's child-guard does not apply to
capture — see Technical Approach). The forward-accumulation invariant guarantees
each run's per-run counters are folded exactly once, even when a child-guarded
record survives deletion. Unit-test with two terminal duplicates AND the A→B→C
child-guarded-survivor sequence.

### Risk 2: Capture-before-delete race with the pop-loop escalation
**Impact:** `_delete_stale_terminal_duplicates` is also called from the pop-loop
escalation (line 1911). If a delete removes the prior base before capture reads it,
the rollup could be missed.
**Mitigation:** Capture is a **separate pre-read** that snapshots the base into a
detached dict *before* any delete runs (its own `asyncio.to_thread`, immediately
before the line-326 delete). Because the snapshot is detached, a later delete — by
this path OR the escalation — cannot invalidate it. The escalation fires only after
the new pending record (and its captured rollup) already exist, and it only removes
stale duplicates that are never the most-recent base, so it never strands a rollup a
future capture needs. A genuinely missed base (prior deleted before the pre-read)
degrades to null (per-run display) and increments the null-degrade counter, never a
crash. See Race 1 below.

### Risk 3: (removed) No migration
The former "migration touching a large corpus" risk is eliminated by the
no-migration decision (see Popoto Migration Decision): additive nullable non-indexed
fields self-heal (#1099/#1172); no backfill write traffic occurs.

## Race Conditions

### Race 1: Prior terminal record deleted before the pre-read capture
**Location:** `agent/agent_session_queue.py` — `_push_agent_session` (line 224), the
capture pre-read + line-326 delete + line-336 create sequence.
**Trigger:** Two reply-resumes interleave, or the pop-loop escalation (line 1911)
races the enqueue-time reconcile for the same `session_id`.
**Data prerequisite:** The prior terminal record's `thread_*`/`turn_count`/
`tool_call_count`/`created_at` must be read before that record is deleted.
**State prerequisite:** Exactly one accumulation base is chosen — the single
max-`created_at` terminal record — even if two reconcilers run.
**Mitigation:** `_capture_thread_rollup` runs in its **own** `asyncio.to_thread`
**before** the delete and snapshots the base into a **detached dict**. Ordering, not
shared-lock atomicity, is what matters: once snapshotted, a subsequent delete (this
path's line-326 delete, or a racing escalation delete) cannot invalidate the dict.
If the base is already gone when the pre-read runs (a delete won the race),
`_capture_thread_rollup` returns `None`; the new record displays per-run only and
the null-degrade counter increments — a non-fatal degrade, never a crash.
Single-base selection (max `created_at`) makes the chosen base deterministic even
under two interleaved reconcilers.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG] Nothing deferred to a separate issue — every relevant item is in
  scope for this plan.
- Querying `session_archive.db` for prior-run rows at render time — explicitly
  rejected (see Rabbit Holes), not deferred.
- Back-computing thread rollups from historical archived runs — out of scope; the
  gap is forward-looking and the rollup seeds forward from the live record only.

Nothing deferred — every relevant item is in scope for this plan.

## Update System

No update-script changes required. This feature adds no migration (see Popoto
Migration Decision), no new dependencies, and no config files to propagate — the
four additive nullable fields self-heal on existing `AgentSession` records via the
descriptor self-heal path (#1099/#1172). Nothing to run on `/update`.

## Agent Integration

No agent integration required — this is a bridge/worker-internal + dashboard change.
The new fields are populated by the enqueue path (`_push_agent_session`) and read by
the dashboard serializer (`ui/`); no new CLI entry point or MCP surface is needed,
and the bridge does not import new code. The existing
`curl -s localhost:8500/dashboard.json` surface gains the new fields automatically.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/session-lifecycle.md` (or the dashboard feature doc) to
  describe per-run vs per-thread timing and the `thread_*` rollup fields.
- [ ] Note the rollup fields in whichever feature doc documents `AgentSession`
  telemetry fields (cross-reference the #1536 telemetry doc if present).

### Inline Documentation
- [ ] Docstring on `_capture_thread_rollup` explaining bootstrap semantics,
  single-base (max-`created_at`) selection, the unconditional read (child-guard does
  not apply to capture), and the forward-accumulation no-double-count invariant.
- [ ] Comment at the `_push_agent_session` capture site explaining the separate
  pre-read to_thread and why the shared `_delete_stale_terminal_duplicates` is left
  unchanged.

## Success Criteria

- [ ] `AgentSession` has `thread_first_created_at`, `thread_turn_count`,
  `thread_tool_call_count`, `thread_run_count` fields (all nullable/`default=0`,
  none `IndexedField`).
- [ ] A resumed thread's dashboard row shows per-thread start time reflecting the
  ORIGINAL run's start, plus cumulative turn/tool counts and run count > 1.
- [ ] A never-resumed thread renders identically to today (per-thread == per-run).
- [ ] A parent-with-children terminal base still has its counters folded (child-guard
  does not suppress capture) — asserted by the child-guarded-base test.
- [ ] The forward-accumulation invariant holds (no double-count across the A→B→C
  child-guarded-survivor sequence) — test-asserted.
- [ ] Rollup capture failure never blocks new-record creation (test-asserted).
- [ ] The `thread_rollup_capture_null` counter increments only on the genuine
  prior-existed-but-gone degrade, not on first-run enqueue (test-asserted).
- [ ] No Popoto migration added (decision recorded); the `_delete_stale_terminal_duplicates`
  signature and its line-1911 caller are unchanged (`grep` confirms).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] `grep` confirms the dashboard serializer references the new `thread_*` fields.

## Team Orchestration

### Team Members

- **Builder (model + enqueue)**
  - Name: rollup-builder
  - Role: Add the four `AgentSession` fields, the `_capture_thread_rollup` helper +
    accumulation semantics, the separate pre-read to_thread wiring in
    `_push_agent_session`, and the null-degrade counter. No migration.
  - Agent Type: builder
  - Domain: async/concurrency, Redis/Popoto data
  - Resume: true

- **Builder (dashboard surface)**
  - Name: dashboard-builder
  - Role: Add `thread_*` to `PipelineProgress` and the dashboard JSON payload;
    implement the render-time per-thread fold with per-run fallback.
  - Agent Type: builder
  - Resume: true

- **Validator**
  - Name: rollup-validator
  - Role: Verify accumulation semantics, first-run fallback, child-guarded-base fold,
    forward-accumulation invariant, capture-failure degrade, null-degrade counter,
    and dashboard rendering against success criteria.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add thread rollup fields
- **Task ID**: build-model
- **Depends On**: none
- **Validates**: `tests/unit/` AgentSession field tests
- **Assigned To**: rollup-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `thread_first_created_at` (DatetimeField, null), `thread_turn_count`,
  `thread_tool_call_count`, `thread_run_count` (IntField, default 0) to
  `models/agent_session.py`. All plain value fields (NOT `IndexedField`).
- No migration — see Popoto Migration Decision. Confirm no `scripts/update/migrations.py`
  entry is added.

### 2. Capture helper + pre-read wiring + null-degrade metric
- **Task ID**: build-enqueue
- **Depends On**: build-model
- **Validates**: new unit tests for `_capture_thread_rollup`; enqueue/reconcile tests;
  null-degrade counter test
- **Assigned To**: rollup-builder
- **Agent Type**: builder
- **Domain**: async/concurrency, Redis/Popoto data (paste matching DOMAIN_FRAMING rules)
- **Parallel**: false
- Add pure helper `_capture_thread_rollup(session_id) -> dict | None`: filter
  `AgentSession.query.filter(session_id=session_id)` to `TERMINAL_STATUSES`, select
  max-`created_at` base **unconditionally** (child-guard does NOT apply to capture),
  return a detached dict of the four rollup kwargs (bootstrap, null-counter, single-base
  semantics). Returns `None` when no terminal record exists.
- In `_push_agent_session`, call `_capture_thread_rollup` in its **own**
  `asyncio.to_thread` immediately before the existing line-326
  `_delete_stale_terminal_duplicates` call; wrap in `try/except` so a capture failure
  degrades to null and never blocks `async_create`. Pass the dict (or null-defaults)
  into `async_create` at line 336.
- Increment the `thread_rollup_capture_null` analytics counter (log at `info`) only
  when capture returns `None` AND a prior record for the `session_id` existed.
- Do NOT modify `_delete_stale_terminal_duplicates` or its line-1911 pop-loop
  escalation caller; add a regression assertion that the escalation call site is
  untouched.

### 3. Dashboard per-run + per-thread surface
- **Task ID**: build-dashboard
- **Depends On**: build-model
- **Validates**: `tests/unit/test_ui_app.py`
- **Assigned To**: dashboard-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `thread_*` fields to `PipelineProgress` (`ui/data/sdlc.py`) and to
  `_session_to_json` (`ui/app.py`).
- Implement render-time per-thread fold (`thread_* + per-run`) with per-run fallback
  when thread fields are null.

### 4. Validation
- **Task ID**: validate-all
- **Depends On**: build-enqueue, build-dashboard
- **Assigned To**: rollup-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all success criteria, run the verification commands, report pass/fail.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: dashboard-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update the session-lifecycle / dashboard feature doc with per-run vs per-thread
  timing and the `thread_*` fields.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_ui_app.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Fields added | `grep -c "thread_turn_count" models/agent_session.py` | output > 0 |
| No migration added | `grep -c "thread" scripts/update/migrations.py` | output 0 |
| Capture helper present | `grep -c "_capture_thread_rollup" agent/agent_session_queue.py` | output > 0 |
| Shared helper unchanged | `grep -c "def _delete_stale_terminal_duplicates(session_id: str) -> int:" agent/agent_session_queue.py` | output 1 |
| Dashboard emits rollup | `grep -c "thread_turn_count" ui/app.py` | output > 0 |
| No archive-query added | `grep -rn "session_archive" ui/data/sdlc.py` | exit code 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

**Revision pass (run ef1a3b96):** addressed the NEEDS REVISION findings —
1 blocker (capture seam mechanism: chose the separate pre-read/detached-dict
mechanism, named `_push_agent_session` as the site, documented the line-1911 second
call site as no-change), 3 concerns (child-guarded base now folded via unconditional
single-base read; Popoto migration dropped with recorded rationale; all
`enqueue_agent_session` misattributions corrected to `_push_agent_session`), and
2 nits (null-degrade counter added; Open Questions marked non-blocking).

---

## Open Questions

Both questions are **non-blocking** — each has a default assumption the builder
should proceed with. They are surfaced for the PM to override if desired, not to
gate the build.

1. **(Non-blocking)** Display semantics: should the dashboard show per-run and
   per-thread as two distinct fields (e.g. `27s (this run) · 6m 12s (thread)`), or
   default to per-thread with per-run in the detail modal? **Default assumption
   (build with this):** emit both fields in JSON, show per-thread in the row and
   per-run in the modal.
2. **(Non-blocking)** `thread_run_count` semantics: count runs that *started*
   (bootstrap to 1 on first run, +1 per resume) vs. runs that *completed*?
   **Default assumption (build with this):** started-runs (simpler, matches "how
   many times this thread has been picked up").
