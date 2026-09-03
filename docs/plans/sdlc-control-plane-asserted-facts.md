---
status: Planning
type: bug
appetite: Large
owner: Valor Engels
created: 2026-09-03
tracking: https://github.com/tomcounsell/ai/issues/3065
last_comment_id: 5521519215
---

# SDLC Control Plane: Route on Read Facts, Not Asserted Facts (Residual)

## Problem

Issue #3065 consolidated 51 self-filed pipeline issues under one property:

> A fact the pipeline routes on must be readable from ground truth at the moment it is used,
> or it must not be routed on.

The 2026-09-02/03 seven-lane upvote batch exercised the control plane hard enough to fix three
of those defects on `main` during the run and to leave a precise, first-hand record of what
survived. **This plan scopes only the survivors.** The three landed hotfixes are out of scope
and are treated as the baseline this work builds on.

What survived is not 51 problems, and it is not six unrelated ones. It is four clusters, each of
which is the consolidation property pointed at a different substrate.

### Cluster A: the router decides from a subset of the state it can read

Four separate router defects share one mechanism: **a decision is computed from part of the
readable state, and the omitted part would have changed the answer.**

- `guard_g3_pr_lock` validates the skill a caller *proposed*, never the skill the routing table
  actually *returns* (`agent/sdlc_router.py:469-488`; guards run at `:2246-2248`, the table at
  `:2250-2255`, and nothing re-validates in between). The PR lock therefore constrains a
  suggestion but not a decision. `agent/session_runner/runner.py:1408` passes no context at all,
  so on that path the guard's input is permanently empty.
- G3's redirect ladder (`:501-509`) has exactly three arms and no `/do-docs` arm, so a lane with
  REVIEW complete, APPROVED, and DOCS pending falls to the `else` and is sent back to
  `/do-pr-review` it has already passed.
- Row 2b (`_rule_critique_verdict_stale`, `:1568-1592`) decides a critique verdict is stale from
  a bare timestamp comparison and reads neither `pr_number`, nor `REVIEW`, nor `DOCS`, nor any
  review verdict. It sits at table position 3; `/do-docs` is row 9, fifteen positions later. On a
  shipped lane every guard steps aside and 2b preempts DOCS unconditionally.
- G8's artifact probe derives a branch name from a recorded lane slug that no code path can
  correct once wrong (`tools/lane_identity.py:37-39` concedes this), and
  `_check_branch_pushed` (`tools/sdlc_next_skill.py:181-198`) cannot tell a wrong-but-present
  slug from a genuinely unpushed branch. The two states get the same answer and the same
  consequence: `/do-patch` force-dispatched on an open, approved, CI-green PR.

The observed cost is not a wrong dispatch, it is a **wedge**. Both #2771 and #2334 converged on
`same_stage_dispatch_count == 2`, one dispatch from a G4 oscillation hard-stop, while their real
state was APPROVED-ready. A no-op stage records no new verdict, so the stale fact that caused the
loop survives to cause the next iteration. The loop is self-sustaining precisely because the
thing that would break it is the thing that keeps failing to happen.

### Cluster B: the verification runner cannot say "I could not evaluate this"

`evaluate_expectation` (`agent/verification_parser.py:304-384`) supports six expectation forms
and returns `False` for everything else at `:384`. A timeout also returns `passed=False`
(`:423-432`), rendered `[FAIL]` (`:499`). A trailing-prose command cell is executed verbatim
under `shell=True` (`:286-288`). A non-check table whose second column happens to be named
`Command` has its *third* column executed as a shell command (`_is_check_table_header:178-183`),
with no diagnostic emitted.

Every one of these produces the same output token: **FAIL**. A gate that reports red when it
means "I did not understand the question" is asserting a fact it never read, and the supervisor
batch hand-verified every such "failure" as actually passing. The second runner
(`scripts/validate_build.py`) disagrees on the same event, calling it `SKIP` at a 30s bound where
the canonical runner calls it `FAIL` at 120s.

### Cluster C: the merge predicate cannot see a gate the plan declared

On 2026-09-03 (#3080) an owner ruling written in plan prose — "FAIL and UNRESOLVED hold the PR at
REVIEW" — was merged past, because the predicate checks a recorded APPROVED verdict and nothing
else. `tools/merge_predicate.py` contains zero occurrences of `plan` or `docs/plans`. This is the
consolidation property stated in the issue's own terms: the fact that governed was not the fact
routed on. Note that it composes with Cluster B — the ruling was *about* verification outcomes,
which are exactly what Cluster B is making trustworthy.

### Cluster D: a blocked decision carries no evidence

The batch reported a `NO_RULE` block on a lane with "CRITIQUE APPROVED+completed, BUILD
`in_progress`, no PR". That state is owned by row 5 (`_rule_branch_exists_no_pr:1320-1325`) and
has been since `c1e991972`; the report cannot be true as written. It could not be checked against
ground truth because **a `NO_RULE` payload does not carry the `stage_states` and `meta` it decided
on**. A control plane that consolidates 51 issues about routing on unread facts publishes its own
most important verdict as an unread fact.

**Current behavior:** shipped lanes get routed backward into plan stages and wedge one dispatch
short of a hard stop; gates report red when they mean "unparseable"; plan-level rulings are
invisible at merge; and the router's own refusals cannot be diagnosed without reproducing them.

**Desired outcome:** every one of those decisions reads the state that governs it, at the moment
it is used, and says so in its output. Where a fact genuinely cannot be read, the decision fails
closed with a named diagnostic rather than guessing.

## Freshness Check

**Baseline commit:** `00a3d93ca` (code read); re-confirmed against `c5150256b` at plan time.
**Issue filed at:** 2026-08-31; last comment `5521519215` at 2026-09-03T06:28:01Z.
**Disposition:** **Minor drift** — one open finding's premise is falsified (see below); everything
else holds verbatim.

Every `file:line` in this plan was re-read at `00a3d93ca`, and four behaviors were *executed*
against `main` rather than inferred. `git log 00a3d93ca..origin/main` over
`agent/sdlc_router.py`, `agent/verification_parser.py`, `tools/lane_identity.py`,
`tools/sdlc_next_skill.py`, `tools/sdlc_session_ensure.py`, `tools/merge_predicate.py`,
`scripts/validate_build.py`, and `models/session_lifecycle.py` returns **no commits**, so the
read baseline is still current.

**The three in-batch hotfixes, verified present:**

| Commit | Subject | Disposition |
|---|---|---|
| `bfa4a6f7d` | critique cycle cap made real (durable `_revision_round_count`, G2 before G1) | Already fixed — out of scope |
| `d9cf29dd6` | crashed PLAN routes instead of wedging at NO_RULE | Already fixed — out of scope |
| `3c689f211` | row 8g re-dispatches a `/do-patch` that died before completing | Already fixed — out of scope |

**Falsified premise (drift):** open finding 4 claims "BUILD `in_progress` with no PR appears to
have no owning row". `_rule_branch_exists_no_pr` (row 5, `agent/sdlc_router.py:1320-1325`) returns
`build_status == STATUS_IN_PROGRESS or context["branch_exists"] is True` after an early
`if meta.get("pr_number"): return False`, and rows 4a/4b/4c step aside on that state because they
require BUILD in `(None, pending, ready)`. Row 5 is reached in table order and dispatches
`/do-build`. The clause dates to `c1e991972`, so unlike PLAN and PATCH there is no crashed-BUILD
gap. **The residual is therefore Cluster D (evidence in the blocked payload), not a new routing
row.** This plan does not add one.

**Closed-set members re-checked — closed as consolidated, never fixed:**

- **#2791** (`prints \`N\`` false-FAILs): the *named* mechanism is genuinely gone —
  `scripts/validate_build.py:243-245` now delegates to `evaluate_expectation` (converged in
  `05a444b1b`, #2871) — but the symptom reproduces on `main` today, because `prints` was never
  added to the grammar.
- **#3022** (non-check tables executed, wrong column read): entirely unfixed. Reproduced on `main`
  with the issue's exact table shape; the "Observed stdout" column is executed as a shell command
  and no `SkippedTable` diagnostic is emitted. Per-block table *scoping* (`_iter_pipe_blocks:156`)
  was fixed for #2836 and is correct; classification and column selection were not in that lane's
  scope.
- **#2901** (120s bound, BRE alternation, narrow vocabulary): unfixed, all three parts reproduce.

**Overlapping active plans:**

- `docs/plans/wave4-hooks-guards-gates.md` (tracking #2736, `covers: [2779, 2736, 3021, 2715]`,
  status Planning, four critique rounds). It targets **PreToolUse Bash hooks** — a different
  substrate from this plan's router rows, verification runner, and merge predicate. Two real
  contact points: its task 7 pins BUILD to a Task-capable agent type (#2715, in this issue's
  closed set), and it edits `.claude/skills-global/do-sdlc/SKILL.md`, which this plan also
  touches. Both are called out under Risks; neither is a blocker. **This plan does not touch
  `.claude/hooks/validators/`.**
- `session/wave5-verification-runner-grammar` exists as a branch but carries no commits ahead of
  `main` and its plan (`sdlc-3020`) is archived complete. Not an active lane; the name is
  misleading and the verification-runner ground is unclaimed.

## Prior Art

- **#2836 / `docs/archive/plans-completed/verification-runner-convergence.md`** (merged
  `05a444b1b`) — collapsed two expectation evaluators into one and fixed per-pipe-block table
  scoping. **This is the most important piece of prior art in the plan**, because it deliberately
  drew its boundary just short of the residual: its spike-5 found "**68** Expected cells across
  **9** active plans use grammar *neither* evaluator recognizes (`prints \`0\``, `output == N`,
  `> 0`, `ok`, `exit 0`); all 68 already FAIL the canonical runner today", and concluded: "Do
  **not** add grammar to `evaluate_expectation` — that is #2791's job, not this lane's." #2791 was
  then closed into #3065 without being done. This plan is where that debt lands.
- **PR #2792 / #2718 / #2735 / #2793** — closed the wrong-lane-slug class via a different cause.
  #2816 recurred **one day later**. A patched instance, not a fixed root.
- **`e50eba258`** — added adoption rung 2 (match the lane PR's head SHA against a full
  `git ls-remote --heads origin` listing). The right idea, wired at the wrong time: it runs only
  during *healing*, only when the recorded slug is *empty*.
- **#1668 / row 2c, and row 8g (`3c689f211`)** — the crashed-stage dead-end pattern for CRITIQUE
  and PATCH. Precedent for how this repo shapes a "started, did not finish" row, and evidence that
  the pattern is applied one stage at a time as each is discovered.
- **#1639** — established row 2b's marker-agnosticism deliberately, to escape a CRITIQUE
  `in_progress` dead end. Its docstring (`:1573-1574`) states the intent. Any change to 2b must
  preserve that escape; this plan constrains 2b from *outside* rather than editing its predicate.
- **`docs/archive/plans-completed/sdlc-router-rereview-crash-and-row3-pr-guard.md:38-39`** — the
  G3 proposal-only hole is already written down, in an archived completed plan, unfixed.
- **#2871 / `05a444b1b`** — the delegation that removed `validate_build.py`'s private evaluator.
  The two runners still disagree on timeout disposition and bound, which that lane did not cover.

### Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #2792 (#2718, #2735, #2793) | Fixed a wrong-recorded-slug lane by removing one cause of the bad mint | Treated the mint as the bug. The actual invariant — a recorded slug is authoritative forever, with no repair path — was untouched, so #2816 reproduced the next day through a different cause. |
| `e50eba258` (adoption rung 2) | Added PR-head-SHA matching to the adoption ladder | Correct mechanism, unreachable placement. The ladder runs only with `allow_heal=True` **and** an empty slug, so it can prevent a wrong slug but can never repair one — which is the state every observed incident was in. |
| #2836 / `05a444b1b` | Converged two expectation evaluators onto one | Convergence made both runners wrong in the *same* way instead of two ways. It explicitly deferred the grammar gap to #2791, which was then closed as "consolidated" rather than fixed, so the 68 known-unparseable cells it measured are still failing today. |
| Row 2c (#1668), row 8g (`3c689f211`), crashed-PLAN (`d9cf29dd6`) | Added a crashed-stage row for CRITIQUE, then PATCH, then PLAN | Each is correct and each was written only after that specific stage wedged a lane in production. The pattern is diagnosed per-stage from incidents rather than derived once from the stage model, so the next uncovered stage is found the same expensive way. |
| #1639 | Made row 2b marker-agnostic to escape a CRITIQUE dead end | Solved the dead end by removing 2b's ability to see *any* marker, including the ones (PR open, REVIEW approved, DOCS pending) that make its verdict irrelevant. The escape was bought with blindness. |

**Root cause pattern:** every prior fix repaired the *instance* the incident produced and left the
*invariant* that generated it. The invariant in each row above is the same one #3065 names: a
value minted or recorded earlier is trusted at use time in preference to state that is one command
away. This plan's tasks are written to change invariants — where a decision must consult a fact,
make the fact readable at the decision point and make the unreadable case explicit — rather than
to patch the six reported instances.

## Research

No external research required. This work is entirely internal to this repository's control plane:
no new library, service, API, or ecosystem pattern is involved, and the substrate (Popoto, Redis,
`gh`, `git`) is already in use throughout the touched modules. The behavioral evidence that would
normally come from external sources came instead from **executing the current code against `main`**
(four verification-runner behaviors and one `session-ensure` failure-rate measurement), which is a
stronger source than documentation for a question about what this repo's code actually does.

## Spike Results

Five spikes ran before this plan was written. Four were code-reads across
`agent/sdlc_router.py`, `agent/verification_parser.py`, `tools/lane_identity.py`,
`tools/sdlc_next_skill.py`, `tools/sdlc_session_ensure.py`, `tools/merge_predicate.py`, and
`scripts/validate_build.py`; the fifth executed the current code against `main`.

### spike-1: Is there a routing-table row that owns BUILD `in_progress` with no PR?

- **Assumption:** "Finding 4 is a routing gap needing a crashed-BUILD row."
- **Method:** code-read — enumerate every table row whose predicate reads `stage_states["BUILD"]`.
- **Finding:** **Falsified.** Row 5 (`_rule_branch_exists_no_pr:1320-1325`) owns it unconditionally
  and has since `c1e991972`. Rows 4a/4b/4c step aside because they require BUILD in
  `(None, pending, ready)`.
- **Confidence:** high.
- **Impact on plan:** removed a whole task. The residual is that `NO_RULE` carries no evidence, so
  a misreported state could not be checked. Redirected into the Cluster D observability task.

### spike-2: Does G3 constrain the skill the routing table returns?

- **Assumption:** "G3 is a lock on plan-stage dispatch whenever a PR is open."
- **Method:** code-read — trace `decide_next_dispatch` control flow end to end.
- **Finding:** **Confirmed, and narrower than the field report.** Guards complete at `:2246-2248`
  *before* the table runs at `:2250-2255`, and no code re-applies a guard to the selected row.
  G3's only inputs are `context["proposed_skill"]` and `meta["last_dispatched_skill"]`. The report's
  wording ("only evaluates volunteered skills") is imprecise: the `last in plan_family` arm does fire
  without a proposal. The true hole is **PR open + last dispatch was a PR-stage skill + no
  `proposed_skill`**. `agent/session_runner/runner.py:1408` passes no context at all.
- **Confidence:** high.
- **Impact on plan:** this is the keystone. Reconciling guards against the *selected* dispatch fixes
  finding 6a directly and renders finding 7 harmless without editing row 2b's predicate — which
  matters, because #1639 made 2b marker-agnostic on purpose.

### spike-3: Can a wrong recorded lane slug be repaired?

- **Assumption:** "Adoption rung 2 (`e50eba258`) already handles the wrong-slug case."
- **Method:** code-read — `resolve_lane_slug`, `adopt_lane_slug`, `_record_slug_if_empty`,
  `_adopt_from_pr`, and `docs/features/sdlc-lane-identity.md`.
- **Finding:** **No repair path exists.** Rung 1 returns the recorded slug unconditionally
  (`tools/lane_identity.py:535-538`), `allow_heal` defaults to `False`, and both write paths are
  no-overwrite. The module docstring concedes it at `:37-39`. Rung 2 is the right mechanism but runs
  only while healing an *empty* slug, so it prevents the bad state and cannot exit it.
- **Confidence:** high.
- **Impact on plan:** the fix is not a better mint. It is (a) verifying the recorded slug against
  ground truth at the point a fail-closed decision depends on it, and (b) permitting a
  contradiction-driven repair. Both are read-facts changes.

### spike-4: How large is the verification grammar gap, and was it ever measured?

- **Assumption:** "The unparseable-expectation gap is anecdotal."
- **Method:** code-read of `evaluate_expectation` plus the archived #2836 plan.
- **Finding:** **Already measured, by the lane that chose not to fix it.** #2836's spike-5 counted
  **68** Expected cells across **9** active plans using grammar neither evaluator recognized, and
  recorded the decision to defer them to #2791. #2791 was closed as consolidated into #3065.
- **Confidence:** high.
- **Impact on plan:** the corpus for the grammar task already exists and is re-derivable by the same
  method, so the task has a measurable, non-invented acceptance target rather than a guessed
  vocabulary.

### spike-5: Do the reported runner and `session-ensure` defects reproduce on `main` today?

- **Assumption:** "The field reports describe current behavior."
- **Method:** prototype — execute the current code against `main`.
- **Finding:** **All reproduce.** `evaluate_expectation` returns `False` for `` prints `0` ``
  (stdout `0`, exit 0), `>= 1` (stdout `5`), `== 0` (stdout `0`), and `empty output` (stdout empty).
  A cell `` `echo hi` — this checks greeting `` yields the shell string
  ``echo hi` — this checks greeting``. The #3022 table shape (`| Command | Observed stdout | Observed exit |`)
  is classified as a check table and its "Observed stdout" column is executed, with an empty
  `skipped` list. Six consecutive `session-ensure` calls on a duplicated-row lane produced four
  `RUN_BIND_FAILED / post-save readback mismatch` results, consistent with a two-row coin flip.
- **Confidence:** high.
- **Impact on plan:** every fix has a demonstrated-red starting point, satisfying the
  demonstrated-red requirement (#2658) with observed behavior rather than a constructed one.

## Data Flow

The two flows this plan changes, traced end to end.

**Flow 1 — a routing decision.**

1. **Entry point**: a supervisor calls `sdlc-tool next-skill --issue-number N --run-id R`
   (`tools/sdlc_next_skill.py`), or `agent/session_runner/runner.py:1408` calls
   `decide_next_dispatch` in-process.
2. **Fact gathering** (`tools/sdlc_stage_query.py`): stage markers, verdicts, and plan-doc
   frontmatter are read into `stage_states` and `meta`. `meta["plan_exists"]`,
   `revision_applied`, and `revision_applied_at` enter here.
3. **Context gathering** (`tools/sdlc_next_skill.py:267-268, 323-361, 476-493`):
   `resolve_lane_slug` → `lane_branch_name` → `_check_branch_pushed` (`git ls-remote`) produces
   `stage_artifacts_verified`, `unverified_stage`, and `branch_exists`. **This is where a wrong
   recorded slug becomes a wrong fact**, and where the CLI path and the in-process path diverge:
   the runner supplies none of it.
4. **Guards** (`agent/sdlc_router.py:2246-2248`): `evaluate_guards` returns the first non-`None`.
   G8 consumes step 3's flags; G3 consumes `proposed_skill` and `last_dispatched_skill`.
5. **Routing table** (`:2250-2255`): first matching predicate wins; the loop breaks on `primary`.
   **No step re-validates `primary` against the guards.** This gap is the Cluster A keystone.
6. **Output**: a `Dispatch`, `Blocked`, or `Terminal`. `Blocked(NO_RULE)` carries a reason string
   and no decision inputs.
7. **Persistence**: none. The caller must separately call `sdlc-tool dispatch record`
   (`tools/sdlc_dispatch.py:75-153`) to append to `_sdlc_dispatches`. Nothing enforces it; skipping
   it re-derives the same row next call until G4 caps the lane for oscillating.

**Flow 2 — a verification check.**

1. **Entry point**: `/do-build` or `scripts/validate_build.py` reads a plan's `## Verification`
   section.
2. **Table selection** (`agent/verification_parser.py:156, 178-183`): pipe blocks are scoped
   per-block (correct, #2836), then classified by `_is_check_table_header` — `any` of the first
   three column names equal to `Command`. **A non-check table passes this test.**
3. **Row parsing** (`:286-288`): positional — `cells[0]` name, `cells[1]` command with backticks
   stripped, `cells[2]` expectation. **Trailing prose survives into the command.**
4. **Execution** (`run_checks:387-432`): `subprocess` under `shell=True`, 120s bound; timeout and
   any exception both yield `passed=False`.
5. **Evaluation** (`evaluate_expectation:304-384`): six forms, else `False`.
6. **Output** (`format_results:499`): `passed` renders as `[PASS]`/`[FAIL]`. **Steps 2-5 have four
   distinct ways to produce `[FAIL]` without a check having actually failed.**
7. **Consumption**: the build gate reads the aggregate. `tools/merge_predicate.py` reads **none of
   it** — that is Cluster C.

## Architectural Impact

- **New dependencies**: none. No new import, service, or package.
- **Interface changes**: three, all additive-then-migrated:
  - `decide_next_dispatch` gains a post-selection reconciliation step. Its signature is unchanged;
    its return value can now be a `Dispatch` whose `row_id` records a guard redirect applied to a
    table selection.
  - The check-result type gains a third outcome. This is a **breaking change to a boolean**, and is
    deliberately not additive: leaving `passed: bool` alongside a new status field would recreate the
    ambiguity this plan exists to remove. Every reader is migrated in the same PR.
  - `tools/merge_predicate.py` gains one input group (verification outcomes).
- **Coupling**: net *decreased* in the router. Today `tools/sdlc_next_skill.py` owns branch-truth
  derivation and hands the router pre-chewed booleans, while `agent/session_runner/runner.py` hands
  it nothing — the same decision reaches two different answers depending on the caller. Moving
  branch truth behind one resolver that both callers use removes that divergence. Coupling
  *increases* by one edge in the merge predicate, which is the point of Cluster C.
- **Data ownership**: the lane slug's owner changes from "whoever recorded it first, forever" to
  "the ledger, subject to correction by demonstrated branch truth". This is the single most
  consequential change in the plan and is why the repair path is narrow and evidence-gated.
- **Reversibility**: high for Clusters A and D (pure decision logic, no persisted state shape
  changes). Medium for Cluster B (the check-result type is consumed in several places, though all
  in-repo). Low-to-medium for the slug repair, because it writes a corrected slug — the mitigation
  is that it only ever writes a value proven by `git ls-remote` against the PR head, and it records
  the evidence.

## Appetite

**Size:** Large

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 2-3 (one on the Cluster C scope ruling, one on whether to split the lane, one at
  merge)
- Review rounds: 2+ (the router changes and the runner changes want separate review attention)

This is Large honestly rather than by padding: it changes decision logic in the component every
other lane routes through, and a regression here wedges every concurrent lane rather than one
feature. Most of the appetite is verification and blast-radius care, not implementation volume —
several tasks are single-digit line changes whose *proof* is the expensive part.

**The lane is deliberately splittable.** Clusters A and D touch `agent/sdlc_router.py`,
`tools/sdlc_next_skill.py`, and `tools/lane_identity.py`. Cluster B touches
`agent/verification_parser.py` and `scripts/validate_build.py`. The two file sets are disjoint, so
this can ship as two sequential PRs from one lane if review load warrants it. Cluster C depends on
Cluster B and must follow it. See Open Questions.

## Prerequisites

No external prerequisites. This work needs no new secret, service, dependency, or network access,
touches no Popoto model schema (so no `scripts/update/migrations.py` entry is required), and runs
entirely against this repo's existing toolchain.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Repo venv on the pinned interpreter | `python -m tools.doctor` | `scripts/pytest-clean.sh` aborts on an off-pin venv |
| `gh` authenticated | `gh auth status` | PR-state reads in the merge predicate and branch-truth tests |
| `git ls-remote` reaches origin | `git ls-remote --heads origin main` | Branch-truth resolution is a live remote read |

## Solution

### Key Elements

- **Post-selection reconciliation** (`agent/sdlc_router.py`): after the routing table selects a row,
  the guards are evaluated once more against **the selected skill**. A guard that would have vetoed
  the same skill as a proposal now vetoes it as a decision. Bounded to a single pass: if the
  redirect target is itself vetoed, the router returns `Blocked` with both verdicts as evidence
  rather than iterating.
- **A complete G3 redirect ladder**: the missing `/do-docs` arm, plus arm 1 gated on a recorded
  APPROVED verdict rather than on the REVIEW marker alone, bringing it into agreement with rows 9
  and 10.
- **Branch truth as a resolver, not a derived string**: one function answers "which pushed branch
  holds this lane's work?" from the PR head SHA via `tools/pr_head_resolver.py::resolve_pr_head_sha`
  and a `git ls-remote --heads origin` listing. Both the CLI and in-process callers use it, so they
  stop disagreeing. It returns a three-valued answer — *found*, *absent*, *indeterminate* — and the
  artifact probe may only fail closed on *absent*.
- **A repairable lane slug**: when branch truth uniquely contradicts the recorded slug, the ledger
  is corrected and the correction is recorded with its evidence. This is the one write this plan
  adds to lane identity, and it is gated on a unique `git ls-remote` match against the PR head.
- **A tri-state check outcome**: `PASS` / `FAIL` / `UNEVALUATED`. Timeouts, unparseable
  expectations, and malformed command cells become `UNEVALUATED` with a reason, and can never
  render as `[FAIL]`. `UNEVALUATED` is **blocking** — it does not pass — but it is reported as a
  distinct, nameable condition rather than as a code failure.
- **An expectation grammar that covers the measured corpus**: the forms #2836 counted
  (`prints \`N\``, `== N`, `>= N`, `> N`, `empty output`, `exit N`, and the existing six), with the
  tri-state catching whatever still falls through instead of silently reddening it.
- **Structural table classification and command-cell extraction**: a check table is identified by
  its column *contract*, not by the presence of the word `Command` in any of three positions; a
  command cell yields its first backticked span, not the whole cell.
- **One runner disposition**: `scripts/validate_build.py` and `agent/verification_parser.py` agree
  on the timeout bound and on what a timeout means, enforced by the existing parity fixture.
- **Verification outcomes visible to the merge predicate** (Cluster C, minimal form): the predicate
  gains the plan's verification outcome as an input, so a `FAIL` or `UNEVALUATED` row holds the PR
  the way the #3080 ruling said it should. **No general plan-declared-gate DSL** — see Technical
  Approach for why.
- **Decisions that carry their evidence**: `Blocked(NO_RULE)` carries the `stage_states` and `meta`
  it decided on; a dispatch decision reports when the previous decision was never recorded.
- **A `session-ensure` that reads back what it wrote**: readback by primary key, and a candidate
  that knows whether it was minted or adopted, so the cleanup path can never release a lease this
  call did not create.

### Flow

Router decision, after this plan:

**`next-skill` called** → gather stage/verdict facts → **resolve branch truth** (PR head → pushed
branch; *found* / *absent* / *indeterminate*) → guards → routing table selects a row →
**reconcile: re-evaluate guards against the selected skill** → [vetoed?] → **redirect via the
complete ladder** → reconcile the redirect once → [still vetoed?] → **`Blocked` with both verdicts
as evidence** → **decision emitted with its inputs attached**

Verification check, after this plan:

**Plan `## Verification` section** → per-block scoping → **classify by column contract** →
[not a check table?] → **`SkippedTable` diagnostic** → **extract first backticked span as the
command** → execute under one agreed bound → **evaluate to PASS / FAIL / UNEVALUATED** → render
each distinctly → **merge predicate reads the aggregate**

### Technical Approach

**The reconciliation step is the keystone, and it must not become a loop.** The guard list is
already a plain `list[Callable[[dict, dict, dict], Dispatch | Blocked | Terminal | None]]`
(`agent/sdlc_router.py:1058`), and guards already step aside by returning `None`, so re-running them
with `context["proposed_skill"]` set to the table's selection needs no new abstraction. The design
constraint is termination: reconciliation runs **exactly once** on the table's selection, and at
most once more on the resulting redirect. A second veto is a `Blocked` carrying both the selected
row and the vetoing guard, not a third iteration. This is deliberately fail-closed: a lane that
cannot produce a self-consistent decision should stop with evidence rather than oscillate toward a
G4 cap, which is precisely the failure mode observed on #2771 and #2334.

**Row 2b is constrained from outside, never edited.** #1639 made it marker-agnostic on purpose, to
escape a CRITIQUE `in_progress` dead end, and its docstring says so. Narrowing its predicate would
re-open that dead end. Reconciliation lets G3 veto 2b's output on a shipped lane while leaving 2b
free to fire on the lane shape it exists for. This is the difference between fixing the invariant
and patching the instance.

**Branch truth replaces name derivation at the decision point, not everywhere.** The recorded slug
remains the lane's identity for worktrees, branches, and task lists — that is not in question and
changing it would be a far larger blast radius. What changes is narrower: **a decision that fails
closed on "the branch is not pushed" must first establish that it is asking about the right
branch.** `_check_branch_pushed`'s current two-valued answer is the defect, because it collapses
"this name does not exist" into "this lane has no branch". The three-valued resolver keeps them
apart, and `guard_g8_artifact_verification` may only dispatch `/do-patch` on *absent*. On
*indeterminate* it must step aside, because dispatching a stage on an unreadable fact is exactly
what this issue forbids.

**Slug repair is narrow and evidence-gated.** It fires only when the PR head SHA resolves (via
`tools/pr_head_resolver.py::resolve_pr_head_sha`, per CLAUDE.md — never a bare `gh` read, because a
stale `gh` head SHA is what flipped the verdict-staleness gate open in #2895) to **exactly one**
branch in the `git ls-remote --heads origin` listing, and that branch's slug differs from the
recorded one. Zero matches or two-or-more matches leave the record alone, matching `_adopt_from_pr`'s
existing ambiguity discipline. This reuses rung 2's mechanism at read time rather than inventing a
second one.

**The tri-state is a replacement, not an addition.** Keeping `passed: bool` beside a new status
field would let a caller keep asking the ambiguous question, which is how #2871's convergence left
both runners wrong in the same way instead of two ways. `passed` is removed and every reader is
migrated in the same change. `UNEVALUATED` blocks — this is not a softening of the gate. It is the
difference between a gate that says "your code is wrong" and one that says "my grader is wrong",
and the supervisor batch spent real time chasing the first message when the second was true.

**Cluster C is deliberately scoped to verification outcomes, not to a plan-declared-gate DSL.**
The PM asked for this weighed on its merits, so, explicitly: a general mechanism letting plan
frontmatter declare arbitrary machine-readable merge gates is **rejected for this lane**. Three
reasons. First, the plan document is itself an asserted artifact — 12 of the 51 consolidated issues
link a `docs/plans/*.md` that does not exist on disk, and #2870 links `docs/plans/foo.md` — so
building a gate language on top of it adds a new unread-fact surface while claiming to close one.
Second, this repo already carries **two** private plan-document grammars in
`scripts/validate_build.py` plus three hand-rolled frontmatter regexes; #2491 counted 7 hardcoded
duplicates of the pipeline graph, and a fourth grammar would extend exactly the drift the issue
complains about. Third, and decisively, the #3080 incident did not need a general mechanism: the
ruling that was merged past was *about verification-row outcomes*, which Cluster B is already making
machine-readable and trustworthy. Wiring the merge predicate to read that one aggregate closes the
actual incident at a fraction of the surface. If a future ruling genuinely cannot be expressed as a
verification row, that is the moment to reconsider — and it will be reconsidered with an incident to
point at rather than a hypothesis.

**`session-ensure` gets two surgical fixes and is sequenced first**, because it is a hard wedge on
any lane whose session row got duplicated: the readback re-reads the row it wrote by primary key
instead of re-querying an unordered index, and `candidate` carries its provenance so the cleanup
path releases only a lock this call minted. The compare-and-delete in `release_issue_lock` stays
exactly as it is — it is correct, and the bug is that it was being handed an adopted id. The
missing distinction is provenance, not identity.

**Duplicate `AgentSession` rows per `session_id` are not fixed here.** They are the upstream cause
and deserve their own treatment; this plan makes the ensure path correct in their presence, which
is what unwedges lanes today. Recorded in No-Gos with an issue.

## Failure Path Test Strategy

Every gate this plan touches is a gate that was *already* reporting a confident verdict it had not
earned, so #2658's demonstrated-red rule is not a formality here: **no gate change in this plan
counts as done until a test shows it firing red on a case it previously passed.** Spike-5 supplies
the red starting points from observed behavior rather than constructed ones.

### Exception Handling Coverage

- [ ] `agent/verification_parser.py:433-442` — the bare `except Exception` that turns any runner
      failure into `passed=False`. After the tri-state lands it must produce `UNEVALUATED` with the
      exception's reason attached; test asserts the reason is observable, not just the status.
- [ ] `tools/sdlc_next_skill.py:377-387` — `_verify_stage_artifacts` catches
      `TimeoutExpired`/`SubprocessError`/`OSError` and falls open (returns verified). That is the
      correct direction, but it is currently indistinguishable in the output from a genuine
      verification. Test asserts the *indeterminate* case is reported as indeterminate and that G8
      steps aside on it rather than dispatching.
- [ ] `tools/sdlc_session_ensure.py:618` — the readback `except` path releases the lock. Test
      asserts an adopted candidate is **not** released when the readback itself errors.
- [ ] `agent/sdlc_router.py:2260-2263` — rule predicates are wrapped in try/except and a raising
      predicate is silently skipped, while guards at `1083-1086` have no wrapper and propagate.
      The reconciliation step must not change this asymmetry silently; test asserts a raising guard
      during reconciliation surfaces rather than being swallowed into a `NO_RULE`.

### Empty/Invalid Input Handling

- [ ] `evaluate_expectation` with an empty expectation cell, a whitespace-only cell, and `None` —
      each must be `UNEVALUATED`, never `FAIL`, and never `PASS`.
- [ ] A command cell containing no backticked span at all, and one containing two — the first
      yields `UNEVALUATED` (nothing to run); the second takes the first span and records that it
      did.
- [ ] `resolve_lane_slug` / branch truth with: no recorded slug, no PR number, a PR whose head
      resolves to zero branches (merged-and-deleted), and to two-or-more branches. The last two must
      both be *indeterminate*, matching `_adopt_from_pr`'s existing ambiguity discipline, and
      neither may trigger a repair write.
- [ ] `decide_next_dispatch` called with `context=None` and with `context={}` — the
      `agent/session_runner/runner.py:1408` shape. Reconciliation must behave identically to the CLI
      path for the facts it can read, and must not silently no-op just because the caller passed no
      context.

### Error State Rendering

- [ ] `format_results` renders `UNEVALUATED` as its own token with its reason, never as `[FAIL]`.
      Test asserts the string `[FAIL]` does not appear for a timed-out or unparseable row.
- [ ] `Blocked(NO_RULE)` output carries the `stage_states` and `meta` it decided on, and that
      payload survives to the CLI's JSON output where a supervisor can read it. This is the
      finding-4 lesson: the failure to test was a failure to *report*.
- [ ] The merge predicate's refusal on a `FAIL`/`UNEVALUATED` verification row names which row and
      why, rather than returning a bare false.

## Test Impact

- [ ] `tests/unit/test_verification_parser.py::test_unknown_expectation_returns_false` (`:210`) —
      **REPLACE**: it currently asserts the silent `False` fall-through as *intended* behavior, so it
      is a test that pins the bug. It must assert `UNEVALUATED` with a reason instead.
- [ ] `tests/unit/test_verification_parser.py::TestInverseExpectations` (~`:224-388`) — **UPDATE**:
      the six existing forms keep their semantics, but assertions written against a boolean return
      migrate to the tri-state.
- [ ] `tests/unit/test_verification_parser.py::TestPipesInCommands` (~`:397-490`) — **UPDATE**:
      command extraction changes from `strip("\`")` to first-backticked-span, which these cases
      exercise directly.
- [ ] `tests/unit/test_verification_parser.py::TestPerBlockTableScoping` (~`:519-580`) — **UPDATE**:
      scoping is correct and stays; classification changes underneath it, so the fixtures need a
      non-check table added to prove the new `SkippedTable` diagnostic.
- [ ] `tests/fixtures/verification/runner_agreement.md` — **UPDATE**: gains a timeout row and a
      malformed-expectation row. This fixture asserts per-check parity between the two runners and
      currently cannot catch their FAIL-vs-SKIP divergence because it contains neither shape.
- [ ] `tests/unit/test_validate_build.py` — **UPDATE**: `check_verification_table`'s 30s bound and
      `SKIP` disposition converge on the canonical runner's; assertions pinning either change.
- [ ] `tests/unit/test_validate_verification_section.py` — **UPDATE**: section-level assertions that
      count passes/failures gain the third outcome.
- [ ] `tests/unit/test_sdlc_router.py` — **UPDATE**: G3's ladder gains an arm and its arm-1
      condition tightens; the reconciliation step changes which dispatch several existing scenarios
      produce. Tests added by `bfa4a6f7d`, `d9cf29dd6`, and `3c689f211` must all still pass
      unchanged — they are the regression floor for the three in-batch hotfixes.
- [ ] `tests/unit/test_sdlc_verdict.py` — **UPDATE**: verdict-staleness assertions interact with the
      reconciliation path.
- [ ] No existing test covers an em-dash/trailing-prose command cell, `run_checks` timeout handling,
      the #3022 header shape, `session-ensure` readback under duplicate rows, or a `NO_RULE`
      payload's contents. Those are **new** tests, not updates, and each starts from a
      demonstrated-red case established in spike-5.

## Rabbit Holes

- **Rewriting the routing table.** Twenty-plus rows with hand-tuned ordering and an ordering comment
  on each. The reconciliation step exists specifically so that Cluster A is fixed *without*
  reordering or re-deriving rows, and row 2b is constrained from outside for the same reason.
  Touching the ordering invites regressions in lane shapes nobody in this lane has evidence about.
- **Deriving crashed-stage rows once, from the stage model.** Genuinely appealing: rows 2c, 8g and
  the crashed-PLAN fix are three instances of one pattern discovered three times at production
  expense. But the stage model is exactly what #2491 found duplicated 7 times with drift, so a
  general derivation would need the single-source work first. Note it and leave it.
- **Fixing the pipeline-graph duplication (#2491).** Seven hardcoded duplicates, ~30 prose
  restatements, 4 disagreeing tables. It is real, it is in the closed set, and it is a lane of its
  own. This plan imports from `agent/pipeline_graph.py` where it needs the stage model and adds no
  new restatement, which is the most it can honestly do without becoming that lane.
- **A general plan-declared merge-gate DSL.** Rejected on the merits in Technical Approach. The
  tempting version is a small frontmatter key that grows into a fourth plan grammar.
- **Making `session_id` unique.** Correct instinct, wrong lane — it is a session-lifecycle change
  with its own blast radius. Deferred to #3091.
- **Auditing all 51 closed issues for other unfixed members.** Two were found unfixed (#2791,
  #3022) while checking this plan's own scope, which is a reason to suspect more. It is also an
  unbounded excavation that would swallow the lane. The four re-checked here are re-checked because
  the residual findings pointed at them.

## Risks

### Risk 1: Reconciliation changes a routing answer some lane depends on

**Impact:** The router is the component every concurrent lane passes through. A wrong redirect does
not break one feature, it wedges every in-flight lane at once — and the observed failure mode is a
*silent* wedge that looks like slow progress until `same_stage_dispatch_count` hits the G4 cap.
**Mitigation:** reconciliation only ever *withholds* a dispatch the guards would already have
refused as a proposal; it can never invent a dispatch the current code would not produce, because
the redirect targets come from G3's existing ladder. The three hotfix test suites
(`bfa4a6f7d`, `d9cf29dd6`, `3c689f211`) are the regression floor and must pass unchanged. Every new
routing behavior gets a two-pole test: the state that must dispatch, and the state that must not.

### Risk 2: The bounded reconciliation pass turns a soft wedge into a hard block

**Impact:** Failing closed on a second veto converts lanes that today limp toward a G4 cap into
lanes that stop immediately with `Blocked`. If the veto logic is wrong, that is a faster, louder
failure than the status quo.
**Mitigation:** this is the intended trade and it is why the `Blocked` payload must carry both the
selected row and the vetoing guard. A loud stop with evidence is recoverable in one read; the
current silent oscillation cost two lanes a full manual unwedge each. The Cluster D evidence work is
sequenced **before** reconciliation for exactly this reason.

### Risk 3: Slug repair writes a wrong correction

**Impact:** The recorded slug names the worktree, branch, and task list. A wrong repair is worse
than a wrong original, because it moves a lane that was merely mislabelled.
**Mitigation:** the repair fires only on a **unique** `git ls-remote --heads origin` match against a
PR head resolved through `tools/pr_head_resolver.py::resolve_pr_head_sha` (never a bare `gh` read —
#2895 is the incident where a stale `gh` head SHA flipped a staleness gate fail-open). Zero matches
and two-or-more matches both leave the record untouched. The repair records its evidence, so a wrong
one is auditable rather than silent.

### Risk 4: Removing `passed: bool` breaks a reader outside the searched set

**Impact:** A silent `AttributeError` in a gate is exactly the class of failure this plan exists to
remove.
**Mitigation:** removal rather than addition is deliberate (a lingering boolean would preserve the
ambiguity), so the migration must be exhaustive: a repo-wide sweep for the attribute, with the
absence of any remaining reader asserted as an anti-criterion in Verification. Per the sweep
convention, the acceptance is a clean grep, not an enumerated site list.

### Risk 5: `.claude/skills-global/do-sdlc/SKILL.md` is a hot shared file

**Impact:** `wave4-hooks-guards-gates` also edits it, and skills-global files are hardlinked to
`~/.claude/skills/` — a replace-and-rename write breaks the hardlink and leaves the live skill on
pre-edit text.
**Mitigation:** confine edits there to the smallest necessary passage, use `Edit` rather than
whole-file `Write`, and coordinate ordering with the wave4 lane at merge. The PostToolUse relink
hook repairs the hardlink, but the merge conflict is the real hazard, not the hardlink.

### Risk 6: Two disjoint change sets reviewed as one PR

**Impact:** Router decision logic and verification grammar want different reviewer attention; bundled,
both get skimmed.
**Mitigation:** the file sets are disjoint by construction and the lane is written to split into two
sequential PRs. Raised in Open Questions rather than decided unilaterally.

### Risk 7: The verification-runner change is graded by the verification runner

**Impact:** This plan's own `## Verification` table is executed by the code it modifies. A change
that broke the runner could report itself green.
**Mitigation:** the runner's own correctness is asserted by `pytest`, not by this plan's verification
table, and the anti-criteria rows are written to fail if the tri-state is absent. Any row whose
truth depends on the modified evaluator is a row that proves nothing; the Verification section keeps
those to direct `grep`/`pytest` assertions.

## Race Conditions

### Race 1: Branch truth is a live remote read taken mid-push

**Location:** the branch-truth resolver (new), replacing
`tools/sdlc_next_skill.py:181-198, 323-361`.
**Trigger:** a builder pushes the lane branch while `git ls-remote --heads origin` is in flight, or
between the PR-head resolve and the listing.
**Data prerequisite:** the PR head SHA and the remote branch listing must describe the same instant
closely enough that a unique match is meaningful.
**State prerequisite:** the lane has an open PR whose head is pushed.
**Mitigation:** a stale *negative* is the dangerous direction, and it is handled by the three-valued
answer: a listing that does not contain the head yields **indeterminate**, not *absent*, unless the
lane has no PR at all. G8 may only fail closed on *absent*, so a mid-push read causes a deferral,
never a spurious `/do-patch`. Slug repair additionally requires uniqueness, which a partial listing
cannot manufacture.

### Race 2: Two lanes repair the same ledger slug concurrently

**Location:** the slug-repair write in `tools/lane_identity.py`.
**Trigger:** a supervisor and a dev session both call the router for the same issue within the same
window.
**Data prerequisite:** both must have resolved the same unique branch, or the write is not
attempted.
**State prerequisite:** the recorded slug contradicts branch truth.
**Mitigation:** the repair is idempotent by construction — both callers compute the same corrected
value from the same ground truth, so a concurrent double-write converges. The existing
`_record_slug_if_empty` no-overwrite path is *not* reused (it cannot overwrite, which is the bug);
the repair path is separate and evidence-gated, and it must re-read the recorded value immediately
before writing so a repair that another caller already applied becomes a no-op rather than a second
write.

### Race 3: `session-ensure` readback races a concurrent row write

**Location:** `tools/sdlc_session_ensure.py:613-620`.
**Trigger:** a second ensure, or a lifecycle transition, saves the same row between this call's save
and its readback.
**Data prerequisite:** the readback must observe the write this call made, not merely *a* write.
**State prerequisite:** at least one other writer touching the same session.
**Mitigation:** reading back by primary key removes the row-selection race entirely, which is the
whole defect. A genuine value race (another writer overwrote `active_run_id` between save and
readback) remains possible and is correctly a mismatch — but with provenance tracked, the mismatch
path releases only a lock this call minted, so a losing race no longer destroys the winner's lease.

### Race 4: Plan-document writes in the shared `main` checkout

**Location:** `docs/plans/` in the shared checkout.
**Trigger:** a concurrent lane runs `git pull --rebase`, autostashing this lane's uncommitted plan
edits; #2650, reproduced during the 2026-09-02 batch when one lane's unscoped `git add` swept 225
lines of another lane's in-progress plan into three of its own commits.
**Data prerequisite:** none — the hazard is uncommitted state existing across an await.
**State prerequisite:** more than one lane writing plans, which is the normal condition here.
**Mitigation:** operational, and already in force for this plan: it is authored in a dedicated
worktree rather than the shared checkout, staged path-scoped (never `git add -A`), and committed
after every two or three sections so nothing sits dirty across a subagent or critique await.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #3091] **Making `AgentSession` rows unique per `session_id`, or reconciling
  duplicates.** This is the upstream cause of the `session-ensure` wedge; this plan makes the ensure
  path correct *in the presence of* duplicates, which is what unwedges lanes today. Choosing between
  prevent / reconcile / encode is a session-lifecycle decision with its own blast radius, filed with
  full recon.
- [SEPARATE-SLUG #2736] **PreToolUse Bash-hook guards that match text instead of command
  structure.** Owned by the active `wave4-hooks-guards-gates` plan, including its BUILD
  Task-capability gate (#2715). This plan touches no file under `.claude/hooks/validators/`.
- [ORDERED] **Deploying the change to running services.** `./scripts/valor-service.sh restart`
  cycles the bridge, watchdog, and worker, and `/update` propagates a merged ref to every machine.
  Both are gated on the merge landing, and both are the `/do-deploy` and `/update` steps rather than
  this lane's — restarting mid-lane would cycle services other in-flight lanes are using.
- [EXTERNAL] **Repairing the two lanes (#2771, #2334) whose ledgers were hand-corrected during the
  batch.** Both were already unwedged manually with head-SHA evidence and need no further action;
  any remaining ledger archaeology is a judgement call for the operator who performed the repair,
  not a code change.

**This plan does not fully close #3065.** It closes the residual the 2026-09-02/03 batch left open,
which is what it was scoped to. Members of the consolidated set that neither the three in-batch
hotfixes nor this plan address — #2491's pipeline-graph duplication is the largest — remain
un-actioned under the umbrella issue. The implementation PR should therefore use `Refs #3065`, not
`Closes #3065`, and the decision about what closes the umbrella belongs to its owner. Raised in Open
Questions.

## Update System

[skeleton]

## Agent Integration

[skeleton]

## Documentation

- [ ] Update `docs/features/sdlc-lane-identity.md` — the recorded-slug repair path changes.
- [ ] Update `docs/features/machine-readable-dod.md` — the expectation grammar and malformed-row handling change.
- [ ] Create `docs/features/sdlc-router-ground-truth-reads.md` describing the read-facts property and where each router decision now sources its facts.

## Success Criteria

[skeleton]

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Router unit tests pass | `scripts/pytest-clean.sh tests/unit/test_sdlc_router.py -q -n 2` | exit code 0 |

## Step by Step Tasks

[skeleton]

## Open Questions

[skeleton]
