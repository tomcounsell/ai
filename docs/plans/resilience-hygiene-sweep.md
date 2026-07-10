---
status: Ready
type: chore
appetite: Medium
owner: Valor Engels
created: 2026-07-10
tracking: https://github.com/tomcounsell/ai/issues/2004
last_comment_id: none
revision_applied: true
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
   "stale" and still emit false blocks (#1933: 40 flags at 60 days). The loud surface today
   is the refresh script — it already emits a WARNING and exits non-zero when it writes a
   degraded (`runs < 2`) baseline. The *silent* surface is the **persisted artifact** the
   merge gate reads later: `data/main_test_baseline.json` carries no `degraded` field, no
   run count the gate consults, and an argv-join provenance bug (`generated_by:
   "python --merge"`), so a gate run days later cannot tell the artifact was degraded at
   write time. Additionally `flaky` entries never decay, and the refresh reflection is
   unregistered on this machine and reimplements freshness age-only.
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
- **#1539 (CLOSED)**: crash-signature library + auto-resume policy — the precedent
  `reflection_register.py` cites for "reflection built, registration never landed"; its
  single-reflection registration shape is what T1.3 must generalize to add a second
  reflection.

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
signal/artifact/fallback got a local patch while the shared conventions (one evidence-leaf
home, one staleness function) were never created. Degradation stays two intentional
representations (the baseline envelope `degraded` bool and `find_affected`'s `meta.degraded`)
— a single shared type across a JSON artifact and an in-memory return value would be
over-engineering; the win is that each *makes degradation loud*, not that they share a class.

## Architectural Impact

- **New dependencies:** none (ruff already installed).
- **Interface changes:** `find_affected` returns `(results, meta)`; `ExitReason` StrEnum
  replaces raw strings at producer sites (string *values* unchanged); `ArtifactEnvelope`
  fields added to baseline JSON (readers tolerate absence during migration).
- **Coupling:** decreases — four predicates read one shared leaf presence signal (each
  keeps its own grace/freshness policy); gate and reflection share one staleness function.
- **Data ownership:** `session_runner/liveness.py` becomes the single home for the
  progress-evidence *leaf signal* (presence-only, no clock); `scripts/_baseline_common.py`
  for artifact freshness.
- **Reversibility:** high — each of the seven items is independently revertable.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1-2 (open-questions resolution)
- Review rounds: 1

## Prerequisites

No hard blockers — every task can proceed now. The one sequencing constraint (#1979 owns
the delivery guard) is encoded as Task 2's Gate, which is the **single source of truth**:
Task 2 does NOT touch the delivery-guard code regardless of #1979's merge state, so there is
no "wait for #1979 to merge" gate that could block Task 2 indefinitely. If a merge check is
ever needed, use merged-PR evidence, not issue-closed:
`gh issue view 1979 --json closedByPullRequestsReferences -q '.closedByPullRequestsReferences[].state' | grep -qi MERGED`
(issue-closed does not imply the delivery-guard fix merged).

## Solution

### Key Elements

- **`ExitReason` StrEnum (T1.1)**: every member declares `is_clean` / `wrapup_eligible` /
  `is_anomaly`; the frozensets become derivations; role-driver failures become
  `TurnFailure(reason, detail)`. Telemetry string values unchanged.
- **`SessionEvidence` (T1.2) — field-set-precise, per-caller parameterized**: the callers
  do NOT share one leaf; they read **different presence field-sets** and must keep doing so.
  Verified in code: `session_health` reads `turn_count`/`log_path`/`claude_session_uuid`
  plus the `*_at` triple via the *already-shared* `derive_sdk_ever_output`
  (`liveness.py:20`, imported at `session_health.py:31`, called at :992/:1153/:1336/:2207 —
  its delegation is already done); `session_stall_classifier._has_demonstrable_progress`
  (:208-214) and `crash_signature._has_demonstrable_progress` (:174) each read only
  `{turn_count, last_tool_use_at}`. The consolidation is **narrow**: reuse the existing
  `derive_sdk_ever_output` as session_health's leaf (no rename, no churn), and add ONE small
  composable helper `has_demonstrable_activity(entry, *, freshness_window=None)` in
  `liveness.py` reading only `{turn_count, last_tool_use_at}` — the exact subset both forks
  already use — with an optional freshness window supplied by the caller
  (`stall_classifier` passes `IDLE_SUSPECT_SECS`, `crash_signature` passes `None` for
  presence-only). The two forks delegate to it; `session_health` is untouched beyond the leaf
  it already calls. **B1 guard: crash/stall must NOT gain `log_path`/`claude_session_uuid`/
  `last_stdout_at`/`last_turn_at` as presence signals** — an init-only/log-only session
  (`log_path` set, `turn_count==0`, `last_tool_use_at` None) must still read no-progress for
  the stall/crash paths. **`liveness.py` stays presence-only** (dependency-light, stdlib —
  the reason `crash_signature.py` can import it where it cannot import `session_health`); the
  freshness window is arithmetic on a caller-supplied value, no module-level clock.
  **Tests:** (a) an init-only session with only `last_stdout_at`/`log_path` set asserts
  `crash_signature._has_demonstrable_progress(...) is False` and
  `stall_classifier._has_demonstrable_progress(...) is False`; (b) an in-grace-window session
  still reads live through `session_health` post-refactor.
- **Attempt-scoping (T1.2) — durable field DEFERRED**: this sweep lands the pure
  predicate-consolidation only. The durable `attempt_generation` schema field is **deferred**
  to whichever of #1979 (OPEN/unmerged) or #1927 (schema diet, may own the name) settles the
  field name first — this hygiene sweep adds no durable schema field, no migration, and does
  not touch the delivery guard (#1979 owns it). #1979's in-flight fix already generalizes
  attempt-scoping on the one guard it owns; the fleet-wide generation field rides that work.
- **`ArtifactEnvelope` (T1.3)**: stamped `{generated_at, commit, generated_by, runs,
  degraded, max_age_days, max_commit_distance}`; one `staleness(envelope)` shared by gate
  and reflection; degraded writes stamped (the persisted artifact carries `degraded`/`runs`,
  the surface the gate reads later); provenance fixed; reflection registered via the update
  path; `flaky` decay added. **Strict-freshness sequencing (deadlock guard):**
  `/do-merge --strict-freshness` (stale/degraded ⇒ refuse to gate) is wired in **strictly
  after** a confirmed committed `runs >= 2` artifact exists — the regen task lands first and
  fails **loudly** (non-zero, no write) rather than silently persisting another degraded
  artifact. `--strict-freshness` honors the existing `data/merge_authorized_{N}` break-glass
  sentinel: if the operator has authorized the merge, the strict refusal yields to it rather
  than self-locking every merge.
- **Degraded-result metadata (T1.4)**: `find_affected` → `(results, meta)` with mandatory
  `degraded`, `reason`, `rerank_failures`, `candidates`. All `find_affected` callers are
  in-repo and enumerable, so they migrate to the tuple return **in the same PR** — no
  list-subclass shim (it would be dead weight at merge; no out-of-repo consumer exists).
- **Silent-failure lint (T1.5)**: ruff `S110`/`S112` enabled for `agent/ bridge/ tools/
  worker/ monitoring/` plus the four `scripts/` files this sweep touches; ~87 sites triaged
  to fix (add logging) or allowlist (per-line
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
- T1.2: reuse the existing `derive_sdk_ever_output` (`liveness.py:20`) as session_health's
  leaf (already wired). Add one composable `has_demonstrable_activity(entry, *,
  freshness_window=None)` in `liveness.py` reading only `{turn_count, last_tool_use_at}` —
  pure over the entry dict/model, stdlib only, the clock is arithmetic on the caller-supplied
  `freshness_window`. Point `session_stall_classifier._has_demonstrable_progress` (passing
  `IDLE_SUSPECT_SECS`) and `crash_signature._has_demonstrable_progress` (passing `None`) at
  it; their field-set is unchanged, only the duplication is removed. Do NOT widen crash/stall
  to `log_path`/`claude_session_uuid`/`*_stdout_at` presence (B1). No durable
  `attempt_generation` field this sweep (deferred to #1979/#1927); no migration. Tests pin
  both the init-only-reads-False case for the two forks and the in-grace-window session still
  reads live through `session_health`.
- T1.3: envelope helpers in `scripts/_baseline_common.py` (shared by `baseline_gate.py`,
  `refresh_test_baseline.py`, `reflections/housekeeping/test_baseline_refresh_check.py`);
  gate reads envelope fields defensively (absent ⇒ legacy, warn). **Enforcement lives in the
  gate exit code, not the addendum** (C1): `baseline_gate.parse_args` (:459) gains
  `--strict-freshness` and `--pr-number`; under strict, the gate computes
  `envelope.degraded or envelope.runs < 2 or staleness(envelope)` and, if true,
  short-circuits to a **refuse** exit code (never a false pre-existing/regression verdict),
  printing the exact `refresh_test_baseline.py` regen command. The break-glass seam:
  strict refusal is skipped when `data/merge_authorized_{pr_number}` exists (parity with the
  existing merge gate sentinel). `data/main_test_baseline.json` is gitignored/per-machine
  (`.gitignore:181`) — so the artifact cannot travel with the PR; the runtime gate check +
  break-glass ARE the guard (a committed baseline in the PR is not relied upon). `/do-merge`
  passes `--strict-freshness --pr-number {N}`; the addendum documents it but enforcement is
  the gate exit code.
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
      UPDATE: extractor consults `has_demonstrable_activity`; #1917 regression pins
      unchanged; ADD init-only-reads-False pin (B1)
- [ ] `tests/unit/session_runner/test_liveness.py` — UPDATE: add
      `has_demonstrable_activity` unit coverage (field-set + freshness-window param);
      `derive_sdk_ever_output` call sites in `session_health` unchanged
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
Independently, the low-risk T1.4 degraded-meta (subtask 5a) and T1.8 import asserts
(subtask 5b) have their own commit/PR checkpoint and ship first if the T1.5 lint triage
stalls — they never wait on the lint negotiation.

### Risk 2: Predicate consolidation flattens divergent grace semantics into one verdict
**Impact:** dropping `session_health._has_progress`'s grace window while delegating marks a
still-live in-grace-window session stalled/dead — the exact silent degradation this plan
exists to kill.
**Mitigation:** SessionEvidence is per-caller parameterized (see T1.2) — only the leaf
presence signal is shared; each caller keeps its own grace/freshness. A parity test pins
that an in-grace-window session still reads live after the refactor, and the builder diffs
the grace branches before deleting `_has_progress`'s body.

### Risk 3: Strict freshness blocks merges right after enablement (stale baseline on day one)
**Impact:** the first `/do-merge` after this lands refuses on a degraded artifact.
**Mitigation:** the baseline is gitignored/per-machine, so the guard cannot be "commit a
fresh baseline in the PR" — it is enforced at runtime in the gate exit code: (a) under
`--strict-freshness` the gate refuses (distinct exit code) on `degraded`/`runs<2`/stale and
prints the exact regen command, so a machine with a stale local baseline gets an actionable
refusal, not a false verdict; (b) refusal is skipped when `data/merge_authorized_{pr}`
exists, so an operator can always authorize past a false refusal (existing break-glass
parity). The `runs>=2` regen (Task 4) still lands before `/do-merge` starts passing the
strict flag, so the common path is a fresh artifact.

## Race Conditions

No race conditions in this sweep's scope. The durable `attempt_generation` field — the one
item that would have introduced a resume/health-check timing hazard — is **deferred** to
#1979/#1927 (see T1.2). The remaining items (enum, predicate leaf-signal delegation,
envelope, lint, contract test, import assert) are synchronous, single-writer changes: each
caller reads the shared presence signal on its own thread with its own clock, and no shared
mutable state is written by the consolidation.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2003] Workstream A (run ownership, merge enforcement, PR resolution) —
  concurrent, disjoint pipeline.
- [SEPARATE-SLUG #1927 / #1979] The durable `attempt_generation` (or equivalently-named)
  AgentSession field — deferred entirely to whichever settles the name first. This sweep
  adds no durable schema field.
- [SEPARATE-SLUG #1925] `HarnessResult` / harness return-shape changes (program T2.4).
- [SEPARATE-SLUG #1926] Deletion/pruning of liveness machinery, watchdogs, or the
  stall-classifier taxonomy — this plan unifies what exists; #1926 decides what dies.
- [ORDERED] Delivery-guard changes — blocked until #1979's in-flight PR merges (its build
  owns that surface).

## Update System

- `scripts/update/reflection_register.py` is **single-reflection-shaped today**
  (`REFLECTION_NAME = "crash-recovery"` at :52, `_EXPECTED_CALLABLE` at :55, `_build_entry_block`
  interpolates the literal name, `_has_entry` at :167/:187 tests `== REFLECTION_NAME`,
  `register_crash_recovery` at :244). Registering a SECOND reflection (baseline-refresh)
  requires a **generalization**, scoped as a concrete T1.3 subtask: add
  `register_reflection(project_dir, *, name, callable_path, description, cadence, priority)`
  routed through the existing `_append_entry`/`_resolve_target`/`_this_machine_owns_valor`
  machinery, with `_has_entry(text, name)` taking the name as an argument; keep
  `register_crash_recovery` as a thin wrapper over it (no behavior change for the existing
  reflection). Then call `register_reflection(..., name="test-baseline-refresh", ...)` from
  the update path (+ `scripts/update/run.py` step if a new one is needed). This closes the
  #1539-class "reflection built, registration never landed" deployment gap.
- `pyproject.toml` ruff config propagates with the repo; no new dependencies. The four
  `scripts/` files this sweep touches or creates (`_baseline_common.py`, `baseline_gate.py`,
  `refresh_test_baseline.py`, `update/reflection_register.py`) adopt S110/S112 now (they carry
  the exact envelope/degraded-write code this sweep hardens).
- **No Popoto migration** — the durable `attempt_generation` field is deferred to
  #1979/#1927, so this sweep adds no schema field and needs no migration. No other
  update-system changes required.

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
      `docs/features/agent-session-health-monitor.md` — SessionEvidence leaf consolidation
      (the two `_has_demonstrable_progress` forks share one activity helper)
- [ ] Update `docs/features/headless-session-runner.md` — ExitReason enum
- [ ] Update `docs/features/semantic-doc-impact-finder.md` +
      `docs/features/code-impact-finder.md` — degraded-result contract
- [ ] Update `docs/features/README.md` index entries as needed

## Success Criteria

- [ ] Adding an `ExitReason` member without classification fails a completeness test; no
      raw exit-reason string literals outside `router.py` (grep check)
- [ ] The two `_has_demonstrable_progress` forks (`session_stall_classifier`,
      `crash_signature`) delegate to one `has_demonstrable_activity` reading only
      `{turn_count, last_tool_use_at}`; `session_health` reuses `derive_sdk_ever_output`;
      #1962 and #1917 regression tests pass unmodified; #1983's 3 failures fixed
- [ ] B1 pin: an init-only/log-only session (`log_path`/`last_stdout_at` set, `turn_count==0`,
      `last_tool_use_at` None) reads no-progress for BOTH forks (they do not gain
      log_path/uuid/stdout presence)
- [ ] A parity test asserts an in-grace-window session still reads live through
      `session_health` post-refactor (grace semantics preserved, not flattened)
- [ ] Durable `attempt_generation` field is NOT added this sweep (deferred to #1979/#1927);
      the fleet-wide attempt-scoping test rides that follow-up. #1979's own regression test
      stays green when its PR merges
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
  - Role: T1.1 enum + T1.2 SessionEvidence leaf/predicate consolidation
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

### 2. SessionEvidence leaf/predicate consolidation
- **Task ID**: build-session-evidence
- **Depends On**: build-exit-enum
- **Validates**: tests/unit/test_never_started_recovery.py, test_crash_signature.py,
  tests/integration/test_session_heartbeat_progress.py (#1983)
- **Assigned To**: signals-builder
- **Agent Type**: builder
- **Parallel**: false
- **Gate**: do NOT touch the delivery guard (owned by #1979, OPEN). No durable
  `attempt_generation` field this sweep (deferred to #1979/#1927).
- Reuse `derive_sdk_ever_output` as session_health's leaf (already wired); add one
  `has_demonstrable_activity(entry, *, freshness_window=None)` in liveness.py reading only
  `{turn_count, last_tool_use_at}`; point both `_has_demonstrable_progress` forks at it
  (stall passes `IDLE_SUSPECT_SECS`, crash passes `None`); do NOT widen crash/stall presence
  fields (B1); add the init-only-reads-False test for both forks + the in-grace-window parity
  test; fix #1983's 3 failures

### 3. ArtifactEnvelope + strict freshness + reflection registration
- **Task ID**: build-envelope
- **Depends On**: none
- **Validates**: tests/unit/test_do_merge_baseline.py, test_refresh_test_baseline.py,
  reflections tests
- **Assigned To**: gates-builder
- **Agent Type**: builder
- **Parallel**: true
- Envelope helpers; shared staleness(); gate strict mode (`--strict-freshness`+`--pr-number`
  in `parse_args`, refusal in the exit code, `data/merge_authorized_{pr}` break-glass);
  degraded stamp + provenance fix; flaky decay. **Subtask 3a (blocker fix):** generalize
  `reflection_register.py` to `register_reflection(project_dir, *, name, callable_path,
  description, cadence, priority)` with `register_crash_recovery` as a thin wrapper; then
  register `test-baseline-refresh` via the update path.

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
- **Subtask 5a (T1.4, lands first if T1.5 stalls):** `(results, meta)` + full in-PR
  call-site migration (no shim). **Subtask 5b (T1.8):** `_assert_distinct` at import. 5a+5b
  are low-risk and can be committed/validated independent of 5c. **Subtask 5c (T1.5):** ruff
  S110/S112 (scoped to `agent/ bridge/ tools/ worker/ monitoring/` + the four touched
  `scripts/` files) + triage pass + allowlist; delete the string-scan guard. If 5c's triage
  balloons past review budget (Risk 1), 5a+5b ship in their own commit/PR checkpoint first.

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
| Leaf read unified | `grep -c "has_demonstrable_activity" agent/session_stall_classifier.py agent/crash_signature.py` | each file >= 1 (both forks delegate; predicates + their own freshness windows stay) |
| Baseline stamped | `python3 -c "import json; d=json.load(open('data/main_test_baseline.json')); assert d['runs']>=2 and 'degraded' in d"` | exit code 0 |
| Strict mode refuses stale | `pytest tests/unit/test_do_merge_baseline.py -k strict -q` | exit code 0 |
| String-scan guard deleted | `grep -c "TestNoSilentPassRemaining" tests/integration/test_silent_failures.py` | match count == 0 |
| Import assert live | `python -c "import bridge.response"` | exit code 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk / Scope / History (cross-validated) | Four-predicate collapse can silently reclassify live sessions as dead if SessionEvidence flattens divergent grace semantics into one verdict | T1.2 rewritten: SessionEvidence is per-caller parameterized; only the leaf presence signal (`has_started`) is shared; each caller keeps its own grace/freshness. New in-grace-window parity test + grace-branch diff before deleting `_has_progress`. Risk 2 rewritten. | Delegate the leaf read only; keep the ~250 lines of grace logic in `session_health`; assert an in-grace session still reads live post-refactor |
| CONCERN | Scope & Value | `has_recent_progress` pulls a clock dependency into the deliberately presence-only `liveness.py` | `liveness.py` stays presence-only (`has_started` only, stdlib); freshness homed per-caller where the clock seam already exists | No `now`/window param enters `liveness.py`; callers pass their own clock |
| CONCERN | Risk / History | `attempt_generation` None comparison crashes the health guard on legacy sessions | Moot — the durable field is deferred entirely (see below); no generation comparison lands this sweep | If revived later: default 0, coalesce `(x or 0)`, None-case test |
| CONCERN | Scope & Value | `attempt_generation` durable field couples this hygiene sweep to shifting #1979/#1927 ground | Open Question 2 resolved toward DEFER: land pure predicate-consolidation now; defer the durable field + migration to #1979/#1927 | Scope, Update System, Race Conditions, No-Gos, Success Criteria all updated for the deferral |
| CONCERN | Risk & Robustness | Strict-freshness + degraded regen can self-lock all merges with no break-glass | T1.3 sequences `--strict-freshness` strictly after a committed `runs>=2` artifact; regen fails loudly (no degraded write); strict refusal honors `data/merge_authorized_{N}` break-glass | Risk 3 rewritten with both guards |
| CONCERN | History & Consistency | Problem (3) framing blamed the already-loud refresh script; the silent surface is the persisted artifact | Problem (3) + T1.3 reworded to target the persisted artifact (no `degraded`/`runs`/provenance the gate reads later), not the refresh script's exit code | Success criterion asserts the envelope fields the gate consumes |

### Cycle 2 (2026-07-10, war room re-run) — Verdict: NEEDS REVISION (2 blockers) — all resolved

| Severity | Critic | Finding | Addressed By |
|----------|--------|---------|--------------|
| BLOCKER | Risk & Robustness | `has_started(entry)` field-set is ambiguous: the three predicates use different field sets, so a shared leaf silently loses/gains a signal for one caller; #1962/#1917 tests won't catch the shift | T1.2 rewritten field-set-precise: reuse `derive_sdk_ever_output` for session_health (unchanged); new `has_demonstrable_activity(entry, *, freshness_window=None)` reads ONLY `{turn_count, last_tool_use_at}` for the two forks (crash passes `None`/presence-only, stall passes `IDLE_SUSPECT_SECS`). B1 guard forbids crash/stall gaining log_path/uuid/stdout presence; init-only-reads-False test for both forks + stale-`last_tool_use_at` tradeoff test (crash sees progress, stall does not) |
| BLOCKER | History & Consistency | `reflection_register.py` is single-reflection-shaped end to end (`REFLECTION_NAME="crash-recovery"` hardcoded); no task scopes the generalization needed to register a second reflection; Prior Art omits #1539 | Subtask 3a scopes `register_reflection(project_dir, *, name, callable_path, description, cadence, priority)` through existing `_append_entry`/`_resolve_target`/`_this_machine_owns_valor`, `_has_entry(text, name)` takes the name, `register_crash_recovery` becomes a thin wrapper; then register `test-baseline-refresh`. #1539 added to Prior Art |
| CONCERN | Risk / History | Prerequisites table (`grep -qi closed`, would block Task 2 indefinitely) contradicts Task 2's proceed-while-avoiding Gate; issue-closed ≠ fix-merged | Prerequisites row deleted; Task 2's Gate is the single source of truth (avoid the guard regardless of #1979 state); documented the merged-PR evidence check (`closedByPullRequestsReferences[].state == MERGED`) for any future use |
| CONCERN | Risk & Robustness | Task 5 bundles T1.4 + T1.5 (may balloon) + T1.8 with no split point; Risk 1 covers T1.5 only | Task 5 split into subtasks 5a (T1.4, no shim), 5b (T1.8), 5c (T1.5 triage); 5a+5b have their own commit/PR checkpoint and ship first if 5c stalls; Risk 1 updated |
| CONCERN | Scope & Value | OQ1 excludes `scripts/` but this sweep touches four `scripts/` files → new envelope/degraded code ships uncovered by the exact lint | The four touched `scripts/` files adopt S110/S112 now; OQ1 wording narrowed to "scripts/ files NOT touched adopt when next touched"; T1.5 scope + Update System updated |
| CONCERN | Scope & Value | T1.4 list-subclass shim contradicts Agent Integration's same-PR caller migration | Shim dropped; `find_affected` returns the tuple outright, all in-repo callers migrate in the same PR |
| NIT | Scope / History | Residual "attempt scoping" labels after the deferral (Task 2 title, role line, Documentation bullet) | Reworded to "SessionEvidence leaf/predicate consolidation" at all three sites |
| NIT | Structural | Verification "Predicate forks gone" row is incoherent (`grep -rc ... | grep -v :0` can never contain "def") | Row renamed "Leaf read unified"; asserts `has_demonstrable_activity` delegation present in both forks (>=1 each), not removal |

**Open Questions resolved at revision:**
1. **T1.5 scope** — scoped to `agent/ bridge/ tools/ worker/ monitoring/` (not repo-wide) PLUS the four `scripts/` files this sweep touches/creates (`_baseline_common.py`, `baseline_gate.py`, `refresh_test_baseline.py`, `update/reflection_register.py`); keeps the initial triage bounded while covering the new envelope/degraded-write code. `scripts/` files NOT touched by this sweep adopt S110/S112 when next touched.
2. **`attempt_generation` naming** — DEFERRED (see critique table); no durable field this sweep.
3. **Strict-freshness default** — warn-by-default everywhere, `--strict-freshness` only at `/do-merge` (per the program plan), sequenced after the first `runs>=2` refresh.
