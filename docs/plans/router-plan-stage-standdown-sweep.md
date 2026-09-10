---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-09-09
tracking: https://github.com/tomcounsell/ai/issues/3249
also_closes: https://github.com/tomcounsell/ai/issues/3260
lane_slug: sdlc-3249
last_comment_id:
revision_applied: true
revision_applied_at: 2026-09-10T02:40:46Z
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

**#3260 widened to G6 and row 10 (ratified + critique correction).**
`guard_g6_terminal_merge_ready` (`:976`) has the identical absent-key fail-open on the
second terminal `/do-merge` fast-path. Its WS3d comment at `:971-975` states a fail-closed
*intent* — *"never fast-path a head_sha-stale APPROVED verdict ... (or the live-head lookup
failed, which fails closed toward stale)"* — that is **broader than the delivered
behavior**. Read literally, the comment covers the EMPTY-sentinel lookup-failure case, which
`_review_verdict_head_is_stale` already handles correctly. It never mentions the ABSENT-key
case, and that is the actual gap: on an absent key the predicate returns "not stale" and G6
fast-paths to merge. The comment is not a lie about the empty sentinel; it is an intent the
code does not carry all the way to the absent-key input.

`_rule_ready_to_merge` (row 10, `:2017`, stale call at `:2029`) is the **third** terminal
`/do-merge` site with the same absent-key fail-open, and the critique proved by live probe
that fixing only G3 leg 1 and G6 *relocates* the hole rather than closing it: with G6
widened, an absent-key APPROVED state falls through row 8f (inert on an absent key) and row
9 (DOCS complete) straight into row 10's `/do-merge`. Verified at plan-revision time:
baseline `decide_next_dispatch` on that state returns
`Dispatch(/do-merge, row_id='G6')`; with G6 alone widened it returns
`Dispatch(/do-merge, row_id='10')`. Row 10 is therefore in scope. Row 8f is **not** — it
dispatches `/do-pr-review`, not a merge, and the inert reading is correct for it.

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

**Revision-round re-verification (revision 1, `main` @ `5bf9333a4`).** The two critique
BLOCKERs were re-probed independently at revision time before being written into the plan;
neither is carried on the critique's word:

- **Blocker 1 (G3 leg 3).** `guard_g3_pr_lock` at `:518-531` re-read verbatim: leg 3's
  `elif` checks `review_status`, `REVIEW_APPROVED`, and `not _review_verdict_head_is_stale`
  — and **never** `docs_status`. Confirmed. It works today only because the unconditional
  leg 1 intercepts every `docs_status == completed` case first.
- **Blocker 2 (row 10).** Live `decide_next_dispatch` probe on the absent-`pr_head_sha`
  APPROVED/DOCS-complete state: baseline → `Dispatch(/do-merge, row_id='G6')`; with G6
  widened only → `Dispatch(/do-merge, row_id='10')`. The hole relocates. Confirmed.
- **Post-fix landing, probed (this is a correction to both the ratified design and the
  critique's proposed remedy).** With G6 **and** row 10 widened and row 8f untouched, the
  absent-key state returns **`Blocked(reason='no matching dispatch rule', guard_id='NO_RULE')`**,
  not `/do-pr-review` via row 8f. Row 10 is the last entry in `DISPATCH_RULES`, row 8f is
  inert on an absent key, and row 9 declines because DOCS is complete — so **no row owns the
  absent-key landing**. `Blocked` is the correct, fail-closed answer for a state that
  spike-1 shows no production producer can emit; it escalates to a human instead of merging
  on absent evidence. Widening row 8f to absorb it is explicitly out of scope (see
  **No-Gos**). The same probe confirms the controls are unaffected: fresh key →
  `Dispatch(/do-merge, row_id='G6')`; stale key → `Dispatch(/do-pr-review, row_id='8f')`;
  empty sentinel + `pr_head_sha_lookup_failed` → `Dispatch(/do-pr-review, row_id='8f')`.

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
     on an absent key. Its body keeps its exact current contract. Of its four current call
     sites it retains two: `:527` (G3 leg 3) and `:1998` (row 8f). The other two — `:976`
     (G6) and `:2029` (row 10) — swap to the new predicate because both are terminal
     `/do-merge` dispatches.
   - `_review_verdict_head_is_verified_fresh` (**new**) — *"is there positive evidence of
     freshness?"*, False on an absent key. Used **only** on terminal `/do-merge` dispatch,
     at exactly three sites: G3 leg 1 (`:518`), G6 (`:976`) and row 10 (`:2029`).
   - `_plan_stage_stood_down` (**new, shared**) — *"has this lane left the plan stage?"*,
     True when `meta['pr_number']` is set or `BUILD in (in_progress, completed)`.

   One further consumer changes without gaining a new predicate: **G3 leg 3** picks up an
   explicit `docs_status != STATUS_COMPLETED` gate. It never checked DOCS; it only behaved
   correctly because the unconditional leg 1 above it consumed every DOCS-complete case.

3. **Downstream authorization — `tools/merge_predicate.py`.** Untouched. It keeps its
   independent verdict and freshness checks and remains the authorization gate. This change
   converges the *router* onto the predicate; it does not move authorization into the router.

The stand-down flows the other way: rows 1/2/2b/2c/3 and 4b/4c call `_plan_stage_stood_down`
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
- **#2062 shipped the freshness predicate and a comment stating a fail-closed intent
  broader than the delivered behavior.** G6's `:971-975` comment is literally true about
  the case it names — the EMPTY-sentinel lookup failure, which the predicate does handle
  correctly. What it does not name, and what the code does not do, is the ABSENT-key case.
  A correct mechanism plus a comment whose stated intent runs past it is how the gap stayed
  invisible for a release cycle.
- **The same fix, applied to two of three sites, would have relocated the hole.** The
  critique's row-10 probe is the third instance of this plan's own lesson: a defect class
  closes on a sweep of every site, proven by probe, not on the sites the design happened to
  enumerate.

The lesson carried into this plan: express the condition **once**, apply it to **every**
site of the class in the same diff, and prove each site reachable by probe rather than by
reading the predicate.

## Architectural Impact

Contained. One module changes (`agent/sdlc_router.py`), plus its unit tests and one docs
page. No schema, no Popoto model, no config, no new dependency, no CLI surface.

Three architectural notes worth recording:

- **Two freshness predicates is the design, not duplication.** They answer genuinely
  different questions and the difference is load-bearing at exactly one place: an absent
  signal. Non-terminal consumers (row 8f, G3 leg 3) legitimately want the inert reading;
  terminal merge dispatch (G3 leg 1, G6, row 10) does not. **The split is by dispatch
  terminality, not by guard-vs-row**: row 10 is a dispatch-table row and still takes the
  strict predicate because it dispatches `/do-merge`; row 8f is right next to it and keeps
  the inert one because it dispatches `/do-pr-review`. Each gets its own named predicate with the absent-key
  behavior stated in its docstring, so neither call site has to remember an implicit rule.
- **The stand-down becomes a named concept.** Today "this lane has left the plan stage" is
  an idiom hand-copied into seven predicates (and twice within two of them). After this
  change it is one function with one docstring — with **no** remaining hand-written copy,
  row 2b's included — which is what makes the *next* plan-stage row correct by default.
- **Router/predicate convergence continues.** This is the fourth step of the #2062 program:
  the router's routing opinion now matches `tools/merge_predicate`'s authorization opinion on
  **all three** terminal merge paths. Divergence between them is the drift this closes.

## Appetite

**Small.** Roughly a day. The bounding facts: two new small predicates, seven predicate
call sites edited, three terminal merge-site changes (G3 leg 1, G6, row 10) plus G3 leg 3's
DOCS gate, and one test file extended. Every state to be tested has already been probed and
recorded in the two issues' Recon Summaries or in this plan's revision-round probes, so the
expensive part — establishing what the router actually does — is already paid for.

**There is no scope cut available in this plan.** Every item is either the issue itself or
ratified: the rows 1/2/2b/2c/3 sweep is #3249; the 4b/4c de-duplication is ratified design
Part C and is separately hard-required by Success Criterion 7 and tests T7/T8; the G6 and
row-10 widenings are the #3260 fix (dropping row 10 relocates the hole rather than closing
it). Pre-authorizing the 4b/4c cut is exactly the "narrow it to keep the diff small"
reflex #3249 was filed against, so it is not offered here.

The only genuinely discretionary work is the optional polish in the **Documentation**
section — the SKILL.md dispatch-table wording refinements beyond the G3/G6/row-10 condition
rows. If the appetite is threatened, that is the sole item to trim, and it must be named in
the PR body. If ratified scope is nonetheless dropped, Success Criterion 7 and tests T7/T8
must be struck in the same revision and the drop stated explicitly in the PR body — never
silently.

## Prerequisites

- **PR #3246 merged** (`2fb7df519`, 2026-09-08) — establishes the canonical stand-down shape
  and creates `tests/unit/sdlc_router_decision/test_sdlc_router_decision_plan_rule_standdown.py`,
  the file this plan extends. ✅ satisfied.
- **Lane identity recorded** — slug `sdlc-3249`, worktree
  `/Users/valorengels/src/ai/.worktrees/sdlc-3249`, branch `session/sdlc-3249`, both already
  at `origin/main`. ✅ satisfied. Do not re-derive.
- No new dependency, service, credential, or migration. Nothing else blocks build.

## Solution

All code changes are in `agent/sdlc_router.py`. All test changes are in
`tests/unit/sdlc_router_decision/test_sdlc_router_decision_plan_rule_standdown.py`.

### 1. New predicate `_review_verdict_head_is_verified_fresh` (added next to `:1385`)

A narrow sibling of `_review_verdict_head_is_stale`, **not** a modification of it. The
existing predicate's absent-key contract (*"key ABSENT from context → signal not supplied →
False (inert; other rules own routing)"*) is legitimate for its non-terminal consumers and
**must not change**.

```python
def _review_verdict_head_is_verified_fresh(stage_states: dict, meta: dict, context: dict) -> bool:
    """Return True only on POSITIVE evidence that the REVIEW verdict judged the live head.

    Narrow sibling of :func:`_review_verdict_head_is_stale`, for TERMINAL
    ``/do-merge`` dispatch only (G3 leg 1, G6). The two differ on exactly two
    inputs, both deliberate:

    - ``pr_head_sha`` ABSENT from context → **False** here (no evidence, no
      merge), where the stale predicate returns False meaning "inert".
    - no recorded REVIEW verdict → **False** here, where the stale predicate
      returns False meaning "the no-verdict recovery rows own this".

    Requiring presence is free in production: ``tools/sdlc_next_skill._build_context``
    sets ``pr_head_sha`` unconditionally whenever ``pr_number`` is set and a
    REVIEW verdict is recorded — a real SHA, or ``""`` plus
    ``pr_head_sha_lookup_failed`` on lookup failure. Both conditions hold on
    every path that reaches either call site, so the key is never absent for a
    live lane; requiring it closes the hole against non-CLI and future callers.
    """
    if "pr_head_sha" not in context:
        return False
    head_sha = context.get("pr_head_sha") or ""
    if not head_sha:
        return False  # fail-closed lookup-failure sentinel
    if not _latest_review_verdict(stage_states, meta).strip():
        return False
    recorded_head = _latest_review_head_sha(stage_states, meta)
    if not recorded_head:
        return False  # unattributable verdict is never "verified fresh"
    return recorded_head.lower() == head_sha.lower()
```

### 2. G3 leg 1 requires an APPROVED verdict AND verified-fresh head (`:518`)

```python
if (
    review_status == STATUS_COMPLETED
    and docs_status == STATUS_COMPLETED
    and REVIEW_APPROVED in review_verdict_norm
    and _review_verdict_head_is_verified_fresh(stage_states, meta, context or {})
):
    target = SKILL_DO_MERGE
    suffix = "review clean and docs complete"
```

**Leg 3 needs a DOCS gate in the same hunk — fall-through is NOT already correct.** Leg 3
(`:524-531`) checks `review_status`, `REVIEW_APPROVED` and `not
_review_verdict_head_is_stale`, and **never checks `docs_status`**. It only behaves
correctly today because the unconditional leg 1 intercepts every `docs_status == completed`
case before leg 3 is reached. Once leg 1 requires a verified-fresh head, the ABSENT-key
state falls to leg 3, which reads the UNMODIFIED (and inert-on-absent-key)
`_review_verdict_head_is_stale` and dispatches `/do-docs` with the reason *"review APPROVED
and docs pending"* while DOCS is already `completed`. Driver-verified by probe:
`guard_g3_pr_lock` with `REVIEW=completed`, `APPROVED`, `pr_head_sha` absent returns
`Dispatch(/do-docs, row_id='G3')`.

So leg 3's `elif` gains one clause, in the same hunk as the leg-1 change:

```python
elif (
    review_status == STATUS_COMPLETED
    and docs_status != STATUS_COMPLETED
    and REVIEW_APPROVED in review_verdict_norm
    and not _review_verdict_head_is_stale(stage_states, meta, context or {})
):
```

Leg 3 keeps its existing `_review_verdict_head_is_stale` call — it dispatches `/do-docs`,
not a merge — so Success Criterion 1's call-site count for the NEW predicate is unaffected
by this clause.

With both clauses in place the fall-through is correct: `CHANGES REQUESTED` reaches leg 2
(`/do-patch`); an APPROVED that is stale, unverifiable, or absent-key reaches leg 4
(`/do-pr-review`); and an APPROVED with DOCS genuinely pending still reaches leg 3
(`/do-docs`), which T11b pins.

**Deliberate strengthening over #3260's stated AC.** The acceptance criteria in #3260's
comment thread (comment `5583298719`, carried over from the duplicate #3261) ask for
`not _review_verdict_head_is_stale(...)`. The ratified design supersedes that with the
strictly stronger `_review_verdict_head_is_verified_fresh(...)`: it satisfies the AC on every
input the AC describes and additionally refuses the ABSENT-key case, which the AC's version
would pass. The same comment's other three ACs — the `REVIEW_APPROVED` requirement, the
`CHANGES REQUESTED` test, the stale-head test — are T9 and T10, and its RED requirement
(*"a guard that certifies absence is worthless until it has been seen to fail on the
known-bad code"*) is this plan's rule 1 under **Failure Path Test Strategy**.

### 3. G6 uses the same predicate (`:976`) — RATIFIED WIDENING, strictly one line

```python
if not _review_verdict_head_is_verified_fresh(stage_states, meta, context):
    return None
```

replacing `if _review_verdict_head_is_stale(stage_states, meta, context):`. The WS3d comment
directly above (`:971-975`) is updated in the same hunk to name the ABSENT-key case
explicitly, so the comment's stated intent and the delivered behavior finally line up. Word
it as an extension, not a correction of a falsehood: the existing sentence is accurate about
the empty sentinel; what it lacked was the absent-key clause.

**Conditions on this change, non-negotiable:**
- Strictly the predicate swap plus its comment. Do **not** touch G6's other gates
  (`pr_number` `:953-955`, `pr_merge_state`, `ci_all_passing`, the DOCS gate, the
  `REVIEW_APPROVED` gate `:966-969`) and do **not** touch row 8f.
- **The PR body must name the deliberate widening** and cite the WS3d comment at
  `agent/sdlc_router.py:971-975` — stating that the comment is evidence of G6's fail-closed
  **intent**, and that the ABSENT-key case is the newly closed gap the comment never
  covered. Do not claim the comment already asserts the absent-key behavior; it does not,
  and a reviewer reading it literally will mark the citation overclaimed.
- Fall-through is safe but is **not** row 8f — see step 3b and **Success Criterion 4**.
- Add `Refs #2062` alongside the closers.

### 3b. Row 10 uses the same predicate (`:2029`) — REQUIRED, not optional

`_rule_ready_to_merge` (row 10, `:2017`) is the third terminal `/do-merge` site and carries
the identical absent-key fail-open. Without this hunk the two-site fix **relocates** the
#3260 hole from G6 to row 10 instead of closing it (probe evidence in **Freshness Check**).

```python
# WS3d (#2062) / #3260: a terminal merge dispatch requires POSITIVE evidence
# that the APPROVED verdict judged the live head. An absent pr_head_sha signal
# is not evidence — row 10 must decline rather than merge on it.
if not _review_verdict_head_is_verified_fresh(stage_states, meta, context):
    return False
```

replacing `if _review_verdict_head_is_stale(stage_states, meta, context): return False`.

**Where the absent-key state lands, stated exactly (probed, not inferred).** Row 10 is the
last entry in `DISPATCH_RULES`; row 8f is inert on an absent key and row 9 declines because
DOCS is complete. So once G6 and row 10 both decline, **no row owns the state** and
`decide_next_dispatch` returns
`Blocked(reason='no matching dispatch rule', guard_id='NO_RULE')`.

That is the intended outcome, not a gap:
- It is fail-closed. The alternative is merging on absent freshness evidence, which is the
  defect.
- Production cannot reach it. spike-1 traced the sole producer:
  `tools/sdlc_next_skill._build_context` sets `pr_head_sha` unconditionally whenever
  `pr_number` is set and a REVIEW verdict is recorded, and both conditions hold on every
  path that reaches row 10. The `Blocked` is a backstop against non-CLI and future callers.
- Widening row 8f to absorb it is **out of scope** (see **No-Gos**): 8f dispatches
  `/do-pr-review`, not a merge, so the inert reading is correct for it, and changing it
  would convert a fail-closed escalation into a silent re-review loop for a state that
  should never occur.

T14 pins this landing by `guard_id`, and T14b pins row 10 in isolation with G6 removed.

### 4. New shared predicate `_plan_stage_stood_down` (added above row 1, near `:1121`)

The #3249 condition, expressed **once**:

```python
def _plan_stage_stood_down(stage_states: dict, meta: dict) -> bool:
    """Return True when the lane has moved past the plan stage (#3249).

    One definition for the step-aside that rows 1, 2, 2c and 3 were each
    supposed to carry and hand-copied inconsistently. Two signals:

    - ``pr_number`` set — a PR-stage lane has no plan-stage question left to
      answer; rows 7-10 own that state.
    - ``BUILD`` at ``in_progress`` or ``completed`` — the plan was accepted when
      the build was dispatched. Row 5 (``_rule_branch_exists_no_pr``) owns the
      pre-PR resume: its predicate is ``BUILD == in_progress OR
      context['branch_exists'] is True``, so the branch half answers regardless
      of BUILD status. Nothing is stranded.
    """
    if meta.get("pr_number"):
        return True
    return stage_states.get("BUILD") in (STATUS_IN_PROGRESS, STATUS_COMPLETED)
```

### 5. Route the five plan-stage rows through it

| Row | Function | Line | Today | Change |
|-----|----------|------|-------|--------|
| 1 | `_rule_no_plan` | `:1121` | `pr_number` only | replace with `_plan_stage_stood_down` |
| 2 | `_rule_plan_not_critiqued` | `:1153` | **neither** | add `_plan_stage_stood_down` as the first check |
| 2b | `_rule_critique_verdict_stale` | `:1594` (pair at `:1641-1644`) | hand-written copy of both signals | replace the pair with `_plan_stage_stood_down` |
| 2c | `_rule_critique_in_progress_no_verdict` | `:1650` | `pr_number` only | replace with `_plan_stage_stood_down` |
| 3 | `_rule_critique_needs_revision` | `:1180` | `pr_number` only | replace with `_plan_stage_stood_down` |

Each becomes, as the first statement in the predicate body:

```python
if _plan_stage_stood_down(stage_states, meta):
    return False
```

Row 2's docstring gains a sentence recording that it had no step-aside at all and why
(#3249); rows 1/2c/3's existing step-aside comments are rewritten to name the shared helper
rather than restating the condition.

**Row 2b is in scope (critique ruling, 2026-09-10).** The ratified design cites row 2b's
`:1641-1644` pair as the *pattern source*, which says where the shape came from, not that
2b must keep its own copy. Leaving it converted-by-nobody would ship the sweep with the
last hand-written copy of the exact condition the sweep exists to express once, and would
make this plan's own Success Criterion 5 false on landing. The conversion is provably
behavior-identical — 2b's inline pair is literally the helper's body, in the same order:

```python
if meta.get("pr_number"):
    return False
if stage_states.get("BUILD") in (STATUS_IN_PROGRESS, STATUS_COMPLETED):
    return False
```

Scope cost is two lines. Keep row 2b's long `#3237` docstring, rewritten only to name the
helper instead of restating the condition. Pinned by T5b plus its negative control.

### 6. Fold rows 4b/4c's duplicated `pr_number` checks into the helper

Rows 4b (`~:1214`) and 4c (`~:1249`) each check `meta.get("pr_number")` **twice** in the
same predicate — the hand-copied duplication #3249 exists to eliminate. Replace the leading
`if meta.get("pr_number") or stage_states.get("BUILD") == STATUS_COMPLETED: return False`
with `if _plan_stage_stood_down(stage_states, meta): return False`, and delete the redundant
second `if meta.get("pr_number"): return False` further down.

**Behavior-identical, and this must be argued explicitly, not assumed.** The helper is
broader than the check it replaces (it also catches `BUILD == in_progress`), but both rows
end with `return build_status in (None, "pending", "ready")`, which already excludes
`in_progress`. So no input changes answer.

**Keep rows 4a/4b/4c's `build_status in (None, "pending", "ready")` gates.** All three
carry it — 4a at `:1210`, 4b at `:1247`, 4c in its own trailing return — and all three are
strictly NARROWER than the shared stand-down, because they also exclude `BUILD == failed`.
The helper is added *alongside* them, **never substituted for them**; substituting would be
a behavior change. Row 4a is otherwise untouched. T8 pins the `BUILD == failed` behavior of
all three rows, 4b included.

### 7. Docs

Update `docs/features/gh-stale-state-verdict-gate.md` to describe the two-predicate split
and the terminal-dispatch rule (see **Documentation**).

### What this plan does NOT change

- `_review_verdict_head_is_stale` itself. Its body is byte-identical after this change; it
  retains its call sites at `:527` (G3 leg 3, whose surrounding `elif` gains a DOCS clause
  but whose predicate call is unchanged) and `:1998` (row 8f, entirely untouched).
- Row 8f (`_rule_review_verdict_head_stale`). It dispatches `/do-pr-review`, not a merge.
- `tools/merge_predicate.py` — authorization stays where it is, and is not relaxed.
- Row 5. See **No-Gos**.

## Failure Path Test Strategy

All new tests go in
`tests/unit/sdlc_router_decision/test_sdlc_router_decision_plan_rule_standdown.py`, using the
existing helper style (`_build_in_progress_states`, `_plan_context`, `_approved_pr_states`,
`_approved_pr_meta`). Run with `scripts/pytest-clean.sh`, targeted node IDs only, never bare
`pytest`, never `pkill -f pytest`.

**Two hard rules, both from the ratified design:**

1. **Every new assertion is proven RED before it is made green**, and the RED output is
   captured as evidence in the PR body. An assertion that has never failed proves nothing.
2. **Reachability is proven by direct `decide_next_dispatch` probes, not by inference** —
   one probe per row and per guard leg touched. Reading a predicate and concluding it fires
   is exactly the reasoning that let this class survive three rounds.

### Coverage matrix

| # | Path | Input state | Expected after fix |
|---|------|-------------|--------------------|
| T1 | row 1 | `PLAN=in_progress`, no plan doc, `BUILD=in_progress`, no PR, branch exists | `Dispatch(/do-build, row_id='5')` |
| T2 | row 2 | `PLAN=completed`, `CRITIQUE=pending`, open PR #999, `last_dispatched_skill=/do-build`, `REVIEW=pending` | `Dispatch(/do-pr-review, row_id='7')` |
| T3 | row 2 | same, no PR, `BUILD=in_progress`, branch exists | `Dispatch(/do-build, row_id='5')` |
| T4 | row 2c | `CRITIQUE=in_progress`, no verdict, `BUILD=in_progress`, no PR, branch exists | `Dispatch(/do-build, row_id='5')` |
| T5 | row 3 | `NEEDS REVISION` verdict, **`context['current_plan_hash']` DIFFERENT from the verdict's `artifact_hash`** (see the G5 note below — mandatory, or row 3 is never reached), `BUILD=in_progress`, no PR, branch exists | `Dispatch(/do-build, row_id='5')`; pre-fix `Dispatch(/do-plan, row_id='3')` |
| T5b | **row 2b** | stale critique verdict, `BUILD=in_progress`, no PR, branch exists | `Dispatch(/do-build, row_id='5')` — 2b converted to the shared helper |
| T5c | row 2b negative control | genuinely stale critique verdict, `BUILD=pending`, no PR | row 2b still fires `/do-plan-critique` — the conversion is behavior-identical |
| T6 | rows 1/2/2b/2c/3 negative control | `BUILD=pending`, no PR. **The row-3 leg needs the same changed-plan-hash setup as T5**, or G5 answers instead of row 3 | each row still fires its own skill — the sweep must not disable the rows |
| T7 | rows 4b/4c | the `pr_number` and `BUILD=completed` states each row already refuses | unchanged answers (refactor is behavior-identical) |
| T8 | rows 4a/**4b**/4c narrowing guard | `BUILD=failed`, with-concerns verdict, no PR | all three rows still decline — proves the narrower `build_status` gate was not replaced by the broader helper. Row 4b must be asserted explicitly, not just 4a/4c |
| T9 | G3 leg 1 | `REVIEW=completed`, `DOCS=completed`, verdict `CHANGES REQUESTED` at live head | `Dispatch(/do-patch, row_id='G3')` (leg 2) |
| T10 | G3 leg 1 | markers completed, `APPROVED` recorded against an OLDER head | `Dispatch(/do-pr-review, row_id='G3')` (leg 4) |
| T11 | G3 legs 1+3 | `REVIEW=completed`, **`DOCS=completed`**, `APPROVED`, **`pr_head_sha` ABSENT** | `Dispatch(/do-pr-review, row_id='G3')` (leg 4). **NOT `/do-merge` and NOT `/do-docs`** — without leg 3's new `docs_status != STATUS_COMPLETED` clause this state returns `Dispatch(/do-docs, row_id='G3')`, which is the driver-verified pre-fix answer |
| T11b | G3 leg 3 preserved | `REVIEW=completed`, **`DOCS=pending`**, `APPROVED` at the live head | still `Dispatch(/do-docs, row_id='G3')` — pins that the leg-3 narrowing did not disable leg 3 |
| T12 | G3 leg 1 | markers completed, `APPROVED`, `pr_head_sha == ""` + `pr_head_sha_lookup_failed` | `/do-pr-review`, NOT `/do-merge` |
| T13 | G3 leg 1 positive control | markers completed, `APPROVED` at the live head | still `Dispatch(/do-merge, row_id='G3')` |
| T14 | **G6 + row 10, absent key (end to end)** | `pr_number`, `pr_merge_state=CLEAN`, `ci_all_passing=True`, all stages settled, `DOCS=completed`, `APPROVED`, **`pr_head_sha` ABSENT** | `guard_g6_terminal_merge_ready` returns `None` **and** `decide_next_dispatch` returns `Blocked(guard_id='NO_RULE')`. Assert the `guard_id`, not just "not `/do-merge`" |
| T14b | **row 10 in isolation, absent key** | same state, `guard_g6_terminal_merge_ready` removed from `GUARDS` (monkeypatched) | `Blocked(guard_id='NO_RULE')`. Pre-fix this returns `Dispatch(/do-merge, row_id='10')` — that is the RED that proves the hole was closed rather than relocated |
| T15 | G6 positive control | same but `pr_head_sha` matches the verdict's head | still `Dispatch(/do-merge, row_id='G6')` |
| T16 | G6 stale control | same but `pr_head_sha` differs | still `None` from G6 → `Dispatch(/do-pr-review, row_id='8f')` (pre-existing behavior, pinned) |
| T16b | row 10 empty-sentinel control | `pr_head_sha == ""` + `pr_head_sha_lookup_failed` | `Dispatch(/do-pr-review, row_id='8f')` — the empty sentinel keeps landing on 8f; only the ABSENT key escalates |
| T17 | `_review_verdict_head_is_stale` unchanged | absent key, with a recorded verdict | still returns `False` — pins that the existing contract was not modified |
| T18 | row 8f untouched | absent key, APPROVED, `pr_number` set | `_rule_review_verdict_head_stale` still returns `False` — pins that 8f kept the inert predicate |

**T14 and T14b are the mandatory REDs for the #3260 widening, and both must be RED on the
ABSENT-key input specifically.** T16's stale-key path already passes today and proves
nothing about this change. T14b is the one that distinguishes *closing* the hole from
*relocating* it: run it with G6 monkeypatched out of `GUARDS` so row 10 is exercised
directly. If either is green before the code change, the test is wrong — fix the test, do
not proceed.

**T14's expected value is `Blocked`, not `/do-pr-review`.** This corrects the ratified
design's Part B fall-through claim and the critique's proposed remedy alike; the landing was
established by live probe at revision time (see **Freshness Check**). Row 8f is inert on an
absent key, row 9 declines on DOCS complete, and row 10 is the last rule — so no row owns
the state. Asserting `/do-pr-review` here would only pass by also widening row 8f, which is
a No-Go.

### Guard-ordering hygiene for the G3 probes

G3 only engages when `last_dispatched_skill` / `proposed_skill` puts it in scope, and G6 can
answer first. Follow the recon's shape: set `last_dispatched_skill=/do-plan-critique` and
`pr_merge_state="DIRTY"` on the G3 probes so G6 cannot pre-empt them, and use
`pr_merge_state="CLEAN"` only on the G6 probes.

### Guard-ordering hygiene for the row-3 probes: G5 pre-empts row 3 (probed)

**T5 and T6's row-3 leg are unreachable unless the plan hash is deliberately changed.** Row
3 (`_rule_critique_needs_revision`) sits behind `guard_g5_artifact_hash_cache`, which
short-circuits whenever the CRITIQUE verdict's `artifact_hash` equals
`context["current_plan_hash"]` and returns the cached verdict's downstream decision. The
existing helpers in the test file line those two values up by default:
`_plan_context()` returns `{"current_plan_hash": _PLAN_HASH, ...}` and the state builders
stamp the same `_PLAN_HASH` into the verdict record. So the naive T5 shape never reaches row
3 at all — probed against both the patched and the unpatched router, **identical output on
both sides**:

```
Dispatch(skill='/do-plan',
         reason='G5: cached CRITIQUE verdict is NEEDS REVISION on unchanged plan hash',
         row_id='G5')
```

A test in that shape is green before and after the sweep, cannot be proven RED, and
therefore violates rule 1 above while appearing to pass. Give the row-3 probes a
`current_plan_hash` that differs from the verdict's `artifact_hash` so G5 steps aside. With
that one change the real RED/GREEN pair appears (both probed):

```
pre-fix : Dispatch(skill='/do-plan',  reason='Revise plan based on critique findings', row_id='3')
post-fix: Dispatch(skill='/do-build', reason='Build must create the PR — resume build',  row_id='5')
```

and the row-3 negative control (`BUILD=pending`, same changed hash) correctly stays
`Dispatch(row_id='3')` on both sides. Row 3 is the only swept row whose negative control
needs this setup; rows 1, 2, 2b and 2c are reached without touching the hash.

## Test Impact

- [ ] `tests/unit/sdlc_router_decision/test_sdlc_router_decision_plan_rule_standdown.py` —
      UPDATE: extend with T1–T18 above. The existing `TestRow2bStandsDownOnceBuildStarted`
      and `TestG3DocsLeg` classes stay as-is; new classes are added alongside them, and the
      module docstring's line "whose missing `pr_number` step-aside is tracked as #3249" is
      updated to record that #3249 has landed. `TestRow2bStandsDownOnceBuildStarted` must
      stay green **unmodified** after row 2b is converted to `_plan_stage_stood_down` — it
      is the behavior-identity proof for that conversion. `TestG3DocsLeg` must likewise stay
      green unmodified after leg 3 gains its `docs_status` clause: its cases run with DOCS
      pending, which the clause does not touch.
- [ ] `tests/unit/test_sdlc_router.py::TestHeadShaStaleness` (currently `:1477-1500`) — UPDATE (verify only): this is the direct
      unit test of `_review_verdict_head_is_stale`. That function is deliberately unchanged,
      so these cases must stay green **unmodified**. If any of them needs editing, the
      existing predicate was touched and the change is wrong. T17 pins the same contract from
      the router-decision side.
- [ ] `tests/unit/sdlc_router_decision/test_sdlc_router_decision_dispatch_rows.py` — AUDIT:
      the row-by-row table tests. Any case asserting rows 1/2/2c/3/4b/4c fire while
      `BUILD in (in_progress, completed)` or a PR is open is asserting the defect and must be
      UPDATED to the new expected routing, with the change called out in the PR body.
- [ ] `tests/unit/sdlc_router_decision/test_sdlc_router_decision_terminal.py` and
      `test_sdlc_router_decision_convergence.py` — AUDIT: these exercise G6, **row 10** and
      the merge fast-path. Any case that reaches `/do-merge` (via G6 or row 10) without a
      `pr_head_sha` in context now returns `Blocked(guard_id='NO_RULE')` and must be UPDATED
      by supplying the key — the shape production actually produces — never by relaxing the
      assertion. Expect this to be the largest audit in the lane: row 10 is the router's
      default merge landing and fixtures that omit `pr_head_sha` are common.
- [ ] `tests/unit/sdlc_router_decision/test_sdlc_router_decision_with_concerns.py` — AUDIT:
      rows 4b/4c live here. The refactor is behavior-identical, so every case must stay green
      unmodified; a failure means step 6 changed behavior and must be reverted to the literal
      duplicated checks.

No expected-failure markers exist for these defects — `grep -rn 'pytest.mark.xfail\|pytest.xfail('
tests/unit/sdlc_router_decision/` returns nothing, so there are no xfails (decorator or
runtime) to convert.

## Rabbit Holes

- **Unifying the two freshness predicates.** They look like near-duplicates and the pull to
  collapse them into one parameterized function is strong. Resist it: the difference is one
  boolean on one input, and the parameter would immediately be got wrong at a call site. Two
  named functions with two docstrings is the deliverable.
- **"While I'm here" guard reordering.** `GUARDS` evaluation order
  (`[T, G1, G2, G3, G4, G9, G8, G7, G5, G6]`) is pinned and load-bearing. Nothing in this
  plan requires touching it, and a reorder would invalidate every probe in the coverage
  matrix.
- **Auditing all ~25 dispatch rows for stand-downs.** The sweep is over the **plan-stage**
  rows the issue names (1, 2, 2c, 3, plus row 2b's copy and the 4b/4c duplication).
  PR-stage and patch-stage rows have different correct step-asides and are a separate class.
- **Widening row 8f so the absent-key state has a row to land on.** The `Blocked` is
  deliberate; see **No-Gos** and **Success Criterion 4**. The pull to "make the test say
  `/do-pr-review`" is exactly how this fail-closed escalation would get quietly removed.
- **Rewriting the recon probes as a reusable harness.** Tempting, and out of scope. Use the
  existing helper functions in the test file.
- **Chasing `tools/merge_predicate.py` into agreement.** It already agrees. This change moves
  the router toward the predicate; touching the predicate reverses the direction of the fix.
- **Fixing the "Known gap — stale REVIEW verdict after PATCH" note** in
  `.claude/skills-global/do-sdlc/SKILL.md:278`. That describes a *different* staleness axis
  (`/do-patch`-relative, `_review_verdict_is_stale`) and is not touched here.

## Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| The G6 and row-10 widenings read as out-of-scope hunks and get bounced at review | Medium | PR body names them explicitly, cites the WS3d comment at `agent/sdlc_router.py:971-975` as evidence of G6's fail-closed *intent* (not as an assertion of the absent-key behavior, which it never made), and quotes the row-10 relocation probe. Non-negotiable, not optional prose. |
| The absent-key state now escalates to `Blocked` instead of routing | Low | Intended and fail-closed; spike-1 shows no production producer can emit it. Pinned by T14/T14b on `guard_id`, and stated in Success Criterion 4 so it cannot be "fixed" later by widening row 8f. |
| A `pr_head_sha`-less fixture in an existing suite now hits `Blocked` and gets "fixed" by relaxing the assertion | Medium | Test Impact names the audit targets and requires supplying the key rather than weakening the expectation. Every changed existing assertion is enumerated in the PR body. |
| A live lane stalls because `pr_head_sha` is genuinely absent on some path not surveyed | Low | spike-1 traced the sole producer and both call sites' gates. Worst case is a `/do-pr-review` dispatch, not a bad merge — the failure mode is noise, not damage. |
| The 4b/4c refactor silently changes behavior | Medium | Argued explicitly in Solution step 6 (the trailing `build_status in (None, pending, ready)` already excludes `in_progress`), and pinned by T7/T8 plus the unmodified `test_..._with_concerns.py` suite. Any red there means revert step 6. |
| Existing tests encode the defect and get "fixed" by relaxing assertions | Medium | Test Impact lists the audit targets by file with explicit dispositions and requires supplying the real context key rather than weakening an assertion. Every changed existing assertion is called out in the PR body. |
| Standing four rows down strands a lane at `Blocked` | Low | spike-2 plus #3249's recorded second probe pass: every state lands on row 5 or row 7. The residual `Blocked` subcase (BUILD settled AND no live branch) is inherited from #3246 unchanged. |
| Rebase conflict with a parallel lane | Low | The two live lanes touch `scripts/`, `tools/process_lookup.py`, `monitoring/` and `agent/agent_session_queue.py` — disjoint from every file here. #3260 and #3249 are one lane precisely to avoid conflicting with each other. |
| Someone widens row 5 to catch the stood-down lanes | **High** | See No-Gos. This is the known trap and the failure is silent. |

## Race Conditions

The router is a pure function: `decide_next_dispatch(stage_states, meta, context)` performs
no I/O, makes no `gh` calls, and mutates no shared state. Nothing in this change introduces
async work, shared mutable state, or a cross-process handoff, so there is no new timing
hazard to design against.

One **pre-existing** ordering fact this change interacts with, worth stating so it is not
mistaken for a race: `context['pr_head_sha']` is a point-in-time read taken by
`tools/sdlc_next_skill._build_context` before the router runs. A commit landing between that
read and the merge dispatch would leave the router acting on a head that is one commit
behind. That window exists today and is unchanged here; it is closed downstream by
`tools/merge_predicate.py`, which re-reads at merge time. The change strictly narrows the
window (the router now refuses more, never fewer, states) and must not be relied on to close
it — that remains the merge predicate's job.

## No-Gos (Out of Scope)

- **DO NOT widen row 5 under any circumstances.** This is *the* trap, carried from #3246 and
  restated in #3249: *"do not widen row 5 to absorb these states. Row 5 precedes row 6, so
  widening it diverts `TEST == failed` lanes away from `/do-patch`."* Once rows 1/2/2c/3
  stand down on BUILD, those lanes need somewhere to land and row 5 is the tempting
  catch-all. It is the wrong place and **the failure is silent** — a lane with failing tests
  gets sent to `/do-build` instead of `/do-patch` and nothing reports it. The correct landing
  is row 5's **existing, unmodified** predicate: `build_status == in_progress OR
  context['branch_exists'] is True`. The branch half already answers regardless of BUILD
  status. **No widening is required, and none is permitted.**
- **DO NOT modify `_review_verdict_head_is_stale`.** Its absent-key → `False` contract is
  correct for its non-terminal consumers and is documented in its own docstring. The new
  behavior arrives as a sibling.
- **DO NOT change row 8f** (`_rule_review_verdict_head_stale`, `:1977-1998`). It dispatches
  `/do-pr-review`, not a merge, so the inert-on-absent-key reading is correct for it.
  Widening it would convert the deliberate fail-closed `Blocked` on the absent-key state
  into a silent re-review loop. Row 10 **is** in scope — it is a terminal `/do-merge`
  dispatch, and leaving it out relocates the #3260 hole instead of closing it (probe
  evidence in **Freshness Check**).
- **DO NOT replace rows 4a/4b/4c's `build_status in (None, "pending", "ready")` gates** with
  the shared helper. All three carry it and all three are strictly narrower (they also
  exclude `BUILD == failed`); substituting would be a behavior change. Add alongside, never
  substitute.
- **DO NOT touch G6's other gates** (`pr_merge_state`, `ci_all_passing`, the DOCS gate) or
  the `REVIEW_APPROVED` gate. One line plus its comment.
- **DO NOT relax or modify `tools/merge_predicate.py`.**
- **DO NOT touch the parallel lanes' files**: `scripts/`, `tools/process_lookup.py`,
  `monitoring/`, `agent/agent_session_queue.py`.
- **DO NOT edit the shared checkout root** at `/Users/valorengels/src/ai` for code. All code
  work happens in `/Users/valorengels/src/ai/.worktrees/sdlc-3249` on branch
  `session/sdlc-3249`. Plan and `.md` docs are the exception: they commit directly on `main`.

## Update System

No update-system changes required. This is a pure change to an in-repo Python module and its
tests: no new dependency, no new config file, no new env key, no new launchd plist, no
migration. `scripts/remote-update.sh` and the `/update` skill propagate it as an ordinary
code change on `main`.

One propagation note that is **not** an update-script change: the dispatch/guard tables in
`.claude/skills-global/do-sdlc/SKILL.md` are hardlinked to `~/.claude/skills/` by `/update`.
Editing that file in-place (never replace-and-rename, which breaks the hardlink) means a
routine `/update` after merge carries the corrected G3/G6 descriptions fleet-wide. Per
project convention, run `/update` after this merges so running services pick up the new ref.

## Agent Integration

No agent integration required — this is entirely internal to the router. No new CLI entry
point in `pyproject.toml [project.scripts]`, and the bridge imports nothing new.

The router already reaches the agent through the existing surface: `sdlc-tool next-skill`
(`tools/sdlc_next_skill.py`) calls `decide_next_dispatch` and is the only caller that
assembles a real `context`. That path is unchanged in shape — the same `pr_head_sha` key the
producer already writes is simply read by one more predicate. The `/sdlc` router skill and
`/do-sdlc` supervisor consume `next-skill`'s output and need no change beyond the doc updates
below.

## Documentation

- [ ] Update `docs/features/gh-stale-state-verdict-gate.md` — document the two-predicate
      split: `_review_verdict_head_is_stale` (inert on an absent signal, for non-terminal
      consumers) vs `_review_verdict_head_is_verified_fresh` (positive evidence required, for
      terminal `/do-merge` dispatch only), name all three terminal sites (G3 leg 1, G6, row
      10), and record that an absent `pr_head_sha` on a merge-ready state escalates to
      `Blocked(guard_id='NO_RULE')` by design.
- [ ] Update `.claude/skills-global/do-sdlc/SKILL.md:248` — G3's ladder description: leg 1 now
      reads "`/do-merge` (REVIEW and DOCS complete, verdict `APPROVED`, head verified fresh)",
      and leg 3 gains "AND DOCS not completed".
- [ ] Update `.claude/skills-global/do-sdlc/SKILL.md:254` — G6's condition row: add "AND the
      REVIEW verdict's head is verified fresh against `context['pr_head_sha']`".
      Edit in place; do not replace-and-rename (it is hardlinked to `~/.claude/skills/`).
- [ ] Update the same SKILL.md dispatch table's **row 10** entry with the same
      verified-fresh condition, and leave row 8f's entry unchanged.
- [ ] Update the plan-stage rows in the same SKILL.md dispatch table so rows 1, 2, 2b, 2c and
      3 all record the shared plan-stage stand-down.
- [ ] Update the "Open-PR step-asides" note (`.claude/skills-global/do-sdlc/SKILL.md:263`) to
      name the shared `_plan_stage_stood_down` condition instead of listing rows individually.
- [ ] Update the module docstring of
      `tests/unit/sdlc_router_decision/test_sdlc_router_decision_plan_rule_standdown.py`,
      which currently says row 2's missing step-aside "is tracked as #3249" — describe the
      new status quo, no historical artifact.
- [ ] No new feature doc and no `docs/features/README.md` entry: this modifies behavior
      already documented by `gh-stale-state-verdict-gate.md` and the do-sdlc dispatch tables
      rather than adding a capability.
- [ ] No `docs/infra/` doc: no new dependency, service, external API call, quota, or
      deployment change.

## Success Criteria

1. `_review_verdict_head_is_verified_fresh` exists in `agent/sdlc_router.py`, returns
   `False` on an ABSENT `pr_head_sha` key, and is called from exactly **three** sites — the
   three terminal `/do-merge` dispatches: G3 leg 1 (`:518`),
   `guard_g6_terminal_merge_ready` (`:976`), and `_rule_ready_to_merge` (row 10, `:2029`).
2. `_review_verdict_head_is_stale` is byte-identical to its pre-change form, and
   `tests/unit/test_sdlc_router.py::TestHeadShaStaleness` passes **unmodified** — cite the class, not a line range, which has already drifted once (the absent-key case `test_inert_when_context_signal_absent` is at `:1490` on this baseline, not `:1484`).
3. G3 leg 1 requires `REVIEW_APPROVED in review_verdict_norm` AND a verified-fresh head.
   `CHANGES REQUESTED` routes to `/do-patch`; a stale or unverifiable `APPROVED` routes to
   `/do-pr-review`; a fresh `APPROVED` with DOCS complete still routes to `/do-merge`.
3b. G3 leg 3 additionally requires `docs_status != STATUS_COMPLETED`. An absent-key
   `APPROVED` with DOCS complete routes to `/do-pr-review` (leg 4), **not** `/do-docs`; an
   `APPROVED` at the live head with DOCS pending still routes to `/do-docs`.
4. G6 returns `None` on an absent `pr_head_sha`, and row 10 declines on the same state, so
   `decide_next_dispatch` returns `Blocked(reason='no matching dispatch rule',
   guard_id='NO_RULE')` — a deliberate fail-closed escalation, asserted by `guard_id`. Row
   8f is unmodified and does not absorb the state. G6's other gates are untouched, and its
   `:971-975` comment now names the ABSENT-key case alongside the empty sentinel it already
   described.
4b. Row 10 declines the absent-key state **in isolation**, proven with G6 monkeypatched out
   of `GUARDS` (T14b). Pre-fix that same probe returns `Dispatch(/do-merge, row_id='10')`.
   The stale-key and empty-sentinel states still land on row 8f → `/do-pr-review`.
5. `_plan_stage_stood_down` exists as a single shared predicate and is the **only** place the
   #3249 condition is written — **row 2b included**; no hand-written copy of the condition
   survives anywhere in the module. `grep -c 'meta.get("pr_number")' agent/sdlc_router.py` is
   strictly lower than before, and no plan-stage row re-states the BUILD condition inline.
6. Rows 1, 2, **2b**, 2c and 3 all call it. Row 2 — which had no step-aside at all — stands
   down on both an open PR and a running/completed BUILD. Row 2b's converted behavior is
   identical to its pre-change inline pair, proven by `TestRow2bStandsDownOnceBuildStarted`
   passing unmodified plus T5b/T5c.
7. Rows 4b and 4c each check `pr_number` exactly **once**, and rows 4a, **4b** and 4c all
   retain their narrower `build_status in (None, "pending", "ready")` gates.
8. Row 5 (`_rule_branch_exists_no_pr`) is **unchanged**. `git diff` shows no hunk touching it.
9. T1–T18 all pass, and each new assertion has a captured RED run from before the fix. The
   T14 and T14b REDs are on the ABSENT-key case specifically.
10. Full `tests/unit/sdlc_router_decision/` and `tests/unit/test_sdlc_router.py` are green via
    `scripts/pytest-clean.sh`; every existing assertion that changed is enumerated in the PR body.
11. `python -m ruff check` and `python -m ruff format` clean.
12. Every checkbox under **Documentation** is done.
13. PR body names the G6 **and row-10** widenings as deliberate; cites
    `agent/sdlc_router.py:971-975` as evidence of G6's fail-closed *intent* while stating
    plainly that the ABSENT-key case is the newly closed gap the comment never covered; and
    quotes the row-10 relocation probe (`Dispatch(/do-merge, row_id='10')` with G6 widened
    alone) as the reason row 10 is in scope.
14. Commit trailers carry `Closes #3260`, `Closes #3249`, `Refs #2062`.

## Team Orchestration

Single lane, single builder. No parallel sub-agents: every change lands in one file plus one
test file, so parallel edits would only manufacture conflicts.

- **Lane**: slug `sdlc-3249`, worktree `/Users/valorengels/src/ai/.worktrees/sdlc-3249`,
  branch `session/sdlc-3249`. Recorded, not re-derived.
- **Coordination with parallel lanes**: `pgrep-sweep-finish` (`scripts/`,
  `tools/process_lookup.py`, `monitoring/`) and the `agent/agent_session_queue.py` lane. File
  sets are disjoint; no handoff needed, no shared file to negotiate.
- **Commit early and often** to `session/sdlc-3249` at each numbered task below. A prior lane
  on this issue died at PLAN and lost everything; small checkpoints are the mitigation.
- **Plan/docs commits go on `main`** in the shared checkout, never on the feature branch, and
  are never left uncommitted across an await.

## Step by Step Tasks

1. **Set up.** Work in `/Users/valorengels/src/ai/.worktrees/sdlc-3249` on
   `session/sdlc-3249`; confirm it is at `origin/main` and rebase if not. Never edit the
   shared checkout root for code.
2. **Write the RED tests first.** Add T1–T18 to
   `tests/unit/sdlc_router_decision/test_sdlc_router_decision_plan_rule_standdown.py`. Run
   them via `scripts/pytest-clean.sh` with targeted node IDs and **capture the failing
   output**. Confirm T14 (G6 + row 10 end to end, ABSENT key) and T14b (row 10 in
   isolation, G6 monkeypatched out of `GUARDS`, ABSENT key) are both RED — if either is
   green, the test is wrong. Commit the RED tests.
3. **Add `_review_verdict_head_is_verified_fresh`** next to `_review_verdict_head_is_stale`
   (`:1385`), with the docstring from Solution step 1. Do not modify the existing predicate.
   Commit.
4. **Apply it to G3 leg 1** (`:518`) per Solution step 2, **and add leg 3's
   `docs_status != STATUS_COMPLETED` clause in the same hunk**. Run T9–T13 (T11 and T11b
   included); confirm green and confirm `TestG3DocsLeg` still passes unmodified. Commit.
5. **Apply it to G6** (`:976`) per Solution step 3 — the one-line swap plus the `:971-975`
   comment extension, nothing else. Commit.
5b. **Apply it to row 10** (`:2029`) per Solution step 3b. Run T14, T14b, T15, T16, T16b and
   T18; confirm T14/T14b flipped RED→green with a `Blocked(guard_id='NO_RULE')` assertion,
   and that T15/T16/T16b/T18 never regressed. Commit.
6. **Add `_plan_stage_stood_down`** per Solution step 4. Commit.
7. **Route rows 1, 2, 2b, 2c, 3 through it** per Solution step 5, updating each row's
   step-aside comment to name the helper and keeping row 2b's `#3237` docstring. Run T1–T6
   (T5b and T5c included); confirm green, including the negative controls, and confirm
   `TestRow2bStandsDownOnceBuildStarted` passes unmodified. Commit.
8. **Fold rows 4b/4c's duplicate `pr_number` checks** per Solution step 6, keeping
   4a/4b/4c's narrower `build_status` gates. Run T7, T8 (all three rows asserted) and the
   whole `test_sdlc_router_decision_with_concerns.py` suite unmodified; any red means revert
   this task. Commit.
9. **Audit the existing suites** named in **Test Impact**
   (`..._dispatch_rows.py`, `..._terminal.py`, `..._convergence.py`,
   `tests/unit/test_sdlc_router.py`). For each failure, decide UPDATE-the-expectation vs
   the-change-is-wrong, and record every changed assertion for the PR body. Confirm
   `test_sdlc_router.py::TestHeadShaStaleness` needed no edit. Commit.
10. **Run the full router suites** — `tests/unit/sdlc_router_decision/` and
    `tests/unit/test_sdlc_router.py` — via `scripts/pytest-clean.sh`. Never bare `pytest`,
    never `pkill -f pytest`.
11. **Documentation pass**: every checkbox under **Documentation**. Edit
    `.claude/skills-global/do-sdlc/SKILL.md` in place (hardlinked — never replace-and-rename).
    Commit.
12. **Quality gate**: `python -m ruff check` and `python -m ruff format`. Commit.
13. **Open the PR.** Body must include: the RED evidence for each new assertion (T14 and
    T14b's ABSENT-key REDs called out specifically); an explicit paragraph naming the **G6
    and row-10 widenings as deliberate**, citing the WS3d comment at
    `agent/sdlc_router.py:971-975` as evidence of G6's fail-closed *intent* (not as an
    assertion of the absent-key behavior it never made) and quoting the row-10 relocation
    probe; a sentence explaining that the absent-key state now lands on
    `Blocked(guard_id='NO_RULE')` by design and why row 8f was deliberately not widened;
    and the list of changed existing assertions. Trailers: `Closes #3260`, `Closes #3249`,
    `Refs #2062`.

## Verification

- **Reachability, not inference.** For each of rows 1, 2, 2b, 2c, 3, 4b, 4c, 10 and for G3
  legs 1 and 3 and G6, a direct `decide_next_dispatch` (or `guard_g6_terminal_merge_ready` /
  `guard_g3_pr_lock`) probe demonstrates both the pre-fix wrong answer and the post-fix right
  one. No claim of the form "reading the predicate, it must fire."
- **RED-before-green ledger.** Each new assertion has a captured failing run from before its
  fix landed. T14's and T14b's REDs are on the ABSENT-key input specifically; a RED captured
  on the stale-key input does not count and must be redone. T14b's pre-fix output must be
  literally `Dispatch(/do-merge, row_id='10')` — anything else means the probe is not
  exercising row 10.
- **Negative controls.** T5c, T6, T11b, T13, T15, T16, T16b and T18 prove the change did not
  simply disable the rows and guards it touches — the happy paths still route as before, and
  row 8f plus `_review_verdict_head_is_stale` are demonstrably unmodified.
- **Non-substitution proof.** T8 (`BUILD == failed`) proves rows 4a, 4b **and** 4c kept their
  narrower gate rather than inheriting the broader helper.
- **No-surviving-copy proof.** After the sweep, `grep -n 'STATUS_IN_PROGRESS, STATUS_COMPLETED'
  agent/sdlc_router.py` shows the BUILD half of the condition only inside
  `_plan_stage_stood_down` — row 2b's copy is gone.
- **Untouched-surface proof.** `git diff main -- agent/sdlc_router.py` is read end to end and
  shows: no hunk in `_review_verdict_head_is_stale`'s body, no hunk in
  `_rule_branch_exists_no_pr` (row 5), no hunk in row 8f, and no diff at all in
  `tools/merge_predicate.py`. Row 10 **does** carry a hunk — exactly one, the predicate swap
  plus its comment.
- **Suite green.** `scripts/pytest-clean.sh` over `tests/unit/sdlc_router_decision/` and
  `tests/unit/test_sdlc_router.py`, with a non-zero test count (the `ZERO TESTS EXECUTED`
  guard must not have fired).
- **Lint.** `python -m ruff check` and `python -m ruff format` clean.
- **Docs.** Every **Documentation** checkbox ticked; the SKILL.md hardlink intact after the
  edit.

## Probe Ledger (independent re-verification, 2026-09-10)

Every routing claim this plan relies on was re-probed independently at revision time, by
patching a **copy** of `agent/sdlc_router.py` (all three merge sites + the leg-3 DOCS gate +
the five stand-down rows), importing it under a separate module name, and calling the real
`decide_next_dispatch` / `guard_g3_pr_lock` against both the patched and the unpatched
module. No claim below is an inference.

This ledger exists because the round-1 design asserted a routing fact (*"G6's absent-key
fall-through lands on row 8f"*) from a **code comment** rather than a probe — in a lane
whose entire subject is code whose comments misdescribe its behavior. Every future edit to
this plan's routing claims must clear the same bar.

**Site enumeration** (`grep -n 'SKILL_DO_MERGE' agent/sdlc_router.py`): `:163` constant,
`:519` G3 leg 1, `:979` G6, `:2245` row 10. Exactly **three** dispatch sites; all three route
through `_review_verdict_head_is_verified_fresh` after this change.

| Probe | State | Unpatched (pre-fix) | Patched (post-fix) |
|---|---|---|---|
| G3 leg 1, absent key, `DOCS=completed` | leg-1 patch only | — | `Dispatch(/do-docs, row_id='G3')` ← blocker 1 confirmed |
| same, with leg-3 `docs_status != STATUS_COMPLETED` | | — | `Dispatch(/do-pr-review, row_id='G3')` |
| G6 shape, absent key, `DOCS=completed`, CLEAN+CI | end to end | `Dispatch(/do-merge, row_id='G6')` | `Blocked(reason='no matching dispatch rule', guard_id='NO_RULE')` |
| same, every `DISPATCH_RULES` predicate evaluated individually | | — | **zero rows accept** — confirms no row owns the state |
| positive control, `pr_head_sha` matches | | `/do-merge` G6 | `Dispatch(/do-merge, row_id='G6')` |
| stale-key control | | row 8f | `Dispatch(/do-pr-review, row_id='8f')` |
| empty-sentinel + `pr_head_sha_lookup_failed` | | row 8f | `Dispatch(/do-pr-review, row_id='8f')` |
| T1 row 1 | | `Dispatch(/do-plan, row_id='1')` | `Dispatch(/do-build, row_id='5')` |
| T2 row 2, open PR | | `Dispatch(/do-plan-critique, row_id='2')` | `Dispatch(/do-pr-review, row_id='7')` |
| T3 row 2, no PR | | `Dispatch(/do-plan-critique, row_id='2')` | `Dispatch(/do-build, row_id='5')` |
| T4 row 2c | | `Dispatch(/do-plan-critique, row_id='2c')` | `Dispatch(/do-build, row_id='5')` |
| T5 row 3, **changed plan hash** | | `Dispatch(/do-plan, row_id='3')` | `Dispatch(/do-build, row_id='5')` |
| T5 row 3, *unchanged* plan hash | the naive shape | `Dispatch(/do-plan, row_id='G5')` | `Dispatch(/do-plan, row_id='G5')` ← **identical, cannot go RED** |
| T5b row 2b conversion | | `Dispatch(/do-build, row_id='5')` | `Dispatch(/do-build, row_id='5')` ← behavior-identical, as ruled |
| T5c row 2b negative | `BUILD=pending`, stale verdict | `Dispatch(/do-plan-critique, row_id='2b')` | same |
| T6 negatives, rows 1/2/2c | `BUILD=pending` | each fires its own row | unchanged |

**Rejected alternative, also probed.** Widening row 8f to
`not _review_verdict_head_is_verified_fresh` *does* give the absent-key state a routable
landing (`Dispatch(/do-pr-review, row_id='8f')`) instead of `Blocked`. It is rejected because
the same edit diverts absent-key + `DOCS=pending` lanes away from row 9 `/do-docs` to
`/do-pr-review` — a behavior change on a second state, to buy a routable landing for a state
production cannot reach. `Blocked` is fail-closed and *reported*; the alternative is a silent
routing change. Row 8f stays untouched.

## Critique Results

FULL depth (force-FULL: `agent/sdlc_router.py` is a doctrine path). Roster 3/3 complete,
3/3 grounded. Mode: independent roster (3 critics). Both BLOCKERs were independently
re-verified by the driver with live `decide_next_dispatch` / `guard_g3_pr_lock` probes
against `main` before being recorded — neither is an inference.

**Revision 1 (2026-09-10): all six findings addressed in one pass.** Both BLOCKERs were
re-probed by the revising agent rather than accepted on the critique's word, and blocker 2
was escalated to the design owner, who ruled row 10 in scope and overturned the ratified
design's Part B fall-through claim. One finding-level correction went the other way: the
critique's proposed remedy for blocker 2 assumed a row-8f landing that the probe disproves
(see the disposition cell and **Freshness Check**). Row 2b was ruled into scope by the
same owner. Dispositions per finding: 6 addressed, 0 deferred, 0 rejected.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness (Skeptic); driver-verified | Solution step 2 claims "Fall-through is already correct and needs no other edit" — it is not. G3 leg 3 (`agent/sdlc_router.py:524-528`) never checks `docs_status`; it only works today because the unconditional leg 1 intercepts every `docs_status == completed` case first. Once leg 1 requires a verified-fresh head, the ABSENT-key state (T11) falls to leg 3, which reads the UNMODIFIED `_review_verdict_head_is_stale` (False on an absent key) and dispatches `/do-docs` with the reason "review APPROVED and docs pending" while DOCS is `completed`. T11's expected `/do-pr-review` is therefore wrong as written. Driver probe: `guard_g3_pr_lock` with `REVIEW=completed, APPROVED, pr_head_sha` absent returns `Dispatch(/do-docs, row_id='G3')`. | **ADDRESSED (revision 1).** Re-probed independently before acceptance: leg 3 confirmed to have no `docs_status` check. Solution step 2 now carries the `docs_status != STATUS_COMPLETED` clause with its own code block and the driver probe quoted; Data Flow records leg 3 as a changed consumer; Step-by-Step task 4 applies both clauses in the same hunk; T11's expectation corrected to `Dispatch(/do-pr-review, row_id='G3')` with the pre-fix `/do-docs` answer recorded inline; T11b added for `DOCS=pending` + live head; Success Criterion 3b added; Test Impact records that `TestG3DocsLeg` must stay green unmodified. | Add `and docs_status != STATUS_COMPLETED` to G3 leg 3's `elif` condition in the same hunk as the leg-1 change. Leg 3 keeps its existing `_review_verdict_head_is_stale` call, so Success Criterion 1's "exactly two call sites" for the NEW predicate still holds. Record the leg-3 gate in Solution step 2, in the Step-by-Step task 4, and fix T11's expected value; add a T11b covering `DOCS=pending` + absent key so the leg-3 narrowing is itself pinned. |
| BLOCKER | History & Consistency (Consistency Auditor); driver-verified | The G6 fall-through claim is false, and it makes Success Criterion 4, T14 and the No-Gos mutually unsatisfiable. On the ABSENT-key state G6 returns `None`, but row 8f (`_rule_review_verdict_head_stale`, `:1976-1999`) calls `_review_verdict_head_is_stale`, which returns `False` on an absent key — so 8f declines. Row 9 declines (DOCS complete). Row 10 (`_rule_ready_to_merge`, `:2018-2035`) calls the same inert predicate, so it also reads "not stale" and dispatches `/do-merge`. Driver probe with G6 removed from `GUARDS`: `Dispatch(/do-merge, row_id='10')`. The absent-key merge hole is relocated from G6 to row 10, not closed — and the No-Gos forbid touching row 10. | **ADDRESSED (revision 1) — escalated and ruled IN SCOPE by the design owner.** Re-probed independently: baseline absent-key → `Dispatch(/do-merge, row_id='G6')`; G6 widened alone → `Dispatch(/do-merge, row_id='10')`. Row 10 joins the widening as Solution step 3b; the new predicate now has THREE call sites (SC1); the No-Go became "DO NOT change row 8f" and explicitly places row 10 in scope. **One correction to the finding's own remedy:** with G6 and row 10 both widened and row 8f untouched, no row owns the absent-key state — probed landing is `Blocked(reason='no matching dispatch rule', guard_id='NO_RULE')`, not `/do-pr-review` via 8f. That is recorded as the intended fail-closed outcome in SC4, pinned by `guard_id` in T14, and pinned in isolation by the new T14b (G6 monkeypatched out; pre-fix RED is `Dispatch(/do-merge, row_id='10')`). Freshness Check carries the full probe transcript. | Row 10 must join the widening: `if not _review_verdict_head_is_verified_fresh(stage_states, meta, context): return False` in `_rule_ready_to_merge` (row 10 is a terminal `/do-merge` dispatch, exactly the class the new predicate is scoped to). Row 8f stays on the existing predicate. This makes the new predicate's call-site count THREE — update Success Criterion 1 and the No-Go "DO NOT change rows 8f or 10" to "DO NOT change row 8f" — and T14's expectation becomes `/do-pr-review` via row 8f only if 8f is also widened, so state explicitly which row owns the absent-key landing and pin it with a `row_id` assertion, not just a skill assertion. Escalate to the design owner before building: this is a fact that the ratified design's Part B got wrong, not a scope choice the builder may take alone. |
| CONCERN | Scope & Value (Simplifier) | The Appetite section pre-authorizes cutting the rows 4b/4c de-duplication under time pressure, but that fold is explicit ratified scope (design Part C: "Also fold in rows 4b/4c") and is separately hard-required by the plan's own Success Criterion 7 and by T7/T8. As written a builder could take the cut and ship a PR that fails the plan's own Definition of Done — the same "narrow it to keep the diff small" failure mode #3249 was filed against. | **ADDRESSED (revision 1) — ruled by the design owner.** The cut-line is deleted, not softened. Appetite now states plainly that no scope cut is available: the sweep is the issue, the 4b/4c fold is ratified Part C and hard-required by SC7/T7/T8, and dropping row 10 relocates rather than closes the #3260 hole. The only discretionary item named is the optional SKILL.md wording polish. The escape hatch the finding asked for survives only as a conditional: if ratified scope is dropped anyway, SC7 and T7/T8 must be struck in the same revision and the drop named in the PR body — never silently. | Replace the cut-line: the only cuttable item at Small appetite is the optional docs polish, not ratified scope. If 4b/4c is nonetheless cut, Success Criterion 7 and tests T7/T8 must be struck in the same revision and the drop named in the PR body — never silently. The 4b/4c edit itself is two lines per row: swap the leading `if meta.get("pr_number") or stage_states.get("BUILD") == STATUS_COMPLETED:` for `if _plan_stage_stood_down(stage_states, meta):` and delete the second `if meta.get("pr_number"): return False`. |
| CONCERN | History & Consistency (Archaeologist) | The Problem section says G6's WS3d comment at `:971-975` "already asserts the behavior it does not have" and calls it "the same lie." The comment's literal text covers the EMPTY-sentinel lookup-failure case only — which `_review_verdict_head_is_stale` handles correctly. It never mentions the ABSENT-key case, which is the actual gap. A reviewer reading the comment literally will read the PR body's citation as overclaimed. | **ADDRESSED (revision 1).** Every citation of `agent/sdlc_router.py:971-975` is reworded. The Problem section now says the comment states a fail-closed *intent broader than the delivered behavior*, is literally true about the EMPTY sentinel, and never mentions the ABSENT-key case — which is the actual gap. "Same lie" is gone. Why Previous Fixes Failed carries the same wording. Solution step 3 and Success Criterion 13 both require the PR body to cite the comment as evidence of intent while stating plainly that the absent-key case is the newly closed gap, with an explicit instruction not to overclaim. | Reword to: the comment states a fail-closed intent that is broader than the delivered behavior (it covers the empty sentinel but not the absent key). Carry the same wording into the PR-body paragraph required by Success Criterion 13, so the citation of `agent/sdlc_router.py:971-975` matches what the comment actually says. |
| NIT | Driver (structural) | Solution step 6 and No-Go 4 both say "rows 4a/4c" carry the narrower `build_status in (None, "pending", "ready")` gate, but row 4b carries it too (`:1247`). T8 likewise only names 4a/4c, so row 4b's `BUILD == failed` behavior is unpinned by any test. | **ADDRESSED (revision 1).** Confirmed at `agent/sdlc_router.py:1247` that row 4b carries the `build_status in (None, "pending", "ready")` gate too. Solution step 6, the No-Go, Success Criterion 7, T8 and the Verification non-substitution bullet all now say "rows 4a/4b/4c", and T8 requires row 4b to be asserted explicitly rather than inferred from its siblings. | — |
| NIT | Driver (structural) | Cross-reference check: Success Criterion 4 ("G6 ... lands that state on row 8f → `/do-pr-review`") contradicts No-Go "DO NOT change rows 8f or 10". Resolved by BLOCKER 2's revision; recorded here so the cross-reference table is honest. | **ADDRESSED (revision 1) — resolved via blocker 2.** Success Criterion 4 no longer claims a row-8f landing; it asserts `Blocked(guard_id='NO_RULE')`. The No-Go is now "DO NOT change row 8f" and states affirmatively that row 10 is in scope. The cross-reference is consistent in both directions. | — |

**Round 2 (2026-09-10) — `READY TO BUILD (with concerns)`.** FULL depth, roster 3/3 complete,
3/3 grounded. Mode: independent roster (3 critics). **Zero blockers.** Every round-1
disposition was spot-checked as landed in the plan body, not merely recorded in the table
above. Three concerns for BUILD to carry; all three were independently re-verified by the
driver against `main` @ `6cd510502` before recording — none is carried on a critic's word.
The driver also confirmed at this HEAD that every `agent/sdlc_router.py` line reference the
plan cites is accurate, that `_latest_review_head_sha` exists at `:345`, and that row 10 is
the last entry in `DISPATCH_RULES`.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | History & Consistency (Consistency Auditor); driver-verified | Two **Documentation** checkboxes instruct the builder to update "the same SKILL.md dispatch table's **row 10** entry" and "the **plan-stage rows** in the same SKILL.md dispatch table" (rows 1, 2, 2b, 2c, 3). **No such row-numbered dispatch table exists in `.claude/skills-global/do-sdlc/SKILL.md`** — its only table near there is the Step 3.5 *guard* table (`:243-254`, rows `T`/`G1`…`G6`), and Step 4 deliberately has no hand-authored row table. Re-introducing one is actively forbidden by `tests/unit/test_sdlc_skill_md_parity.py::test_step4_has_no_hand_authored_dispatch_table`, which fails on any line matching `^\|\s*\d+[a-z]?\s*\|` inside Step 4. Success Criterion 12 ("every checkbox under Documentation is done") is therefore unsatisfiable as written. Related: **Test Impact never lists `tests/unit/test_sdlc_skill_md_parity.py`** even though the plan edits the exact SKILL.md guard rows that test parses. | pending (BUILD carries) | Delete the two dispatch-table checkboxes; the row-level routing change needs no SKILL.md edit because SKILL.md carries no row table. Keep the three that are real and whose line refs the driver verified: G3's ladder at `:248`, G6's condition at `:254`, the "Open-PR step-asides" note at `:263` — and note that `:263`'s note is written in terms of **guards** (G1/G5/G3/G7), not rows, so reword it to add the shared `_plan_stage_stood_down` condition rather than "instead of listing rows individually". Add `tests/unit/test_sdlc_skill_md_parity.py` to **Test Impact** as AUDIT: `test_every_guard_has_skill_md_row`, `test_g6_guard_row_present_in_skill_md` and `test_escaped_pipe_in_cell_is_preserved` all parse the guard table being edited, and `test_every_dispatch_rule_has_documented_predicate` requires every `DISPATCH_RULES` predicate to keep a non-empty `__doc__` (satisfied by the `__doc__ =` reassignments at `agent/sdlc_router.py:2037-2060`, which the sweep must not disturb). Run it in step 11 and step 12. |
| CONCERN | Risk & Robustness (Skeptic); driver-verified | **spike-1 and Data Flow name `tools/sdlc_next_skill.py` as the sole producer of `context['pr_head_sha']`; it is not the sole caller of the router.** `agent/session_runner/runner.py:1557` calls `decide_next_dispatch(stage_states, meta)` with **no `context` argument at all**, so `context` resolves to `{}` and `pr_head_sha` is unconditionally absent on that path regardless of real freshness. Post-fix, a genuinely merge-ready lane read through that path gets `Blocked(guard_id='NO_RULE')` where it previously got `Dispatch(/do-merge)`. Driver-traced: the consumer is the completion guard's `_load_ledger` → `next_skill = getattr(decision, "skill", None)`, and `next_skill` feeds only `completion_guard._reroute_message` (`:85-87`), which already falls back to "the next pipeline stage (run `sdlc-tool next-skill`)". The allow/refuse decision comes from `is_pipeline_complete`, not from this value. So the blast radius is **advisory nudge text only** — which is why this is a concern, not a blocker — but the plan's "every reachable path" / "sole producer" framing is factually incomplete. | pending (BUILD carries) | Do **not** change `agent/sdlc_router.py` for this, and do **not** add a `context` argument at `agent/session_runner/runner.py:1557` (that is a different lane's file surface and would widen the diff). Correct the claim: spike-1's Result and the **Data Flow** section should say the CLI producer is the only caller that assembles a real `context`, and name `agent/session_runner/runner.py:1557` as a second, context-less caller whose only consumer of the result is the completion guard's advisory `reroute_message`. Add a Risks row: "merge-ready nudge text degrades from `/do-merge` to the generic fallback on the context-less runner path — cosmetic, fail-open, no gate effect." |
| CONCERN | Scope & Value (Simplifier) | **Appetite's bounding facts do not bound the largest piece of work in the plan.** Appetite lists "two new small predicates, seven predicate call sites edited, three terminal merge-site changes … and one test file extended" — but **Test Impact** separately requires AUDITing four *existing* suites (`..._dispatch_rows.py`, `..._terminal.py`, `..._convergence.py`, `tests/unit/test_sdlc_router.py`) and calls one of them "**the largest audit in the lane**" because "row 10 is the router's default merge landing and fixtures that omit `pr_head_sha` are common". That audit is open-ended per-fixture judgement, not a bounded edit, and it is invisible in the appetite estimate — the exact shape of a Small-appetite plan that runs long and then gets narrowed under pressure. | pending (BUILD carries) | Additive, no scope change. Add the audit to Appetite's bounding facts with a measured count taken before step 9 begins: `grep -c` the `pr_head_sha`-less merge fixtures in `tests/unit/sdlc_router_decision/test_sdlc_router_decision_terminal.py` and `..._convergence.py` and state the number. Then give **Step-by-Step task 9** an explicit stop-and-report threshold — e.g. if the audit requires editing more than ~15 existing assertions, stop and report to the supervisor rather than continuing, since a fixture count that large is evidence the change is broader than the plan modelled. The disposition rule itself is already correct and must not be relaxed: supply the real `pr_head_sha` key, never weaken the assertion. |

## Resolved Questions

1. **Should row 2b (`_rule_critique_verdict_stale`, `:1594`) also route through
   `_plan_stage_stood_down`?** **RESOLVED — YES, in this lane** (critique + design owner,
   2026-09-10). The ratified design names row 2b as the pattern *source*, which says where
   the shape came from, not that it must keep its own copy. Leaving it would ship the sweep
   with the last hand-written copy of the exact condition the sweep exists to express once,
   and would make Success Criterion 5 false on landing. The conversion is provably
   behavior-identical (2b's `:1641-1644` pair is literally the helper's body, in the same
   order) and costs two lines. Folded into **Solution step 5** as a fifth row, pinned by
   T5b and its T5c negative control, and named in Success Criteria 5 and 6.
2. **Is row 10 in scope for the #3260 widening?** **RESOLVED — YES** (design owner, after
   the critique's driver probe). The ratified design's Part B claim that G6's absent-key
   fall-through lands on row 8f is factually wrong; a two-site fix relocates the hole to
   row 10. See **Solution step 3b** and **Freshness Check**.

## Open Questions

None. Both questions above are resolved and folded into the plan; the critique's six
findings are all dispositioned as addressed. Nothing is waiting on human input.
