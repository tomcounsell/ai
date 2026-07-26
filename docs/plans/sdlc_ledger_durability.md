---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-07-26
tracking: https://github.com/tomcounsell/ai/issues/2395
last_comment_id: 
---

# SDLC Session Ledger Durability

## Problem

During a `/do-sdlc` run, the issue-keyed `PipelineLedger` record for a live
pipeline is silently wiped mid-run: all stage markers reset to `{}` and the
`run_id` is invalidated. The router then reads an empty ledger, concludes the
pipeline never started, and falls back to "Cannot build without a plan" —
restarting at PLAN — on an issue that at that moment has a committed plan, an
open PR, and an approved build.

**Current behavior:**
- `PipelineLedger.get_or_create` is a non-atomic filter-then-create. Its
  existence check (`cls.query.filter(ledger_key=key)`) is a **class-set index
  read**, which popoto's `rebuild_indexes()` transiently empties (the #1720
  hazard). On a false-miss it calls `cls.create(...)`, and `create()` on an
  already-populated key **fully overwrites `stage_states_json` back to `"{}"`** —
  a wipe, not a merge. Reproduced 2/2 for in-flight pipelines (#2376, #2337).
- The read path mutates: `_resolve_issue_record` calls `get_or_create` on **every**
  `stage-query`. Because the router polls `stage-query` constantly, the router's
  own liveness polling is itself a wipe trigger — and it litters empty ledgers
  for every issue ever queried.
- On any ledger loss, the router's recovery path from "no markers" is to restart
  from PLAN, which on an issue with an open/merged PR means duplicate work or a
  confused no-op.

**Desired outcome:**
- A live ledger's stage markers survive concurrent `get_or_create`/`for_issue`
  calls that land inside a `rebuild_indexes` window. No wipe.
- Read-only stage queries never create or mutate a ledger. A never-seen issue
  reads as empty without a side-effect record being written.
- When a ledger is nonetheless found empty for an issue with observable durable
  state (plan doc, open PR, review), the router reconstructs stage state from
  those signals instead of restarting at PLAN.

## Freshness Check

**Baseline commit:** `056c76e3d456af9c1cf2384f3e958bf7d1500147`
**Issue filed at:** 2026-07-26T06:48:40Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `agent/pipeline_ledger.py:85-115` — `get_or_create` is a non-atomic
  `query.filter` existence check then `cls.create(...)` with
  `stage_states_json="{}"`. Confirmed verbatim, still present.
- `tools/sdlc_stage_query.py:576-616` — `_resolve_issue_record` calls
  `PipelineLedger.get_or_create(target_repo, issue_number)` on every read
  (line 609). Confirmed.
- `agent/pipeline_state.py:973` — `derive_from_durable_signals` exists and
  reconstructs stage state from plan doc + open/merged PR + review comments.
  Confirmed. It is currently only consulted by `/do-merge` as a cold-Redis
  fallback (docstring lines 976-980).
- `agent/pipeline_state.py:419` — `_refresh_ledger` already uses
  `type(self._ledger).load(ledger_key=...)`, proving a direct-key GET pattern
  that survives the class-set-empty window. Confirmed.
- `tools/sdlc_stage_query.py:65-66` — `_CLASS_SET_RETRY_ATTEMPTS = 5`,
  `_CLASS_SET_RETRY_BACKOFF_S = 0.20`; the #1720 retry constants used by
  `_find_session_by_id`. Confirmed — the pattern to mirror.

**Cited sibling issues/PRs re-checked:**
- #1720 — the class-set-empty `rebuild_indexes` window; still the governing
  hazard, `_find_session_by_id` carries the canonical 5×200ms retry.
- #1629 — OPEN. Proposes a *new* canonical durable SDLC state artifact. This
  plan is orthogonal (see Architectural Impact).
- #2012 (PR #2015) — created `PipelineLedger`. The clobber is a latent defect
  introduced there.

**Commits on main since issue was filed (touching referenced files):** none —
`git log --since=2026-07-26T06:48:40Z -- agent/pipeline_ledger.py agent/pipeline_state.py tools/sdlc_stage_query.py` is empty.

**Active plans in `docs/plans/` overlapping this area:** none.

**Notes:** Root cause is fully proven in the issue's second comment (direct
reproduction of the wipe mechanism). This plan implements exactly that fix
direction; it does not re-litigate the root cause.

## Prior Art

- **PR #2015 (#2012)**: "SDLC issue-keyed stage ledger: survive driver->takeover
  handoffs" — introduced `PipelineLedger` and moved the ledger off the ephemeral
  session onto the `(target_repo, issue_number)` pair. It solved lifecycle-driven
  loss (session death) but introduced the non-atomic `get_or_create` that this
  plan fixes.
- **PR #2166 (#2144)**: "Self-heal SDLC run identity on resumed pipeline turns" —
  state-mutating writes self-heal a lost `run_id` from the environment. Adjacent:
  it recovers the *identity* on resume but does not prevent the ledger *contents*
  from being wiped; a wiped ledger still derails the router.
- **PR #2171 (#2158)**: "Ledger-aware headless completion guard" — consumes the
  ledger; unaffected by this fix beyond benefiting from a durable ledger.
- **PR #2036 (#2034)**: keyed merge-predicate tracked-issue resolution on
  `PipelineLedger.pr_number`. Confirms the ledger is already the durable
  cross-session source of truth these fixes lean on.
- **#1629 (open)**: proposes a *new* durable per-project SDLC artifact. Distinct
  scope — see Architectural Impact.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #2015 (#2012) | Moved the ledger off the session onto the issue to survive session death | Fixed lifecycle-driven loss but shipped a non-atomic `get_or_create` whose index-based existence check false-misses in the #1720 window and clobbers live state via `create()` |
| PR #2166 (#2144) | Self-healed a lost `run_id` on resumed turns | Recovers identity, not contents. A ledger wiped to `{}` still has valid identity re-established but no stage markers, so the router still restarts at PLAN |

**Root cause pattern:** the durability effort protected the ledger against the
*wrong* loss vector (session death) while leaving it exposed to a *write-time*
race (concurrent `get_or_create` inside a `rebuild_indexes` window) and a
*read-time* mutation (the read path creates records). Both are index-mechanism
bugs, not lifecycle bugs.

## Data Flow

1. **Entry point (write path):** A pipeline stage tool (`sdlc-tool
   stage-marker` / `verdict record` / `meta-set` / `dispatch record`) or
   `PipelineStateMachine.for_issue(target_repo, issue_number)` resolves the
   ledger via `PipelineLedger.get_or_create` (`agent/pipeline_ledger.py:85`).
2. **get_or_create:** `query.filter(ledger_key=key)` → class-set index read. If
   the index is mid-`rebuild_indexes` (empty), returns `[]` → false-miss →
   `cls.create(..., stage_states_json="{}")` overwrites the live record. **This
   is the wipe.**
3. **Entry point (read path):** the router polls `sdlc-tool stage-query` →
   `_resolve_issue_record` (`tools/sdlc_stage_query.py:576`) → also calls
   `get_or_create` (line 609). A never-seen issue gets an empty ledger written
   as a side-effect; a live issue queried during a rebuild window gets wiped.
4. **Router decision:** `tools/sdlc_next_skill.decide` → `_resolve_enriched` →
   empty `stages` dict → `agent/sdlc_router.decide_next_dispatch` returns
   `/do-plan` (restart at PLAN) even when a PR is open.
5. **Output:** duplicate PLAN dispatch / confused no-op on an issue that already
   has a plan, PR, and approved build.

The fix intervenes at (2) [make existence check index-independent + retry], (3)
[make the read pure], and (4) [reconstruct from durable signals before defaulting
to PLAN].

## Architectural Impact

- **New dependencies:** none. `load()`, `derive_from_durable_signals`, and the
  #1720 retry constants all already exist.
- **Interface changes:**
  - `PipelineLedger` gains a read-only `get(target_repo, issue_number) -> PipelineLedger | None`
    classmethod (non-mutating lookup). `get_or_create` keeps its signature but
    changes its existence check to a direct-key `load()` with bounded retry and
    a re-load-before-create guard.
  - `_resolve_issue_record` switches from `get_or_create` to the new read-only
    `get`.
- **Coupling:** unchanged. The recovery hook reuses the existing
  `derive_from_durable_signals`; no new artifact, no new writer.
- **Data ownership:** unchanged — the ledger remains the single issue-keyed
  store. This fix stops it being destroyed, it does not move it.
- **Reversibility:** high. All three changes are localized method-body edits plus
  one router branch; revert is a clean git revert with no data migration.
- **Relationship to #1629:** #1629 proposes adding a *new* canonical durable
  artifact. This plan (a) stops the *existing* ledger from being destroyed and
  (b) reconstructs from *already-durable* signals (plan doc, PR, review) on loss.
  Narrower and orthogonal: if #1629 later lands, it layers on top of a ledger
  that no longer self-destructs. This plan introduces no artifact that #1629
  would have to reconcile with.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1-2 (confirm recovery-hook placement, retry-constant sharing)
- Review rounds: 1

The code changes are small and localized; the care is in the concurrency
correctness (index-independent existence check, re-load-before-create) and in
not regressing the empty-ledger-is-valid contract that `for_issue()` depends on.

## Prerequisites

No prerequisites — this work has no external dependencies. It edits existing
modules and reuses existing helpers; Redis/Popoto are already in the test
environment.

## Solution

### Key Elements

- **Index-independent existence check**: `get_or_create` uses a direct-key
  `PipelineLedger.load(ledger_key=key)` (an `HGETALL` on the specific key,
  independent of the class-set index) instead of `query.filter`, so the #1720
  empty-index window cannot cause a false-miss.
- **Bounded retry + re-load-before-create**: mirror the `_find_session_by_id`
  retry (5 attempts × 200ms) around the `load()`; on a genuine miss, re-load one
  final time immediately before `create()` so a racing concurrent create can
  never be clobbered.
- **Non-mutating read path**: a new read-only `PipelineLedger.get(...)` returns
  `None` when the record is absent; `_resolve_issue_record` uses it so stage
  queries never write.
- **Router recovery via existing signals**: when the router resolves an empty
  ledger for an issue that has observable durable state, reconstruct stage state
  via the existing `derive_from_durable_signals` before concluding the pipeline
  should restart at PLAN.

### Flow

Stage tool / router poll → resolve ledger → **direct-key `load()` (retry)** →
found: return live record (no wipe) / genuinely absent: **re-load, then create
empty** → write path proceeds.

Router poll → **read-only `get()`** → empty/None → **reconstruct via
`derive_from_durable_signals`** (plan + PR + review) → route to the correct
resume stage instead of PLAN.

### Technical Approach

- **`get_or_create` (`agent/pipeline_ledger.py:85`)**: replace
  `cls.query.filter(ledger_key=key)` with a bounded-retry loop over
  `cls.load(ledger_key=key)` (returns the record or `None`). On a hit, return
  it. On a cap-exhausted miss, `load()` once more, then `create()`. Define the
  retry constants once and share them with `sdlc_stage_query` (see Rabbit Holes)
  — do not duplicate the numbers. `load()` is confirmed index-independent:
  popoto `Model.load` resolves to `query.get(db_key=...)` (`popoto/models/base.py:1586`),
  and `_refresh_ledger` already relies on it (`agent/pipeline_state.py:419`).
- **`PipelineLedger.get(...)` (new, read-only)**: same direct-key
  `load()`-with-retry lookup, but returns `None` instead of creating. This is
  the reader-side primitive.
- **`_resolve_issue_record` (`tools/sdlc_stage_query.py:576`)**: call
  `PipelineLedger.get(...)` instead of `get_or_create(...)`. The existing
  "ledger empty → fall back to `find_session_by_issue`" branch is preserved;
  a `None` return is treated exactly like an empty ledger (falls through to the
  session fallback, then to `{}`). No stage query ever writes.
- **Router recovery (`tools/sdlc_next_skill.decide` / `agent/sdlc_router`)**:
  before `decide_next_dispatch` concludes PLAN on an empty `stages` dict,
  attempt reconstruction. `derive_from_durable_signals(session)` needs a
  `session` with a `.slug`; resolve it via the existing `find_session_by_issue`
  (or the ledger's own context). If reconstruction yields non-empty completed
  stages, feed them into the dispatch decision so the router resumes at the
  correct stage. Reconstruction is best-effort and read-only; on any failure the
  behavior is exactly today's (default to PLAN) — no new failure mode.
- **No Popoto schema change**: no new field is added (only methods and a router
  branch), so **no migration is required** per `docs/sdlc/do-plan.md`'s Popoto
  Schema Migration Requirement.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_resolve_issue_record` wraps ledger load in `except Exception` (line 610-611,
  logs at debug). The new `get()` path preserves this — add a test asserting a
  raised load error returns `None`/falls back rather than propagating.
- [ ] The router recovery hook must swallow `derive_from_durable_signals` failures
  (it already returns `{}` on subprocess error) and fall back to PLAN. Add a test
  asserting a failed reconstruction leaves today's behavior intact (no crash,
  routes to PLAN).

### Empty/Invalid Input Handling
- [ ] `get_or_create` / `get` with a genuinely absent key: `get_or_create`
  creates empty-but-valid; `get` returns `None`. Test both.
- [ ] Empty `stages` dict into the router with NO durable signals (no plan, no
  PR): recovery yields nothing → routes to PLAN (correct restart). Test this so
  recovery does not mask legitimately-fresh issues.

### Error State Rendering
- [ ] Not user-visible (internal ledger + router). The observable "error" is a
  wrong route; covered by the router recovery tests above.

## Test Impact
- [ ] `tests/unit/test_pipeline_ledger.py` — UPDATE: add cases for (a)
  `get_or_create` returning the live record when the class-set index is empty
  (simulate the #1720 window), (b) `create()`-clobber is no longer reachable,
  (c) new read-only `get()` returns `None` for absent, existing for present.
- [ ] `tests/unit/test_sdlc_stage_query.py` — UPDATE: assert `_resolve_issue_record`
  no longer creates a ledger as a side-effect (query a never-seen issue → no
  record persisted afterward).
- [ ] `tests/unit/test_pipeline_state_machine.py` / `test_pipeline_state.py` —
  UPDATE if needed: confirm `for_issue()` still constructs an empty-but-valid
  machine for a fresh issue (the empty-ledger-is-valid contract must not regress).
- [ ] Router tests (`agent/sdlc_router` coverage / `tools/sdlc_next_skill`) —
  UPDATE/ADD: empty ledger + open-PR durable signals → resumes at the correct
  stage, not PLAN; empty ledger + no signals → PLAN.

No existing xfail markers relate to this bug (searched `tests/` for
ledger/clobber/get_or_create/1720/durable — none found).

## Rabbit Holes

- **Sharing the retry constants across modules.** `_CLASS_SET_RETRY_ATTEMPTS` /
  `_CLASS_SET_RETRY_BACKOFF_S` live in `tools/sdlc_stage_query.py`;
  `get_or_create` lives in `agent/`. Extract them to a single shared location
  (or define once and import) rather than hardcoding new magic numbers — but do
  NOT embark on a broad "unify all #1720 retries" refactor. Scope: these two
  call sites only.
- **Rewriting `get_or_create` into a distributed lock / SETNX.** The direct-key
  `load()` + re-load-before-create is sufficient for the observed failure; a
  full atomic upsert or lock is over-engineering for this record's write pattern.
- **Reworking `derive_from_durable_signals` into a primary signal source.** It
  stays a fallback. This plan only adds one more consumer (the router recovery
  branch); do not promote it to the primary path or change its signal set.
- **Chasing #1629's new-artifact design.** Explicitly out of scope (see No-Gos).
- **Purging pre-existing empty ledgers already littered by the mutating read
  path.** Tempting cleanup, but a separate concern; the fix stops new ones being
  created and reconstruction handles the existing ones at read time.

## Risks

### Risk 1: `for_issue()`'s empty-ledger-is-valid contract regresses
**Impact:** `for_issue()` deliberately creates an empty ledger for a
never-written issue (predecessor backfill on first write). If the read-path
change or a mis-scoped `get_or_create` edit breaks this, first writes fail.
**Mitigation:** keep `get_or_create` (writer primitive) creating-on-genuine-absence;
only the *reader* (`_resolve_issue_record`) switches to non-mutating `get()`.
Explicit test that `for_issue()` on a fresh issue still yields an empty machine.

### Risk 2: Retry latency added to every ledger resolution
**Impact:** the bounded retry only sleeps on a miss (a fresh/absent record), and
the hot path (present record) returns on the first `load()`. But a genuinely
new issue now pays up to 5×200ms before create.
**Mitigation:** mirror the existing `_find_session_by_id` budget (already deemed
acceptable for the router's polling). The sleep is miss-only; steady-state
pipelines (populated ledger) see zero added latency.

### Risk 3: Router recovery resumes at the wrong stage
**Impact:** if `derive_from_durable_signals` over-reports completion, the router
skips a stage that legitimately needs to run.
**Mitigation:** reconstruction only *supplements* an empty ledger (it never
overrides recorded markers), and `derive_from_durable_signals` already returns
`pending` on any signal ambiguity/failure. Test the empty-signal case routes to
PLAN so recovery cannot mask a genuinely fresh issue.

## Race Conditions

### Race 1: concurrent `get_or_create` inside a `rebuild_indexes` window (the bug)
**Location:** `agent/pipeline_ledger.py:106-115`
**Trigger:** a live, populated ledger + any concurrent `for_issue()`/
`get_or_create`/read-path call landing while `rebuild_indexes()` has transiently
emptied the class set. The index read returns `[]`, `create()` overwrites the
record to `"{}"`. Worse under "several subagents active" (more index churn).
**Data prerequisite:** a populated `stage_states_json` must exist and remain the
source of truth throughout the window.
**State prerequisite:** the class-set index may be empty at any instant during a
concurrent `rebuild_indexes`; correctness must not depend on it being populated.
**Mitigation:** direct-key `load()` (index-independent `HGETALL`) for the
existence check, bounded retry across the window, and a final re-load
immediately before `create()` so a value that appears mid-window (written by a
racing create) is never clobbered.

### Race 2: read-path create racing a writer
**Location:** `tools/sdlc_stage_query.py:609` (today)
**Trigger:** the router's `stage-query` poll and a stage-writing tool run
concurrently; the read path's `get_or_create` creates/overwrites while the
writer holds a populated instance.
**Mitigation:** making the read path non-mutating (`get()` → `None`) removes the
reader as a writer entirely, eliminating this race by construction.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1629] A new canonical durable per-project SDLC state artifact.
  This plan makes the *existing* ledger durable and reconstructs from
  *already-durable* signals; the new-artifact design is tracked separately in
  #1629 and is orthogonal.
- [ORDERED] Backfilling / purging the empty ledgers already written by the old
  mutating read path across all historical issues. The fix stops new ones and
  reconstruction handles them at read time; a bulk sweep waits until the fix is
  merged and observed, and is not required for correctness.

## Update System

No update system changes required — this is a purely internal fix to existing
modules (`agent/pipeline_ledger.py`, `agent/pipeline_state.py`,
`tools/sdlc_stage_query.py`, `tools/sdlc_next_skill.py` / `agent/sdlc_router.py`).
No new dependencies, config files, or `/update`-propagated artifacts. No Popoto
schema change, so no migration in `scripts/update/migrations.py`.

## Agent Integration

No agent integration required — this is an SDLC-internal change. The affected
surfaces (`sdlc-tool stage-query`, the `/do-sdlc` router) already exist and are
already wired. No new CLI entry point, MCP tool, or bridge import is introduced;
the fix changes the behavior of existing entry points only.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/sdlc-issue-keyed-stage-ledger.md` to document the
  index-independent existence check, the non-mutating read path, and the
  router's durable-signal recovery on empty ledger.
- [ ] Cross-reference the #1720 retry rationale so the shared retry constants are
  discoverable from the ledger doc.

### Inline Documentation
- [ ] Docstring on `get_or_create` explaining why the existence check is a
  direct-key `load()` (not `query.filter`) and citing #1720 + #2395.
- [ ] Docstring on the new read-only `get()` stating it never mutates.
- [ ] Comment on the router recovery branch citing `derive_from_durable_signals`
  and #2395 concern #2.

## Success Criteria

- [ ] `get_or_create` returns the live, populated record when the class-set index
  is empty (simulated #1720 window) — no wipe to `{}`.
- [ ] `create()`-on-existing-key clobber is unreachable from the resolution path
  (existence check is direct-key + re-load-before-create).
- [ ] `_resolve_issue_record` / `stage-query` never persist a ledger as a
  side-effect (a never-seen issue leaves no record).
- [ ] Router reconstructs stage state via `derive_from_durable_signals` for an
  empty ledger with an open PR + committed plan, resuming at the correct stage
  instead of PLAN.
- [ ] Router still routes to PLAN for an empty ledger with no durable signals.
- [ ] `for_issue()` on a fresh issue still yields an empty-but-valid machine.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (ledger-durability)**
  - Name: ledger-builder
  - Role: fix `get_or_create` (direct-key load + retry + re-load-before-create),
    add read-only `get()`, make `_resolve_issue_record` non-mutating, share retry
    constants
  - Agent Type: builder
  - Domain: async/concurrency + Redis/Popoto
  - Resume: true

- **Builder (router-recovery)**
  - Name: router-builder
  - Role: wire `derive_from_durable_signals` reconstruction into the router's
    empty-ledger path so it resumes at the correct stage instead of PLAN
  - Agent Type: builder
  - Domain: async/concurrency
  - Resume: true

- **Validator (ledger + router)**
  - Name: ledger-validator
  - Role: verify no-wipe under simulated index window, non-mutating reads,
    correct recovery routing, and the empty-signal→PLAN case
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Harden ledger resolution
- **Task ID**: build-ledger-resolution
- **Depends On**: none
- **Validates**: tests/unit/test_pipeline_ledger.py, tests/unit/test_pipeline_state_machine.py, tests/unit/test_pipeline_state.py
- **Assigned To**: ledger-builder
- **Agent Type**: builder
- **Domain**: async/concurrency + Redis/Popoto (paste matching DOMAIN_FRAMING rules)
- **Parallel**: true
- Replace `get_or_create`'s `query.filter` existence check with a bounded-retry
  `cls.load(ledger_key=key)` loop; on cap-exhausted miss, re-load once, then `create()`.
- Add read-only `PipelineLedger.get(target_repo, issue_number) -> PipelineLedger | None`.
- Extract/share the #1720 retry constants (do not duplicate the numbers).
- Preserve the empty-ledger-is-valid contract for `for_issue()`.

### 2. Make the read path non-mutating
- **Task ID**: build-read-path
- **Depends On**: build-ledger-resolution
- **Validates**: tests/unit/test_sdlc_stage_query.py
- **Assigned To**: ledger-builder
- **Agent Type**: builder
- **Parallel**: false
- Switch `_resolve_issue_record` from `get_or_create` to the new read-only `get()`.
- Preserve the `None`/empty → `find_session_by_issue` fallback branch unchanged.

### 3. Router recovery from durable signals
- **Task ID**: build-router-recovery
- **Depends On**: none
- **Validates**: router/next-skill unit tests (add cases)
- **Assigned To**: router-builder
- **Agent Type**: builder
- **Domain**: async/concurrency (paste matching DOMAIN_FRAMING rules)
- **Parallel**: true
- In the router's empty-`stages` path, attempt reconstruction via
  `derive_from_durable_signals` (resolve the required `.slug` via
  `find_session_by_issue`) before defaulting to PLAN.
- Best-effort and read-only: any failure leaves today's PLAN default intact.

### 4. Validation
- **Task ID**: validate-all
- **Depends On**: build-read-path, build-router-recovery
- **Assigned To**: ledger-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all touched unit tests; verify no-wipe under simulated index window,
  non-mutating reads, correct recovery routing, empty-signal→PLAN, and
  `for_issue()` fresh-issue contract.
- Report pass/fail with evidence.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Ledger tests pass | `pytest tests/unit/test_pipeline_ledger.py -q` | exit code 0 |
| Stage-query tests pass | `pytest tests/unit/test_sdlc_stage_query.py -q` | exit code 0 |
| State-machine tests pass | `pytest tests/unit/test_pipeline_state_machine.py tests/unit/test_pipeline_state.py -q` | exit code 0 |
| Read path no longer creates | `grep -n "get_or_create" tools/sdlc_stage_query.py` | match count == 0 |
| Existence check is direct-key | `grep -c "query.filter(ledger_key=" agent/pipeline_ledger.py` | match count == 0 |
| Lint clean | `python -m ruff check agent/pipeline_ledger.py agent/pipeline_state.py tools/sdlc_stage_query.py tools/sdlc_next_skill.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/pipeline_ledger.py tools/sdlc_stage_query.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Recovery hook placement.** Should the durable-signal reconstruction live in
   `tools/sdlc_next_skill.decide` (before it calls `decide_next_dispatch`) or
   inside `agent/sdlc_router.decide_next_dispatch` itself? The former keeps the
   pure guard table (`decide_next_dispatch`) untouched (consistent with the
   #1954 note at `sdlc_next_skill.py:429-435`); the latter centralizes routing
   logic. Leaning toward the former — confirm.
2. **Reconstruction scope.** Should recovery only supplement a fully-empty ledger
   (`stages == {}`), or also a partially-empty one (some markers present)? Plan
   assumes fully-empty only, to avoid overriding legitimately-recorded partial
   state. Confirm that bound.
