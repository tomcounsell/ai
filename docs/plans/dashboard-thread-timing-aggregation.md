---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-07-23
tracking: https://github.com/tomcounsell/ai/issues/2212
last_comment_id:
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
(`agent/agent_session_queue.py:1342`, invoked at enqueue time line 326) deletes
the prior terminal record **before** the new pending record is created (line
336). The new record is born with a fresh `created_at=datetime.now(tz=UTC)` and
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
- `agent/agent_session_queue.py:326` — reconcile invoked before `async_create` (line 336) — still holds.
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
   `enqueue_agent_session()` (`agent/agent_session_queue.py:1483`) with the same
   `session_id`.
2. **Reconcile (loss point):** `_delete_stale_terminal_duplicates(session_id)`
   (line 326) deletes the prior terminal record — the only carrier of the prior
   run's `created_at`/`turn_count`/`tool_call_count`.
3. **New record:** `AgentSession.async_create(... created_at=now, ...)` (line
   336) creates a fresh pending record with zeroed counters.
4. **Live accrual:** the worker increments `turn_count`/`tool_call_count` on the
   new record as the resume runs.
5. **Dashboard read:** `_pipeline_progress_from_session()`
   (`ui/data/sdlc.py:936-1076`) → `PipelineProgress` → `_session_to_json()`
   (`ui/app.py:682`) emits per-record fields.
6. **Output:** dashboard renders the single record as the whole thread.

The fix inserts a **capture-before-delete** step between (2) and (3): read the
prior terminal record's thread rollup, fold its per-run counters in, and seed the
new record's thread-level fields so history rides forward on the live record.

## Architectural Impact

- **New dependencies:** none.
- **Interface changes:** four new nullable fields on `AgentSession`
  (`thread_first_created_at`, `thread_turn_count`, `thread_tool_call_count`,
  `thread_run_count`); additive fields on `PipelineProgress` and the dashboard
  JSON payload. No signature changes to `enqueue_agent_session`.
- **Coupling:** keeps thread history on the live ORM record — does NOT couple the
  live dashboard to the async-exported `session_archive.db`. Lower coupling than
  the archive-query alternative (see Rabbit Holes).
- **Data ownership:** the enqueue path becomes the owner of thread-level rollups;
  the worker continues to own per-run counters.
- **Reversibility:** high. Fields are nullable and additive; the migration is
  backfill-only; the dashboard degrades gracefully to per-run display when the
  thread fields are null.

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
- **Capture-before-delete in enqueue**: before `_delete_stale_terminal_duplicates`
  removes prior terminal records, read the surviving/most-recent terminal record
  and compute the new record's rollup, then pass it into `async_create`.
- **Dashboard surface**: `PipelineProgress` gains `thread_first_created_at`,
  `thread_turn_count`, `thread_tool_call_count`, `thread_run_count`; the dashboard
  emits both per-run (existing fields, unchanged) and per-thread values.
- **Popoto migration**: backfill the four fields on existing records
  (idempotent; thread rollup seeds from the record's own per-run values so a
  never-resumed thread reads identically under both models).

### Flow

Reply resumes thread → enqueue reads prior terminal record's rollup → folds prior
run's per-run counters into thread totals + increments `thread_run_count` +
preserves earliest `created_at` as `thread_first_created_at` → stale terminal
record deleted → new pending record created carrying the rollup → worker accrues
this run's per-run counters → dashboard renders **per-run** (this resume) and
**per-thread** (rollup + current run) side by side.

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
- **Multiple terminal duplicates** (rare divergent case): pick the
  most-recently-`created_at` terminal record as the accumulation base to avoid
  double-counting; the others are stale divergent duplicates whose counters would
  duplicate the base. Document this choice inline.
- **Capture site**: fold the "read prior rollup" into the same enqueue block that
  currently calls `_delete_stale_terminal_duplicates` (line 326), so the read
  happens before the delete within the same code path. Return the computed rollup
  and pass it to `async_create` (line 336) as new kwargs.
- **Popoto schema migration** (`scripts/update/migrations.py`): iterate existing
  `AgentSession` records via the ORM; seed `thread_first_created_at=created_at`,
  `thread_turn_count=0`, `thread_tool_call_count=0`, `thread_run_count=1` where
  the fields are null. Idempotent; recorded in `data/migrations_completed.json`.
  ORM-only (`instance.save()`), never raw Redis.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The existing reconcile block wraps `_delete_stale_terminal_duplicates` in
  `try/except Exception` (line 325-334) and logs a warning. The new rollup-capture
  logic goes inside/adjacent to this block — a capture failure must NOT block the
  new record's creation. Add a test asserting: when rollup capture raises, the new
  record is still created (with null/0 rollup) and a warning is logged.
- [ ] No new bare `except: pass` blocks — the capture path either produces a
  rollup or logs and continues with null rollup.

### Empty/Invalid Input Handling
- [ ] First run (no prior terminal record) → rollup fields null/0; test the
  dashboard renders per-thread == per-run.
- [ ] Prior record with null per-run counters → treated as 0 (no `None + int`
  crash); unit test the accumulation helper with `turn_count=None`.
- [ ] Prior record with null `thread_*` (pre-migration record resumed before
  migration runs) → bootstrap path (`or 1` / `or 0` / `or created_at`) engages;
  unit test.

### Error State Rendering
- [ ] Dashboard degrades gracefully: when `thread_*` fields are null, per-thread
  display falls back to per-run values (never blank, never a crash). Test
  `_session_to_json` / `_pipeline_progress_from_session` with null thread fields.

## Test Impact
- [ ] `tests/unit/test_ui_app.py` — UPDATE: assert the new `thread_*` keys appear
  in the `_session_to_json` payload and that null thread fields fall back to
  per-run values.
- [ ] `tests/unit/` agent_session_queue enqueue/reconcile tests (locate via
  `grep -rln "_delete_stale_terminal_duplicates\|enqueue_agent_session" tests/`) —
  UPDATE: add assertions that a resume carries forward the prior run's rollup and
  that first-run enqueue leaves rollup null/0.
- [ ] Add NEW unit tests for the accumulation helper (bootstrap, null counters,
  multiple-duplicate base selection) — no existing test covers this logic.

## Rabbit Holes

- **Querying `session_archive.db` at render time** (the issue's alternative
  direction): couples the live dashboard to an async-exported SQLite store that
  can lag terminal exports, requires deserializing per-run JSON payloads on every
  dashboard render, and reintroduces a read against a store the dashboard
  otherwise never touches. Rejected in favor of carrying the rollup on the live
  record. Do NOT implement archive queries for this fix.
- **Persisting per-thread totals as a second write on run completion**: unneeded —
  fold the in-flight run into the thread total at render time instead.
- **A separate `Thread` model / dedicated thread registry**: over-engineered for a
  presentation gap; four nullable fields on `AgentSession` suffice.
- **Reconciling historical archived runs into the rollup**: the migration seeds
  from the live record only; back-computing rollups from `session_archive.db` is
  out of scope (the gap is forward-looking).

## Risks

### Risk 1: Double-counting when multiple terminal duplicates exist
**Impact:** Inflated thread turn/tool counts in the rare divergent-duplicate case.
**Mitigation:** Select the single most-recently-`created_at` terminal record as
the accumulation base; treat the rest as stale duplicates whose counters are not
added. Unit-test with two terminal duplicates.

### Risk 2: Capture-before-delete race with the pop-loop escalation
**Impact:** `_delete_stale_terminal_duplicates` is also called from the pop-loop
escalation (line 1911). If the escalation deletes the prior record between the
enqueue-time read and use, the rollup could be missed.
**Mitigation:** Read the prior record's fields into locals within the same
`asyncio.to_thread` block *before* the delete call, so capture and delete are
consecutive in one path. A missed rollup degrades to null (per-run display), never
a crash. See Race 1 below.

### Risk 3: Migration touching a large AgentSession corpus
**Impact:** A slow or partial backfill on many records.
**Mitigation:** Idempotent, resumable migration (skip records already seeded);
recorded once in `data/migrations_completed.json`. Fields are nullable so an
unmigrated record still renders (fallback path).

## Race Conditions

### Race 1: Prior terminal record deleted between capture and use
**Location:** `agent/agent_session_queue.py:325-336` (enqueue reconcile → create).
**Trigger:** Two reply-resumes interleave, or the pop-loop escalation (line 1911)
races the enqueue-time reconcile for the same `session_id`.
**Data prerequisite:** The prior terminal record's `thread_*`/`turn_count`/
`tool_call_count`/`created_at` must be read before that record is deleted.
**State prerequisite:** Exactly one accumulation base is chosen even if two
reconcilers run.
**Mitigation:** Perform the rollup read in the same `asyncio.to_thread` call that
does the delete, reading fields into locals *before* `dup.delete()`. If the record
is already gone (Race 2 in `_delete_stale_terminal_duplicates`), the rollup is
null and the new record displays per-run only — a caught, non-fatal degrade.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG] Nothing deferred to a separate issue — every relevant item is in
  scope for this plan.
- Querying `session_archive.db` for prior-run rows at render time — explicitly
  rejected (see Rabbit Holes), not deferred.
- Back-computing thread rollups from historical archived runs — out of scope; the
  gap is forward-looking and the migration seeds from the live record only.

Nothing deferred — every relevant item is in scope for this plan.

## Update System

No update-script changes required beyond the standard migration path. The Popoto
schema migration is added to `scripts/update/migrations.py` and registered in the
`MIGRATIONS` dict; `run_pending_migrations()` picks it up automatically on the
next `/update`. No new dependencies or config files to propagate.

## Agent Integration

No agent integration required — this is a bridge/worker-internal + dashboard
change. The new fields are populated by the enqueue path and read by the dashboard
serializer (`ui/`); no new CLI entry point or MCP surface is needed, and the
bridge does not import new code. The existing `curl -s localhost:8500/dashboard.json`
surface gains the new fields automatically.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/session-lifecycle.md` (or the dashboard feature doc)
  to describe per-run vs per-thread timing and the `thread_*` rollup fields.
- [ ] Note the rollup fields in whichever feature doc documents `AgentSession`
  telemetry fields (cross-reference the #1536 telemetry doc if present).

### Inline Documentation
- [ ] Docstring on the accumulation helper explaining bootstrap semantics and
  multiple-duplicate base selection.
- [ ] Comment at the enqueue capture site explaining capture-before-delete.

## Success Criteria

- [ ] `AgentSession` has `thread_first_created_at`, `thread_turn_count`,
  `thread_tool_call_count`, `thread_run_count` fields.
- [ ] A resumed thread's dashboard row shows per-thread start time reflecting the
  ORIGINAL run's start, plus cumulative turn/tool counts and run count > 1.
- [ ] A never-resumed thread renders identically to today (per-thread == per-run).
- [ ] Rollup capture failure never blocks new-record creation (test-asserted).
- [ ] Popoto migration added to `scripts/update/migrations.py`, registered in
  `MIGRATIONS`, idempotent.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] `grep` confirms the dashboard serializer references the new `thread_*` fields.

## Team Orchestration

### Team Members

- **Builder (model + enqueue)**
  - Name: rollup-builder
  - Role: Add the four `AgentSession` fields, the accumulation helper, the
    capture-before-delete wiring in `enqueue_agent_session`, and the Popoto
    migration.
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
  - Role: Verify accumulation semantics, first-run fallback, capture-failure
    degrade, and dashboard rendering against success criteria.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add thread rollup fields + migration
- **Task ID**: build-model
- **Depends On**: none
- **Validates**: `tests/unit/` AgentSession field tests; migration idempotency
- **Assigned To**: rollup-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `thread_first_created_at` (DatetimeField, null), `thread_turn_count`,
  `thread_tool_call_count`, `thread_run_count` (IntField, default 0) to
  `models/agent_session.py`.
- Add an idempotent Popoto migration to `scripts/update/migrations.py`, register
  it in `MIGRATIONS`; seed `thread_first_created_at=created_at` and
  `thread_run_count=1` where null (ORM-only).

### 2. Accumulation helper + capture-before-delete wiring
- **Task ID**: build-enqueue
- **Depends On**: build-model
- **Validates**: new unit tests for the accumulation helper; enqueue/reconcile tests
- **Assigned To**: rollup-builder
- **Agent Type**: builder
- **Domain**: async/concurrency, Redis/Popoto data (paste matching DOMAIN_FRAMING rules)
- **Parallel**: false
- Add a pure helper computing the new record's rollup from the prior terminal
  record (bootstrap, null-counter, multiple-duplicate base-selection semantics).
- In `enqueue_agent_session`, read the prior rollup inside the same
  `asyncio.to_thread` block before `_delete_stale_terminal_duplicates`, then pass
  the rollup kwargs to `async_create`. Ensure capture failure degrades to null and
  never blocks creation.

### 3. Dashboard per-run + per-thread surface
- **Task ID**: build-dashboard
- **Depends On**: build-model
- **Validates**: `tests/unit/test_ui_app.py`
- **Assigned To**: dashboard-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `thread_*` fields to `PipelineProgress` (`ui/data/sdlc.py`) and to
  `_session_to_json` (`ui/app.py`).
- Implement render-time per-thread fold (`thread_* + per-run`) with per-run
  fallback when thread fields are null.

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
| Migration registered | `grep -c "thread" scripts/update/migrations.py` | output > 0 |
| Dashboard emits rollup | `grep -c "thread_turn_count" ui/app.py` | output > 0 |
| No archive-query added | `grep -rn "session_archive" ui/data/sdlc.py` | exit code 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. Display semantics: should the dashboard show per-run and per-thread as two
   distinct fields (e.g. `27s (this run) · 6m 12s (thread)`), or default to
   per-thread with per-run in the detail modal? Default assumption: emit both
   fields in JSON, show per-thread in the row and per-run in the modal.
2. `thread_run_count` semantics: count runs that *started* (bootstrap to 1 on
   first run, +1 per resume) vs. runs that *completed*? Default assumption:
   started-runs (simpler, matches "how many times this thread has been picked up").
