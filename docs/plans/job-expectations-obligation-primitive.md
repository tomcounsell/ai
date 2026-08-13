---
status: Planning
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-08-13
tracking: https://github.com/tomcounsell/ai/issues/2708
last_comment_id: 5236176220
revision_applied: false
---

# Expectations as the Single Obligation Primitive on Job, with a Reconciler for Orphaned Lanes

## Problem

Nothing records what a PM is waiting on, so nothing can tell that a PM stopped waiting.
When a PM session crashes or ends, the lanes it spawned are accounted for by nothing in the
session model. The founding incident of #2494: a backgrounded dev subagent was SIGKILLed
mid-turn, exited 0 with status `completed`, its worktree deleted one second later — all work
gone, and "nothing detected this. Nothing could have." Conversely, three shipped lanes
(#2688, #2676, #2681) sat idle 22-40 hours with zero surviving session rows; the stall
detector recovered them only because git/GitHub live outside the session model.

**Current behavior:** the promise model on `Job.goal` records only the inbound direction
(what we owe a requester). The outbound direction (what a PM expects a lane to deliver) has
no home. `AgentSession.expectations`, the prior art in this shape, was already dead in the
field (0 of 57 sessions carried a value) and has since been removed entirely (#2494 M3,
PR #2631).

**Desired outcome:**
- A PM records what it is waiting on at the moment it knows — one primitive, both directions.
- A reconciler compares recorded expectations against live sessions, cross-checks git/GitHub,
  and hands orphaned work back to a PM without resurrecting shipped work.
- "Is this Job finished?" is answerable from state: a Job with an open expectation cannot be
  at rest.
- Shipping this closes out #2494's promise-model acceptance criteria by superseding them
  (owner directive 2026-08-13), so #2494 can close on its remaining orthogonal cutover work.

## Freshness Check

**Baseline commit:** `34ab8da2f`
**Issue filed at:** 2026-08-10T05:14:12Z
**Disposition:** Minor drift (drift is in the plan's favor; premises hold)

**File:line references re-verified:**
- `models/job.py:89` (`status` IndexedField), `:103` (`has_open_promises` IndexedField,
  `type=bool`), `:165-175` (`_write_goal_data` chokepoint), `:223-231` (append-only
  discharge via `removed_ts`), `:313-339` (`at_rest_with_open_promises` index
  intersection) — all hold as claimed.
- `models/agent_session.py:393` — `parent_agent_session_id = KeyField(null=True)` holds;
  graph walkable (`children()` at `:2005`), no orphan reconciler walks it — gap confirmed.
- `agent/output_handler.py:1294-1297` — records the retirement of
  `AgentSession.expectations`. **Drift:** the field is already gone from the model; there
  is nothing to remove. `MessageDraft.expectations` (`bridge/message_drafter.py:237`,
  consumed by `bridge/redundancy_filter.py:99-197`) survives as a *different* drafter-local
  concept (verbatim extracted open questions) — a naming collision this plan resolves.
- `bridge/promise_gate.py:243/:253/:279` (pattern families), `:407-411` (advisory framing,
  do-not-tighten ruling), `:423` (`build_promise_advisory`) — all hold.

**Cited sibling issues/PRs re-checked:**
- #2696 — **CLOSED** 2026-08-10 via PR #2710: `reflections/sdlc_progress.py` shipped a
  steer/resume/create ladder with per-(slug,sha) cooldowns, attempt caps, escalate-once.
  The reconciler composes with this; it does not rebuild steering.
- #2704 — OPEN (observable ownership); no code landed. Hazard 5 alignment only.
- #2705 — CLOSED (investigation). #2747 landed: maintenance writes no longer forge
  `updated_at`. Hazard 6 stance unchanged: a session row is not evidence of life.
- #2494 — OPEN. Remaining work is orthogonal to the promise model (steering writer flip,
  inbox phase-2/3 cutover behind #2636, catchup re-enable with #2204). This plan neither
  depends on nor blocks those phases.
- #2489 — CLOSED (deferred_self_draft_pending release path fixed); cited here only as the
  leak-shape cautionary tale.

**Commits on main since issue was filed (touching referenced files):**
- `971ff1caf` (#2747) — liveness-forging fix; sharpens the reconciler's liveness posture,
  no premise change.
- `c8b2136b1` (#2671) — Job registered in guarded index-repair sweep; the rename task must
  keep `repair_indexes` / `backfill_open_promises_index` coherent.
- `45d0961f9` (#2728) — docs-auditor; irrelevant.

**Active plans in `docs/plans/` overlapping this area:**
`session-liveness-tick-counter.md` (#2716, Ready) and
`sdlc-lease-heartbeat-supervisor-lifetime.md` (#2714, Ready) occupy the adjacent
liveness/ownership space. File overlap expected disjoint (`models/job.py`,
`tools/job_tool.py`, `reflections/` vs. their worker/lease surfaces); coordinate at build
time. `docs/plans/durability-room-job-agentrun.md` (#2494) is amended **by this plan** —
see "Reconciling the durability plan" below; the two plans must not disagree about Job's
schema.

## Prior Art

- **PR #2631** (Durability M3): shipped the Job model, advisory promises, and retired
  `AgentSession.expectations`. This plan generalizes its promise machinery.
- **PR #2646 / #2653 / #2671**: `has_open_promises` index intersection, write-scoped
  backfill, guarded index repair — the mechanics the renamed field inherits verbatim.
- **PR #2710** (#2696): the shipped stall auto-resume ladder — the reconciler's template
  and its steer primitive.
- **`AgentSession.expectations` (removed)**: the failed predecessor. Write-only, no release
  path, zero field writes in 57 live sessions. Documented under "Why Previous Fixes Failed."

## Research

No relevant external findings — this is purely internal (Popoto models, repo-local
reflections and gating); proceeding with codebase context.

## Data Flow

1. **Entry (outbound):** a PM spawns a lane (`valor-session create` /
   `tools/valor_session.py` core, or a dev subagent with a slug). The spawn chokepoint
   records an outbound expectation on the PM's bound Job:
   `{direction: "outbound", holder: <pm session id>, owner: <lane session id or slug>,
   what: <spawn instruction summary>}` — via the same `_write_goal_data` chokepoint.
2. **Entry (inbound):** the promise gate (`bridge/promise_gate.py`) stays advisory. When an
   outbound message reads like an undischarged obligation, the advisory tells the PM to
   revise or deliberately record an inbound expectation via
   `python -m tools.job_tool expectation-add --direction inbound`.
3. **Storage:** `Job.goal` JSON — `promises` list becomes `expectations` list (entries gain
   `direction`, `holder`, `owner`; discharge still appends `removed_ts`, never deletes).
   `_write_goal_data` derives `has_open_expectations` (renamed IndexedField) and forces
   `status="active"` whenever an open expectation exists.
4. **Rest derivation:** `sweep_to_rest` transitions only Jobs with **zero** open
   expectations past the age threshold. A Job with an open expectation cannot go to rest;
   an idle Job with no recorded expectations still rests by age (under-recording reads as
   *unknown surfaced by time*, not *done* — hazard 1).
5. **Reconciler:** `reflections/expectation_reconciler.py` on the reflections cadence:
   enumerate open **outbound** expectations (index-bounded), resolve each `owner` against
   `AgentSession` rows; for a gone owner, cross-check git/GitHub
   (`git ls-remote`/local branch `session/{slug}`, `gh pr list --head session/{slug}`);
   then the ladder — steer a live PM with evidence; else respawn a lane with the recorded
   `what` and steer/create a PM to re-own it; escalate once on cap. Shipped work is never
   respawned — evidence goes to the PM for deliberate discharge.
6. **Discharge:** always owner-authored (`expectation-remove`), mirroring promise discharge.
   Discharged expectations stop being surfaced everywhere (open-set queries filter
   `removed_ts is None`).

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| `AgentSession.expectations` | Drafter-derived free-text field on the session row | Write-only (no reader), no release path (written only when non-None, producer returned None never ""), and voluntary — 0 of 57 live sessions carried a value. Died with its session rows. |
| Promise model (#2494 M3) | PM-authored promises on `Job.goal`, advisory gate, at-rest backstop | Sound, but models only the inbound direction; cannot express "PM waits on lane Y" (a PM does not promise itself). |
| Stall detector (#2696/PR #2710) | Recovers idle SDLC lanes from git/GitHub state | Only sees work that reached a pushed branch/PR; the founding incident (nothing committed) is invisible to it. |

**Root cause pattern:** obligations recorded on mortal rows (or not at all) die with their
holder. The fix is recording them on the immortal Job, with an enforcement point at the
moment of creation and an owner-authored release path.

## Architectural Impact

- **New dependencies:** none (Popoto, existing reflections runner, `gh`).
- **Interface changes:** `Job.add_promise/remove_promise/open_promises/all_promises` are
  **replaced** by `add_expectation/discharge_expectation/open_expectations/all_expectations`
  (no legacy aliases — NO LEGACY CODE). `has_open_promises` → `has_open_expectations`.
  `at_rest_with_open_promises` → `open_expectations_with_gone_owner` (reconciler query) +
  `at_rest_with_open_expectations` retained as the operator backstop during the transition
  of meaning — see Solution. `MessageDraft.expectations` → `MessageDraft.open_questions`.
- **Coupling:** the reconciler couples reflections ↔ Job ↔ AgentSession graph ↔ git/GitHub
  read-only checks; writes go only through the ORM and steering.
- **Data ownership:** Job owns all obligations; AgentSession owns none (status quo after
  #2631, now made explicit).
- **Reversibility:** goal JSON is append-only and the migration preserves entries
  (promises are rewritten in place as inbound expectations, `removed_ts` history intact);
  reverting is a re-mapping, not data recovery.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM check-ins at plan finalization and pre-merge.

**Interactions:**
- PM check-ins: 1-2 (schema reconciliation ruling, spawn-chokepoint interpretation)
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies (Redis, Popoto, `gh`, and the
reflections runner are already required by the repo).

## Solution

### Key Elements

- **One primitive:** `(holder, owner, what, direction)` entries in `Job.goal` JSON. Inbound
  subsumes promises; outbound is the new lane-tracking direction. No separate promise model
  survives.
- **Chokepoint-derived state:** `_write_goal_data` derives `has_open_expectations` AND
  maintains the `status` projection (open expectation ⇒ `active`). The two fields cannot
  disagree because a single write site computes both (issue OQ4: explicit status maintained
  at the chokepoint, not a pure read-time projection — keeps every existing
  `filter(status=...)` reader and the SortedField sweep working).
- **Rest derivation:** `sweep_to_rest` gains one clause: skip Jobs with open expectations.
  Age still gates rest for expectation-less Jobs (hazard 1 mitigation: under-recording
  degrades to today's behavior, never to a false "done").
- **Spawn enforcement point:** the session-create core records the outbound expectation
  mechanically when the creating PM's Job is resolvable, and the PM prime mandates
  supplying it. This is bookkeeping of a deliberate PM act (the spawn), not a
  classifier-inferred write; the 2026-07-31 advisory-only ruling governs message
  classification, which stays advisory (see Open Questions #1 for the owner to confirm
  this interpretation).
- **Advisory rewrite (inbound):** `build_promise_advisory` and the PM prime speak
  expectations; the issue-comment grammar (bare reassurance leaks inbound; third-party
  future leaks outbound) is documented as what the advisory should recognize — **no new
  regexes** (two false-positive incidents and ~0.44 blocks/day make mechanical broadening
  unfalsifiable).
- **Reconciler:** `reflections/expectation_reconciler.py`, modeled on
  `reflections/sdlc_progress.py`: index-bounded scan, owner-liveness check that treats a
  session row as a claim not proof (#2705), git/GitHub shipped-work guard, steer-first
  ladder, cooldown + attempt cap + escalate-once, every failure logged-and-continue.

### Reconciling the durability plan (#2494) — owner directive 2026-08-13

- **Schema:** amend `docs/plans/durability-room-job-agentrun.md` in place with **Schema
  Gate Amendment 2**: `has_open_promises` is renamed `has_open_expectations`; still a
  two-valued derived `IndexedField(type=bool)`; the governing cardinality rule is
  unchanged. The at-rest-with-open-promise intersection ruling is superseded: `status` is
  now chokepoint-maintained and cannot coexist at-rest with an open expectation, so the
  backstop query's steady-state answer is structurally empty; the operator backstop
  becomes the reconciler's orphaned-owner surface.
- **Superseded #2494 acceptance criteria (named per directive):** the promise-model ACs —
  "the promise gate is advisory... PM-authored promises append/remove as `Job.goal`
  versions; the at-rest backstop surfaces open promises to the operator surface" (plan line
  ~396) and the at-rest-backstop operator-surface criterion (line ~258) — are marked
  superseded-by-#2708 in that plan, satisfied in their new expectation form by this work.
  Closing #2494 then requires only its orthogonal remaining phases: steering writer flip,
  inbox phase-2/3 (gated on #2636), catchup re-enable (#2204). This plan touches none of
  them and blocks none of them.

### Flow

PM spawns lane → outbound expectation recorded on Job → lane dies silently → reconciler
tick → owner gone → git/GitHub check → (shipped? steer PM with PR link for discharge) /
(unshipped? respawn lane with recorded `what`, steer PM to re-own) → PM discharges →
`_write_goal_data` clears flag → Job rests by age when idle.

### Technical Approach

- Goal JSON schema v2: `{"versions": [...], "expectations": [{"id", "ts", "direction",
  "holder", "owner", "what", "removed_ts"}]}`. Migration rewrites `promises` entries as
  `direction:"inbound", holder:"requester", owner:"pm"` preserving ids/timestamps.
- `_goal_data()` setdefaults `expectations`; a lingering `promises` key is migrated on
  read (self-healing) and the offline migration sweeps the population once.
- Rename `backfill_open_promises_index` → `backfill_open_expectations_index`; keep the
  `update_fields` write-scoping and no-timestamp convergence invariants verbatim
  (PR #2653's guarantees).
- `repair_indexes` leg 2 continues to clear `$IndexF:Job:*` wholesale — the rename needs
  no special index surgery beyond the registered migration clearing the old
  `has_open_promises` index sets via the guarded path.
- Reconciler liveness: owner is *gone* when no `AgentSession` row resolves for the
  recorded owner id/slug, or its status is terminal. A row claiming `running` is
  respawn-blocking only (never discharge evidence); the tie-breaker for stale-running rows
  stays with the #2716 liveness work — this plan does not add heartbeats (hazard 6).
- No lock semantics anywhere: expectations are readable ownership records; a second PM
  reads and decides (#2704 alignment, hazard 5).
- Popoto bool trap: `type=bool` on the renamed field is load-bearing; regression test
  asserts hydration of `False` is falsy.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Every `except Exception` in the reconciler (scan, per-expectation, gh/git subprocess,
      steer/create rungs) has a test asserting a `logger.warning` and loop continuation —
      mirror `tests/unit/reflections/test_sdlc_progress_check.py` patterns.
- [ ] `_write_goal_data` derivation failure cannot lose the goal write (derive before save;
      test with malformed entries).
- [ ] Migration per-row failure costs only that row (logged), never aborts the sweep.

### Empty/Invalid Input Handling
- [ ] Empty/None/malformed `goal` JSON: `_goal_data()` self-heals, `open_expectations()`
      returns `[]`, flag derives `False`.
- [ ] Expectation with empty `owner` or `what`: rejected at `add_expectation` with a clear
      error (an unownable expectation is unreconcilable — worse than none).
- [ ] Reconciler with zero open expectations: no-op tick, no subprocess spawned.

### Error State Rendering
- [ ] Reconciler escalation goes to the operator surface once per (job, expectation) with
      cooldown; test asserts no repeat-page storm and no raw errors reaching human chat.
- [ ] `job_tool` subcommands print actionable errors (job not in your Room, unknown
      expectation id) — extend `tests/unit/test_job_tool.py`.

## Test Impact

- [ ] `tests/unit/test_job_model.py` — UPDATE: promise API calls become expectation API;
      add direction/holder/owner assertions, status-projection assertions, rest-skip
      assertion, bool-trap regression for `has_open_expectations`.
- [ ] `tests/unit/test_job_tool.py` — UPDATE: `promise-add`/`promise-remove` →
      `expectation-add`/`expectation-remove` (+ `--direction`, `--owner`); ownership-error
      paths unchanged.
- [ ] `tests/unit/test_promise_advisory.py` — UPDATE: advisory copy speaks expectations;
      zero-writes assertion stays.
- [ ] `tests/unit/test_promise_gate.py` / `test_promise_gate_audit.py` /
      `test_promise_gate_session_events.py` — UPDATE: verdict grammar unchanged (no new
      regexes), only advisory-text and Job-API touchpoints.
- [ ] `tests/unit/test_redundancy_filter.py`, `tests/unit/test_message_drafter.py`,
      `tests/unit/test_drafter_validators.py` — UPDATE: `expectations` param/field renamed
      `open_questions`; behavior identical.
- [ ] `agent/session_health.py` at-rest backstop tests (in its test module) — REPLACE:
      `_check_jobs_at_rest_with_open_promises` becomes the expectation-form backstop; the
      sweep-then-scan ordering assertion stays.
- [ ] `tests/unit/test_relay_job_record.py`, `tests/unit/test_job_router.py` — UPDATE only
      if they touch promise fields (audit at build; expected minimal).

## Rabbit Holes

- **Rewriting `session_health.py`** (5,700+ lines): change only the backstop function and
  its invocation block — the durability plan's own warning stands.
- **Heartbeats / fencing / liveness inference:** explicitly out — #2716 owns liveness
  signals. The reconciler keys on *absence* (no row), not on freshness arithmetic.
- **Widening promise-gate regexes** to catch bare reassurance or third-party futures: the
  #2688 adverb false-positive and the 0.44/day signal volume make this unfalsifiable.
  Advisory prose only.
- **Mechanical discharge on PR merge:** tempting for outbound; killed by the same evidence
  that killed auto-discharge for promises (spike-4). Reconciler surfaces evidence; PM
  discharges.
- **Room-level expectation storage:** Room is the scan root only via its Jobs; storing
  there loses which responsibility an expectation serves (30 Jobs/Room observed).
- **Backfilling expectations for pre-existing lanes:** the reconciler only sees
  expectations recorded after ship; do not synthesize history.

## Risks

### Risk 1: Under-recording makes "at rest" lie (issue hazard 1)
**Impact:** an unrecorded lane's Job rests by age and reads as done; the reconciler never
looks.
**Mitigation:** rest stays age-gated (never instant-on-empty); spawn-time recording is
mechanical at the session-create chokepoint, not discretionary; a drift advisory (PM has
live children but its Job has no open outbound expectation) surfaces on the health cadence
to the operator surface. Test: a Job with no expectations is never asserted complete by any
new code path.

### Risk 2: Respawn loop on a permanently-failing lane
**Impact:** reconciler recreates a lane that dies the same way, forever.
**Mitigation:** per-(job, expectation) attempt cap + cooldown + escalate-once, copied from
`reflections/sdlc_progress.py` (already field-proven); attempts keyed in Redis with TTL
longer than the cadence.

### Risk 3: Rename breaks the guarded index-repair path mid-flight
**Impact:** daily maintenance restamps a field that no longer exists, or leaves stale
`$IndexF:Job:has_open_promises` sets flooding Redis (#2207 shape).
**Mitigation:** single-PR cutover (model + repair + backfill + migration together); the
registered migration clears old index sets through the guarded repair path; grep sweep
verification row asserts zero `has_open_promises` references post-build.

### Risk 4: The spawn chokepoint misses spawn paths
**Impact:** lanes created via a path that skips the chokepoint carry no expectation —
silent regression to today.
**Mitigation:** record in the session-create *core* (`tools/valor_session.py` create path)
that all CLI/worker creation flows through, not in a wrapper; build task includes a sweep
for AgentSession-creation sites with eng type + parent, and the drift advisory (Risk 1)
catches leaks in the field.

## Race Conditions

### Race 1: Concurrent expectation write vs. maintenance backfill
**Location:** `models/job.py` `backfill_open_expectations_index` vs. `add_expectation`.
**Trigger:** backfill re-derives between a PM's read-modify-write of goal JSON.
**Data prerequisite:** goal JSON present before flag derivation.
**State prerequisite:** flag must converge to the goal's truth.
**Mitigation:** inherited unchanged from PR #2653 — `update_fields=["has_open_expectations"]`
EVAL-only write, per-row re-fetch, no timestamps; consumers re-verify against
`open_expectations()` so a stale flag costs a hydration, never a wrong answer.

### Race 2: Reconciler respawns while the lane is being recreated by another actor
**Trigger:** `reflections/sdlc_progress.py` (PR-based) and the expectation reconciler both
act on the same slug in one window.
**Mitigation:** shared per-slug action cooldown namespace (reuse `sdlc_progress`'s cooldown
key shape); the reconciler checks for an open PR / fresh branch first (shipped-work guard
doubles as the collision guard); both rungs are steer-first, and a double-steer is benign
(idempotent instruction, PM reads both).

### Race 3: Discharge vs. reconciler tick
**Trigger:** PM discharges while a reconciler pass holds a stale open-expectation snapshot.
**Mitigation:** re-fetch the Job by KeyFields immediately before acting on any expectation;
act only if still open (`removed_ts is None`) — same re-fetch idiom as the backfill.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2716] Liveness signals for stale-`running` rows (tick counter /
  forced-progress ceiling) — the reconciler treats a present row as respawn-blocking and
  defers liveness judgment to that plan.
- [SEPARATE-SLUG #2704] Replacing SDLC issue-lock enforcement with observable ownership —
  this plan adds no lock semantics but does not remove existing SDLC leases.
- [SEPARATE-SLUG #2494] Steering writer flip, inbox phase-2/3 authoritative cutover
  (gated on #2636), and catchup re-enable (#2204) — named as orthogonal remaining #2494
  work; untouched here by owner directive.
- [DESTRUCTIVE] No deletion of historical promise entries anywhere: the migration rewrites
  in place preserving ids, timestamps, and `removed_ts` history. (Anti-criterion in
  Verification.)

## Update System

- One migration function in `scripts/update/migrations.py`, registered in `MIGRATIONS`:
  rewrite `promises` → `expectations` in every Job's goal JSON (ORM-only, idempotent,
  per-row fault-isolated), restamp `has_open_expectations`, and clear stale
  `$IndexF:Job:has_open_promises` sets via `Job.repair_indexes()`. Propagates to all
  machines through the existing `/update` flow — no new steps, deps, or config.
- Reflections registration for the reconciler follows the existing reflections convention
  (code-registered like `sdlc_progress`; no vault `reflections.yaml` edit required unless
  the build finds the cadence is config-driven — builder verifies against
  `reflections/utilities.py`).

## Agent Integration

- `python -m tools.job_tool` gains `expectation-add` (`--direction`, `--owner`, `--text`),
  `expectation-remove` (`--expectation-id`), and `show`/`list` output switching to
  expectations. Same CLI surface the PM already uses — no new `pyproject.toml` entry point.
- `tools/valor_session.py` create core records the outbound expectation (new
  `--expect-what` optional override; defaults to the session's initial instruction
  summary) when the creating context resolves a bound Job; silently skips (logged) when no
  Job resolves — never blocks session creation.
- PM prime (`.claude/skills/roles/prime-pm-role/SKILL.md`) updated: goal-authoring mandate
  extends to expectation hygiene (record on spawn if the mechanical write was skipped;
  discharge on delivery); promise vocabulary replaced.
- `bridge/promise_gate.py` advisory copy rewritten to expectation vocabulary; verdict
  machinery untouched.
- Integration test: create a test-keyed Job + eng child session via the create core and
  assert the outbound expectation appears via `job_tool show`.

## Documentation

- [ ] Rewrite `docs/features/durability-model.md` "Goals and promises" →
      "Goals and expectations": the single obligation primitive, both directions, the
      chokepoint-derived `status`/`has_open_expectations`, rest derivation, discharge
      rule (AC: doc describes expectations as the single obligation primitive).
- [ ] Add reconciler section (or `docs/features/expectation-reconciler.md` + README index
      entry) covering the ladder, shipped-work guard, caps, and operator escalation.
- [ ] Amend `docs/plans/durability-room-job-agentrun.md`: Schema Gate Amendment 2 + mark
      the superseded promise-model ACs "superseded by #2708" (owner directive).
- [ ] Update `docs/features/session-steering.md` only if the reconciler adds a new steer
      producer worth naming (expected: one line).
- [ ] Docstrings: `models/job.py` header rewritten for the new status quo (no historical
      promise narration — describe only what is).

## Success Criteria

- [ ] Expectations recorded bidirectionally on Job with `direction`; no separate promise
      model, API, or vocabulary survives outside history entries.
- [ ] Lane spawn through the session-create core records an outbound expectation when a Job
      resolves (integration-tested), and the PM prime mandates the hygiene.
- [ ] Discharge path works and discharged expectations disappear from every surfacing
      query.
- [ ] A Job with an open expectation cannot be swept to rest; an expectation-less idle Job
      still rests by age (both unit-tested).
- [ ] Reconciler surfaces open outbound expectations with gone owners, steers/respawns per
      the ladder, and never respawns when the shipped-work guard finds a pushed branch or
      open/merged PR (tested with fake git/gh fixtures).
- [ ] Under-recording test: no code path asserts a Job complete from an empty expectation
      list alone.
- [ ] Popoto bool-trap regression test on `has_open_expectations`.
- [ ] Migration is idempotent and preserves promise history verbatim as inbound
      expectations.
- [ ] Durability plan amended; #2494's superseded ACs named there.
- [ ] Tests pass (`/do-test`); documentation updated (`/do-docs`).

## Team Orchestration

### Team Members

- **Builder (model + migration)** — Name: `job-model-builder` — Role: `models/job.py`
  expectation primitive, status projection, rename, migration — Agent Type: builder —
  Domain: redis-popoto — Resume: true
- **Builder (enforcement + reconciler)** — Name: `reconciler-builder` — Role: spawn
  chokepoint, advisory rewrite, drafter rename, `reflections/expectation_reconciler.py` —
  Agent Type: builder — Resume: true
- **Validator** — Name: `expectation-validator` — Role: verify success criteria,
  verification table, red-state anti-criterion proof — Agent Type: validator — Resume: true
- **Documentarian** — Name: `durability-documentarian` — Role: Documentation section
  tasks — Agent Type: documentarian — Resume: true

## Step by Step Tasks

### 1. Job model: expectations primitive + status projection + rename
- **Task ID**: build-job-model
- **Depends On**: none
- **Validates**: tests/unit/test_job_model.py, tests/unit/test_job_tool.py
- **Assigned To**: job-model-builder
- **Agent Type**: builder
- **Parallel**: true
- Goal JSON v2 (`expectations` with direction/holder/owner/what/removed_ts); replace
  promise methods; `_write_goal_data` derives `has_open_expectations` + status projection;
  `sweep_to_rest` open-expectation skip; read-time self-heal of a `promises` key; rename
  backfill/repair coherently; bool-trap test.
- Extend `tools/job_tool.py` subcommands; update its tests.

### 2. Migration + update wiring
- **Task ID**: build-migration
- **Depends On**: build-job-model
- **Validates**: migration unit test (idempotency + history preservation)
- **Assigned To**: job-model-builder
- **Agent Type**: builder
- **Parallel**: false
- `scripts/update/migrations.py` function + `MIGRATIONS` registration; guarded index-set
  cleanup for the renamed field; per-row fault isolation.

### 3. Spawn chokepoint + inbound advisory + drafter rename
- **Task ID**: build-enforcement
- **Depends On**: build-job-model
- **Validates**: tests/unit/test_promise_advisory.py, test_promise_gate*.py,
  test_redundancy_filter.py, test_message_drafter.py, valor-session create tests
- **Assigned To**: reconciler-builder
- **Agent Type**: builder
- **Parallel**: true
- `tools/valor_session.py` create-core expectation write (skip-and-log on no Job; never
  block creation); sweep other eng-child creation sites.
- `bridge/promise_gate.py` advisory copy → expectations (no verdict/regex changes).
- `MessageDraft.expectations` → `open_questions` across drafter/redundancy
  filter/output_handler/session_completion.
- PM prime expectation-hygiene mandate.

### 4. Expectation reconciler reflection
- **Task ID**: build-reconciler
- **Depends On**: build-job-model
- **Validates**: tests/unit/reflections/test_expectation_reconciler.py (create),
  integration test with fake gh/git
- **Assigned To**: reconciler-builder
- **Agent Type**: builder
- **Parallel**: false
- `reflections/expectation_reconciler.py` per the Data Flow §5 ladder; cooldown/attempt/
  escalate machinery mirroring `sdlc_progress.py`; shared per-slug cooldown namespace;
  session_health backstop function swapped to expectation form.

### 5. Validate all
- **Task ID**: validate-all
- **Depends On**: build-migration, build-enforcement, build-reconciler
- **Assigned To**: expectation-validator
- **Agent Type**: validator
- **Parallel**: false
- Run Verification table; produce red-state proof for the anti-criteria; confirm success
  criteria.

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: durability-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Execute the Documentation section, including the durability-plan amendment.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Targeted tests pass | `scripts/pytest-clean.sh tests/unit/test_job_model.py tests/unit/test_job_tool.py tests/unit/test_promise_gate.py tests/unit/test_promise_advisory.py tests/unit/test_redundancy_filter.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No promise API survives | `grep -rn "add_promise\|remove_promise\|open_promises\|has_open_promises" models/ agent/ bridge/ tools/ worker/ reflections/ --include="*.py" \| grep -v migrations` | exit code 1 |
| Drafter collision resolved | `grep -rn "expectations" bridge/message_drafter.py bridge/redundancy_filter.py` | exit code 1 |
| Reconciler registered | `grep -rn "expectation_reconciler" reflections/ \| wc -l` | output > 1 |
| Migration registered | `grep -c "expectation" scripts/update/migrations.py` | output > 0 |
| No history deletion (anti-criterion) | `grep -n "\"promises\"\].*=.*\[\]\|del data\[\"promises\"\]" scripts/update/migrations.py models/job.py` | match count == 0 |
| Spawn chokepoint wired | `grep -c "expectation" tools/valor_session.py` | output > 0 |
| Durability doc updated | `grep -c "obligation primitive" docs/features/durability-model.md` | output > 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Spawn-time mechanical write vs. the advisory-only ruling.** This plan reads the
   2026-07-31 "no mechanical trigger writes an obligation" ruling as governing
   *classifier-inferred* obligations (message text → promise), and treats spawn-time
   recording as bookkeeping of a deliberate PM act — the issue's own AC requires "an
   enforcement point rather than discretion." Confirm this interpretation, or direct that
   the spawn write also be advisory (PM-authored on a nudge), accepting a higher
   under-recording risk against the measured zero-write base rate of the predecessor field.
2. **Naming: `expectation-add`/`expectation-remove` vs. `expect`/`discharge`** in
   `job_tool` — plan uses the former for symmetry with the retired promise verbs; cheap to
   change now, expensive later.
3. **Does the at-rest operator backstop survive?** With status chokepoint-maintained, the
   old intersection is structurally empty; this plan replaces it with the reconciler's
   gone-owner surface and deletes `_check_jobs_at_rest_with_open_promises`'s query form.
   Confirm no residual operator report depends on the old shape.
