---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-07-12
tracking: https://github.com/Yudame/valor-engels/issues/2042
last_comment_id:
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
A local `/do-sdlc` run and a live worker coexist safely. CLI-created `sdlc-local-*` anchors are explicitly marked as **non-executable ledgers**. Every worker path that would requeue or run a session honors that mark and skips them. Exactly one anchor exists per issue.

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
- PM check-ins: 1-2 (confirm field name + dedupe strategy)
- Review rounds: 1 (concurrency-sensitive read sites warrant a careful review)

The coding is small (one field, a handful of guards, one migration, tests). The care is in auditing every requeue/pickup site so no path is missed — a single missed site reopens the bug.

## Prerequisites

No external prerequisites — this work has no new secrets, services, or API dependencies. It touches only in-repo Popoto models and worker code.

## Solution

### Key Elements

- **`is_ledger` boolean on `AgentSession`**: a persisted, defaulted (`Field(default=False)`) flag marking a record as a non-executable ledger. Mirrors the existing `requires_real_chrome` / `retain_for_resume` boolean-field pattern.
- **Set at creation**: `sdlc_session_ensure.py` passes `is_ledger=True` into `create_local`, so every CLI anchor is born non-executable.
- **Worker guards (three surfaces)**: startup recovery, health-check requeue (running + pending loops), and pickup all short-circuit on `is_ledger` — never transition to `pending`, never pop/run.
- **Defensive discriminator fix**: replace the fragile `session_id.startswith("local")` with a helper predicate so `sdlc-local-` is recognized as local-and-non-executable even if a flag were somehow absent (belt-and-suspenders).
- **Per-issue dedupe**: harden `session_ensure` so exactly one `sdlc-local-{N}` anchor can exist per issue under concurrent creation.
- **Confirm-style migration**: register a read-only migration proving the additive field heals on legacy rows.

### Flow

Local supervisor runs `/do-sdlc N` → `session-ensure` creates `sdlc-local-N` **with `is_ledger=True`, status=running** → worker startup/health/pickup each see `is_ledger` and skip → anchor stays owned by the supervisor → no twin driver.

### Technical Approach

- **Field** (`models/agent_session.py`, near the other boolean fields ~321-345): add `is_ledger = Field(default=False)`. Plain `Field` (not `IndexedField`): Popoto negative filtering is awkward, and every read site already loads the candidate object, so an attribute check via a `_truthy()`-style helper is cheaper and consistent with `requires_real_chrome`.
- **Creation** (`tools/sdlc_session_ensure.py` ~kwargs build ~line 435): add `kwargs["is_ledger"] = True` alongside the existing `kwargs["issue_number"] = issue_number` write-once mirror.
- **Startup recovery** (`agent/session_health.py::_recover_stale_sessions`, loop ~671): at the top of the per-entry loop, `if _is_ledger(entry): continue` (with a debug log). This precedes the `is_local`/`else` fork so ledgers are never reset to `pending` (line 778) nor abandoned/deleted (~750). Do NOT count them in `bridge_count`/`local_dev_count`.
- **Health check** (`agent/session_health.py::_agent_session_health_check`): guard **both** the RUNNING-session recovery path (~3360, before `should_recover` is set) and the PENDING-session loop (line 3448, `for entry in pending_sessions`) — skip ledgers so neither the `worker_dead` recovery nor the orphaned-pending abandon touches them.
- **Pickup** (`agent/session_pickup.py`, candidate loop ~383): add `if _truthy(getattr(candidate, "is_ledger", False)): continue` alongside the throttle/terminal/real-chrome skips, so a ledger that somehow reached `pending` is never chosen. This is the last-line guard.
- **Discriminator fix** (defensive, `agent/session_health.py:673`): introduce a module-level `_is_local_session(session_id)` returning `session_id.startswith(("local", "sdlc-local"))` and use it at 673 and at the pending-loop `worker_key.startswith("local")` site (3478). This corrects the long-standing prefix bug so that even absent the flag, `sdlc-local-` routes to a safe branch rather than bridge recovery.
- **Dedupe** (`tools/sdlc_session_ensure.py`): the existing `existing_by_id` query already returns early when a `sdlc-local-{N}` exists, but two racing callers can both pass the check before either saves. Serialize creation with a short-lived Redis SETNX claim keyed by `sdlc-local-ensure:{issue_number}` (reuse the `claim_pending_run` primitive's redis client) around the find-or-create critical section; on losing the claim, re-read and return the winner's record. Because the id is deterministic (`sdlc-local-{N}`), a duplicate `save()` would collide on `session_id` anyway — the claim closes the check-then-act window and guarantees one ledger per issue.
- **Migration** (`scripts/update/migrations.py`): add `confirm_is_ledger_field_readable` mirroring `_migrate_confirm_issue_number_field_readable` (read-only probe over a sample of records), register it in the `MIGRATIONS` dict. Additive defaulted field needs no backfill; legacy `sdlc-local-*` rows are already terminal from prior runs and are swept by existing cleanup.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The touched recovery/pickup blocks wrap work in `try/except` with `logger.warning` + best-effort `delete()`. New guards are pure `continue` before those blocks — assert via test that a ledger entry produces the skip **log line** and no status transition (observable behavior, not swallowed).
- [ ] No new `except Exception: pass` introduced; the dedupe claim's lock-release is `finally`-guarded and logged on failure.

### Empty/Invalid Input Handling
- [ ] `_is_ledger` / `_truthy(getattr(..., "is_ledger", False))` must treat missing attribute, `None`, `False`, and legacy rows (field absent) as non-ledger (executable) — test each.
- [ ] `_is_local_session(None)` and empty-string session_id must not raise — guard and test.

### Error State Rendering
- Not user-visible output. The observable "error state" is a worker log line; tests assert the skip is logged and the anchor's `status` is unchanged (stays `running`).

## Test Impact

- [ ] `tests/unit/test_session_health*.py` (startup recovery + health-check tests) — UPDATE: add cases asserting `is_ledger` entries are skipped; verify existing bridge/local-dev recovery cases still pass unchanged (guard is additive, precedes existing forks).
- [ ] `tests/unit/test_session_pickup*.py` (if present) — UPDATE: add a case that a pending `is_ledger` candidate is skipped and pop returns the next eligible (or None).
- [ ] `tests/*/test_sdlc_session_ensure*.py` (if present) — UPDATE: assert the created anchor carries `is_ledger=True`; add a concurrent-creation dedupe test asserting exactly one `sdlc-local-{N}` row.
- [ ] `scripts/update/migrations.py` test coverage (if a migrations test exists) — UPDATE: assert the new migration key runs idempotently and returns None.

If a precise target file does not yet exist for a surface above, the builder creates it. No existing test asserts the *current* (buggy) requeue-of-anchor behavior, so nothing needs DELETE/REPLACE — the change is additive guarding.

## Rabbit Holes

- **Reworking the ledger vs. session model.** #2012 already moved durable state to `PipelineLedger`; do NOT attempt to eliminate the `AgentSession` anchor entirely here. Just mark it non-executable.
- **Making `is_ledger` an `IndexedField` to filter at query time.** Popoto negative/exclusion filtering is awkward and the read sites already materialize candidates; a plain field + attribute check is simpler and matches `requires_real_chrome`. Avoid the index rabbit hole.
- **A general "session capability" taxonomy.** Tempting to model executable/schedulable/ledger as an enum. Out of scope — one boolean solves the bug.
- **Auditing every `status="pending"` reader in the whole codebase.** Scope the audit to worker requeue + pickup surfaces (the ones that transition `sdlc-local-*` to pending or pop it). The dashboard/read-only callers do not run sessions.

## Risks

### Risk 1: A requeue/pickup site is missed
**Impact:** The bug reopens silently — a missed path still resets or runs the anchor.
**Mitigation:** Enumerate all sites in Technical Approach (startup recovery, health running loop, health pending loop, pickup candidate loop). Add a Verification grep asserting `is_ledger` is referenced in both `session_health.py` and `session_pickup.py`. The pickup-loop guard is a catch-all last line: even if an upstream requeue is missed, a ledger that reaches `pending` is never popped.

### Risk 2: Legacy rows lack the field
**Impact:** Popoto lazy-load on a row written before the field existed could error.
**Mitigation:** Additive `Field(default=False)`; access via `getattr(..., "is_ledger", False)`. Confirm-style migration probes legacy rows. Legacy `sdlc-local-*` anchors from prior runs are terminal and swept by existing cleanup, so they never hit the guarded live paths anyway.

### Risk 3: Dedupe claim adds a new failure mode
**Impact:** A stuck lock could block anchor creation.
**Mitigation:** Short TTL on the SETNX claim; `finally`-release; on claim-loss the caller re-reads and returns the existing deterministic-id record rather than erroring. The deterministic `session_id` is itself a natural uniqueness backstop.

## Race Conditions

### Race 1: Concurrent duplicate anchor creation
**Location:** `tools/sdlc_session_ensure.py` find-or-create block (~405-501)
**Trigger:** Two `session-ensure` calls for the same issue (e.g., local `/do-sdlc` retry + a routing lookup with `ensure=True`) both pass the `existing_by_id` check before either `save()`s.
**Data prerequisite:** The `sdlc-local-{N}` record must be visible to the second caller's query before it decides to create.
**State prerequisite:** Exactly one ledger per issue.
**Mitigation:** SETNX claim keyed by issue around the critical section; loser re-reads. Deterministic `session_id` collision is the backstop.

### Race 2: session-ensure `transition_status(running)` vs. worker recovery
**Location:** `session_ensure` line ~471 (`transition_status → running`) vs. `session_health.py` recovery.
**Trigger:** Worker recovery scans the anchor in the window between `create_local` (status defaults) and `transition_status(..., running)`.
**Data prerequisite:** `is_ledger=True` must be persisted **at `create_local` time**, not after — so the flag is visible in the earliest window.
**State prerequisite:** The guard reads `is_ledger`, which is independent of `status`, so it holds regardless of whether the anchor is momentarily `pending` or `running`.
**Mitigation:** Set `is_ledger` in the `create_local` kwargs (persisted with the initial `save()`), never in a follow-up write. The worker guards key on `is_ledger`, not `status`, so the race window is closed.

## No-Gos (Out of Scope)

- Nothing deferred — every relevant item (field, creation flag, three guard surfaces, discriminator fix, dedupe, migration, tests, docs) is in scope for this plan.

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

- [ ] `AgentSession` has a persisted `is_ledger` field defaulting to `False`.
- [ ] `sdlc-tool session-ensure` creates `sdlc-local-{N}` anchors with `is_ledger=True` (set at `create_local` time).
- [ ] Worker startup recovery, health-check (running + pending loops), and `session_pickup` all skip `is_ledger` records — never transition them to `pending`, never pop/run them.
- [ ] The `startswith("local")` discriminator is corrected to also recognize `sdlc-local-`.
- [ ] Exactly one `sdlc-local-{N}` anchor exists per issue under concurrent creation (dedupe test passes).
- [ ] Regression test: a simulated live-worker recovery pass over a freshly created `sdlc-local` anchor asserts it is NOT requeued (status stays `running`) and NOT run.
- [ ] Confirm-style migration registered and idempotent.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] grep confirms `is_ledger` is referenced in both `agent/session_health.py` and `agent/session_pickup.py`.

## Team Orchestration

The lead agent orchestrates; it does not build directly.

### Team Members

- **Builder (model+ensure)**
  - Name: `ledger-flag-builder`
  - Role: Add `is_ledger` field + migration; set it in `session_ensure`; add dedupe claim.
  - Agent Type: builder
  - Domain: data (Redis/Popoto)
  - Resume: true

- **Builder (worker-guards)**
  - Name: `worker-guard-builder`
  - Role: Add the three guard surfaces + discriminator fix in `session_health.py` / `session_pickup.py`.
  - Agent Type: builder
  - Domain: async/concurrency
  - Resume: true

- **Test engineer**
  - Name: `ledger-tester`
  - Role: Regression tests (recovery skip, pickup skip, creation flag, dedupe race, legacy-row safety).
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

### 2. Set flag in session-ensure + dedupe
- **Task ID**: build-ensure
- **Depends On**: build-field
- **Validates**: `tests/*/test_sdlc_session_ensure*.py`
- **Assigned To**: ledger-flag-builder
- **Agent Type**: builder
- **Parallel**: false
- In `tools/sdlc_session_ensure.py`, add `kwargs["is_ledger"] = True` where `issue_number` is set.
- Add a SETNX claim keyed by `sdlc-local-ensure:{issue_number}` around the find-or-create critical section; loser re-reads and returns the existing record.

### 3. Add worker guards + discriminator fix
- **Task ID**: build-guards
- **Depends On**: build-field
- **Validates**: `tests/unit/test_session_health*.py`, `tests/unit/test_session_pickup*.py` (create if absent)
- **Informed By**: Technical Approach (exact line anchors)
- **Assigned To**: worker-guard-builder
- **Agent Type**: builder
- **Parallel**: true
- `agent/session_health.py`: skip `is_ledger` at the top of the startup-recovery loop (~671), the health running-session path (~3360), and the pending-session loop (3448). Add `_is_local_session()` helper and use it at 673 + 3478.
- `agent/session_pickup.py`: skip `is_ledger` candidates in the selection loop (~383) using `_truthy`.

### 4. Regression tests
- **Task ID**: build-tests
- **Depends On**: build-ensure, build-guards
- **Assigned To**: ledger-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Recovery-skip test (anchor stays `running`, not requeued/run).
- Pickup-skip test.
- Creation-flag test + concurrent-creation dedupe test.
- Legacy-row (field-absent) safety test for `_is_ledger` / `_is_local_session`.

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
| Health guard present | `grep -c "is_ledger" agent/session_health.py` | output > 0 |
| Pickup guard present | `grep -c "is_ledger" agent/session_pickup.py` | output > 0 |
| Discriminator fixed | `grep -n "sdlc-local" agent/session_health.py` | output contains sdlc-local |
| Migration registered | `grep -n "confirm_is_ledger_field_readable" scripts/update/migrations.py` | output contains confirm_is_ledger_field_readable |
| Ledger tests pass | `pytest tests/ -k "ledger or is_ledger" -q` | exit code 0 |
| Lint clean | `python -m ruff check agent/ tools/ models/ scripts/` | exit code 0 |
| Format clean | `python -m ruff format --check agent/ tools/ models/ scripts/` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

## Open Questions

1. **Field name**: `is_ledger` (chosen) vs. `executable=False`. I chose `is_ledger=True` because the positive name reads clearly at every guard site (`if is_ledger: continue`) and matches the issue's "non-executable ledger" framing. Confirm or override.
2. **Dedupe mechanism**: SETNX claim around find-or-create (chosen) vs. relying solely on the deterministic `session_id` collision. The deterministic id already prevents two *distinct* rows; the claim additionally closes the check-then-act window so the loser returns the winner cleanly instead of erroring. Confirm the SETNX approach is acceptable, or prefer the simpler collision-only path.
3. **Discriminator scope**: the defensive `startswith(("local", "sdlc-local"))` fix touches the local-dev requeue routing. Confirm we want the corrected predicate to route `sdlc-local-` into the local branch (where the `is_ledger` guard then skips it) rather than leaving 673 untouched and relying only on the flag.
