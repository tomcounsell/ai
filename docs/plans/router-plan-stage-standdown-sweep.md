---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-09-09
tracking: https://github.com/tomcounsell/ai/issues/3249
last_comment_id:
---

# Router plan-stage stand-down sweep, and G3's merge leg verdict gate

## Problem

Two defects in `agent/sdlc_router.py`, planned and shipped as ONE lane because they live
in the same file and splitting them guarantees a rebase conflict.

**#3249 — plan-stage rows keep answering for lanes that have left the plan stage.**
`_rule_plan_not_critiqued` (row 2, `:1153`) is the only plan-stage row with **no** PR
step-aside at all, so it dispatches `/do-plan-critique` on a lane with an open PR. Three
siblings — row 1 `_rule_no_plan` (`:1121`), row 2c `_rule_critique_in_progress_no_verdict`
(`:1650`), row 3 `_rule_critique_needs_revision` (`:1180`) — each carry the `pr_number`
step-aside but are missing the `BUILD` one, so they answer with a plan-stage skill while
BUILD is `in_progress`. This is one defect class, not four symptoms: #3237 and #3227 were
two earlier instances of it, and each cost a supervisor a manual override on a live lane
(#3195, #3181). The issue is explicit that the fix "should close on a **sweep** ... with
the stand-down condition expressed once rather than hand-copied into each rule predicate."

**#3260 — G3 leg 1 routes to `/do-merge` on stage markers alone.** At `:518`:

```python
if review_status == STATUS_COMPLETED and docs_status == STATUS_COMPLETED:
    target = SKILL_DO_MERGE
    suffix = "review clean and docs complete"
```

`review_verdict_norm` is computed immediately above (`:516`) and simply not consulted. The
two legs below it both check the verdict and one checks head freshness, so the
**most-taken** leg is the only one that skips both. Two states route to MERGE that should
not: (1) REVIEW marker `completed` with a `CHANGES REQUESTED` verdict — the `elif` that
catches it is unreachable because leg 1 already matched; (2) an APPROVED verdict recorded
against an older head, which occurs on every PR whose DOCS stage produces a commit.

The realized impact today is a wasted dispatch, not an unauthorized merge:
`tools/merge_predicate.py` independently refuses both states and demonstrably did so across
the #3246/#3245/#3244 batch. The concern is defense-in-depth and drift — the router telling
lanes to attempt merges the gate will reject is exactly the disagreement that later gets
"fixed" by relaxing the predicate.

**#3260 widened to G6 (ratified).** `guard_g6_terminal_merge_ready` (`:976`) has the
identical absent-key fail-open on the OTHER terminal `/do-merge` fast-path. Its own comment
at `:971-975` already asserts the behavior it does not have — *"never fast-path a
head_sha-stale APPROVED verdict ... (or the live-head lookup failed, which fails closed
toward stale)"* — while the code returns "not stale" on an absent signal. Same lie, one
guard over, same diff.

## Freshness Check

Baseline: `main` @ `a15c5eab7` (2026-09-10). Both issues recorded their recon against
`fd0847a50` (2026-09-09).

**Disposition: Unchanged.**

- `git log origin/main --since=2026-09-08 -- agent/sdlc_router.py tests/unit/sdlc_router_decision/`
  returns exactly one commit, `2fb7df519` (#3246), which predates both recons. The file the
  fix modifies has not moved since either issue was written.
- Every cited `file:line` re-read verbatim at the current HEAD and confirmed at the **same
  line number**, no drift:
  - `:518` — G3 leg 1, quoted text identical.
  - `:527` — leg 3's `_review_verdict_head_is_stale` call.
  - `:936` `guard_g6_terminal_merge_ready`; `:971-975` the WS3d comment; `:976` the stale call.
  - `:1121` `_rule_no_plan`; `:1153` `_rule_plan_not_critiqued`; `:1180`
    `_rule_critique_needs_revision`; `:1650` `_rule_critique_in_progress_no_verdict`.
  - `:1385` `_review_verdict_head_is_stale`, absent-key contract unchanged in its docstring.
  - `:1594` `_rule_critique_verdict_stale` (row 2b), canonical stand-down pair at `:1641-1644`.
  - `:1998` row 8f, `:2029` row 10 — both still call the existing predicate; untouched here.
- Producer re-verified: `tools/sdlc_next_skill.py:512-531` sets `context["pr_head_sha"]`
  **unconditionally** whenever `pr_number` is set and a REVIEW verdict is recorded — a real
  SHA, or `""` plus `pr_head_sha_lookup_failed` on lookup failure. The key is omitted only
  when there is no PR or no recorded verdict.
- Rows 4b (`~:1214`) and 4c (`~:1249`) each still check `meta.get("pr_number")` **twice**
  within the same predicate.
- Sibling issues re-checked: #3237 CLOSED, #3227 CLOSED, #2062 CLOSED, PR #3246 MERGED
  (2026-09-08). None changed the root cause; #3246 is the prerequisite and it landed.
- Both recon validators pass: `validate_issue_recon.py 3249` and `3260` both `continue`.

**Overlap check.** `grep -l sdlc_router docs/plans/*.md` names only
`resilience-simplification-three-tier.md` and `sdlc-control-plane-asserted-facts.md`,
neither of which touches these rows or guards. Two lanes run in parallel on this machine —
`pgrep-sweep-finish` (`scripts/`, `tools/process_lookup.py`, `monitoring/`) and a lane on
`agent/agent_session_queue.py` — and neither touches any file this plan modifies. No overlap.

**Bug reproduction.** Both defects were reproduced at recon time by direct
`decide_next_dispatch` probes (recorded verbatim in each issue's Recon Summary), and the
code paths those probes exercise are byte-identical at this baseline. Reproduction is
re-run as the RED step of this plan's tests rather than as a throwaway probe.

## Prior Art

- **PR #3246** (merged 2026-09-08, `2fb7df519`) — "Router: stand row 2b down past the plan
  stage, add G3's missing DOCS leg". Closed #3237 and #3227. It established both the
  canonical stand-down shape (`agent/sdlc_router.py:1641-1644`) and the test file this plan
  extends. It deliberately left row 2 and the three siblings alone to keep the diff
  conservative on load-bearing routing, and filed #3249 for the sweep. It also surfaced
  #3260 via its round-2 `code-quality` judge and correctly left it out of scope.
- **#3237** (CLOSED) — row 2b outranked row 5, leaving a lane with BUILD `in_progress` and
  an armed concern gate no exit from the plan loop. Symptom one of the class.
- **#3227** (CLOSED) — G3's ladder had no DOCS leg, so an approved PR with docs pending
  re-dispatched `/do-pr-review` forever. Symptom two.
- **#2062** (CLOSED, WS3d) — introduced `_review_verdict_head_is_stale` and the
  router↔`tools/merge_predicate` freshness convergence. G6's `:971-975` comment is its
  artifact; this plan finishes what that comment promised, hence `Refs #2062`.
- **PR #2797** (merged 2026-08-19) — "G9 owns the BLOCKED_ON_CONFLICT verdict". Same
  file, unrelated guard; noted only to confirm no live overlap.

No prior attempt tried and failed at either of these two fixes, so there is no failed-fix
pattern to avoid — only the *scope* pattern: fixing this class one reported symptom at a
time is how it survived this long.

## Research

No external research performed — and none is warranted. This work is entirely internal:
one Python module (`agent/sdlc_router.py`), its unit tests, and one docs file. No external
library, API, service, or ecosystem pattern is involved, so Phase 0.7's skip condition
applies verbatim. All evidence comes from the codebase itself and from the two issues'
recon probes, re-verified above.

## Spike Results

Small appetite caps spikes at two. Both load-bearing assumptions were resolvable by direct
inspection at plan time and were resolved inline rather than dispatched — a spike agent
would have read the same two files.

### spike-1: Requiring a *present* `pr_head_sha` costs live lanes nothing

- **Assumption**: "On every path that can reach G3 leg 1 or G6, `context['pr_head_sha']` is
  already present, so a predicate that returns False on an absent key never strands a real lane."
- **Method**: code-read (`tools/sdlc_next_skill.py:505-533`, resolved inline).
- **Result**: **CONFIRMED.** `_build_context` sets the key unconditionally when
  `meta['pr_number']` is truthy AND a REVIEW verdict is recorded — a real SHA on success, or
  `""` plus `pr_head_sha_lookup_failed` on failure. The comment at `:507-511` states the
  fail-closed contract explicitly: it "never silently omits the key." The key is omitted
  only when there is no PR or no recorded verdict. G3 leg 1 requires `review_status ==
  completed`, and G6 gates on `pr_number` (`:953-955`) and `REVIEW_APPROVED` (`:966-969`),
  so both producer conditions hold on every reachable path.
- **Confidence**: high.
- **Impact if false**: live lanes would fall through to `/do-pr-review` instead of merging —
  noisy but not unsafe. This is why the design accepts the closed-by-default posture: the
  failure mode of requiring presence is a re-review, and the failure mode of not requiring
  it is a merge dispatch on unverified evidence.

### spike-2: Nothing is stranded once rows 1/2/2c/3 stand down on BUILD

- **Assumption**: "Rows that stand down on `BUILD in (in_progress, completed)` have a
  correct landing row, without widening row 5."
- **Method**: code-read plus the recon probes recorded in #3249.
- **Result**: **CONFIRMED.** Row 5 `_rule_branch_exists_no_pr` fires on
  `BUILD == in_progress OR context['branch_exists'] is True`; the branch half answers
  regardless of BUILD status, and a BUILD cannot reach `completed` without pushing its lane
  branch. #3249's second probe pass re-ran all five states with the shared stand-down applied
  (monkeypatched) and every one routed correctly: rows 1/2/2c/3 with `BUILD=in_progress` and
  no PR → `Dispatch(/do-build, row_id='5')`; row 2 with an open PR and `REVIEW=pending` →
  `Dispatch(/do-pr-review, row_id='7')`. None reached `Blocked`.
- **Confidence**: high.
- **Impact if false**: a lane would dead-end at `Blocked('no matching dispatch rule')`. The
  residual `Blocked` subcase (BUILD settled AND no live branch) is inherited from #3246
  unchanged — there is nothing left to resume there, so it is correct, not a regression.

## Data Flow

The change sits at one point in a three-hop chain and reads a signal produced upstream.

1. **Producer — `tools/sdlc_next_skill.py::_build_context` (`:505-533`).** Given
   `stage_states` and `meta`, when `meta['pr_number']` is set and a REVIEW verdict exists in
   `_verdicts` or `meta['latest_review_verdict']`, it calls `_fetch_pr_head_sha` (which
   resolves through `tools/pr_head_resolver.py::resolve_pr_head_sha`, never a bare `gh`
   read) and writes `context['pr_head_sha']` — the SHA on success, `""` plus
   `context['pr_head_sha_lookup_failed'] = True` on failure. **The key is never omitted when
   both conditions hold.**

2. **Consumer — `agent/sdlc_router.py::decide_next_dispatch` (`:2306`).** Guards run first
   (G1–G6, G9), then `DISPATCH_RULES` in row order. The router itself makes no `gh` calls;
   it reads only what `context` carries. Three freshness readings coexist after this change:
   - `_review_verdict_head_is_stale` — *"is there positive evidence of staleness?"*, inert
     on an absent key. Keeps its exact current contract and its four current call sites
     (`:527` G3 leg 3, `:976` G6 → moves, `:1998` row 8f, `:2029` row 10).
   - `_review_verdict_head_is_verified_fresh` (**new**) — *"is there positive evidence of
     freshness?"*, False on an absent key. Used **only** on terminal `/do-merge` dispatch:
     G3 leg 1 (`:518`) and G6 (`:976`).
   - `_plan_stage_stood_down` (**new, shared**) — *"has this lane left the plan stage?"*,
     True when `meta['pr_number']` is set or `BUILD in (in_progress, completed)`.

3. **Downstream authorization — `tools/merge_predicate.py`.** Untouched. It keeps its
   independent verdict and freshness checks and remains the authorization gate. This change
   converges the *router* onto the predicate; it does not move authorization into the router.

The stand-down flows the other way: rows 1/2/2c/3 and 4b/4c call `_plan_stage_stood_down`
and return False, letting evaluation fall through to row 5 (pre-PR) or rows 7–10 (post-PR).

## Why Previous Fixes Failed

No prior fix attempted either of these two defects, so nothing was applied at the wrong
layer or aimed at a symptom. What failed was **scope**, twice:

- **#3237 and #3227 were each fixed as a single row/guard.** PR #3246 fixed row 2b and G3's
  DOCS leg and consciously left the four siblings alone. That was the right call for that
  diff's risk posture, but it is the third consecutive round of fixing one instance of a
  class whose other instances were already visible. #3249 exists specifically to break that
  pattern, which is why narrowing this plan to row 2 would reproduce the failure it was
  filed against.
- **#2062 shipped the freshness predicate and a comment describing stronger behavior than
  the code has.** G6's `:971-975` comment claims a lookup failure "fails closed toward
  stale" — true for the EMPTY sentinel, false for an absent key. A correct mechanism plus a
  comment that overstates it is how the gap stayed invisible for a release cycle.

The lesson carried into this plan: express the condition **once**, apply it to **every**
site of the class in the same diff, and prove each site reachable by probe rather than by
reading the predicate.

## Architectural Impact

Contained. One module changes (`agent/sdlc_router.py`), plus its unit tests and one docs
page. No schema, no Popoto model, no config, no new dependency, no CLI surface.

Three architectural notes worth recording:

- **Two freshness predicates is the design, not duplication.** They answer genuinely
  different questions and the difference is load-bearing at exactly one place: an absent
  signal. Non-terminal consumers (rows 8f/10, G3 leg 3) legitimately want the inert reading;
  terminal merge dispatch does not. Each gets its own named predicate with the absent-key
  behavior stated in its docstring, so neither call site has to remember an implicit rule.
- **The stand-down becomes a named concept.** Today "this lane has left the plan stage" is
  an idiom hand-copied into six predicates (and twice within two of them). After this change
  it is one function with one docstring, which is what makes the *next* plan-stage row
  correct by default.
- **Router/predicate convergence continues.** This is the fourth step of the #2062 program:
  the router's routing opinion now matches `tools/merge_predicate`'s authorization opinion on
  both terminal merge paths. Divergence between them is the drift this closes.

## Appetite

**Small.** Roughly a day. The bounding facts: two new small predicates, six predicate call
sites edited, two one-line guard changes, and one test file extended. Every state to be
tested has already been probed and recorded in the two issues' Recon Summaries, so the
expensive part — establishing what the router actually does — is already paid for.

If the work threatens to exceed the appetite, the thing to cut is **not** the sweep (that is
the issue) and **not** the G6 widening (ratified). Cut the rows 4b/4c de-duplication, which
is hygiene rather than a behavior fix.

## Prerequisites

- **PR #3246 merged** (`2fb7df519`, 2026-09-08) — establishes the canonical stand-down shape
  and creates `tests/unit/sdlc_router_decision/test_sdlc_router_decision_plan_rule_standdown.py`,
  the file this plan extends. ✅ satisfied.
- **Lane identity recorded** — slug `sdlc-3249`, worktree
  `/Users/valorengels/src/ai/.worktrees/sdlc-3249`, branch `session/sdlc-3249`, both already
  at `origin/main`. ✅ satisfied. Do not re-derive.
- No new dependency, service, credential, or migration. Nothing else blocks build.

## Solution

_placeholder_

## Failure Path Test Strategy

_placeholder_

## Test Impact

_placeholder_

## Rabbit Holes

_placeholder_

## Risks

_placeholder_

## Race Conditions

_placeholder_

## No-Gos (Out of Scope)

_placeholder_

## Update System

_placeholder_

## Agent Integration

_placeholder_

## Documentation

_placeholder_

## Success Criteria

_placeholder_

## Team Orchestration

_placeholder_

## Step by Step Tasks

_placeholder_

## Verification

_placeholder_

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

_placeholder_
