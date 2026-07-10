---
status: Planning
type: chore
appetite: Medium
owner: Valor Engels
created: 2026-07-10
tracking: https://github.com/tomcounsell/ai/issues/2004
last_comment_id: none
---

# Resilience Hygiene Sweep: Unified Session Evidence, Enforced Artifact Freshness, Loud Degradation

## Problem

Workstream B of the resilience-simplification program
([`docs/plans/resilience-simplification-three-tier.md`](resilience-simplification-three-tier.md),
items T1.1–T1.6, T1.8 + live defects). Seven small, independent defect classes; each
produced at least one production bug in the 2026-07-08→10 window. File surface is fully
disjoint from Workstream A (#2003).

**Current behavior:**
1. Exit reasons are free-form strings classified by three hand-synced frozensets
   (`session_runner/router.py:152-188`); an unclassified reason silently lands "non-clean"
   (#1922 class).
2. Four forked "has this session made progress" predicates re-implement the sticky-evidence
   triple with divergent freshness semantics; #1962 and #1917 were each the same evidence
   missing from one sibling. Per-run signals are not attempt-scoped (#1979 class — its
   targeted fix is in flight and owns the delivery guard).
3. The merge-gate baseline's four staleness triggers are advisory-only; the gate can print
   "stale" and still emit false blocks (#1933: 40 flags at 60 days). The live baseline is
   degraded (`runs: 1`) with no downstream-visible marker; `flaky` entries never decay; the
   refresh reflection is unregistered on this machine and reimplements freshness age-only.
4. `find_affected` returns bare `[]` on every degraded branch — callers cannot distinguish
   "no docs affected" from "the finder is broken" (#1950).
5. The silent-failure guard string-scans 7 hand-picked functions; ~87
   `except Exception: pass` sites exist repo-wide (#1959 fixed one).
6. `import_error` baseline entries ride as "pre-existing" for months (#1933's
   `_build_draft_prompt` rot); API renames break 18 tests at a time with no designated
   loud failure (#1958).
7. Cross-module constant invariants live only in tests (#1961's duplicate 🤔).

**Desired outcome:** an unclassified exit reason, an unstamped or degraded decaying
artifact, a new silent `except: pass`, an aged import-error allowlist entry, and a
duplicate cross-module constant each fail at write/lint/import time instead of in
production.

## Freshness Check

**Baseline commit:** `a213add4`
**Issue filed at:** 2026-07-10 (same day)
**Disposition:** Unchanged

**File:line references re-verified (spot checks at plan time):**
- `session_runner/router.py:152-188` frozensets — still holds (`CLEAN_EXIT_REASONS` at :152,
  `WRAPUP_ELIGIBLE_EXIT_REASONS` at :166).
- `refresh_test_baseline.py:77` `MIN_USABLE_RUNS_FOR_FLAKY_DETECTION = 2`, `:529` degraded
  write path — still holds.
- Live `data/main_test_baseline.json`: `runs: 1`, `generated_at: 2026-07-02`,
  `generated_by: "python --merge"` (argv-join provenance bug) — confirmed live at plan time.
- `agent/session_executor.py`: 46 `except Exception` occurrences in that one file —
  the ~87 bare-pass estimate across `agent/ bridge/ tools/ worker/ monitoring/` stands.

**Cited sibling issues/PRs re-checked:**
- #1979 — OPEN, no PR yet (build in flight). Sequencing constraint stands: its fix owns the
  delivery guard; this plan's T1.2 work must not touch the delivery guard until it merges.
- #1983 — OPEN (3 pre-existing heartbeat-progress test failures); absorbed by this plan.
- #1927 (schema diet) — OPEN; coordinate `attempt_id` field naming.

**Commits on main since issue was filed (touching referenced files):** none in the
workstream's file surface (verified via `git log --since="3 hours ago"`).

**Active plans in `docs/plans/` overlapping this area:** the program plan (this is its
Workstream B) and `sdlc-run-ownership-merge-enforcement.md` (Workstream A, #2003 —
deliberately disjoint file surface; concurrent pipelines by design).

## Prior Art

- **#1962 / PR #1982**: fresh-heartbeat guard — copied sticky evidence into one more
  predicate; the fork count stayed at four.
- **#1917 / PR #1993**: crash auto-resume revival — added a progress-fields classifier to
  the signature extractor; the same logic now exists in a fourth place.
- **#1933 / PR #1945 + #1965 / PR #1986**: baseline refresh + commit-distance trigger —
  both advisory; enforcement never landed.
- **#1950 / PR #1988**: rerank-failure fallback — bespoke `failure_count` plumbing instead
  of a degradation contract.
- **#1959 / PR #1976**: logged one swallowed exception; the guard covers 7 functions.
- **#1961 / PR #1974**: fixed the duplicate emoji; the invariant stayed test-only.

## Research

No relevant external findings — internal consolidation work. One ecosystem fact from
training data informs T1.5: ruff implements flake8-bandit rules `S110`
(try-except-pass) and `S112` (try-except-continue); both support per-line `noqa` with
reason comments. Verified against the installed ruff during build (no web dependency).

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #1982 (#1962) | Added sticky-evidence short-circuit to the never-started gate | Propagated the copy instead of unifying; the next sibling predicate (#1917's extractor) had the same gap |
| PR #1986 (#1965) | Added commit-distance staleness trigger | Advisory stderr warning; verdict unchanged, so stale baselines still mis-block |
| PR #1988 (#1950) | Per-request rerank failure counting + all-fail fallback | Fixed the one branch; the empty-vs-broken conflation remains on every other degraded path |
| PR #1976 (#1959) | Logged one bare `except: pass` | Guard test enumerates 7 functions by hand; the other ~86 sites ship uncovered |

**Root cause pattern:** fixes at the incident site instead of the contract level — each
signal/artifact/fallback got a local patch while the shared convention (one evidence home,
one freshness contract, one degradation shape) was never created.

## Architectural Impact

- **New dependencies:** none (ruff already installed).
- **Interface changes:** `find_affected` returns `(results, meta)`; `ExitReason` StrEnum
  replaces raw strings at producer sites (string *values* unchanged); `ArtifactEnvelope`
  fields added to baseline JSON (readers tolerate absence during migration).
- **Coupling:** decreases — four predicates collapse onto one helper pair; gate and
  reflection share one staleness function.
- **Data ownership:** `session_runner/liveness.py` becomes the single home for progress
  evidence; `scripts/_baseline_common.py` for artifact freshness.
- **Reversibility:** high — each of the seven items is independently revertable.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1-2 (open-questions resolution)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| #1979 PR merged (before task 2 only) | `gh issue view 1979 --json state -q .state \| grep -qi closed` | T1.2 must not touch the delivery guard while #1979's build owns it; all other tasks are unblocked |

## Solution

### Key Elements

- **`ExitReason` StrEnum (T1.1)**: every member declares `is_clean` / `wrapup_eligible` /
  `is_anomaly`; the frozensets become derivations; role-driver failures become
  `TurnFailure(reason, detail)`. Telemetry string values unchanged.
- **`SessionEvidence` (T1.2)**: `has_started(entry)` + `has_recent_progress(entry, *,
  window, now)` in `session_runner/liveness.py`; all four predicates delegate;
  presence-vs-freshness becomes a parameter. Attempt-scoping: an `attempt_generation`
  counter bumped on resume/requeue; "did X happen this run" guards compare signal
  generation, generalizing the in-flight #1979 fix.
- **`ArtifactEnvelope` (T1.3)**: stamped `{generated_at, commit, generated_by, runs,
  degraded, max_age_days, max_commit_distance}`; one `staleness(envelope)` shared by gate
  and reflection; `/do-merge` invokes `--strict-freshness` (stale/degraded ⇒ refuse to
  gate); degraded writes stamped; provenance fixed; reflection registered via the update
  path; `flaky` decay added.
- **Degraded-result metadata (T1.4)**: `find_affected` → `(results, meta)` with mandatory
  `degraded`, `reason`, `rerank_failures`, `candidates`; a thin list-subclass shim keeps
  old iteration code working during call-site migration.
- **Silent-failure lint (T1.5)**: ruff `S110`/`S112` enabled for `agent/ bridge/ tools/
  worker/ monitoring/`; ~87 sites triaged to fix (add logging) or allowlist (per-line
  `noqa: S110` + mandatory reason comment; memory ops are silent by documented design).
  Delete `TestNoSilentPassRemaining`'s string scan; keep behavioral caplog tests.
- **Import-error fast-expiry + API contract test (T1.6)**: gate never allowlists
  `import_error` entries older than 3 days / 30 commits (via the envelope); one
  contract-test module snapshots `inspect.signature` of the public API surface tests
  depend on (`AgentSession.create_eng` and peers) so a rename fails one designated test
  with a named message.
- **Definition-site invariants (T1.8)**: module-level `_assert_distinct()` at import in
  `bridge/response.py`; one helper shared with the test for lazily-resolved constants.

### Flow

Producer mints `ExitReason.PM_USER` → classification derived from the enum →
executor/telemetry consume one vocabulary. Health check asks `SessionEvidence.has_started`
→ same answer everywhere. `refresh_test_baseline` stamps envelope → gate + reflection call
one `staleness()` → `/do-merge --strict-freshness` refuses or proceeds loudly.
`find_affected` → `(results, meta)` → caller branches on `meta.degraded`.

### Technical Approach

- T1.1: `class ExitReason(StrEnum)` with a `classify` dataclass per member (or member
  attributes via `__new__`); `CLEAN_EXIT_REASONS = frozenset(r for r in ExitReason if
  r.is_clean)` preserves every import site unchanged. A completeness test iterates members.
- T1.2: pure functions over the entry dict/model — no imports beyond stdlib in
  `liveness.py` (it is already the dependency-light home, which is why
  `crash_signature.py` can call it where it couldn't import `session_health`).
  `attempt_generation` is one integer field; signals written by the runner stamp the
  current generation; guards ignore stamps from prior generations. Field name coordinated
  with #1927.
- T1.3: envelope helpers in `scripts/_baseline_common.py` (shared by `baseline_gate.py`,
  `refresh_test_baseline.py`, `reflections/housekeeping/test_baseline_refresh_check.py`);
  gate reads envelope fields defensively (absent ⇒ legacy, warn). `--strict-freshness`
  flag wired into the `/do-merge` addendum (`docs/sdlc/do-merge.md`).
- T1.5: `[tool.ruff.lint]` gains `S110`,`S112` scoped via per-directory `extend-select`
  (or repo-wide with the allowlist pass). Triage rule: swallowed exception on a
  state-mutating or delivery path ⇒ add `logger.warning`; genuinely-by-design silence ⇒
  `noqa` + reason.
- T1.6: contract test lives at `tests/unit/test_public_api_contract.py`; snapshot is
  literal source (signature strings), updated deliberately on real renames.
- Fresh baseline regenerated (`runs >= 2`) after the envelope mechanism lands — removes
  the live degraded artifact.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Every `except` site converted in the T1.5 triage gets/keeps a caplog test asserting
      the warning (extend the existing behavioral classes in `test_silent_failures.py`)
- [ ] Envelope parse failure in the gate ⇒ legacy-mode warning, never a crash (test)

### Empty/Invalid Input Handling
- [ ] `SessionEvidence` helpers on empty/None fields return False, never raise (test)
- [ ] `find_affected` with missing index/key returns `([], meta(degraded=True, reason=...))`
      (tests per branch)
- [ ] Gate on absent/corrupt baseline keeps existing bootstrap behavior (regression pin)

### Error State Rendering
- [ ] `--strict-freshness` refusal names the staleness reasons and the refresh command
- [ ] Import-time `_assert_distinct` failure names the duplicated glyph and both constants

## Test Impact

- [ ] `tests/integration/test_silent_failures.py::TestNoSilentPassRemaining` — DELETE
      (replaced by ruff S110/S112); keep behavioral caplog classes
- [ ] `tests/unit/test_do_merge_baseline.py`, `tests/unit/test_refresh_test_baseline.py`,
      `tests/unit/reflections/test_test_baseline_refresh_check.py` — UPDATE: envelope +
      shared staleness + strict mode + flaky decay
- [ ] `tests/unit/test_doc_impact_finder.py` — UPDATE: `(results, meta)` shape
- [ ] `tests/unit/session_runner/test_runner_turns.py`, `test_runner_liveness.py`,
      `test_liveness.py`, `tests/unit/test_session_executor_runner_dispatch.py` — UPDATE:
      enum members replace string literals at producer assertions
- [ ] `tests/unit/test_never_started_recovery.py`,
      `tests/unit/test_session_health_inference_removed.py`,
      `tests/integration/test_agent_session_health_monitor.py` — UPDATE: predicates
      delegate to `SessionEvidence`; the #1962 fresh-heartbeat regression test must pass
      unmodified
- [ ] `tests/unit/test_crash_signature.py`, `tests/unit/test_crash_recovery_gates.py` —
      UPDATE: extractor consults the shared helper; #1917 regression pins unchanged
- [ ] `tests/integration/test_session_heartbeat_progress.py` — UPDATE: fix the 3
      pre-existing failures (#1983) against the unified predicates
- [ ] `tests/integration/test_reply_delivery.py::TestReactionEmojiSelection` — UPDATE:
      share the distinctness helper with the import-time assert

## Rabbit Holes

- Chasing all ~87 `except` sites to zero: allowlist-with-reason is an acceptable terminal
  state for by-design-silent paths (memory ops especially).
- Redesigning the baseline schema beyond the envelope fields: v2 schema stays; envelope is
  additive.
- Generalizing `ArtifactEnvelope` to every JSON file in `data/`: scope is the merge-gate
  baseline + impact-finder index only; others adopt it when next touched.
- Unifying the telemetry timeline with `session_events`: out of scope (program T3.4).
- Touching the delivery guard while #1979 is in flight: hard sequencing rule, not a
  judgment call.

## Risks

### Risk 1: T1.5's first lint pass floods with findings and stalls the PR
**Impact:** the sweep PR balloons past review capacity.
**Mitigation:** triage is mechanical (log-or-noqa); if the diff exceeds ~2/3 of review
budget, land lint enablement + allowlist in this PR and convert the worst 20 sites,
tracking the remainder in the allowlist itself (each `noqa` reason is the record).

### Risk 2: Attempt-generation stamping misses a writer, recreating a #1962-style false guard
**Impact:** a guard compares against an unstamped signal and misfires.
**Mitigation:** stamps are written at the runner's single spawn/exit seam and the
resume/requeue transitions only; a test enumerates the signal-writing sites (grep-driven)
and asserts each stamps the generation.

### Risk 3: Strict freshness blocks merges right after enablement (stale baseline on day one)
**Impact:** the first `/do-merge` after this lands refuses on the current `runs: 1` artifact.
**Mitigation:** regenerating the baseline is a task in this plan, sequenced before the
strict flag is wired into the addendum.

## Race Conditions

### Race 1: Resume bumps attempt_generation while the health check reads mid-transition
**Location:** `tools/valor_session.py::resume_session` + `agent/session_health.py` scan
**Trigger:** health tick between status transition and generation bump
**Data prerequisite:** generation bump must be saved in the same `save()` as the
pending transition
**State prerequisite:** guards treat "signal generation > current" as current-run (clock
of generations is monotonic per session)
**Mitigation:** bump and status change in one partitioned save; comparison is `>=` on the
guard side.

No other race conditions identified — remaining items (enum, envelope, lint, contract
test, import assert) are synchronous, single-writer changes.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2003] Workstream A (run ownership, merge enforcement, PR resolution) —
  concurrent, disjoint pipeline.
- [SEPARATE-SLUG #1927] Any AgentSession field rename beyond adding `attempt_generation`
  (name coordinated there).
- [SEPARATE-SLUG #1925] `HarnessResult` / harness return-shape changes (program T2.4).
- [SEPARATE-SLUG #1926] Deletion/pruning of liveness machinery, watchdogs, or the
  stall-classifier taxonomy — this plan unifies what exists; #1926 decides what dies.
- [ORDERED] Delivery-guard changes — blocked until #1979's in-flight PR merges (its build
  owns that surface).

## Update System

- `scripts/update/reflection_register.py` (+ `scripts/update/run.py` if a new step is
  needed): register the baseline-refresh reflection on all machines — closing the
  deployment gap is part of T1.3, not a manual vault edit.
- `pyproject.toml` ruff config propagates with the repo; no new dependencies.
- One idempotent migration in `scripts/update/migrations.py` for `attempt_generation`
  (Popoto rule). No other update-system changes required.

## Agent Integration

No new agent surface required — all changes live behind existing entry points
(`baseline_gate.py` invoked by `/do-merge`, `find_affected` by the impact-finder wrappers,
runner/health internals). The `find_affected` return-shape change migrates its in-repo
callers in the same PR; an integration test asserts a degraded finder is visibly degraded
through the `find_affected_docs` wrapper the agent actually calls.

## Documentation

- [ ] Update `docs/features/merge-gate-baseline.md` — envelope, strict freshness, flaky
      decay, reflection registration
- [ ] Update `docs/features/session-recovery-mechanisms.md` +
      `docs/features/agent-session-health-monitor.md` — SessionEvidence, attempt scoping
- [ ] Update `docs/features/headless-session-runner.md` — ExitReason enum
- [ ] Update `docs/features/semantic-doc-impact-finder.md` +
      `docs/features/code-impact-finder.md` — degraded-result contract
- [ ] Update `docs/features/README.md` index entries as needed

## Success Criteria

- [ ] Adding an `ExitReason` member without classification fails a completeness test; no
      raw exit-reason string literals outside `router.py` (grep check)
- [ ] All four progress predicates delegate to `SessionEvidence`; #1962 and #1917
      regression tests pass unmodified; #1983's 3 failures fixed
- [ ] A prior-attempt signal cannot satisfy a current-run guard (attempt-scoping test)
- [ ] `baseline_gate --strict-freshness` refuses on stale/degraded envelopes;
      `refresh_test_baseline.py` stamps `degraded` and correct provenance; reflection
      registered by the update path; `flaky` entries decay
- [ ] `import_error` entries past the window never classify a failure as pre-existing
- [ ] `find_affected` meta reports `degraded=True` + reason on every fallback branch
- [ ] ruff S110/S112 active; a new bare `except: pass` in `agent/` fails lint; allowlist
      documents every intentional site
- [ ] Duplicate reaction emoji crashes at import naming the glyph
- [ ] Fresh baseline regenerated with `runs >= 2`
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (session-signals)**
  - Name: signals-builder
  - Role: T1.1 enum + T1.2 SessionEvidence/attempt scoping
  - Agent Type: builder
  - Resume: true
- **Builder (gates-artifacts)**
  - Name: gates-builder
  - Role: T1.3 envelope + T1.6 expiry/contract test + baseline regen
  - Agent Type: builder
  - Resume: true
- **Builder (loud-failure)**
  - Name: loudness-builder
  - Role: T1.4 degraded meta + T1.5 lint triage + T1.8 import asserts
  - Agent Type: builder
  - Resume: true
- **Validator**
  - Name: sweep-validator
  - Role: verification rows + success criteria
  - Agent Type: validator
  - Resume: true
- **Documentarian**
  - Name: sweep-docs
  - Role: Documentation section checklist
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. ExitReason enum
- **Task ID**: build-exit-enum
- **Depends On**: none
- **Validates**: tests/unit/session_runner/test_runner_turns.py, completeness test (create)
- **Assigned To**: signals-builder
- **Agent Type**: builder
- **Parallel**: true
- Enum with per-member classification; derive frozensets; convert producer sites;
  `TurnFailure(reason, detail)`

### 2. SessionEvidence + attempt scoping
- **Task ID**: build-session-evidence
- **Depends On**: build-exit-enum
- **Validates**: tests/unit/test_never_started_recovery.py, test_crash_signature.py,
  tests/integration/test_session_heartbeat_progress.py (#1983)
- **Assigned To**: signals-builder
- **Agent Type**: builder
- **Parallel**: false
- **Gate**: verify #1979 closed before touching any delivery-guard-adjacent code
- Helpers in liveness.py; four predicates delegate; `attempt_generation` field + migration
  + stamp sites + guard comparisons

### 3. ArtifactEnvelope + strict freshness + reflection registration
- **Task ID**: build-envelope
- **Depends On**: none
- **Validates**: tests/unit/test_do_merge_baseline.py, test_refresh_test_baseline.py,
  reflections tests
- **Assigned To**: gates-builder
- **Agent Type**: builder
- **Parallel**: true
- Envelope helpers; shared staleness(); gate strict mode; degraded stamp + provenance fix;
  flaky decay; register reflection via update path

### 4. Import-error expiry + API contract test + baseline regen
- **Task ID**: build-expiry-contract
- **Depends On**: build-envelope
- **Validates**: tests/unit/test_public_api_contract.py (create), gate tests
- **Assigned To**: gates-builder
- **Agent Type**: builder
- **Parallel**: false
- Fast-expiry rule via envelope; contract-test module; regenerate baseline (`runs >= 2`)
  before wiring `--strict-freshness` into `docs/sdlc/do-merge.md`

### 5. Degraded meta + silent-failure lint + import asserts
- **Task ID**: build-loudness
- **Depends On**: none
- **Validates**: tests/unit/test_doc_impact_finder.py, ruff run,
  tests/integration/test_reply_delivery.py
- **Assigned To**: loudness-builder
- **Agent Type**: builder
- **Parallel**: true
- `(results, meta)` + call-site migration; ruff S110/S112 + triage pass + allowlist;
  delete string-scan guard; `_assert_distinct` at import

### 6. Validation
- **Task ID**: validate-sweep
- **Depends On**: build-session-evidence, build-expiry-contract, build-loudness
- **Assigned To**: sweep-validator
- **Agent Type**: validator
- **Parallel**: false
- All Verification rows; success criteria; report

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-sweep
- **Assigned To**: sweep-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Documentation section checklist

### 8. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: sweep-validator
- **Agent Type**: validator
- **Parallel**: false
- Full suite + criteria re-check + final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean (incl. S110/S112) | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No raw exit-reason literals outside router | `grep -rn "exit_reason=\"" agent/session_runner/ agent/session_executor.py \| grep -v "ExitReason\." \| wc -l` | match count == 0 |
| Predicate forks gone | `grep -rc "_has_demonstrable_progress" agent/session_stall_classifier.py agent/crash_signature.py \| grep -v ":0"` | output does not contain def |
| Baseline stamped | `python3 -c "import json; d=json.load(open('data/main_test_baseline.json')); assert d['runs']>=2 and 'degraded' in d"` | exit code 0 |
| Strict mode refuses stale | `pytest tests/unit/test_do_merge_baseline.py -k strict -q` | exit code 0 |
| String-scan guard deleted | `grep -c "TestNoSilentPassRemaining" tests/integration/test_silent_failures.py` | match count == 0 |
| Import assert live | `python -c "import bridge.response"` | exit code 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **T1.5 scope of enforcement:** enable S110/S112 repo-wide with a large first allowlist,
   or scope to `agent/ bridge/ tools/ worker/ monitoring/` only (proposed)? Repo-wide
   catches future `scripts/` sites but inflates the initial triage.
2. **`attempt_generation` naming:** proposed name pending a quick check against #1927's
   naming direction — accept `attempt_generation`, or defer the field name to the schema
   diet and land it there first?
3. **Strict-freshness default:** proposed warn-by-default everywhere with
   `--strict-freshness` only at `/do-merge` (per the program plan). Confirm, or go strict
   everywhere after the first refresh cycle?
