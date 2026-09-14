---
status: docs_complete
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-07-12
tracking: https://github.com/tomcounsell/ai/issues/2042
last_comment_id:
revision_applied: true
revision_applied_at: 2026-07-12T10:59:30Z
---

# Non-executable-ledger flag for CLI-created sdlc-local anchors

## Problem

When a human runs `/do-sdlc` locally to shepherd an issue through the pipeline, `sdlc-tool session-ensure` creates a **tracking anchor**: an `AgentSession` whose only job is to hold the SDLC ledger state (which stage, which run) for one GitHub issue. It is never meant to be executed. But it is created with `session_type="eng"` and `status="running"`, byte-for-byte identical to a real executable Eng session.

If a standalone `python -m worker` is running on the same machine at the same time, its startup and health-check recovery paths treat the anchor as an interrupted executable session, reset it to `pending`, and then `session_pickup` pops it and runs it as a `claude -p` subprocess. Now two independent drivers — the local `/do-sdlc` supervisor and the worker — advance the **same** issue's pipeline concurrently, producing competing PRs, `SDLC_HOLDER_TOKEN` write collisions, and spurious `ISSUE_LOCKED` errors.

**Current behavior:**
- `sdlc-tool session-ensure` creates `sdlc-local-{N}` (session_type=eng, status=running) with no marker distinguishing it from executable work.
- The worker's recovery discriminator `is_local = entry.session_id.startswith("local")` (`agent/session_health.py:673`) is **False** for `sdlc-local-` (it starts with `sdlc-`), so the anchor falls through to the general bridge-recovery `else` branch (`agent/session_health.py:778`) and is reset to `pending`.
- `agent/session_pickup.py` pops the now-pending anchor (~line 383 candidate loop, ~449 run-claim + `transition_status(chosen, "running")`) and runs it — a twin driver.
- The only mitigation today is manually disabling the worker before each local `/do-sdlc` run: fragile tribal knowledge.

**Desired outcome:**
A local `/do-sdlc` run and a live worker coexist safely. CLI-created `sdlc-local-*` anchors are explicitly marked as **non-executable ledgers**. Every worker path that would requeue, finalize, or run a session honors that mark and skips them. Anchor count stops mattering: because every `sdlc-local-*` row is skipped, a rare concurrent-creation duplicate is inert, and the issue-keyed run lock (#2012) still admits exactly one driver.

## Freshness Check

**Baseline commit:** `ed422b786b53f47ce2c22cb6896f51fd4ec1436f`
**Issue filed at:** 2026-07-12T10:29:30Z (same day as planning)
**Disposition:** Minor drift

**File:line references re-verified:**
- `tools/sdlc_session_ensure.py` ~420-501 — builds `sdlc-local-{issue_number}`, `AgentSession.create_local(session_type="eng", ...)`, then `claim_pending_run` + `transition_status(session, "running")` — **still holds**.
- `models/agent_session.py:1651` `create_local(**kwargs)` forwards arbitrary kwargs to the constructor — **still holds** (this is the injection point for the new flag).
- `agent/session_health.py:673` `is_local = entry.session_id.startswith("local")` — **still holds**; predicate is False for `sdlc-local-`.
- Startup recovery else-branch pending reset — issue said `~764`; actual `new_status="pending"` reset is **line 778** (else block opens ~774). Minor line drift; claim holds.
- Health-check requeue paths — issue cited `~3164 / ~3372`; the running-session `worker_dead` recovery is ~3360 and the **pending-session loop is `session_health.py:3448`** (`AgentSession.query.filter(status="pending")`), whose `worker_key.startswith("local")` abandon branch (3478) also misses `sdlc-local-`. Minor drift; claim holds.
- Pickup gate — issue cited `session_pickup.py ~464`; actual path is **`agent/session_pickup.py`**, candidate loop ~380-440, run-claim + transition at ~449-465. `requires_real_chrome` (`Field(default=False)`, honored via `_truthy()` at ~426) is the exact precedent. Minor drift; claim holds.

**Cited sibling issues/PRs re-checked:**
- #1092 (local-dev requeue), #1558 (sessionless-local deterministic id), #2012 (issue-keyed ledger, CLOSED) — landscape unchanged; `find_session_by_issue` was demoted by #2012 but still the routing/ownership lookup used by `session-ensure`.

**Commits on main since issue was filed (touching referenced files):** none.

**Active plans in `docs/plans/` overlapping this area:** none.

**Notes:** All corrected line numbers are captured inline in Technical Approach. No premise changed; the flag-based approach the issue sketches remains correct.

## Prior Art

- No closed issues or merged PRs found for "sdlc-local anchor / ledger / double-drive / twin driver" — this is the first attempt at a structural fix.
- **#2012** (CLOSED): moved durable pipeline ledger state to the issue-keyed `PipelineLedger` (`agent/pipeline_ledger.py`). Relevant because it demoted `find_session_by_issue` to a routing/ownership lookup — the anchor is now purely a routing/ownership envelope, which reinforces that it should never execute.
- **#1092**: added the worker-owned local-dev requeue branch (`is_local and session_type==ENG`). Relevant because that branch is one of the requeue sites that must learn to skip ledgers.
- **#1558**: introduced the deterministic `sdlc-local-{N}` id for the sessionless-local case. This is the record we are now marking non-executable.

## Data Flow

1. **Entry point**: human runs `/do-sdlc {N}` → `sdlc-tool session-ensure --issue-number N`.
2. **`tools/sdlc_session_ensure.py`**: idempotency check (`find_session_by_issue`, `existing_by_id`) → `AgentSession.create_local(session_id="sdlc-local-N", session_type="eng", ...)` → `claim_pending_run` → `transition_status(..., "running")`. **← flag set here.**
3. **Redis (Popoto)**: anchor persisted with `status=running` and (new) `is_ledger=True`.
4. **Worker startup** (`agent/session_health.py::_recover_stale_sessions`): scans non-terminal sessions → currently resets anchor to `pending`. **← must skip if `is_ledger`.**
5. **Worker health check** (`agent/session_health.py::_agent_session_health_check`): scans running + pending → currently recovers/abandons anchor. **← must skip if `is_ledger`.**
6. **Worker pickup** (`agent/session_pickup.py::_pop_agent_session`): pops pending candidate → runs `claude -p`. **← must skip if `is_ledger`.**
7. **Output**: with the flag honored, the anchor stays exactly where `session-ensure` left it (running, owned by the local supervisor); the worker never touches it.

## Architectural Impact

- **New dependencies**: none.
- **Interface changes**: one additive, nullable/defaulted boolean field on `AgentSession` (`is_ledger`). `create_local` signature unchanged (threaded via existing `**kwargs`).
- **Coupling**: decreases operational coupling — removes the "disable the worker first" hidden dependency between local `/do-sdlc` and the worker.
- **Data ownership**: unchanged. The anchor already belongs to the local supervisor; the flag makes that ownership machine-enforced.
- **Reversibility**: high. The field is additive; removing it reverts to prior behavior. No destructive migration.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1-2 (confirm field name + duplicate-handling decision)
- Review rounds: 1 (concurrency-sensitive read sites warrant a careful review)

The coding is small (one field, a handful of guards, one migration, tests). The care is in auditing every requeue/pickup site so no path is missed — a single missed site reopens the bug.

## Prerequisites

No external prerequisites — this work has no new secrets, services, or API dependencies. It touches only in-repo Popoto models and worker code.

## Solution

### Key Elements

- **`is_ledger` boolean on `AgentSession`**: a persisted, defaulted (`Field(default=False)`) flag marking a record as a non-executable ledger. Mirrors the existing `requires_real_chrome` / `retain_for_resume` boolean-field pattern.
- **Set at creation**: `sdlc_session_ensure.py` passes `is_ledger=True` into `create_local`, so every CLI anchor is born non-executable (persisted with the initial `save()` — before any worker path can observe the row).
- **Worker guards (four surfaces)**: startup recovery, the health-check RUNNING loop, the health-check PENDING loop, and pickup all short-circuit on `is_ledger` at the **top of each per-entry loop** — never finalize, never transition to `pending`, never pop/run. Each guard emits a structured skip log for runtime observability.
- **Duplicates are harmless, not deduped**: the flag makes the twin-driver bug independent of anchor count — every `sdlc-local-*` row carries `is_ledger=True` and is skipped by every guard, and the issue-keyed run lock (#2012) still admits exactly one driver. So a rare concurrent-creation duplicate is inert; no SETNX serialization is added. The existing best-effort idempotency (`find_session_by_issue` + `existing_by_id`) continues to collapse the common sequential case to one row.
- **Confirm-style migration**: register a read-only migration proving the additive field heals on legacy rows.

### Flow

Local supervisor runs `/do-sdlc N` → `session-ensure` creates `sdlc-local-N` **with `is_ledger=True`, status=running** → worker startup/health/pickup each see `is_ledger` and skip → anchor stays owned by the supervisor → no twin driver.

### Technical Approach

Line anchors below were re-verified against baseline `ed422b78` during this revision (see the Critique Response). Helpers: `_truthy()` already exists in both worker modules and coerces missing-attr / None / False / legacy-absent to non-ledger; a single module-level `_is_ledger(entry)` wrapper (`_truthy(getattr(entry, "is_ledger", False))`) is added to `agent/session_health.py` and reused at its three sites, and pickup uses `_truthy(getattr(candidate, "is_ledger", False))` inline (consistent with its adjacent `requires_real_chrome` check).

- **Field** (`models/agent_session.py`, near the other boolean fields ~321-345, e.g. beside `requires_real_chrome` at line 335): add `is_ledger = Field(default=False)`. Plain `Field` (not `IndexedField`): Popoto negative filtering is awkward, and every read site already loads the candidate object, so an attribute check via the `_truthy()` helper is cheaper and consistent with `requires_real_chrome`. Confirmed: `create_local(**kwargs)` forwards to the constructor and `save()`s in one call (`models/agent_session.py:1672-1674`), so a `create_local(is_ledger=True)` value is persisted at the initial write.
- **Creation** (`tools/sdlc_session_ensure.py`, kwargs build at line 443 where `kwargs["issue_number"] = issue_number` is set): add `kwargs["is_ledger"] = True` immediately alongside it, so the flag is persisted by the single `AgentSession.create_local(..., **kwargs)` at line 470 — never in a follow-up write (closes Race 2).
- **Startup recovery** (`agent/session_health.py::_recover_stale_sessions`, per-entry loop opens at line 671, `for entry in stale_sessions:`): as the **first statement inside the loop** — before `wk`/`is_local`/`session_type` are read at 672-674 — add `if _is_ledger(entry): logger.info("[startup-recovery] Skipping non-executable ledger %s (is_ledger, #2042)", entry.agent_session_id); continue`. This precedes the `is_local and session_type==ENG` requeue branch (680, which sets `new_status="pending"` at 699), the local abandon branch (722), and the bridge-recovery `else` (764, `new_status="pending"` at 778). Ledgers are counted in none of `bridge_count`/`local_dev_count`/`abandoned`.
- **Health check — RUNNING loop** (`agent/session_health.py::_agent_session_health_check`, loop at line 3281, `for entry in running_sessions:`): **hoist the guard to the top of the loop.** Place `if _is_ledger(entry): logger.info(...); continue` immediately **after** the terminal-status guard (which `continue`s at 3296) and **before** the `_delivery_belongs_to_current_run(entry)` exit at 3310. This is load-bearing: 3310 would `finalize_session(entry, "completed", ...)` at 3320 and destroy the supervisor's live ledger; 3360-3374 would `should_recover` via the `worker_dead` branch (the anchor reads `worker_alive=False`, `started_at=now`, so it trips once `running_seconds > AGENT_SESSION_HEALTH_MIN_RUNNING`); and 3395 is the #944 `no_progress` orphan net. One loop-top guard covers all three uniformly.
- **Health check — PENDING loop** (`agent/session_health.py`, loop at line 3449, `for entry in pending_sessions:`): as the first statement after `checked += 1`, add the same `if _is_ledger(entry): logger.info(...); continue`, so the orphaned-local-pending abandon at 3478-3500 (`worker_key.startswith("local")` → `finalize_session(..., "abandoned")`) never fires on a ledger that momentarily sits at `pending`.
- **Pickup** (`agent/session_pickup.py`, candidate selection loop at line 384, `for candidate in eligible:`): add `if _truthy(getattr(candidate, "is_ledger", False)): logger.info("[worker:%s] Skipping non-executable ledger %s (is_ledger, #2042)", worker_key, candidate.session_id); continue` alongside the throttle/terminal/`requires_real_chrome` skips (the `requires_real_chrome` skip at 424-434 is the exact precedent). This is the last-line guard: even if an upstream requeue were missed, a ledger that reached `pending` is never popped/run.
- **Discriminator predicate — dropped, not fixed.** The plan previously proposed correcting `session_id.startswith("local")` (line 673) to also match `sdlc-local-`. This is dropped: every `sdlc-local-*` row is a ledger (only `session-ensure` mints that id), the loop-top `is_ledger` guard skips it before the discriminator is ever read, and branch 680 that the "fix" would route into itself sets `new_status="pending"` (699) — the same requeue outcome as the `else`, so the change offered no protection the flag doesn't already provide. Leaving line 673 untouched keeps the change surface minimal and avoids perturbing the #1092 local-dev requeue routing.
- **Duplicates — no serialization.** No SETNX claim is added. `session_id = Field()` (`models/agent_session.py:141`) is a plain, **non-unique** field; the only unique pk is `id = AutoKeyField()` (line 140), and `query.filter(session_id=...)` returns a `list` precisely because `session_id` is non-unique (see the comment at 1799-1800). A duplicate `create_local` therefore does NOT collide — it produces a second row. With `is_ledger=True` on every such row and the four guards above, both rows are inert, so a duplicate is a cosmetic artifact swept by existing terminal cleanup, not a correctness hazard. The existing `find_session_by_issue` (line 405) and `existing_by_id` (line 423) checks still collapse the common sequential re-`ensure` to a single row.
- **Migration** (`scripts/update/migrations.py`): add `confirm_is_ledger_field_readable` mirroring `_migrate_confirm_issue_number_field_readable` (read-only probe over a sample of records), register it in the `MIGRATIONS` dict. Additive defaulted field needs no backfill; legacy `sdlc-local-*` rows are already terminal from prior runs and are swept by existing cleanup.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The touched recovery/pickup blocks wrap work in `try/except` with `logger.warning` + best-effort `delete()`. New guards are pure `logger.info` + `continue` at the top of each loop, before those blocks — assert via test that a ledger entry produces the skip **log line** and no status transition (observable behavior, not swallowed).
- [ ] No new `except Exception: pass` introduced. No new lock/serialization primitive is added (the dedupe-via-SETNX path is dropped), so no new lock-release failure mode exists.

### Empty/Invalid Input Handling
- [ ] `_is_ledger(entry)` / `_truthy(getattr(..., "is_ledger", False))` must treat missing attribute, `None`, `False`, and legacy rows (field absent) as non-ledger (executable) — test each.
- [ ] A ledger row momentarily at `status="pending"` (e.g. between `create_local` and `transition_status`) must be skipped by both the pickup loop and the health PENDING loop — test the pending-state guard, not only the running-state one.

### Error State Rendering
- Not user-visible output. The observable "error state" is a worker log line; tests assert the skip is logged and the anchor's `status` is unchanged (stays `running`).

## Test Impact

- [ ] `tests/unit/test_session_health*.py` (startup recovery + health-check tests) — UPDATE: add cases asserting `is_ledger` entries are skipped in the startup-recovery loop, the health RUNNING loop, and the health PENDING loop; assert the RUNNING-loop guard fires even when the anchor would otherwise match the delivery-finalize exit (status stays `running`, never flips to `completed`). Verify existing bridge/local-dev recovery cases still pass unchanged (guards are additive, at loop top).
- [ ] `tests/unit/test_session_pickup*.py` (if present) — UPDATE: add a case that a pending `is_ledger` candidate is skipped and pop returns the next eligible (or None).
- [ ] `tests/*/test_sdlc_session_ensure*.py` (if present) — UPDATE: assert the created anchor carries `is_ledger=True` (persisted at `create_local` time). No dedupe/exactly-one test — duplicate anchors are an accepted, inert outcome; instead assert that a duplicate row is also `is_ledger=True` and therefore skipped by the guards.
- [ ] `scripts/update/migrations.py` test coverage (if a migrations test exists) — UPDATE: assert the new migration key runs idempotently and returns None.

If a precise target file does not yet exist for a surface above, the builder creates it. No existing test asserts the *current* (buggy) requeue-of-anchor behavior, so nothing needs DELETE/REPLACE — the change is additive guarding.

## Rabbit Holes

- **Reworking the ledger vs. session model.** #2012 already moved durable state to `PipelineLedger`; do NOT attempt to eliminate the `AgentSession` anchor entirely here. Just mark it non-executable.
- **Making `is_ledger` an `IndexedField` to filter at query time.** Popoto negative/exclusion filtering is awkward and the read sites already materialize candidates; a plain field + attribute check is simpler and matches `requires_real_chrome`. Avoid the index rabbit hole.
- **A general "session capability" taxonomy.** Tempting to model executable/schedulable/ledger as an enum. Out of scope — one boolean solves the bug.
- **Auditing every `status="pending"` reader in the whole codebase.** Scope the audit to worker requeue + pickup surfaces (the ones that transition `sdlc-local-*` to pending or pop it). The dashboard/read-only callers do not run sessions.
- **Serializing anchor creation to guarantee "exactly one" row.** A SETNX/lock around find-or-create was considered and rejected: `session_id` is non-unique so there is no natural collision to lean on, and the flag already makes duplicates inert. Adding a lock introduces a stuck-lock failure mode (a jammed claim blocks anchor creation) for zero correctness gain. Accept harmless duplicates.
- **"Fixing" the `startswith("local")` discriminator.** It looks like a latent bug, but every `sdlc-local-*` row is a ledger skipped at loop top, and the branch the "fix" routes into requeues to `pending` anyway. Touching it perturbs #1092 routing for no benefit. Leave it.

## Risks

### Risk 1: A requeue/pickup site is missed
**Impact:** The bug reopens silently — a missed path still resets or runs the anchor.
**Mitigation:** Enumerate all sites in Technical Approach (startup recovery, health RUNNING loop, health PENDING loop, pickup candidate loop) and place each guard at loop top so ALL downstream branches are covered uniformly — this is what closed CONCERN #2 (the RUNNING-loop delivery-finalize exit at 3310). Each guard emits a structured skip log, so a live worker's skip is observable in prod, not only asserted by a unit test (closes the observability NIT). Add a Verification grep asserting `is_ledger` is referenced in both `session_health.py` and `session_pickup.py`. The pickup-loop guard is a catch-all last line: even if an upstream requeue is missed, a ledger that reaches `pending` is never popped.

### Risk 2: Legacy rows lack the field
**Impact:** Popoto lazy-load on a row written before the field existed could error.
**Mitigation:** Additive `Field(default=False)`; access via `getattr(..., "is_ledger", False)` wrapped in `_truthy()`. Confirm-style migration probes legacy rows. Legacy `sdlc-local-*` anchors from prior runs are terminal and swept by existing cleanup, so they never hit the guarded live paths anyway.

### Risk 3: A concurrent-creation duplicate anchor appears
**Impact:** Two `sdlc-local-{N}` rows for one issue — cosmetically messy on the dashboard.
**Mitigation:** Accepted as inert, not prevented. Both rows carry `is_ledger=True` and are skipped by all four guards; the issue-keyed run lock (#2012) admits exactly one driver regardless of row count; existing terminal cleanup sweeps the extra row. No serialization primitive is added, so no stuck-lock failure mode is introduced. This is a deliberate trade: a rare, harmless duplicate over a new operational failure mode.

## Race Conditions

### Race 1: Concurrent duplicate anchor creation
**Location:** `tools/sdlc_session_ensure.py` find-or-create block (lines 405-501)
**Trigger:** Two `session-ensure` calls for the same issue (e.g., local `/do-sdlc` retry + a routing lookup with `ensure=True`) both pass the `existing_by_id` check (line 423) before either `save()`s.
**Data prerequisite:** The `sdlc-local-{N}` record must be visible to the second caller's query before it decides to create.
**Outcome:** Two rows with the SAME `session_id` (`session_id` is a non-unique `Field()`, `models/agent_session.py:141` — there is NO collision) but distinct `id` (`AutoKeyField`, line 140), **both** `is_ledger=True`.
**Disposition:** Accepted, not mitigated. Both duplicates are inert (skipped by all four guards); the issue-keyed run lock admits one driver; existing cleanup sweeps the extra row. Adding a lock would trade a harmless cosmetic duplicate for a stuck-lock failure mode — not worth it. This corrects the prior plan's factually wrong "session_id collision is a natural backstop" premise (critique BLOCKER).

### Race 2: session-ensure `transition_status(running)` vs. worker recovery
**Location:** `session_ensure` line ~471 (`transition_status → running`) vs. `session_health.py` recovery.
**Trigger:** Worker recovery scans the anchor in the window between `create_local` (status defaults) and `transition_status(..., running)`.
**Data prerequisite:** `is_ledger=True` must be persisted **at `create_local` time**, not after — so the flag is visible in the earliest window.
**State prerequisite:** The guard reads `is_ledger`, which is independent of `status`, so it holds regardless of whether the anchor is momentarily `pending` or `running`.
**Mitigation:** Set `is_ledger` in the `create_local` kwargs (persisted with the initial `save()`), never in a follow-up write. The worker guards key on `is_ledger`, not `status`, so the race window is closed.

## No-Gos (Out of Scope)

- Nothing deferred — every relevant item (field, creation flag, four loop-top guard surfaces with skip logs, migration, tests, docs) is in scope for this plan. Explicitly out of scope by design: SETNX/dedupe serialization and the `startswith("local")` discriminator change (both rejected — see Rabbit Holes and the Critique Response).

## Update System

The new `is_ledger` field is a Popoto schema change, so per the repo's "Popoto Schema Migration Requirement" it needs a migration:

- Add `_migrate_confirm_is_ledger_field_readable(project_dir)` to `scripts/update/migrations.py` (read-only probe, mirrors `_migrate_confirm_issue_number_field_readable`).
- Register it in the `MIGRATIONS` dict (required — `run_pending_migrations()` iterates `MIGRATIONS`). Idempotent; recorded once in `data/migrations_completed.json`.
- No new dependencies or config files to propagate. `scripts/update/run.py` needs no changes beyond the migration registration it already iterates.

## Agent Integration

No agent integration required — this is a worker-internal correctness fix. The flag is set by the existing `sdlc-tool session-ensure` CLI (already wired) and honored by worker code paths (`session_health.py`, `session_pickup.py`) that run in-process. No new MCP surface, no `.mcp.json` change, and the bridge imports nothing new. The behavior is exercised end-to-end by the regression tests, not by an agent tool call.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/eng-session-architecture.md` (or the closest session-lifecycle doc) to document the `is_ledger` non-executable-ledger flag: what sets it, which worker paths honor it, and why (local `/do-sdlc` + worker coexistence).
- [ ] Add/verify an entry in `docs/features/README.md` index if a new section is added.

### Inline Documentation
- [ ] Docstring on the `is_ledger` field explaining it marks CLI-created `sdlc-local-*` anchors as non-executable ledgers.
- [ ] Comments at each guard site (startup recovery, health running/pending loops, pickup loop) referencing issue #2042.

## Success Criteria

- [x] `AgentSession` has a persisted `is_ledger` field defaulting to `False`.
- [x] `sdlc-tool session-ensure` creates `sdlc-local-{N}` anchors with `is_ledger=True` (set at `create_local` time, persisted with the initial `save()`).
- [x] All four worker surfaces skip `is_ledger` records at the **top of their per-entry loop** — startup recovery, the health-check RUNNING loop, the health-check PENDING loop, and `session_pickup` — never finalize them (including the delivery-finalize exit), never transition them to `pending`, never pop/run them.
- [x] Each of the four guard sites emits a structured skip log line when it skips a ledger (runtime observability).
- [x] Regression test: a simulated live-worker recovery pass over a freshly created `sdlc-local` anchor asserts it is NOT requeued (status stays `running`), NOT finalized to `completed`, and NOT run.
- [x] A duplicate `sdlc-local-{N}` row is also `is_ledger=True` and is skipped by the guards (duplicates are inert; no "exactly one" invariant is asserted).
- [x] Confirm-style migration registered and idempotent.
- [x] Tests pass (`/do-test`)
- [x] Documentation updated (`/do-docs`)
- [x] grep confirms `is_ledger` is referenced in both `agent/session_health.py` and `agent/session_pickup.py`.

## Team Orchestration

The lead agent orchestrates; it does not build directly.

### Team Members

- **Builder (model+ensure)**
  - Name: `ledger-flag-builder`
  - Role: Add `is_ledger` field + migration; set `is_ledger=True` in `session_ensure` at `create_local` time.
  - Agent Type: builder
  - Domain: data (Redis/Popoto)
  - Resume: true

- **Builder (worker-guards)**
  - Name: `worker-guard-builder`
  - Role: Add the four loop-top guard surfaces (with structured skip logs) + the `_is_ledger` helper in `session_health.py` / `session_pickup.py`.
  - Agent Type: builder
  - Domain: async/concurrency
  - Resume: true

- **Test engineer**
  - Name: `ledger-tester`
  - Role: Regression tests (startup/running/pending recovery skips, pickup skip, creation flag, duplicate-inert, legacy-row safety).
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: `ledger-validator`
  - Role: Verify all success criteria + Verification rows.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `ledger-doc`
  - Role: Session-lifecycle doc + inline docs.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Add is_ledger field + migration
- **Task ID**: build-field
- **Depends On**: none
- **Validates**: `tests/*/test_sdlc_session_ensure*.py`, migrations test (create/update)
- **Assigned To**: ledger-flag-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `is_ledger = Field(default=False)` to `models/agent_session.py` near the boolean-field cluster (~321-345) with a docstring.
- Add `_migrate_confirm_is_ledger_field_readable` to `scripts/update/migrations.py` and register it in `MIGRATIONS`.

### 2. Set flag in session-ensure
- **Task ID**: build-ensure
- **Depends On**: build-field
- **Validates**: `tests/*/test_sdlc_session_ensure*.py`
- **Assigned To**: ledger-flag-builder
- **Agent Type**: builder
- **Parallel**: false
- In `tools/sdlc_session_ensure.py`, add `kwargs["is_ledger"] = True` at line 443 where `kwargs["issue_number"]` is set, so it is persisted by the `create_local(..., **kwargs)` at line 470.
- No dedupe/SETNX: duplicate anchors are inert (both `is_ledger=True`). Leave the existing `find_session_by_issue` / `existing_by_id` idempotency checks as-is.

### 3. Add worker guards (four loop-top surfaces)
- **Task ID**: build-guards
- **Depends On**: build-field
- **Validates**: `tests/unit/test_session_health*.py`, `tests/unit/test_session_pickup*.py` (create if absent)
- **Informed By**: Technical Approach (exact line anchors, re-verified against `ed422b78`)
- **Assigned To**: worker-guard-builder
- **Agent Type**: builder
- **Parallel**: true
- `agent/session_health.py`: add a module-level `_is_ledger(entry)` helper (`_truthy(getattr(entry, "is_ledger", False))`). Guard at the top of the startup-recovery loop (first statement inside `for entry in stale_sessions:` at line 671); at the top of the RUNNING loop (immediately after the terminal-status `continue` at 3296, **before** the delivery-finalize exit at 3310); and at the top of the PENDING loop (after `checked += 1` at line 3450). Each guard: structured `logger.info` skip line + `continue`.
- `agent/session_pickup.py`: skip `is_ledger` candidates in the selection loop (`for candidate in eligible:` at line 384) using `_truthy(getattr(candidate, "is_ledger", False))`, with a structured skip log, alongside the `requires_real_chrome` skip.
- Do NOT touch the `startswith("local")` discriminator (line 673) — dropped as inert; see Technical Approach.

### 4. Regression tests
- **Task ID**: build-tests
- **Depends On**: build-ensure, build-guards
- **Assigned To**: ledger-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Startup-recovery skip test (anchor stays `running`, not requeued/run).
- Health RUNNING-loop skip test — including the case where the anchor would otherwise hit the delivery-finalize exit; assert status stays `running`, never `completed`.
- Health PENDING-loop skip test (ledger at `pending` is not abandoned).
- Pickup-skip test.
- Creation-flag test (`is_ledger=True` at `create_local` time).
- Duplicate-inert test: two rows with the same `session_id` are both `is_ledger=True` and both skipped by the guards (no "exactly one" assertion).
- Legacy-row (field-absent) safety test for `_is_ledger` / `_truthy`.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: build-guards, build-ensure
- **Assigned To**: ledger-doc
- **Agent Type**: documentarian
- **Parallel**: false
- Document `is_ledger` in the session-lifecycle/eng-session-architecture doc; add inline docstrings/comments referencing #2042.

### 6. Final validation
- **Task ID**: validate-all
- **Depends On**: build-tests, document-feature
- **Assigned To**: ledger-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all Verification commands; confirm every success criterion.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Field exists | `grep -n "is_ledger" models/agent_session.py` | output contains is_ledger |
| Flag set at creation | `grep -n "is_ledger" tools/sdlc_session_ensure.py` | output contains is_ledger |
| Health guards present (3 sites) | `grep -c "is_ledger" agent/session_health.py` | output >= 3 |
| Pickup guard present | `grep -c "is_ledger" agent/session_pickup.py` | output > 0 |
| Skip logs present | `grep -c "is_ledger, #2042" agent/session_health.py agent/session_pickup.py` | output >= 4 across files |
| Migration registered | `grep -n "confirm_is_ledger_field_readable" scripts/update/migrations.py` | output contains confirm_is_ledger_field_readable |
| Ledger tests pass | `pytest tests/ -k "ledger or is_ledger" -q` | exit code 0 |
| Lint clean | `python -m ruff check agent/ tools/ models/ scripts/` | exit code 0 |
| Format clean | `python -m ruff format --check agent/ tools/ models/ scripts/` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room) — FULL depth, 3 critics. Verdict: NEEDS REVISION (1 blocker, 2 concerns, 1 nit). -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness (Skeptic) + Scope & Value (Simplifier) | The plan's "session_id collision is a natural uniqueness backstop" claim (Race 1, Risk 3, Technical Approach, Open Question 2) is factually wrong. `session_id = Field()` (`models/agent_session.py:141`) is a plain non-unique field; the unique pk is `id = AutoKeyField()` (line 140). Two `create_local` calls produce two rows with identical `session_id`, so collision-only does NOT dedupe. Open Question 2 forces a human decision on this false premise. | Correct the uniqueness model in Race 1 / Risk 3 / Technical Approach / Open Question 2 | `session_id` is a plain `Field()` (agent_session.py:141), non-unique; `id = AutoKeyField()` (line 140) is the only unique pk, and `query.filter(session_id=...)` returns a `list` precisely because it is non-unique (see the comment at agent_session.py:1799-1800). If "exactly one anchor" (success criterion line 209) is required, the SETNX claim is **load-bearing, not belt-and-suspenders**, and must be fail-closed (on claim-loss: re-read and return the winner, never create). If duplicate anchors are acceptable (both carry `is_ledger=True` and are skipped by every guard, so the twin-driver bug is fixed regardless), drop the "exactly one" criterion instead. Do NOT rely on a `session_id` collision. |
| CONCERN | Risk & Robustness (Adversary) + History & Consistency (Consistency) | The plan places the health-check RUNNING-loop guard at "~3360, before should_recover is set," but `should_recover` is initialized at 3356 (upstream of 3360) and there is an even-earlier exit at 3310 (`_delivery_belongs_to_current_run` → finalize **completed**), which would flip the anchor `running`→`completed` and destroy the supervisor's ledger. The anchor also matches the #944 no_progress branch at 3395. | Hoist the RUNNING-loop guard to the top of the per-entry loop | Place `if _is_ledger(entry): <debug log>; continue` immediately after the terminal-status check (`agent/session_health.py` ~3289) and **before** the `_delivery_belongs_to_current_run(entry)` exit at 3310 — not at 3360. A single loop-top guard covers all three downstream branches (3310 delivery-finalize, 3360-3369 worker_dead, 3395 #944 no_progress) uniformly. The anchor is `status=running, started_at=now, worker_alive=False, in_scope_handle=None`, so it matches worker_dead once `running_seconds > AGENT_SESSION_HEALTH_MIN_RUNNING`. This directly closes the plan's own Risk 1. |
| CONCERN | Scope & Value (Simplifier) + History & Consistency (Consistency) | The discriminator fix's stated rationale (line 114: `startswith(("local","sdlc-local"))` "routes `sdlc-local-` to a safe branch rather than bridge recovery") is inaccurate. Branch 680 (`is_local and session_type==ENG`) itself sets `new_status="pending"` (699) — the same requeue outcome as the else branch (778). The change is inert only because the `is_ledger` guard precedes it; it adds no protection the flag doesn't already provide. | Reword the claim or drop the discriminator change | `session_health.py:680` routes eng-typed anchors to `new_status="pending"` (699), so the "safe branch" framing is wrong — it moves the anchor from one requeue path to another. Either drop the discriminator change (the `is_ledger` flag is the sole real guarantee) or reword the plan to present it as code hygiene, NOT a defensive safety layer. Do NOT describe branch 680 as "safe." |
| NIT | Risk & Robustness (Operator) | Confidence rests entirely on grep-for-reference checks and unit tests; nothing gives an operator a runtime signal that a live worker actually skipped a ledger in prod. A reordered guard could pass grep and silently reopen the bug. | Add a structured skip log / counter at each guard site | Emit a structured `logger.info` (or a project-scoped Redis counter) at each of the four guard sites when a ledger is skipped, so the skip is observable at runtime, not only asserted by tests. |

### Critique Response (revision, 2026-07-12)

All findings re-verified against source at baseline `ed422b78` before editing.

- **BLOCKER (dedupe model) — RESOLVED via option (b): accept harmless duplicates.** Confirmed `session_id = Field()` is non-unique (`models/agent_session.py:141`) and `id = AutoKeyField()` (line 140) is the sole unique pk (the `list(...)` re-fetch at 1799-1802 depends on exactly this). The false "collision backstop" premise is removed from Technical Approach, Risk 3, and Race 1. Chose option (b) over the fail-closed SETNX (option a) on the merits: the whole point of the fix is that `is_ledger` makes anchor count irrelevant to correctness (every duplicate is skipped by all four guards; the issue-keyed run lock admits one driver). Option (a) would add a stuck-lock failure mode to enforce an invariant the bug fix no longer needs. The "exactly one anchor" success criterion is dropped; a duplicate-inert test replaces the dedupe test.
- **CONCERN (RUNNING-loop guard placement) — RESOLVED.** Confirmed the delivery-finalize exit at 3310 (`finalize_session(..., "completed")` at 3320) precedes `should_recover` (3356) and would destroy the ledger. Guard hoisted to loop top: after the terminal-status `continue` (3296) and before 3310. One guard now covers 3310 / 3360-3374 / 3395 uniformly. Confirmed the anchor (`worker_alive=False`, `started_at=now`) trips the `worker_dead` branch, so the placement matters.
- **CONCERN (discriminator inert) — RESOLVED via drop.** Confirmed branch 680 (`is_local and session_type==ENG`) sets `new_status="pending"` (699) — same requeue as the `else` (778), so the "safe branch" framing was wrong. The discriminator change is dropped entirely (not reworded-and-kept): all `sdlc-local-*` rows are ledgers skipped at loop top, so the predicate never governs them. Left line 673 untouched to avoid perturbing #1092 routing.
- **NIT (observability) — RESOLVED.** Each of the four guard sites emits a structured `logger.info` skip line tagged `is_ledger, #2042`, added to Success Criteria and the Verification table.

## Resolved Decisions

1. **Field name → `is_ledger`.** Positive name reads clearly at every guard site (`if _is_ledger(entry): continue`) and matches the issue's "non-executable ledger" framing.
2. **Dedupe → none (accept harmless duplicates).** See BLOCKER resolution above — `is_ledger` makes anchor count irrelevant; no SETNX serialization, no "exactly one" invariant, no new failure mode.
3. **Discriminator predicate → leave untouched (dropped).** See CONCERN resolution above — inert as a safety layer; the flag is the sole guarantee.
