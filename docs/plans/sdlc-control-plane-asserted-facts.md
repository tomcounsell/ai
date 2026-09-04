---
status: Planning
type: bug
appetite: Large
owner: Valor Engels
created: 2026-09-03
tracking: https://github.com/tomcounsell/ai/issues/3065
last_comment_id: 5521519215
revision_applied: true
revision_applied_at: 2026-09-03T15:05:00Z
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

What survived is not 51 problems, and it is not six unrelated ones. It is **four clusters plus one
standalone wedge**, each of which is the consolidation property pointed at a different substrate.
The standalone is `session-ensure` (Cluster E below): it belongs to none of the four decision-making
shapes, but it is sequenced first because it is a hard wedge that blocks every other lane on the
machine, and omitting it from this framing would undercount what ships.

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

### Cluster E: `session-ensure` destroys the lease it was asked to grant

Not a routing defect, and the reason it leads the task order anyway. `AgentSession.session_id` is a
plain `Field()`, not the primary key, so a lane whose row was recreated after a crash has two rows
sharing one id. `tools/sdlc_session_ensure.py:613-620` discards the handle it just saved and
re-queries by `session_id`, taking `[0]` from what Popoto resolves as an unordered Redis `SMEMBERS`
read — a coin flip. Four of six consecutive invocations failed on the observed lane. Worse, the
mismatch cleanup calls `release_issue_lock` with a `candidate` that may have been *adopted* from the
live lock rather than minted by this call, so the compare-and-delete matches by construction and
deletes a lease this call never created. The missing distinction is **provenance, not identity**.
The upstream duplicate-row cause is deferred to #3091; this plan makes the ensure path correct in
its presence, which is what unwedges lanes today.

**Current behavior:** shipped lanes get routed backward into plan stages and wedge one dispatch
short of a hard stop; gates report red when they mean "unparseable"; plan-level rulings are
invisible at merge; the router's own refusals cannot be diagnosed without reproducing them; and a
read-only-intent probe can revoke a live run's lease.

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
- **This plan's own critique round 1 — the defect class reproduced inside the critique of the plan
  that exists to eliminate it.** Round 1's blocker 1 asserted that the raw-Redis anti-criterion's
  `\|` escapes make the ERE match a literal string, so the row could never fire. The claim was
  verified "empirically" against the **raw markdown text** and is true of it — but the row is never
  executed as raw markdown: `parse_verification_table` unescapes `\|` to a bare `|` when it extracts
  the cell. The critic asserted what the shell receives without reading it through the parser that
  produces it, which is precisely "a fact routed on that was not read at the moment it was used".
  Applying the suggested fix would have **regressed** the row into the `MalformedRow` state it had
  already been repaired out of once. Round 2's History & Consistency critic re-ran the parser over
  this file, printed the parsed command, and executed it two-pole against synthetic diffs, and
  accepted the deviation on that executed evidence. Full adjudication under Critique Results.
  Three things this is prior art *for*, all load-bearing for the build:
  - **A verification row's meaning is the parsed command, not the cell text.** Any future
    anti-criterion reasoning must run through `parse_verification_table` before it is believed. This
    is the authoring hazard task 7's documentation bullet and the `PLAN_TEMPLATE.md` alternation
    sample exist to teach, and it is an argument *for* that documentation work rather than against
    the escaping composition.
  - **Whole-file anti-criteria are the wrong shape when the file is already red.** Chasing the
    blocker's valid secondary claim surfaced `tools/lane_identity.py:389` calling
    `POPOTO_REDIS_DB.delete(...)` on a slug-lock key — legitimate (the rule governs Popoto-managed
    *model* keys, not lock keys), but it means a whole-file row is RED on main for a pre-existing
    reason and can never go green. The row is now diff-scoped, judging what the commit adds. Same
    lesson the wave4 lane cites, reached independently here.
  - **A named deviation is cheaper than a silent one.** The falsification was recorded for the
    decider to overrule rather than quietly acted on, and the round-2 critic could then adjudicate it
    on evidence instead of rediscovering it. Deviations from critique findings in this lane's build
    should be recorded the same way.

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

### Risk 8: The #3080 gate only fires on a RECORDED aggregate; nothing enforces that § 4.5 ran

**Impact:** `tools/merge_predicate.py::_check_verification_outcomes` grades a recorded aggregate
against the PR's current head, but a lane where the § 4.5 verification-table runner is simply never
invoked has no aggregate to grade at all — that case is "reported, not enforced" by design (see the
module's own comment at `tools/merge_predicate.py:762-764`). The invocation itself is not a
mechanical trigger anywhere in this codebase; it is prose in `docs/sdlc/do-pr-review.md` telling the
REVIEW stage to run it. A lane that skips or forgets that step merges unimpeded by this gate, which
means the #3080 ruling is machine-readable once graded but not machine-*guaranteed* to be graded.
**Mitigation:** this is a known, accepted open edge, not a defect — fail-closed-on-absence was
deliberately rejected because it has no incident backing it (no observed case of a lane skipping
§ 4.5 to dodge the ruling) and because it would block every lane whose plan predates this mechanism,
none of which ever recorded an aggregate. Closing this edge, if it is ever worth closing, means making
the § 4.5 invocation itself mechanical (a hook or a stage-transition check) rather than tightening
this gate's absence handling.

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

No `/update` script changes required. This work adds no dependency, config file, secret, or
migration, and every change is to Python modules already deployed by the existing sync.

Two propagation notes that are the *existing* update path doing its job, not new work:

- `.claude/skills-global/do-sdlc/SKILL.md` is hardlinked to `~/.claude/skills/` by
  `scripts/update/hardlinks.py`. Editing it means every machine picks the change up on its next
  `/update`. Use `Edit`, not whole-file `Write` — a replace-and-rename breaks the hardlink and
  leaves the live skill on pre-edit text.
- Per the standing rule, `/update` runs after the PR merges so running services move off the old
  SHA. That is the `/do-deploy` / `/update` step, deliberately not this lane's (see No-Gos).

## Agent Integration

No new CLI entry point in `pyproject.toml [project.scripts]`, and no new bridge import. Every
surface this plan changes is already reachable:

- `sdlc-tool next-skill`, `stage-marker`, `verdict`, and `dispatch record` are existing subcommands.
  The `Blocked(NO_RULE)` evidence payload and the unrecorded-dispatch signal ride out through
  `next-skill`'s existing JSON output, which is what a supervisor already parses — that is the whole
  point of putting the evidence there rather than in a log.
- `agent/verification_parser.py` is consumed by `/do-build` and `scripts/validate_build.py`, both
  already wired.
- `tools/merge_predicate.py` is consumed by `/do-merge`, already wired.

The integration risk here is not reachability, it is **contract drift between the two router
callers**: `tools/sdlc_next_skill.py` supplies context and `agent/session_runner/runner.py:1408`
supplies none, so the same lane state can produce two different decisions depending on who asked.
The branch-truth resolver is placed so both callers use it, and an integration test asserts the two
paths agree on a lane state that today they disagree about.

## Documentation

- [ ] Create `docs/features/sdlc-router-decision-reconciliation.md` — the reconciliation step: why a
      guard must see the selected dispatch and not only a proposal, the single-pass bound, and what
      a `Blocked` carrying two verdicts means for a supervisor reading it.
- [ ] Update `docs/features/sdlc-lane-identity.md` — the adoption ladder gains a read-time
      verification and an evidence-gated repair path. Its lines 118-124 already name this failure
      mode as unfixable; that text must be replaced with the new status quo, not annotated.
- [ ] Update `docs/features/machine-readable-dod.md` — the expectation grammar's new forms, the
      `UNEVALUATED` outcome and what it means for a build gate, and the check-table column contract.
      Fold the BRE/ERE escape rule from `:118-139` into the authoring surface rather than leaving it
      referenced by a link.
- [ ] Update `.claude/skills-global/do-plan/PLAN_TEMPLATE.md` §Verification (`:444-484`) — document
      the expanded vocabulary and add a sample anti-criterion row that actually demonstrates the
      alternation escape. The current sample (`:483`) has no alternation, so it never shows the rule
      it exists to teach.
- [ ] Update `docs/features/README.md` index table with the new feature doc.
- [ ] Update `CLAUDE.md`'s SDLC principle only if the reconciliation step changes the documented
      router contract for callers. If it does not, make no edit — `## Work Completion Criteria` is
      regex-parsed into worker system prompts and asserted byte-for-byte by fixtures, so incidental
      edits to that file are not free.

## Success Criteria

> **Merge-lane note:** the implementation PR must use `Refs #3065`, never `Closes #3065`. This lane
> closes the batch residual, not all 51 consolidated members — #2491's pipeline-graph duplication is
> the largest survivor. Closing the umbrella is its owner's call once the remainder is dispositioned.
> Ratified by the PM 2026-09-03.

- [ ] A lane with an open PR, APPROVED review, DOCS pending, and a stale CRITIQUE verdict routes to
      `/do-docs`, from **both** the `next-skill` CLI path and the in-process
      `decide_next_dispatch(stage_states, meta)` path, with and without `--proposed-skill`.
- [ ] G3 vetoes a plan-family skill selected by the routing table, not only one supplied by a
      caller. Demonstrated red: the pre-change code dispatches `/do-plan-critique` for that state.
- [ ] A recorded lane slug that contradicts branch truth does not produce a `/do-patch` dispatch on
      an open approved PR. A slug whose branch genuinely does not exist still does.
- [ ] An artifact probe that cannot determine branch truth (remote unreachable, ambiguous match)
      causes G8 to step aside rather than dispatch.
- [ ] A wrong recorded slug is repairable: given a PR head resolving uniquely to one origin branch,
      the ledger records the corrected slug together with the evidence that justified it.
- [ ] `Blocked(NO_RULE)` carries the `stage_states` and `meta` it decided on, readable in
      `next-skill`'s JSON output. Finding 4 would have been diagnosable from one call.
- [ ] A timed-out check, an unparseable expectation, a command cell with no backticked span, and a
      non-check table each produce a distinct non-`FAIL`, non-`PASS` outcome carrying a reason.
- [ ] `` prints `0` ``, `>= 1`, `== 0`, `> 0`, `empty output`, and `exit 0` all evaluate correctly
      against their observed output. Demonstrated red: all six return `False` on `main` today.
- [ ] A table shaped `| Command | Observed stdout | Observed exit |` is **not** executed, and emits a
      `SkippedTable` diagnostic. Demonstrated red: today its second column is run as a shell command.
- [ ] `scripts/validate_build.py` and `agent/verification_parser.py` agree on the timeout bound and
      on the disposition of a timeout, enforced by the parity fixture.
- [ ] `tools/merge_predicate.py` refuses to merge a lane whose plan verification carries a `FAIL` or
      `UNEVALUATED` row, and names the offending row in its refusal, reading a **recorded**
      `_verification_outcomes` aggregate and never re-executing plan-authored commands.
- [ ] **A hard shipping gate is expressible as a verification row, and the merge predicate honors
      it.** Motivating case: #3080 / commit `ba092a06d` (owner ruling, 2026-09-02) — "FAIL and
      UNRESOLVED both hold the PR at REVIEW; the satisfied-by-pause rule is now explicitly scoped to
      build progression only, never to shipping." That ruling lived only in plan prose
      (`docs/plans/ask-me-telegram-polls.md:1828`, `:2023`, `:2025`) and PR #3080 merged past it.
      The gate already reports **PASS / FAIL / UNRESOLVED** (`:1953`), the same tri-state Cluster B
      introduces, so no new expressive machinery is needed. Acceptance: such a row's `FAIL` **or**
      `UNEVALUATED` outcome causes the predicate to refuse and name the row, proven by a regression
      test reconstructing the exact shape (APPROVED verdict, DOCS complete, CI green, one
      `UNEVALUATED` gate row). On main today that state merges.
- [ ] **A recorded verification outcome is only trusted while it is fresh.** The aggregate carries the
      PR head SHA it was graded against, stamped via `resolve_pr_head_sha` and never a bare `gh` read.
      The merge predicate compares it the way it already compares the REVIEW verdict trailer
      (`tools/merge_predicate.py:591-599`) and refuses on mismatch, absence, or an unresolvable head,
      naming the reason. Acceptance: an all-`PASS` aggregate merges; the same aggregate with the PR
      head advanced by one commit refuses. On main today there is no aggregate at all, so a lane that
      passed verification and then took a new commit merges on state nobody re-read.
- [ ] **The build-vs-ship split is enforced by consumer, not by row annotation.** The build gate may
      let an `UNEVALUATED` row pause and allow build progression; the merge predicate may not. Test
      asserts the same row with the same outcome permits build progression and refuses merge — with
      no per-row marker, no new frontmatter key, and no addition to any plan grammar.
- [ ] `session-ensure` returns a stable `run_id` across six consecutive invocations on a lane with
      duplicate `AgentSession` rows, and never releases a lease it did not mint. Demonstrated red:
      four of six fail on `main` today.
- [ ] No reader of **`agent/verification_parser.py`'s** removed `passed` boolean remains, across that
      module, `scripts/validate_build.py`, and the three verification test modules. Scoped to the
      type being changed, not to the attribute name: `tools/doctor.py` has its own unrelated
      `CheckResult.passed` (`:57`) with 83 readers that this plan does not touch, so a repo-wide
      claim would be false by construction and could never go green. Demonstrated red: the scoped
      sweep finds 12 real readers on main today, none of them `doctor.py`'s.
- [ ] The test suites added by `bfa4a6f7d`, `d9cf29dd6`, and `3c689f211` pass unchanged.

## Verification

<!-- These rows are graded by the runner this plan modifies (Risk 7), so every row below is a
     direct grep or pytest assertion whose truth does not depend on the modified evaluator, and
     every Expected cell uses only the six forms the CURRENT parser supports — this table must be
     runnable before the fix lands.

     Row classes, stated so a critic can tell them apart (#2658):
     - Two-pole rows, RED on main today and GREEN when the task lands: the G3 docs arm,
       reconciliation, `decision_inputs`, `UNEVALUATED`, `resolve_branch_truth`, and the
       `passed`-reader sweep. These were each executed against main while writing this plan and
       confirmed red.
     - Regression-guard rows, GREEN today and GREEN after, which exist to fail if the builder
       removes or violates something: the `resolve_pr_head_sha` row and the four `ANTI:` scope
       rows. They are not evidence of new work and must not be read as such.
     - Suite rows (`pytest-clean.sh`, `ruff`), whose value is the assertions inside them.

     Every test path named above was confirmed to exist on main at plan time. -->

| Check | Command | Expected |
|-------|---------|----------|
| Router unit tests pass | `scripts/pytest-clean.sh tests/unit/test_sdlc_router.py -q -n 2` | exit code 0 |
| Verdict unit tests pass | `scripts/pytest-clean.sh tests/unit/test_sdlc_verdict.py -q -n 2` | exit code 0 |
| Verification parser tests pass | `scripts/pytest-clean.sh tests/unit/test_verification_parser.py -q -n 2` | exit code 0 |
| Runner parity tests pass | `scripts/pytest-clean.sh tests/unit/test_validate_build.py tests/unit/test_validate_verification_section.py -q -n 2` | exit code 0 |
| Lane identity tests pass | `scripts/pytest-clean.sh tests/unit/test_lane_identity.py -q -n 2` | exit code 0 |
| Merge predicate tests pass | `scripts/pytest-clean.sh tests/unit/test_merge_predicate.py -q -n 2` | exit code 0 |
| G3 ladder has a docs arm | `grep -c G3_REDIRECT_REASON_DOCS_PENDING agent/sdlc_router.py` | output > 0 |
| Reconciliation step exists | `grep -c 'def reconcile_dispatch' agent/sdlc_router.py` | output > 0 |
| NO_RULE payload carries decision inputs | `grep -c decision_inputs agent/sdlc_router.py` | output > 0 |
| Tri-state outcome is defined | `grep -c UNEVALUATED agent/verification_parser.py` | output > 0 |
| Merge predicate reads recorded verification outcomes | `grep -c _verification_outcomes tools/merge_predicate.py` | output > 0 |
| Verification runner persists the outcome aggregate | `grep -c _verification_outcomes agent/verification_parser.py` | output > 0 |
| Outcome aggregate is stamped via the sanctioned head resolver | `grep -c resolve_pr_head_sha agent/verification_parser.py` | output > 0 |
| Merge predicate has a named stale-outcome refusal | `grep -c VERIFICATION_OUTCOMES_STALE_REASON tools/merge_predicate.py` | output > 0 |
| Ruff is clean | `python -m ruff check agent/sdlc_router.py agent/verification_parser.py tools/lane_identity.py tools/sdlc_next_skill.py tools/sdlc_session_ensure.py tools/merge_predicate.py scripts/validate_build.py` | exit code 0 |
| Ruff formatting is clean | `python -m ruff format --check agent/sdlc_router.py agent/verification_parser.py tools/lane_identity.py tools/sdlc_next_skill.py tools/sdlc_session_ensure.py tools/merge_predicate.py scripts/validate_build.py` | exit code 0 |
| ANTI: no reader of the verification `passed` boolean survives | `test -z "$(grep -rnE '(\.passed\b\|passed=)' agent/verification_parser.py scripts/validate_build.py tests/unit/test_verification_parser.py tests/unit/test_validate_build.py tests/unit/test_validate_verification_section.py)"` | exit code 0 |
| ANTI: hooks validators untouched (No-Go #2736) | `test -z "$(git diff --name-only origin/main...HEAD -- .claude/hooks/validators/)"` | exit code 0 |
| ANTI: pyproject and uv.lock untouched | `test -z "$(git diff --name-only origin/main...HEAD -- pyproject.toml uv.lock)"` | exit code 0 |
| Branch-truth resolver exists | `grep -c 'def resolve_branch_truth' tools/sdlc_next_skill.py` | output > 0 |
| Branch truth routes through the sanctioned head resolver | `grep -c resolve_pr_head_sha tools/sdlc_next_skill.py` | output > 0 |
| ANTI: no raw Redis call added on Popoto-managed keys | `test -z "$(git diff origin/main...HEAD -- tools/ agent/ \| grep -E '^\+.*(_R\|POPOTO_REDIS_DB)\.(delete\|srem\|sadd\|zrem)\(')"` | exit code 0 |
| ANTI: no Claude co-authorship trailer | `test -z "$(git log origin/main..HEAD --format=%B --grep='Co-Authored-By: Claude')"` | exit code 0 |

## Step by Step Tasks

Ordered so that the acute wedge clears first, evidence lands before the decision logic that will
need diagnosing, and the two disjoint file sets stay separable into two PRs.

### 1. Fix `session-ensure`'s readback and candidate provenance

- Read back the row this call saved **by primary key** (`agent/session_id` pair / `id`), not by
  re-querying `AgentSession.query.filter(session_id=...)` and taking `[0]`
  (`tools/sdlc_session_ensure.py:613-620`).
- Track `candidate`'s provenance at `:538-540` as an explicit value — minted, adopted-from-supervised
  (`:526`), or adopted-from-live-lock (`:353-355`). `reuse_run_id` cannot serve as this flag; it is
  overwritten at `:526` and its use at `:674-675` already conflates the two adopt shapes.
- Gate all three release sites (`:597`, `:618`, `:638`) on `provenance == minted`. Leave
  `release_issue_lock` (`models/session_lifecycle.py:1484-1520`) unchanged — it is correct; the bug
  is what it was handed.
- Correct the module docstring at `:424-428`, which currently asserts the release is unconditionally
  safe. Replace the claim; do not annotate it.
- Tests: duplicate-row readback stability across repeated ensures; an adopted candidate surviving a
  readback mismatch; a minted candidate still being released on genuine save failure.

### 2. Give blocked and dispatch decisions their evidence

- `Blocked(NO_RULE)` (`agent/sdlc_router.py:2281-2284`) carries the `stage_states` and `meta` it
  decided on under an evidence key named **`decision_inputs`**, surfaced through
  `tools/sdlc_next_skill.py`'s JSON output. (The name is pinned because a Verification row greps for
  it; a grep for `stage_states` would pass today and prove nothing.)
- The decision payload reports when the previous decision was never recorded — the caller skipped
  `sdlc-tool dispatch record`, which today surfaces only much later as a G4 oscillation block
  attributed to the wrong cause. `next-skill` still persists nothing; this is a read, not a write.
- Tests: a `NO_RULE` block round-trips its inputs through the CLI JSON; the unrecorded-dispatch
  signal appears exactly when the last dispatch has no confirming record.

### 3. Complete G3's redirect ladder

- Add the `/do-docs` arm to `agent/sdlc_router.py:501-509` for REVIEW complete + APPROVED + DOCS
  pending. Define its reason text as a **named module constant**,
  `G3_REDIRECT_REASON_DOCS_PENDING = "review clean, docs pending"`, and let the arm reference the
  constant. The Verification row greps for the **constant name**, not the prose.
  (Two reasons, both from critique round 1: a grep for `SKILL_DO_DOCS` would pass today — row 9
  already uses it — and so would be a gate incapable of failing, the #2658 defect this plan exists
  to remove; and a grep for the literal sentence would break on an innocent copy-edit that leaves
  the logic identical. Pinning the constant keeps the row RED-today while decoupling it from
  wording, matching how `reconcile_dispatch`, `decision_inputs`, and `resolve_branch_truth` are
  already pinned.)
- Tighten arm 1 to require a recorded APPROVED verdict rather than the REVIEW marker alone, matching
  rows 9 and 10 (`:1956`, `:1970`).
- Tests: two-pole for each arm, including the state that previously fell to the `else` and produced a
  redundant `/do-pr-review`.

### 4. Reconcile guards against the selected dispatch

- After the routing table selects `primary` (`agent/sdlc_router.py:2250-2255`), re-evaluate the
  guards with the selection as the proposed skill, in a function named **`reconcile_dispatch`**
  (pinned for a Verification row). Exactly one reconciliation pass on the selection, and at most one
  on the resulting redirect.
- A second veto returns `Blocked` carrying the selected row **and** the vetoing guard. Do not
  iterate.
- Do not edit row 2b's predicate (#1639 made it marker-agnostic deliberately); it is constrained
  from outside by this step.
- Preserve the existing asymmetry deliberately: rule predicates are try/except-wrapped
  (`:2260-2263`), guards are not (`1083-1086`). Reconciliation must not silently swallow a raising
  guard into a `NO_RULE`.
- **The guards are not pure, and reconciliation runs them twice — this is a stated invariant, not an
  accident.** `guard_g5_artifact_hash_cache` (`:603-656`) mutates `record["artifact_hash"]` in place
  at `:656` and logs a WARNING at `:648` on legacy-hash migration. Double invocation is idempotent
  today *only* because `stage_states` is passed by reference, so the second pass sees the already-
  migrated record. Reconciliation MUST therefore pass the **same** `stage_states` and `meta` objects
  by reference and never a defensive copy. A copy would re-run the migration branch and double the
  log noise — and copying inputs is exactly the "safe" change a later contributor would make without
  knowing this. Write the invariant into the function's docstring so the next reader cannot
  unknowingly break it.
- Regression test: assert `guard_g5_artifact_hash_cache`'s migration branch executes **exactly once**
  per `decide_next_dispatch` call, asserting on the WARNING log-record count via `caplog` rather than
  on the return value — the return value is identical either way, which is why this needs its own
  test. The alternative (making the side-effecting guards idempotent first) is a larger change and is
  not taken here; if the builder finds a second impure guard that reconciliation cannot safely
  re-run, that is a design question to escalate, not to paper over.
- Tests: the #2771/#2334 lane shape routes to `/do-docs`; a lane needing `/do-plan-critique` with no
  PR still gets it; a double-veto produces `Blocked` with both verdicts and terminates.

### 5. Resolve branch truth once, for both router callers

- Add a resolver named **`resolve_branch_truth`** (pinned for a Verification row) answering "which
  pushed branch holds this lane's work?" from the PR head SHA — via
  `tools/pr_head_resolver.py::resolve_pr_head_sha`, never a bare `gh` read — matched against
  `git ls-remote --heads origin`. Return **found / absent / indeterminate**; zero matches on a lane
  with no PR is *absent*, ambiguity or an unreachable remote is *indeterminate*.
- Note for the builder, learned while writing this plan's own verification rows: an anti-criterion
  grepping for `headRefOid` to prove the bare-`gh` rule was tried and **rejected**, because it
  matches the docstring at `tools/sdlc_next_skill.py:169` that *describes* the sanctioned fallback.
  A guard that cannot tell a call from prose about a call is the wave4 defect class. The rule is
  asserted structurally instead, by the two rows above.
- Replace `_check_branch_pushed`'s two-valued answer at `tools/sdlc_next_skill.py:181-198` and its
  consumers at `:323-361` and `:476-493`.
- `guard_g8_artifact_verification` may dispatch only on *absent*; on *indeterminate* it steps aside.
- Wire the same resolver into the in-process path so `agent/session_runner/runner.py:1408` stops
  deciding from an empty context.
- Tests: wrong-slug lane with a live branch does not dispatch `/do-patch`; genuinely unpushed branch
  still does; ambiguous and unreachable cases defer; an integration test asserts the CLI and
  in-process paths agree on a state where they currently differ.

### 6. Make a wrong recorded lane slug repairable

- Add an evidence-gated repair to `tools/lane_identity.py`: on a **unique** branch-truth match that
  contradicts the recorded slug, write the correction and record the evidence. Re-read immediately
  before writing so a concurrent repair converges to a no-op.
- Do not reuse `_record_slug_if_empty` (`:396+`) — its no-overwrite behavior is the defect.
- **Re-read before building, do not trust this plan's summary.** The rung-2 / `e50eba258` narrative
  (adoption fires only on an empty recorded slug) is load-bearing for this task and for Risk 3, but
  it rests on spike-3's read at plan time. Re-verify `resolve_lane_slug`, `adopt_lane_slug`,
  `_record_slug_if_empty`, and `_adopt_from_pr` against the branch at build time and reconcile any
  drift before writing the repair. A plan that tells a builder to route on its own asserted summary
  of code it read days earlier is committing this issue's defect in miniature.
- Rung 1 (`:535-538`) keeps returning the recorded slug for ordinary reads; only the fail-closed
  decision path verifies it.
- Replace the "could never be corrected" claim in the module docstring (`:37-39`) and the matching
  passage in `docs/features/sdlc-lane-identity.md:118-124`.
- Tests: unique contradiction repairs and records evidence; zero and multiple matches do not write;
  repeated repair is idempotent.

### 7. Make the verification runner able to say "I could not evaluate this"

- Replace `passed: bool` with a three-valued outcome (`PASS` / `FAIL` / `UNEVALUATED`) across
  `agent/verification_parser.py` and every reader. Removal, not addition — no lingering boolean.
- `UNEVALUATED` for: timeout (`:423-432`), any runner exception (`:433-442`), an expectation form the
  grammar does not recognize (`:384`), and a command cell with no backticked span. Each carries a
  reason. `format_results` (`:499`) renders it distinctly and never as `[FAIL]`.
- Extend `evaluate_expectation` (`:304-384`) to cover the corpus #2836's spike-5 measured:
  `` prints `N` ``, `== N`, `>= N`, `> N`, `empty output`, `exit N`. Re-derive the corpus by the
  same method to confirm coverage rather than trusting the historical count. **Closes the substance
  of #2791**, which was closed as consolidated with no fix commit and whose symptom reproduces on
  main today.
- Classify check tables by column contract, not by `any` of the first three headers being `Command`
  (`_is_check_table_header:178-183`), and emit a `SkippedTable` diagnostic for non-check tables.
  Leave per-block scoping (`_iter_pipe_blocks:156`) alone; it is correct. **Closes the substance of
  #3022**, closed as consolidated and entirely unfixed; reproduced on main with the issue's exact
  table shape.
- Extract the command as the first backticked span (`:286-288`), not the whole backtick-stripped
  cell.
- Converge `scripts/validate_build.py` (`:238`, `:260-263`) onto one bound and one timeout
  disposition, and add timeout and malformed rows to
  `tests/fixtures/verification/runner_agreement.md` so the parity fixture can actually catch a
  divergence. **Closes the substance of #2901** (120s bound, BRE alternation lost to cell escaping,
  narrow expectation vocabulary), all three parts of which reproduce on main.
- Persist the graded aggregate to the ledger's `_verification_outcomes` key (see task 8 for the
  substrate and the PM ruling that authorizes it). This is task 7's one new output.
- **Stamp the aggregate with the PR head SHA it was graded against.** The aggregate is written at
  TEST/DOCS time and read by the merge predicate later, so without a freshness anchor a lane that
  passes verification and then takes a new commit — a late `/do-patch`, a manual push — merges on a
  cached PASS. That is this plan's own defect ("a fact readable earlier, not now") reproduced inside
  the mechanism built to close it, and it is the one concern round 2 raised. Persist `head_sha`
  alongside the outcomes, sourced from `tools.pr_head_resolver.resolve_pr_head_sha` — **never a bare
  `gh` read**, per CLAUDE.md, because a stale `gh` head SHA is exactly what flipped the
  verdict-staleness gate fail-open in #2895. A lane with no PR at write time records no `head_sha`;
  task 8 defines what the reader does with that.
- Pin the refusal reason for the stale case as a **named module constant**,
  `VERIFICATION_OUTCOMES_STALE_REASON`, defined where the predicate consumes it (task 8). A
  Verification row greps for the constant name rather than the prose, matching how
  `reconcile_dispatch`, `decision_inputs`, `resolve_branch_truth`, and
  `G3_REDIRECT_REASON_DOCS_PENDING` are already pinned.
- Tests: the aggregate written by a graded run carries a `head_sha` matching what
  `resolve_pr_head_sha` returned; a run on a lane with no PR records the aggregate with no
  `head_sha` and does not crash.
- Replace `tests/unit/test_verification_parser.py:210`, which pins the bug as intended behavior.

### 8. Let the merge predicate see verification outcomes

- **Source of truth: recorded state, never live re-execution. Ruled by the PM, 2026-09-03, in
  response to critique round 1's concern that the source was unspecified.** The merge predicate MUST
  NOT shell out to plan-authored commands — re-running `pytest-clean.sh` suites inside a merge gate
  is a non-starter, and every other check in `merge_predicate.py` already reads recorded state via
  `gh` / `sdlc-tool` rather than executing anything.
- The substrate is a new **underscore-prefixed key in the ledger's existing `stage_states` JSON**,
  named `_verification_outcomes` (pinned; a Verification row greps for it), mirroring the
  `_verdicts` precedent already in that blob. Written by the verification runner when it grades a
  plan at TEST/DOCS time; read by the predicate. **The PM ruled explicitly that this does not count
  as the Popoto schema change Prerequisites forbids** — that prohibition covers model and data
  migrations, not a new key in an already-flexible JSON blob, so no
  `scripts/update/migrations.py` entry is required and Prerequisites stands unchanged.
- Add the write to task 7's outputs: the runner gains a persistence point alongside its existing
  result rendering. **If the builder finds the runner has no natural write point for this, stop and
  escalate rather than forcing one** — that is a real design question, and the PM asked to be
  brought it directly.
- A `FAIL` or `UNEVALUATED` row holds the PR, and the refusal names the row. This is the #3080 /
  `ba092a06d` ruling made machine-readable: that owner ruling said "FAIL and UNRESOLVED both hold
  the PR at REVIEW" and lived only in plan prose, so PR #3080 merged past it.
- **Check the aggregate's freshness against the PR's current head before trusting it, and fail
  closed.** Compare task 7's stamped `head_sha` the way this module already compares the REVIEW
  verdict's trailer at `tools/merge_predicate.py:591-599` — read the record's SHA via
  `head_sha_of_record` (which reads the `head_sha` field first and falls back to the legacy in-token
  trailer, #2769), resolve the PR's current head through `resolve_pr_head_sha`, and compare
  case-insensitively. Three dispositions, all fail-closed:
  - **Match** → the aggregate is fresh; grade it as above.
  - **Mismatch** → treat the aggregate as stale and equivalent to `UNEVALUATED`; refuse with
    `VERIFICATION_OUTCOMES_STALE_REASON` ("verification outcome predates PR head commit"). Do **not**
    read the cached PASS.
  - **Missing, unparseable, or a PR head that cannot be resolved** → refuse, do not fall open. This
    is deliberately stricter than the REVIEW-verdict path's timestamp fallback: that fallback exists
    for records predating #2769 and there are no legacy `_verification_outcomes` records to be
    compatible with, so the weaker comparison would be a fail-open hole (#2404) added on purpose.
  - Note the interaction with the bullet below: a lane with **no plan document** is still not
    blocked. Absence of an aggregate because there is no plan is reported, not enforced; a *present*
    aggregate that cannot be shown fresh is enforced. The two are distinguishable and must not be
    collapsed.
- Keep the build-vs-ship split on the **consumer**, never on the row: the build gate may treat
  `UNEVALUATED` as a pause that allows build progression, the merge predicate may not. No `GATE:`
  row marker and no frontmatter key. A per-row severity annotation is the first step back toward the
  gate DSL this plan rejected, and `ba092a06d` shows it is unnecessary.
- Resolve the plan document through the lane's `tracking:` frontmatter
  (`tools/plan_doc_scope.py`), never by filename match — the lane slug and plan filename are allowed
  to differ.
- A lane with no plan document is not blocked by its absence; that would be a new fail-closed
  behavior this plan has no evidence for. It is reported, not enforced.
- No new plan frontmatter key, and no fourth plan grammar (see Technical Approach).
- Tests: the #3080 shape reconstructed exactly — APPROVED review verdict, DOCS complete, CI green,
  one `UNEVALUATED` gate row — refuses to merge and names the row (on main today that state merges);
  the same shape with a `FAIL` row likewise refuses; the same row and outcome still permits build
  progression, proving the split lives on the consumer; a clean lane still merges; a plan-less lane
  is unaffected.
- Freshness tests, two-pole against the stale case that motivated them: an all-`PASS` aggregate whose
  `head_sha` matches the PR head merges; the **same** aggregate with the PR head advanced by one
  commit refuses with `VERIFICATION_OUTCOMES_STALE_REASON`; an aggregate with an absent or
  unparseable `head_sha` refuses; a lane with no plan document is unaffected by any of it.

### 9. Documentation cascade

- Work the `## Documentation` checklist above. Describe only the new status quo — replace the
  "could never be corrected" and "unconditionally safe release" claims rather than appending
  corrections to them.

## Critique Results

Round 2 of 2 (cap enforced). The seven round-1 findings (2 blockers, 3 concerns, 2 nits) were each
re-verified against the plan body and the code, and all seven are genuinely resolved — round-1
blocker 1 by falsification, adjudicated below and accepted by this round's History & Consistency
critic on executed evidence. The table below carries only what round 2 found.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Risk & Robustness | Task 8's `_verification_outcomes` aggregate is written at TEST/DOCS time and read by the merge predicate with no freshness anchor to the PR's current head commit. Every other verdict that predicate reads is protected against exactly this failure: `tools/merge_predicate.py:586-599` compares a recorded `head_sha` trailer against `resolve_pr_head_sha` and refuses on mismatch (the #2404 fail-open hole). With no analogous check, a lane that passes verification and then takes a new commit — a late `/do-patch`, a manual push — merges on a cached PASS. That is this plan's own defect ("a fact readable earlier, not now") reproduced inside the mechanism built to close it. | Task 7 (stamp `head_sha` via `resolve_pr_head_sha` at the write point; pin `VERIFICATION_OUTCOMES_STALE_REASON`), task 8 (three fail-closed dispositions on compare; freshness tests), two Verification rows (both confirmed RED on main), one Success Criterion | Persist a `head_sha` alongside `_verification_outcomes` at task 7's write point, sourced from `tools.pr_head_resolver.resolve_pr_head_sha` — never a bare `gh` read, per CLAUDE.md. In task 8's predicate check, compare it the way `tools/merge_predicate.py:591-599` compares the REVIEW trailer via `head_sha_of_record`, and on a mismatch treat the aggregate as stale/`UNEVALUATED` and refuse with a named reason ("verification outcome predates PR head commit") rather than reading the cached PASS. Fail closed on a missing or unparseable `head_sha`; do not fail open. |

### Named deviation — critique blocker 1's primary claim is falsified

Recording this rather than silently doing something different, so the decider can overrule me.
**Adjudicated in round 2: ACCEPTED.** The History & Consistency critic re-ran the parser over this
file, printed the parsed command, and executed it two-pole against synthetic diffs (exit 1 on an
added `_R.delete(x)`, exit 0 on a clean diff), and confirmed that substituting bare pipes in the
markdown source makes the row split into 8 columns and parse as `MalformedRow`. The deviation stands
on executed evidence, not assertion.

**The claim:** the raw-Redis row's `\|` escapes make the ERE match a literal string, so the row can
never fire. The critic verified this "empirically" and it is true *of the raw markdown text*.

**Why it does not hold:** the row is never executed as raw markdown. `parse_verification_table`
unescapes `\|` to a bare `|` when it extracts the cell, so the shell receives
`grep -rnE '_R\.(delete|srem|sadd|zrem)\('` — correct ERE alternation. Verified by printing the
parsed command and running it two-pole: it exits 1 against a file containing `_R.delete(x)` and
exits 0 against a clean file. **Applying the suggested fix would have regressed the row**: bare pipes
in the markdown source split the cell into six columns, which is exactly the `malformed` state this
row was already repaired out of once during plan authoring.

The critic asserted what the shell receives without reading it through the parser that produces it.
That is this issue's own defect class, which is worth recording precisely because it happened inside
the critique of the plan that exists to eliminate it. The escaping composition is genuinely
confusing, which is the argument for task 7's documentation bullet rather than against it.

**What was adopted instead** — the secondary claim, which is valid and sharper than the primary one.
`_R` is a function-local alias (`tools/sdlc_session_ensure.py:1042`) with 2 uses in that file and
**0** in `tools/lane_identity.py`, so the row was narrow. Investigating that surfaced a harder
problem the critique did not reach: `tools/lane_identity.py:389` **already** calls
`POPOTO_REDIS_DB.delete(...)` on a slug-lock key. That call is legitimate — the rule governs
Popoto-managed *model* keys, not lock keys — but it means any whole-file anti-criterion is RED on
main for a pre-existing reason and can never go green, which is precisely critique blocker 2's
defect in a second location.

The row is therefore now **diff-scoped**, judging what the commit adds rather than what its files
already contain, matching the wave4 lesson this plan cites in Prior Art. Proven two-pole against
synthetic diffs: exit 1 when a violating `+` line is added, exit 0 when the only violation is on a
`-` line.

---

## Open Questions

1. **Split the lane into two PRs?** Clusters A/D (`agent/sdlc_router.py`, `tools/sdlc_next_skill.py`,
   `tools/lane_identity.py`, `tools/sdlc_session_ensure.py`) and Cluster B/C
   (`agent/verification_parser.py`, `scripts/validate_build.py`, `tools/merge_predicate.py`) have
   disjoint file sets, and tasks 1-6 are independent of 7-8. My recommendation is **two sequential
   PRs from this one lane**: the router half is the higher-risk change and deserves undiluted review
   attention, and a bundled PR large enough to review badly is Risk 6. Confirm or overrule.
2. **Does this plan's PR close #3065?** It closes the residual it was scoped to, but not every member
   of the 51-issue consolidated set — #2491's pipeline-graph duplication is the largest survivor. My
   recommendation is `Refs #3065`, leaving the umbrella open for its owner to close deliberately once
   the remaining members are dispositioned. Confirm.
3. **Is the Cluster C ruling right?** I rejected a general plan-declared merge-gate DSL in favor of
   wiring the merge predicate to verification outcomes, on the grounds that the #3080 incident was
   *about* verification rows and that a fourth plan grammar extends the drift #2491 documents. This
   was the finding flagged as "a candidate, weigh it on its merits", so it is the one scope call I
   most want overruled if the owner reads it differently.
4. ~~**Should `UNEVALUATED` block a merge, or only a build?**~~ **Resolved 2026-09-03 by the
   motivating incident rather than by judgement.** Commit `ba092a06d` scopes satisfied-by-pause "to
   build progression only, never to shipping", so both dispositions are required and the split is by
   *consumer*: the build gate may pause on `UNEVALUATED`, the merge predicate holds on it. Encoded in
   Success Criteria and task 8. No per-row severity marker is needed, which is what keeps the scoped
   Cluster C from growing into the gate DSL rejected in Technical Approach.
