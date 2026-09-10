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

Fall-through is already correct and needs no other edit: `CHANGES REQUESTED` now reaches
leg 2 (`/do-patch`), and a stale or unverifiable APPROVED reaches leg 4 (`/do-pr-review`).

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
directly above (`:971-975`) is updated in the same hunk to describe the absent-key case as
well, so the comment stops overstating the code — that overstatement is the whole reason
this hunk is in scope.

**Conditions on this change, non-negotiable:**
- Strictly the predicate swap plus its comment. Do **not** touch G6's other gates
  (`pr_number` `:953-955`, `pr_merge_state`, `ci_all_passing`, the DOCS gate, the
  `REVIEW_APPROVED` gate `:966-969`) and do **not** touch row 8f.
- **The PR body must name the deliberate widening** and cite the WS3d comment at
  `agent/sdlc_router.py:971-975`, so a reviewer seeing an out-of-scope hunk understands why
  it is there.
- Fall-through is safe and verified: absent key → not verified fresh → `return None` →
  dispatch table → row 8f → `/do-pr-review`.
- Add `Refs #2062` alongside the closers.

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

### 5. Route the four plan-stage rows through it

| Row | Function | Line | Today | Change |
|-----|----------|------|-------|--------|
| 1 | `_rule_no_plan` | `:1121` | `pr_number` only | replace with `_plan_stage_stood_down` |
| 2 | `_rule_plan_not_critiqued` | `:1153` | **neither** | add `_plan_stage_stood_down` as the first check |
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

**Keep rows 4a/4c's `build_status in (None, "pending", "ready")` gates.** They are strictly
NARROWER than the shared stand-down — they also exclude `BUILD == failed`. The helper is
added *alongside* them, **never substituted for them**; substituting would be a behavior
change. Row 4a is otherwise untouched.

### 7. Docs

Update `docs/features/gh-stale-state-verdict-gate.md` to describe the two-predicate split
and the terminal-dispatch rule (see **Documentation**).

### What this plan does NOT change

- `_review_verdict_head_is_stale` itself, or its remaining call sites at `:527` (G3 leg 3),
  `:1998` (row 8f) and `:2029` (row 10).
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
| T5 | row 3 | `NEEDS REVISION` verdict, `BUILD=in_progress`, no PR, branch exists | `Dispatch(/do-build, row_id='5')` |
| T6 | rows 1/2/2c/3 negative control | `BUILD=pending`, no PR | each row still fires its own skill — the sweep must not disable the rows |
| T7 | rows 4b/4c | the `pr_number` and `BUILD=completed` states each row already refuses | unchanged answers (refactor is behavior-identical) |
| T8 | row 4a/4c narrowing guard | `BUILD=failed`, with-concerns verdict, no PR | rows 4a/4c still decline — proves the narrower gate was not replaced |
| T9 | G3 leg 1 | `REVIEW=completed`, `DOCS=completed`, verdict `CHANGES REQUESTED` at live head | `Dispatch(/do-patch, row_id='G3')` (leg 2) |
| T10 | G3 leg 1 | markers completed, `APPROVED` recorded against an OLDER head | `Dispatch(/do-pr-review, row_id='G3')` (leg 4) |
| T11 | G3 leg 1 | markers completed, `APPROVED`, **`pr_head_sha` ABSENT from context** | `/do-pr-review`, NOT `/do-merge` |
| T12 | G3 leg 1 | markers completed, `APPROVED`, `pr_head_sha == ""` + `pr_head_sha_lookup_failed` | `/do-pr-review`, NOT `/do-merge` |
| T13 | G3 leg 1 positive control | markers completed, `APPROVED` at the live head | still `Dispatch(/do-merge, row_id='G3')` |
| T14 | **G6, absent key** | `pr_number`, `pr_merge_state=CLEAN`, `ci_all_passing=True`, `DOCS=completed`, `APPROVED`, **`pr_head_sha` ABSENT** | `guard_g6_terminal_merge_ready` returns `None`; `decide_next_dispatch` → `/do-pr-review` via row 8f |
| T15 | G6 positive control | same but `pr_head_sha` matches the verdict's head | still `Dispatch(/do-merge, row_id='G6')` |
| T16 | G6 stale control | same but `pr_head_sha` differs | still `None` → row 8f (pre-existing behavior, pinned) |
| T17 | `_review_verdict_head_is_stale` unchanged | absent key, with a recorded verdict | still returns `False` — pins that the existing contract was not modified |

**T14 is the mandatory RED for the G6 widening.** The RED proof must be on the **ABSENT-key**
case specifically; T16's stale-key path already passes today and proves nothing about this
change. If T14 is green before the code change, the test is wrong — fix the test, do not
proceed.

### Guard-ordering hygiene for the G3 probes

G3 only engages when `last_dispatched_skill` / `proposed_skill` puts it in scope, and G6 can
answer first. Follow the recon's shape: set `last_dispatched_skill=/do-plan-critique` and
`pr_merge_state="DIRTY"` on the G3 probes so G6 cannot pre-empt them, and use
`pr_merge_state="CLEAN"` only on the G6 probes.

## Test Impact

- [ ] `tests/unit/sdlc_router_decision/test_sdlc_router_decision_plan_rule_standdown.py` —
      UPDATE: extend with T1–T17 above. The existing `TestRow2bStandsDownOnceBuildStarted`
      and `TestG3DocsLeg` classes stay as-is; new classes are added alongside them, and the
      module docstring's line "whose missing `pr_number` step-aside is tracked as #3249" is
      updated to record that #3249 has landed.
- [ ] `tests/unit/test_sdlc_router.py:1484-1488` — UPDATE (verify only): this is the direct
      unit test of `_review_verdict_head_is_stale`. That function is deliberately unchanged,
      so these cases must stay green **unmodified**. If any of them needs editing, the
      existing predicate was touched and the change is wrong. T17 pins the same contract from
      the router-decision side.
- [ ] `tests/unit/sdlc_router_decision/test_sdlc_router_decision_dispatch_rows.py` — AUDIT:
      the row-by-row table tests. Any case asserting rows 1/2/2c/3/4b/4c fire while
      `BUILD in (in_progress, completed)` or a PR is open is asserting the defect and must be
      UPDATED to the new expected routing, with the change called out in the PR body.
- [ ] `tests/unit/sdlc_router_decision/test_sdlc_router_decision_terminal.py` and
      `test_sdlc_router_decision_convergence.py` — AUDIT: these exercise G6 and the merge
      fast-path. Any case that reaches `/do-merge` without a `pr_head_sha` in context now
      routes to `/do-pr-review` and must be UPDATED to supply the key (the shape production
      actually produces) rather than by relaxing the assertion.
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
  rows the issue names (1, 2, 2c, 3, plus the 4b/4c duplication). PR-stage and patch-stage
  rows have different correct step-asides and are a separate class.
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
| The G6 widening reads as an out-of-scope hunk and gets bounced at review | Medium | PR body names it explicitly as a ratified widening and cites the WS3d comment at `agent/sdlc_router.py:971-975`. Non-negotiable, not optional prose. |
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
- **DO NOT change rows 8f or 10.** They already call the existing predicate and need no
  change; row 8f is the landing row this plan depends on.
- **DO NOT replace rows 4a/4c's `build_status in (None, "pending", "ready")` gates** with the
  shared helper. Those gates are strictly narrower (they also exclude `BUILD == failed`);
  substituting would be a behavior change. Add alongside, never substitute.
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
      terminal `/do-merge` dispatch only), and state the rule that terminal merge dispatch
      uses the latter.
- [ ] Update `.claude/skills-global/do-sdlc/SKILL.md:248` — G3's ladder description: leg 1 now
      reads "`/do-merge` (REVIEW and DOCS complete, verdict `APPROVED`, head verified fresh)".
- [ ] Update `.claude/skills-global/do-sdlc/SKILL.md:254` — G6's condition row: add "AND the
      REVIEW verdict's head is verified fresh against `context['pr_head_sha']`".
      Edit in place; do not replace-and-rename (it is hardlinked to `~/.claude/skills/`).
- [ ] Update the plan-stage rows in the same SKILL.md dispatch table so rows 1, 2, 2c and 3
      record the plan-stage stand-down, matching what row 2b already documents.
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
   `False` on an ABSENT `pr_head_sha` key, and is called from exactly two sites: G3 leg 1
   (`:518`) and `guard_g6_terminal_merge_ready` (`:976`).
2. `_review_verdict_head_is_stale` is byte-identical to its pre-change form, and
   `tests/unit/test_sdlc_router.py:1484-1488` passes **unmodified**.
3. G3 leg 1 requires `REVIEW_APPROVED in review_verdict_norm` AND a verified-fresh head.
   `CHANGES REQUESTED` routes to `/do-patch`; a stale or unverifiable `APPROVED` routes to
   `/do-pr-review`; a fresh `APPROVED` with DOCS complete still routes to `/do-merge`.
4. G6 returns `None` on an absent `pr_head_sha`, and `decide_next_dispatch` lands that state
   on row 8f → `/do-pr-review`. G6's other gates are untouched and its `:971-975` comment now
   describes the code accurately.
5. `_plan_stage_stood_down` exists as a single shared predicate and is the **only** place the
   #3249 condition is written. `grep -c 'meta.get("pr_number")' agent/sdlc_router.py` is
   strictly lower than before, and no plan-stage row re-states the BUILD condition inline.
6. Rows 1, 2, 2c and 3 all call it. Row 2 — which had no step-aside at all — stands down on
   both an open PR and a running/completed BUILD.
7. Rows 4b and 4c each check `pr_number` exactly **once**, and rows 4a/4c retain their
   narrower `build_status in (None, "pending", "ready")` gates.
8. Row 5 (`_rule_branch_exists_no_pr`) is **unchanged**. `git diff` shows no hunk touching it.
9. T1–T17 all pass, and each new assertion has a captured RED run from before the fix. The
   T14 RED is on the ABSENT-key case.
10. Full `tests/unit/sdlc_router_decision/` and `tests/unit/test_sdlc_router.py` are green via
    `scripts/pytest-clean.sh`; every existing assertion that changed is enumerated in the PR body.
11. `python -m ruff check` and `python -m ruff format` clean.
12. Every checkbox under **Documentation** is done.
13. PR body names the G6 widening as deliberate and cites `agent/sdlc_router.py:971-975`.
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
2. **Write the RED tests first.** Add T1–T17 to
   `tests/unit/sdlc_router_decision/test_sdlc_router_decision_plan_rule_standdown.py`. Run
   them via `scripts/pytest-clean.sh` with targeted node IDs and **capture the failing
   output**. Confirm T14 (G6, ABSENT key) is RED — if it is green, the test is wrong. Commit
   the RED tests.
3. **Add `_review_verdict_head_is_verified_fresh`** next to `_review_verdict_head_is_stale`
   (`:1385`), with the docstring from Solution step 1. Do not modify the existing predicate.
   Commit.
4. **Apply it to G3 leg 1** (`:518`) per Solution step 2. Run T9–T13; confirm green. Commit.
5. **Apply it to G6** (`:976`) per Solution step 3 — the one-line swap plus the `:971-975`
   comment correction, nothing else. Run T14–T16; confirm T14 flipped RED→green and T15/T16
   never regressed. Commit.
6. **Add `_plan_stage_stood_down`** per Solution step 4. Commit.
7. **Route rows 1, 2, 2c, 3 through it** per Solution step 5, updating each row's step-aside
   comment to name the helper. Run T1–T6; confirm green, including the T6 negative controls.
   Commit.
8. **Fold rows 4b/4c's duplicate `pr_number` checks** per Solution step 6, keeping 4a/4c's
   narrower `build_status` gates. Run T7, T8 and the whole
   `test_sdlc_router_decision_with_concerns.py` suite unmodified; any red means revert this
   task. Commit.
9. **Audit the existing suites** named in **Test Impact**
   (`..._dispatch_rows.py`, `..._terminal.py`, `..._convergence.py`,
   `tests/unit/test_sdlc_router.py`). For each failure, decide UPDATE-the-expectation vs
   the-change-is-wrong, and record every changed assertion for the PR body. Confirm
   `test_sdlc_router.py:1484-1488` needed no edit. Commit.
10. **Run the full router suites** — `tests/unit/sdlc_router_decision/` and
    `tests/unit/test_sdlc_router.py` — via `scripts/pytest-clean.sh`. Never bare `pytest`,
    never `pkill -f pytest`.
11. **Documentation pass**: every checkbox under **Documentation**. Edit
    `.claude/skills-global/do-sdlc/SKILL.md` in place (hardlinked — never replace-and-rename).
    Commit.
12. **Quality gate**: `python -m ruff check` and `python -m ruff format`. Commit.
13. **Open the PR.** Body must include: the RED evidence for each new assertion (T14's
    ABSENT-key RED called out specifically), an explicit paragraph naming the **G6 widening as
    deliberate** and citing the WS3d comment at `agent/sdlc_router.py:971-975`, and the list of
    changed existing assertions. Trailers: `Closes #3260`, `Closes #3249`, `Refs #2062`.

## Verification

- **Reachability, not inference.** For each of rows 1, 2, 2c, 3, 4b, 4c and for G3 leg 1 and
  G6, a direct `decide_next_dispatch` (or `guard_g6_terminal_merge_ready`) probe demonstrates
  both the pre-fix wrong answer and the post-fix right one. No claim of the form "reading the
  predicate, it must fire."
- **RED-before-green ledger.** Each new assertion has a captured failing run from before its
  fix landed. T14's RED is on the ABSENT-key input specifically; a RED captured on the
  stale-key input does not count and must be redone.
- **Negative controls.** T6, T13 and T15 prove the change did not simply disable the rows and
  guards it touches — the happy paths still route as before.
- **Non-substitution proof.** T8 (`BUILD == failed`) proves rows 4a/4c kept their narrower
  gate rather than inheriting the broader helper.
- **Untouched-surface proof.** `git diff main -- agent/sdlc_router.py` is read end to end and
  shows: no hunk in `_review_verdict_head_is_stale`, no hunk in `_rule_branch_exists_no_pr`
  (row 5), no hunk in rows 8f/10, and no hunk in `tools/merge_predicate.py`.
- **Suite green.** `scripts/pytest-clean.sh` over `tests/unit/sdlc_router_decision/` and
  `tests/unit/test_sdlc_router.py`, with a non-zero test count (the `ZERO TESTS EXECUTED`
  guard must not have fired).
- **Lint.** `python -m ruff check` and `python -m ruff format` clean.
- **Docs.** Every **Documentation** checkbox ticked; the SKILL.md hardlink intact after the
  edit.

## Critique Results

FULL depth (force-FULL: `agent/sdlc_router.py` is a doctrine path). Roster 3/3 complete,
3/3 grounded. Mode: independent roster (3 critics). Both BLOCKERs were independently
re-verified by the driver with live `decide_next_dispatch` / `guard_g3_pr_lock` probes
against `main` before being recorded — neither is an inference.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness (Skeptic); driver-verified | Solution step 2 claims "Fall-through is already correct and needs no other edit" — it is not. G3 leg 3 (`agent/sdlc_router.py:524-528`) never checks `docs_status`; it only works today because the unconditional leg 1 intercepts every `docs_status == completed` case first. Once leg 1 requires a verified-fresh head, the ABSENT-key state (T11) falls to leg 3, which reads the UNMODIFIED `_review_verdict_head_is_stale` (False on an absent key) and dispatches `/do-docs` with the reason "review APPROVED and docs pending" while DOCS is `completed`. T11's expected `/do-pr-review` is therefore wrong as written. Driver probe: `guard_g3_pr_lock` with `REVIEW=completed, APPROVED, pr_head_sha` absent returns `Dispatch(/do-docs, row_id='G3')`. | pending | Add `and docs_status != STATUS_COMPLETED` to G3 leg 3's `elif` condition in the same hunk as the leg-1 change. Leg 3 keeps its existing `_review_verdict_head_is_stale` call, so Success Criterion 1's "exactly two call sites" for the NEW predicate still holds. Record the leg-3 gate in Solution step 2, in the Step-by-Step task 4, and fix T11's expected value; add a T11b covering `DOCS=pending` + absent key so the leg-3 narrowing is itself pinned. |
| BLOCKER | History & Consistency (Consistency Auditor); driver-verified | The G6 fall-through claim is false, and it makes Success Criterion 4, T14 and the No-Gos mutually unsatisfiable. On the ABSENT-key state G6 returns `None`, but row 8f (`_rule_review_verdict_head_stale`, `:1976-1999`) calls `_review_verdict_head_is_stale`, which returns `False` on an absent key — so 8f declines. Row 9 declines (DOCS complete). Row 10 (`_rule_ready_to_merge`, `:2018-2035`) calls the same inert predicate, so it also reads "not stale" and dispatches `/do-merge`. Driver probe with G6 removed from `GUARDS`: `Dispatch(/do-merge, row_id='10')`. The absent-key merge hole is relocated from G6 to row 10, not closed — and the No-Gos forbid touching row 10. | pending | Row 10 must join the widening: `if not _review_verdict_head_is_verified_fresh(stage_states, meta, context): return False` in `_rule_ready_to_merge` (row 10 is a terminal `/do-merge` dispatch, exactly the class the new predicate is scoped to). Row 8f stays on the existing predicate. This makes the new predicate's call-site count THREE — update Success Criterion 1 and the No-Go "DO NOT change rows 8f or 10" to "DO NOT change row 8f" — and T14's expectation becomes `/do-pr-review` via row 8f only if 8f is also widened, so state explicitly which row owns the absent-key landing and pin it with a `row_id` assertion, not just a skill assertion. Escalate to the design owner before building: this is a fact that the ratified design's Part B got wrong, not a scope choice the builder may take alone. |
| CONCERN | Scope & Value (Simplifier) | The Appetite section pre-authorizes cutting the rows 4b/4c de-duplication under time pressure, but that fold is explicit ratified scope (design Part C: "Also fold in rows 4b/4c") and is separately hard-required by the plan's own Success Criterion 7 and by T7/T8. As written a builder could take the cut and ship a PR that fails the plan's own Definition of Done — the same "narrow it to keep the diff small" failure mode #3249 was filed against. | pending | Replace the cut-line: the only cuttable item at Small appetite is the optional docs polish, not ratified scope. If 4b/4c is nonetheless cut, Success Criterion 7 and tests T7/T8 must be struck in the same revision and the drop named in the PR body — never silently. The 4b/4c edit itself is two lines per row: swap the leading `if meta.get("pr_number") or stage_states.get("BUILD") == STATUS_COMPLETED:` for `if _plan_stage_stood_down(stage_states, meta):` and delete the second `if meta.get("pr_number"): return False`. |
| CONCERN | History & Consistency (Archaeologist) | The Problem section says G6's WS3d comment at `:971-975` "already asserts the behavior it does not have" and calls it "the same lie." The comment's literal text covers the EMPTY-sentinel lookup-failure case only — which `_review_verdict_head_is_stale` handles correctly. It never mentions the ABSENT-key case, which is the actual gap. A reviewer reading the comment literally will read the PR body's citation as overclaimed. | pending | Reword to: the comment states a fail-closed intent that is broader than the delivered behavior (it covers the empty sentinel but not the absent key). Carry the same wording into the PR-body paragraph required by Success Criterion 13, so the citation of `agent/sdlc_router.py:971-975` matches what the comment actually says. |
| NIT | Driver (structural) | Solution step 6 and No-Go 4 both say "rows 4a/4c" carry the narrower `build_status in (None, "pending", "ready")` gate, but row 4b carries it too (`:1247`). T8 likewise only names 4a/4c, so row 4b's `BUILD == failed` behavior is unpinned by any test. | pending | — |
| NIT | Driver (structural) | Cross-reference check: Success Criterion 4 ("G6 ... lands that state on row 8f → `/do-pr-review`") contradicts No-Go "DO NOT change rows 8f or 10". Resolved by BLOCKER 2's revision; recorded here so the cross-reference table is honest. | pending | — |

---

## Open Questions

None blocking. One scope call is deliberately surfaced rather than taken unilaterally:

1. **Should row 2b (`_rule_critique_verdict_stale`, `:1594`) also route through
   `_plan_stage_stood_down`?** The ratified design enumerates rows 1/2/2c/3 plus 4b/4c as the
   targets and cites row 2b (`:1641-1644`) as the *pattern source*, so this plan does **not**
   convert it. The consequence is that after this lands, row 2b holds the last hand-written
   copy of a condition the change exists to express once. Converting it is behavior-identical
   (its inline pair is literally the helper's body), but it is outside the ratified list, so
   the decision belongs to the critique, not to the builder. **Default if unanswered: leave
   row 2b as-is and stay inside the ratified scope.**

   **RULED (critique, 2026-09-10): convert row 2b through `_plan_stage_stood_down` in this
   lane.** The default is overturned. The design names 2b as the pattern *source*, which
   says where the shape came from, not that it must keep its own copy; leaving it means the
   sweep ships with the last hand-written copy of the exact condition it exists to express
   once, and the plan's own Success Criterion 5 ("`_plan_stage_stood_down` ... is the **only**
   place the #3249 condition is written") is then false on landing. The conversion is
   provably behavior-identical: row 2b's `:1641-1644` pair is literally the helper's body,
   in the same order. Scope cost is two lines. Fold it into Solution step 5 as a fifth row,
   add it to the table there, keep its long `#3237` docstring (rewritten to name the helper),
   and add a T5b probe — row 2b with `BUILD=in_progress`, no PR, branch exists →
   `Dispatch(/do-build, row_id='5')` — plus a negative control that 2b still fires on a
   genuinely stale critique verdict with `BUILD=pending`. Success Criterion 5 and 6 must both
   name row 2b.
