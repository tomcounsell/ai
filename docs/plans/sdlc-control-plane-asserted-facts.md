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

[skeleton]

## Data Flow

[skeleton]

## Architectural Impact

[skeleton]

## Appetite

[skeleton]

## Prerequisites

[skeleton]

## Solution

### Key Elements

[skeleton]

### Flow

[skeleton]

### Technical Approach

[skeleton]

## Failure Path Test Strategy

[skeleton]

## Test Impact

- [ ] `tests/unit/test_verification_parser.py::test_unknown_expectation_returns_false` — REPLACE: currently asserts the silent `False` fall-through as *intended* behavior. It must assert a distinct malformed-expectation outcome instead.
- [ ] `tests/unit/test_sdlc_router.py` — UPDATE: guard and routing-table assertions for G3, G8, and row 2b change shape as those predicates gain ground-truth reads.
- [ ] `tests/unit/test_validate_build.py` — UPDATE: the `runner_agreement.md` parity fixture gains a timeout row, which the two runners currently disagree on.

## Rabbit Holes

[skeleton]

## Risks

[skeleton]

## Race Conditions

[skeleton]

## No-Gos (Out of Scope)

[skeleton]

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
